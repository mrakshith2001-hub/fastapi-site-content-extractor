import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request

from app.api.routes import files, ingestion, pages
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import build_engine, build_session_factory, create_schema
from app.services.ingestion import IngestionService
from app.services.storage import S3Storage

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = build_engine(settings.database_url)
        if settings.auto_create_schema:
            await create_schema(engine)
        settings.mirror_dir.mkdir(parents=True, exist_ok=True)
        app.state.session_factory = build_session_factory(engine)
        app.state.ingestion = IngestionService(settings, app.state.session_factory, S3Storage(settings))
        log.info("app.started", bucket=settings.s3_bucket, prefix=settings.normalized_prefix)
        yield
        await engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=(
            "Ingests a static website from S3-compatible storage, renders it in a headless browser "
            "and stores the extracted text, re-rendering only the pages affected by each change."
        ),
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["x-request-id"] = request_id
            log.info(
                "http.request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            return response
        finally:
            structlog.contextvars.clear_contextvars()

    app.include_router(ingestion.router)
    app.include_router(files.router)
    app.include_router(pages.router)

    @app.get("/health", tags=["Health"])
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
