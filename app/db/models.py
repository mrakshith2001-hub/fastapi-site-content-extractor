from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Operation(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PageStatus(StrEnum):
    ACTIVE = "active"
    DELETED = "deleted"
    FAILED = "failed"


class S3Object(Base):
    """Application-side inventory: the last known state of every object under the prefix."""

    __tablename__ = "s3_objects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    path: Mapped[str] = mapped_column(String(1024), index=True)
    asset_type: Mapped[str] = mapped_column(String(32))
    etag: Mapped[str | None] = mapped_column(String(128))
    version_id: Mapped[str | None] = mapped_column(String(255))
    size: Mapped[int | None] = mapped_column(BigInteger)
    last_modified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_operation: Mapped[str] = mapped_column(String(16))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    events: Mapped[list["FileEvent"]] = relationship(back_populates="s3_object", cascade="all, delete-orphan")

    @property
    def fingerprint(self) -> str:
        return make_fingerprint(self.etag, self.version_id, self.last_modified)


class FileEvent(Base):
    """Append-only change log. The unique constraint makes re-running ingestion a no-op."""

    __tablename__ = "file_events"
    __table_args__ = (UniqueConstraint("s3_object_id", "operation", "fingerprint", name="uq_file_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    s3_object_id: Mapped[int] = mapped_column(ForeignKey("s3_objects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="SET NULL"))
    operation: Mapped[str] = mapped_column(String(16))
    fingerprint: Mapped[str] = mapped_column(String(512))
    etag: Mapped[str | None] = mapped_column(String(128))
    version_id: Mapped[str | None] = mapped_column(String(255))
    last_modified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    s3_object: Mapped[S3Object] = relationship(back_populates="events")


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.RUNNING)
    lookback_hours: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str | None] = mapped_column(String(512))
    content: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=0)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default=PageStatus.ACTIVE)
    error: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_rendered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    dependencies: Mapped[list["PageDependency"]] = relationship(
        back_populates="page", cascade="all, delete-orphan", order_by="PageDependency.asset_path"
    )


class PageDependency(Base):
    """Edge in the page -> asset graph, used to decide which pages a file change affects."""

    __tablename__ = "page_dependencies"
    __table_args__ = (UniqueConstraint("page_id", "asset_path", name="uq_page_dependency"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    asset_path: Mapped[str] = mapped_column(String(1024), index=True)
    source: Mapped[str] = mapped_column(String(16))

    page: Mapped[Page] = relationship(back_populates="dependencies")


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on the way back, so treat naive datetimes as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def make_fingerprint(etag: str | None, version_id: str | None, last_modified: datetime | None) -> str:
    stamp = as_utc(last_modified).isoformat() if last_modified else ""
    return f"{etag or ''}|{version_id or ''}|{stamp}"
