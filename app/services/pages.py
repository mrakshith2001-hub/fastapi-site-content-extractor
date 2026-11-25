"""Render pages, persist extracted content and refresh the dependency graph."""

import asyncio
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.db.models import Page, PageDependency, PageStatus, utcnow
from app.services.content import build_content, content_hash, word_count
from app.services.dependencies import static_dependencies
from app.services.mirror import SiteMirror
from app.services.renderer import RenderResult, SiteRenderer

log = get_logger(__name__)


@dataclass
class PageOutcome:
    path: str
    status: str  # changed | unchanged | failed | retired
    page_id: int | None = None
    version: int | None = None
    reasons: list[str] | None = None
    error: str | None = None


async def load_dependency_map(session: AsyncSession) -> dict[str, set[str]]:
    rows = await session.execute(
        select(Page.path, PageDependency.asset_path)
        .join(PageDependency, PageDependency.page_id == Page.id)
        .where(Page.status != PageStatus.DELETED)
    )
    graph: dict[str, set[str]] = {}
    for page_path, asset_path in rows:
        graph.setdefault(page_path, set()).add(asset_path)
    return graph


class PageProcessor:
    def __init__(
        self, session: AsyncSession, mirror: SiteMirror, renderer: SiteRenderer | None, concurrency: int = 3
    ):
        self._session = session
        self._mirror = mirror
        self._renderer = renderer
        self._semaphore = asyncio.Semaphore(concurrency)

    async def _render(self, path: str) -> RenderResult | Exception:
        async with self._semaphore:
            try:
                return await self._renderer.render(path)
            except Exception as exc:
                log.exception("page.render_failed", path=path)
                return exc

    async def process(self, plan: dict[str, list[str]]) -> list[PageOutcome]:
        """Render pages concurrently, then write results sequentially on one session."""
        paths = [path for path in plan if self._mirror.exists(path)]
        results = await asyncio.gather(*(self._render(path) for path in paths))
        outcomes = []
        for path, result in zip(paths, results):
            outcome = await self._store(path, result)
            outcome.reasons = plan[path]
            outcomes.append(outcome)
        for path in sorted(plan.keys() - set(paths)):
            outcomes.append(PageOutcome(path, "failed", reasons=plan[path], error="page file not found in mirror"))
        return outcomes

    async def _get_or_create(self, path: str) -> Page:
        page = await self._session.scalar(
            select(Page).where(Page.path == path).options(selectinload(Page.dependencies))
        )
        if page is None:
            page = Page(path=path, url=self._renderer.url_for(path), content="", version=0)
            self._session.add(page)
            await self._session.flush()
        return page

    async def _store(self, path: str, result: RenderResult | Exception) -> PageOutcome:
        page = await self._get_or_create(path)
        now = utcnow()
        page.last_rendered_at = now

        if isinstance(result, Exception):
            page.status = PageStatus.FAILED
            page.error = str(result)
            return PageOutcome(path, "failed", page.id, page.version, error=page.error)

        content = build_content(result.blocks, result.popups)
        new_hash = content_hash(result.title, content)
        changed = new_hash != page.content_hash or page.status != PageStatus.ACTIVE
        if changed:
            page.title = result.title or None
            page.content = content
            page.content_hash = new_hash
            page.word_count = word_count(content)
            page.version += 1
            page.extracted_at = now
        page.url = result.url
        page.status = PageStatus.ACTIVE
        page.error = None

        html = self._mirror.read_text(path) or ""
        static = static_dependencies(path, html, self._mirror.read_text)
        await self._replace_dependencies(page, static, result.requested)

        log.info("page.stored", path=path, changed=changed, version=page.version)
        return PageOutcome(path, "changed" if changed else "unchanged", page.id, page.version)

    async def _replace_dependencies(self, page: Page, static: set[str], runtime: set[str]) -> None:
        await self._session.execute(delete(PageDependency).where(PageDependency.page_id == page.id))
        for asset in sorted(static | runtime):
            source = "both" if asset in static and asset in runtime else ("static" if asset in static else "runtime")
            self._session.add(PageDependency(page_id=page.id, asset_path=asset, source=source))
        await self._session.flush()

    async def retire(self, paths: list[str]) -> list[PageOutcome]:
        outcomes = []
        for path in paths:
            page = await self._session.scalar(select(Page).where(Page.path == path))
            if page is None:
                continue
            page.status = PageStatus.DELETED
            await self._session.execute(delete(PageDependency).where(PageDependency.page_id == page.id))
            outcomes.append(PageOutcome(path, "retired", page.id, page.version, reasons=["page deleted"]))
        await self._session.flush()
        return outcomes
