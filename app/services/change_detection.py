"""Pure change detection: compare the live S3 listing with the persisted inventory."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath

from app.db.models import Operation, as_utc
from app.services.storage import RemoteObject

ASSET_TYPES = {
    ".html": "html", ".htm": "html",
    ".css": "css",
    ".js": "javascript", ".mjs": "javascript",
    ".json": "data", ".xml": "data", ".txt": "data",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".svg": "image", ".webp": "image", ".ico": "image", ".avif": "image",
    ".woff": "font", ".woff2": "font", ".ttf": "font", ".otf": "font", ".eot": "font",
}


def asset_type_for(path: str) -> str:
    return ASSET_TYPES.get(PurePosixPath(path).suffix.lower(), "other")


@dataclass(frozen=True)
class KnownObject:
    key: str
    etag: str | None
    version_id: str | None
    is_deleted: bool


@dataclass(frozen=True)
class Change:
    key: str
    operation: Operation
    etag: str | None
    version_id: str | None
    last_modified: datetime | None
    size: int | None = None


def _differs(remote: RemoteObject, known: KnownObject) -> bool:
    # A version id is the strongest signal when both sides have one; otherwise fall back to the ETag.
    if remote.version_id and known.version_id:
        return remote.version_id != known.version_id
    return remote.etag != known.etag


def detect_changes(
    remote: Iterable[RemoteObject],
    known: Mapping[str, KnownObject],
    cutoff: datetime,
    *,
    strict_window: bool = False,
    delete_markers: Mapping[str, datetime] | None = None,
) -> list[Change]:
    """Classify every object as created, updated or deleted relative to the inventory.

    Unchanged objects are omitted, which is what makes repeated runs idempotent.
    """
    delete_markers = delete_markers or {}
    cutoff = as_utc(cutoff)
    changes: list[Change] = []
    seen: set[str] = set()

    for obj in remote:
        seen.add(obj.key)
        previous = known.get(obj.key)
        if previous is None or previous.is_deleted:
            operation = Operation.CREATED
        elif _differs(obj, previous):
            operation = Operation.UPDATED
        else:
            continue
        if strict_window and as_utc(obj.last_modified) < cutoff:
            continue
        changes.append(Change(obj.key, operation, obj.etag, obj.version_id, obj.last_modified, obj.size))

    for key, previous in known.items():
        if key in seen or previous.is_deleted:
            continue
        changes.append(
            Change(key, Operation.DELETED, previous.etag, previous.version_id, delete_markers.get(key))
        )

    return sorted(changes, key=lambda c: (c.operation != Operation.DELETED, c.key))
