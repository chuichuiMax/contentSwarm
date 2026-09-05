from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services.content_type_service import (
    ContentTypeCreate,
    ContentTypeUpdate,
    create_content_type,
    delete_content_type,
    list_content_types,
    update_content_type,
)
from yuxi.storage.postgres.models_business import User

content_types = APIRouter(prefix="/content-types", tags=["content-types"])


@content_types.get("")
async def list_types(
    keyword: str | None = Query(default=None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_content_types(db, keyword)


@content_types.post("")
async def create_type(
    payload: ContentTypeCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_content_type(db, current_user, payload)


@content_types.patch("/{type_pk}")
async def update_type(
    type_pk: str,
    payload: ContentTypeUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_content_type(db, type_pk, payload)


@content_types.delete("/{type_pk}")
async def delete_type(
    type_pk: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_content_type(db, type_pk)
