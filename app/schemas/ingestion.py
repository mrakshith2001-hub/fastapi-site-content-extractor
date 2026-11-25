from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class IngestionRequest(BaseModel):
    lookback_hours: int | None = Field(
        default=None, ge=1, le=24 * 30, description="Override the configured change window (hours)."
    )


class FileChangeOut(BaseModel):
    key: str
    operation: str
    last_modified: datetime | None


class PageOutcomeOut(BaseModel):
    path: str
    status: str
    page_id: int | None
    version: int | None
    reasons: list[str] | None
    error: str | None


class IngestionResult(BaseModel):
    run_id: int
    status: str
    window_start: datetime
    window_end: datetime
    lookback_hours: int
    versioning_enabled: bool
    objects_listed: int
    counts: dict[str, int]
    files: list[FileChangeOut]
    pages: list[PageOutcomeOut]


class IngestionRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    lookback_hours: int
    started_at: datetime
    finished_at: datetime | None
    summary: dict | None
    error: str | None
