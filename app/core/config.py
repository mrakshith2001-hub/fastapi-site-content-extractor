from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Site Content Extractor"
    log_level: str = "INFO"
    log_json: bool = True

    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    auto_create_schema: bool = Field(
        default=True,
        description="Create tables on startup. Disable when Alembic migrations are used.",
    )

    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key_id: str = "minioadmin"
    s3_secret_access_key: str = "minioadmin"
    s3_bucket: str = "ridgeline-site"
    s3_prefix: str = "site/"

    lookback_hours: int = Field(default=24, ge=1)
    strict_window: bool = Field(
        default=False,
        description=(
            "When true, created/updated objects older than the lookback window are ignored. "
            "When false the persisted inventory is authoritative, so a missed run never loses a change."
        ),
    )

    mirror_dir: Path = Path("./data/mirror")
    render_origin: str = "http://site.local"
    render_concurrency: int = Field(default=3, ge=1)
    render_timeout_ms: int = Field(default=20_000, ge=1_000)

    @property
    def normalized_prefix(self) -> str:
        prefix = self.s3_prefix.strip("/")
        return f"{prefix}/" if prefix else ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
