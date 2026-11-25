from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DependencyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    asset_path: str
    source: str


class PageSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    path: str
    url: str
    title: str | None
    status: str
    version: int
    word_count: int
    content_hash: str | None
    extracted_at: datetime | None
    last_rendered_at: datetime | None


class PageDetail(PageSummary):
    content: str
    error: str | None
    dependencies: list[DependencyOut]


class PageList(BaseModel):
    total: int
    items: list[PageSummary]


class ReprocessResult(BaseModel):
    content_changed: bool
    page: PageDetail
