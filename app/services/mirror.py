"""Local copy of the website, rebuilt incrementally from S3 for the renderer."""

import asyncio
from collections.abc import Iterable
from pathlib import Path

from app.core.logging import get_logger
from app.services.storage import S3Storage

log = get_logger(__name__)


class SiteMirror:
    def __init__(self, root: Path, storage: S3Storage, prefix: str, concurrency: int = 8):
        self.root = root.resolve()
        self._storage = storage
        self._prefix = prefix
        self._semaphore = asyncio.Semaphore(concurrency)

    def path_for_key(self, key: str) -> str:
        return key[len(self._prefix):] if self._prefix and key.startswith(self._prefix) else key

    def key_for_path(self, path: str) -> str:
        return f"{self._prefix}{path}"

    def local_file(self, path: str) -> Path:
        file = (self.root / path).resolve()
        if not file.is_relative_to(self.root):
            raise ValueError(f"path escapes mirror: {path}")
        return file

    def exists(self, path: str) -> bool:
        return self.local_file(path).is_file()

    def read_text(self, path: str) -> str | None:
        file = self.local_file(path)
        return file.read_text(encoding="utf-8", errors="replace") if file.is_file() else None

    async def download(self, paths: Iterable[str]) -> None:
        async def fetch(path: str) -> None:
            async with self._semaphore:
                await self._storage.download(self.key_for_path(path), self.local_file(path))

        await asyncio.gather(*(fetch(path) for path in paths))

    def remove(self, paths: Iterable[str]) -> None:
        for path in paths:
            self.local_file(path).unlink(missing_ok=True)

    async def sync(self, changed: Iterable[str], deleted: Iterable[str], expected: Iterable[str]) -> int:
        """Apply changes, then fetch any inventory file missing locally (e.g. fresh container)."""
        changed = set(changed)
        self.remove(deleted)
        missing = {path for path in expected if path not in changed and not self.exists(path)}
        to_fetch = changed | missing
        await self.download(sorted(to_fetch))
        if missing:
            log.info("mirror.backfilled", files=len(missing))
        return len(to_fetch)
