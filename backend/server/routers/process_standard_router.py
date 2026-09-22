from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services.process_standard_service import (
    ProcessStandardCreate,
    ProcessStandardUpdate,
    create_process_standard,
    delete_process_standard,
    list_process_standards,
    update_process_standard,
)
from yuxi.storage.postgres.models_business import User

content_process_standards = APIRouter(
    prefix="/content-process-standards",
    tags=["content-process-standards"],
)


@content_process_standards.get("")
async def list_content_process_standards(
    keyword: str | None = Query(default=None),
    name: str | None = Query(default=None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_process_standards(db, keyword, name)


@content_process_standards.post("")
async def create_content_process_standard(
    payload: ProcessStandardCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_process_standard(db, current_user, payload)


@content_process_standards.patch("/{item_id}")
async def update_content_process_standard(
    item_id: str,
    payload: ProcessStandardUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_process_standard(db, item_id, payload)


@content_process_standards.delete("/{item_id}")
async def delete_content_process_standard(
    item_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_process_standard(db, item_id)
