from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    path: str
    asset_type: str
    etag: str | None
    version_id: str | None
    size: int | None
    last_modified: datetime | None
    operation: str
    is_deleted: bool
    first_seen_at: datetime
    last_seen_at: datetime
    deleted_at: datetime | None


class FileList(BaseModel):
    total: int
    items: list[FileOut]
