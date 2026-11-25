from fastapi import APIRouter, Body, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import IngestionDep, SessionDep
from app.db.models import IngestionRun
from app.schemas.ingestion import IngestionRequest, IngestionResult, IngestionRunOut
from app.services.ingestion import IngestionBusyError, IngestionFailedError

router = APIRouter(prefix="/ingestion", tags=["Ingestion"])


@router.post("/run", response_model=IngestionResult, summary="Trigger S3 ingestion")
async def run_ingestion(
    service: IngestionDep,
    request: IngestionRequest | None = Body(default=None),
) -> dict:
    """Detect created/updated/deleted files, sync them and re-render only the affected pages."""
    try:
        return await service.run(lookback_hours=request.lookback_hours if request else None)
    except IngestionBusyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except IngestionFailedError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            {"message": "ingestion failed, no changes were recorded", "run_id": exc.run_id, "error": repr(exc.cause)},
        ) from exc


@router.get("/runs", response_model=list[IngestionRunOut], summary="List recent ingestion runs")
async def list_runs(session: SessionDep, limit: int = Query(20, ge=1, le=200)) -> list[IngestionRun]:
    result = await session.scalars(select(IngestionRun).order_by(IngestionRun.id.desc()).limit(limit))
    return list(result)
