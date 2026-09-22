from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services.target_audience_service import (
    TargetAudienceCreate,
    TargetAudienceUpdate,
    create_target_audience,
    delete_target_audience,
    list_target_audiences,
    update_target_audience,
)
from yuxi.storage.postgres.models_business import User

content_target_audiences = APIRouter(
    prefix="/content-target-audiences",
    tags=["content-target-audiences"],
)


@content_target_audiences.get("")
async def list_content_target_audiences(
    keyword: str | None = Query(default=None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_target_audiences(db, keyword)


@content_target_audiences.post("")
async def create_content_target_audience(
    payload: TargetAudienceCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_target_audience(db, current_user, payload)


@content_target_audiences.patch("/{item_id}")
async def update_content_target_audience(
    item_id: str,
    payload: TargetAudienceUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_target_audience(db, item_id, payload)


@content_target_audiences.delete("/{item_id}")
async def delete_content_target_audience(
    item_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_target_audience(db, item_id)
