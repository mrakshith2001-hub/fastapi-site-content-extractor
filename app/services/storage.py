from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aioboto3
from botocore.exceptions import ClientError

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class RemoteObject:
    key: str
    etag: str | None
    size: int
    last_modified: datetime
    version_id: str | None = None


class S3Storage:
    """Thin async wrapper over the S3 calls the pipeline needs."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._session = aioboto3.Session()

    def _client(self):
        s = self._settings
        return self._session.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            region_name=s.s3_region,
            aws_access_key_id=s.s3_access_key_id,
            aws_secret_access_key=s.s3_secret_access_key,
        )

    async def list_objects(self, prefix: str) -> AsyncIterator[RemoteObject]:
        async with self._client() as client:
            paginator = client.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._settings.s3_bucket, Prefix=prefix):
                for item in page.get("Contents", []):
                    if item["Key"].endswith("/"):
                        continue  # "folder" placeholder objects
                    yield RemoteObject(
                        key=item["Key"],
                        etag=(item.get("ETag") or "").strip('"') or None,
                        size=item.get("Size", 0),
                        last_modified=item["LastModified"],
                    )

    async def versioning_enabled(self) -> bool:
        async with self._client() as client:
            try:
                response = await client.get_bucket_versioning(Bucket=self._settings.s3_bucket)
            except ClientError as exc:
                log.warning("s3.versioning_check_failed", error=str(exc))
                return False
        return response.get("Status") == "Enabled"

    async def latest_versions(self, prefix: str) -> tuple[dict[str, str], dict[str, datetime]]:
        """Return (key -> current version id, key -> delete-marker time) for a versioned bucket."""
        versions: dict[str, str] = {}
        delete_markers: dict[str, datetime] = {}
        async with self._client() as client:
            paginator = client.get_paginator("list_object_versions")
            async for page in paginator.paginate(Bucket=self._settings.s3_bucket, Prefix=prefix):
                for item in page.get("Versions", []):
                    if item.get("IsLatest"):
                        versions[item["Key"]] = item["VersionId"]
                for marker in page.get("DeleteMarkers", []):
                    if marker.get("IsLatest"):
                        delete_markers[marker["Key"]] = marker["LastModified"]
        return versions, delete_markers

    async def download(self, key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        async with self._client() as client:
            response = await client.get_object(Bucket=self._settings.s3_bucket, Key=key)
            async with response["Body"] as stream:
                data = await stream.read()
        tmp = destination.with_suffix(destination.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(destination)
