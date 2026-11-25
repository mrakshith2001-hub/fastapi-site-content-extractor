"""End-to-end pipeline: S3 listing -> change detection -> mirror sync -> selective re-rendering."""

import asyncio
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models import (
    FileEvent,
    IngestionRun,
    Operation,
    Page,
    PageStatus,
    RunStatus,
    S3Object,
    make_fingerprint,
    utcnow,
)
from app.services.change_detection import Change, KnownObject, asset_type_for, detect_changes
from app.services.impact import PathChange, plan_reprocessing
from app.services.mirror import SiteMirror
from app.services.pages import PageOutcome, PageProcessor, load_dependency_map
from app.services.renderer import SiteRenderer
from app.services.storage import S3Storage

log = get_logger(__name__)


class IngestionBusyError(RuntimeError):
    pass


class PageSourceMissingError(RuntimeError):
    pass


class IngestionFailedError(RuntimeError):
    def __init__(self, run_id: int, cause: Exception):
        super().__init__(f"ingestion run {run_id} failed: {cause!r}")
        self.run_id = run_id
        self.cause = cause


def _insert_ignore(session: AsyncSession, values: dict):
    """INSERT ... ON CONFLICT DO NOTHING for both SQLite and PostgreSQL."""
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return (
        insert(FileEvent)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["s3_object_id", "operation", "fingerprint"])
    )


class IngestionService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        storage: S3Storage,
        renderer_factory: Callable[[], SiteRenderer] | None = None,
    ):
        self._settings = settings
        self._sessions = session_factory
        self._storage = storage
        self._prefix = settings.normalized_prefix
        self.mirror = SiteMirror(settings.mirror_dir, storage, self._prefix)
        self._renderer_factory = renderer_factory or (
            lambda: SiteRenderer(settings.mirror_dir, settings.render_origin, settings.render_timeout_ms)
        )
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ ingestion

    async def run(self, lookback_hours: int | None = None) -> dict:
        if self._lock.locked():
            raise IngestionBusyError("an ingestion run is already in progress")
        async with self._lock, self._sessions() as session:
            hours = lookback_hours or self._settings.lookback_hours
            run = IngestionRun(lookback_hours=hours, status=RunStatus.RUNNING)
            session.add(run)
            await session.commit()
            run_id = run.id
            log.info("ingestion.started", run_id=run_id, lookback_hours=hours)
            try:
                summary = await self._run(session, run, hours)
            except Exception as exc:
                # Undo partial inventory changes so the next run retries them, but keep the run record.
                await session.rollback()
                run.status = RunStatus.FAILED
                run.error = repr(exc)
                run.finished_at = utcnow()
                await session.commit()
                log.exception("ingestion.failed", run_id=run_id)
                raise IngestionFailedError(run_id, exc) from exc
            run.status = RunStatus.SUCCEEDED
            run.finished_at = utcnow()
            run.summary = summary
            await session.commit()
            log.info("ingestion.finished", run_id=run.id, **summary["counts"])
            return {"run_id": run.id, "status": run.status, **summary}

    async def _run(self, session: AsyncSession, run: IngestionRun, hours: int) -> dict:
        now = utcnow()
        cutoff = now - timedelta(hours=hours)

        remote = [obj async for obj in self._storage.list_objects(self._prefix)]
        versioned = await self._storage.versioning_enabled()
        delete_markers = {}
        if versioned:
            versions, delete_markers = await self._storage.latest_versions(self._prefix)
            remote = [replace(obj, version_id=versions.get(obj.key)) for obj in remote]

        inventory = {obj.key: obj for obj in (await session.scalars(select(S3Object))).all()}
        known = {key: KnownObject(key, o.etag, o.version_id, o.is_deleted) for key, o in inventory.items()}
        changes = detect_changes(
            remote, known, cutoff, strict_window=self._settings.strict_window, delete_markers=delete_markers
        )
        events = await self._apply_changes(session, run.id, changes, inventory, now)
        if remote:
            await session.execute(
                update(S3Object).where(S3Object.key.in_([o.key for o in remote])).values(last_seen_at=now)
            )

        to_path = self.mirror.path_for_key
        live_paths = [to_path(obj.key) for obj in remote]
        await self.mirror.sync(
            changed=[to_path(c.key) for c in changes if c.operation != Operation.DELETED],
            deleted=[to_path(c.key) for c in changes if c.operation == Operation.DELETED],
            expected=live_paths,
        )

        plan = plan_reprocessing(
            [PathChange(to_path(c.key), c.operation) for c in changes], await load_dependency_map(session)
        )
        active_pages = set(await session.scalars(select(Page.path).where(Page.status == PageStatus.ACTIVE)))
        for path in live_paths:
            if asset_type_for(path) == "html" and path not in active_pages and path not in plan.to_render:
                plan.to_render[path] = ["page not yet extracted"]

        processor_outcomes = await self._process(session, plan.to_render, plan.to_retire)
        await session.flush()
        return self._summary(now, cutoff, hours, versioned, remote, changes, events, processor_outcomes)

    async def _apply_changes(
        self, session: AsyncSession, run_id: int, changes: list[Change], inventory: dict[str, S3Object], now
    ) -> int:
        written = 0
        for change in changes:
            obj = inventory.get(change.key)
            if change.operation == Operation.DELETED:
                fingerprint = obj.fingerprint
                obj.is_deleted = True
                obj.deleted_at = change.last_modified or now
            else:
                if obj is None:
                    obj = S3Object(
                        key=change.key,
                        path=self.mirror.path_for_key(change.key),
                        asset_type=asset_type_for(change.key),
                        first_seen_at=now,
                    )
                    session.add(obj)
                obj.etag = change.etag
                obj.version_id = change.version_id
                obj.size = change.size
                obj.last_modified = change.last_modified
                obj.is_deleted = False
                obj.deleted_at = None
                fingerprint = make_fingerprint(change.etag, change.version_id, change.last_modified)
            obj.last_operation = change.operation.value
            await session.flush()

            result = await session.execute(
                _insert_ignore(
                    session,
                    {
                        "s3_object_id": obj.id,
                        "run_id": run_id,
                        "operation": change.operation.value,
                        "fingerprint": fingerprint,
                        "etag": change.etag,
                        "version_id": change.version_id,
                        "last_modified": change.last_modified,
                        "detected_at": now,
                    },
                )
            )
            written += result.rowcount or 0
            log.info("ingestion.change", key=change.key, operation=change.operation.value)
        return written

    async def _process(self, session: AsyncSession, to_render: dict[str, list[str]], to_retire: list[str]):
        outcomes: list[PageOutcome] = []
        if to_retire:
            outcomes += await PageProcessor(session, self.mirror, None).retire(to_retire)
        if to_render:
            # The browser is only started when something actually needs rendering.
            async with self._renderer_factory() as renderer:
                processor = PageProcessor(session, self.mirror, renderer, self._settings.render_concurrency)
                outcomes += await processor.process(to_render)
        return outcomes

    @staticmethod
    def _summary(now, cutoff, hours, versioned, remote, changes, events, outcomes) -> dict:
        counts = {op.value: sum(1 for c in changes if c.operation == op) for op in Operation}
        counts["file_events_recorded"] = events
        counts["pages_rendered"] = sum(1 for o in outcomes if o.status in ("changed", "unchanged"))
        counts["pages_content_changed"] = sum(1 for o in outcomes if o.status == "changed")
        counts["pages_retired"] = sum(1 for o in outcomes if o.status == "retired")
        counts["pages_failed"] = sum(1 for o in outcomes if o.status == "failed")
        return {
            "window_start": cutoff.isoformat(),
            "window_end": now.isoformat(),
            "lookback_hours": hours,
            "versioning_enabled": versioned,
            "objects_listed": len(remote),
            "counts": counts,
            "files": [
                {
                    "key": c.key,
                    "operation": c.operation.value,
                    "last_modified": c.last_modified.isoformat() if c.last_modified else None,
                }
                for c in changes
            ],
            "pages": [asdict(o) for o in outcomes],
        }

    # ------------------------------------------------------------------ reprocess

    async def reprocess(self, session: AsyncSession, page: Page) -> PageOutcome:
        if self._lock.locked():
            raise IngestionBusyError("an ingestion run is in progress, try again shortly")
        async with self._lock:
            live = await session.scalars(select(S3Object.path).where(S3Object.is_deleted.is_(False)))
            await self.mirror.sync(changed=[], deleted=[], expected=list(live))
            if not self.mirror.exists(page.path):
                raise PageSourceMissingError(f"{page.path} no longer exists in S3")
            async with self._renderer_factory() as renderer:
                processor = PageProcessor(session, self.mirror, renderer, 1)
                [outcome] = await processor.process({page.path: ["manual reprocess"]})
            await session.commit()
            return outcome
