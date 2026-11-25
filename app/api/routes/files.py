from typing import Literal

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import SessionDep
from app.db.models import S3Object
from app.schemas.files import FileList, FileOut

router = APIRouter(prefix="/files", tags=["Files"])


@router.get("", response_model=FileList, summary="List ingested files")
async def list_files(
    session: SessionDep,
    operation: Literal["created", "updated", "deleted"] | None = None,
    asset_type: str | None = Query(None, description="html, css, javascript, image, font, data, other"),
    include_deleted: bool = True,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> FileList:
    query = select(S3Object)
    if operation:
        query = query.where(S3Object.last_operation == operation)
    if asset_type:
        query = query.where(S3Object.asset_type == asset_type)
    if not include_deleted:
        query = query.where(S3Object.is_deleted.is_(False))

    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.scalars(query.order_by(S3Object.path).limit(limit).offset(offset))
    items = [FileOut.model_validate({**_as_dict(row), "operation": row.last_operation}) for row in rows]
    return FileList(total=total or 0, items=items)


def _as_dict(row: S3Object) -> dict:
    return {column.key: getattr(row, column.key) for column in S3Object.__table__.columns}
