from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import IngestionDep, SessionDep
from app.db.models import Page, PageStatus
from app.schemas.pages import PageDetail, PageList, PageSummary, ReprocessResult
from app.services.ingestion import IngestionBusyError, PageSourceMissingError

router = APIRouter(prefix="/pages", tags=["Pages"])


async def _load_page(session: SessionDep, page_id: int) -> Page:
    page = await session.scalar(
        select(Page).where(Page.id == page_id).options(selectinload(Page.dependencies))
    )
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"page {page_id} not found")
    return page


@router.get("", response_model=PageList, summary="List extracted pages")
async def list_pages(
    session: SessionDep,
    include_deleted: bool = False,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> PageList:
    query = select(Page)
    if not include_deleted:
        query = query.where(Page.status != PageStatus.DELETED)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.scalars(query.order_by(Page.path).limit(limit).offset(offset))
    return PageList(total=total or 0, items=[PageSummary.model_validate(row) for row in rows])


@router.get("/{page_id}", response_model=PageDetail, summary="Get extracted page content")
async def get_page(page_id: int, session: SessionDep) -> Page:
    return await _load_page(session, page_id)


@router.post("/{page_id}/reprocess", response_model=ReprocessResult, summary="Reprocess a page")
async def reprocess_page(page_id: int, session: SessionDep, service: IngestionDep) -> ReprocessResult:
    page = await _load_page(session, page_id)
    try:
        outcome = await service.reprocess(session, page)
    except IngestionBusyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except PageSourceMissingError as exc:
        raise HTTPException(status.HTTP_410_GONE, str(exc)) from exc
    if outcome.status == "failed":
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"rendering failed: {outcome.error}")

    session.expire_all()
    refreshed = await _load_page(session, page_id)
    return ReprocessResult(content_changed=outcome.status == "changed", page=PageDetail.model_validate(refreshed))
