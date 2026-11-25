from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ingestion import IngestionService


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def get_ingestion_service(request: Request) -> IngestionService:
    return request.app.state.ingestion


SessionDep = Annotated[AsyncSession, Depends(get_session)]
IngestionDep = Annotated[IngestionService, Depends(get_ingestion_service)]
