from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services.resident_population_service import (
    ResidentPopulationCreate,
    ResidentPopulationUpdate,
    create_resident_population,
    delete_resident_population,
    list_resident_populations,
    update_resident_population,
)
from yuxi.storage.postgres.models_business import User

content_resident_populations = APIRouter(
    prefix="/content-resident-populations",
    tags=["content-resident-populations"],
)


@content_resident_populations.get("")
async def list_content_resident_populations(
    keyword: str | None = Query(default=None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_resident_populations(db, keyword)


@content_resident_populations.post("")
async def create_content_resident_population(
    payload: ResidentPopulationCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_resident_population(db, current_user, payload)


@content_resident_populations.patch("/{item_id}")
async def update_content_resident_population(
    item_id: str,
    payload: ResidentPopulationUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_resident_population(db, item_id, payload)


@content_resident_populations.delete("/{item_id}")
async def delete_content_resident_population(
    item_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_resident_population(db, item_id)
