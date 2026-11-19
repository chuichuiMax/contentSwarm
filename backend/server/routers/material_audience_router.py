from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services.enterprise_gallery_audience import (
    MaterialAudienceCreate,
    MaterialAudienceUpdate,
    create_material_audience,
    delete_material_audience,
    list_material_audiences,
    update_material_audience,
)
from yuxi.storage.postgres.models_business import User

material_audiences = APIRouter(prefix="/material-audiences", tags=["material-audiences"])


@material_audiences.get("")
async def list_audiences(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    del current_user
    return await list_material_audiences(db)


@material_audiences.post("")
async def create_audience(
    payload: MaterialAudienceCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_material_audience(db, current_user, payload)


@material_audiences.patch("/{audience_id}")
async def update_audience(
    audience_id: str,
    payload: MaterialAudienceUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_material_audience(db, current_user, audience_id, payload)


@material_audiences.delete("/{audience_id}")
async def delete_audience(
    audience_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_material_audience(db, current_user, audience_id)
