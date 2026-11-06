from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services.creative_style_service import (
    CreativeStyleCreate,
    CreativeStyleUpdate,
    create_creative_style,
    delete_creative_style,
    list_creative_styles,
    update_creative_style,
)
from yuxi.storage.postgres.models_business import User

creative_styles = APIRouter(prefix="/creative-styles", tags=["creative-styles"])


@creative_styles.get("")
async def list_styles(
    keyword: str | None = Query(default=None),
    content_type_id: str | None = Query(default=None),
    enabled_only: bool = Query(default=False),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    del current_user
    return await list_creative_styles(
        db,
        keyword=keyword,
        content_type_id=content_type_id,
        enabled_only=enabled_only,
    )


@creative_styles.post("")
async def create_style(
    payload: CreativeStyleCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_creative_style(db, current_user, payload)


@creative_styles.patch("/{style_id}")
async def update_style(
    style_id: str,
    payload: CreativeStyleUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    del current_user
    return await update_creative_style(db, style_id, payload)


@creative_styles.delete("/{style_id}")
async def delete_style(
    style_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    del current_user
    return await delete_creative_style(db, style_id)
