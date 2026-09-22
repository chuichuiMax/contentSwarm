from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services.business_variable_service import (
    BusinessVariableCreate,
    BusinessVariableUpdate,
    create_business_variable,
    delete_business_variable,
    list_business_variables,
    update_business_variable,
)
from yuxi.storage.postgres.models_business import User

content_business_variables = APIRouter(
    prefix="/content-business-variables",
    tags=["content-business-variables"],
)


@content_business_variables.get("")
async def list_content_business_variables(
    keyword: str | None = Query(default=None),
    content_type_id: str | None = Query(default=None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_business_variables(db, keyword, content_type_id=content_type_id)


@content_business_variables.post("")
async def create_content_business_variable(
    payload: BusinessVariableCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_business_variable(db, current_user, payload)


@content_business_variables.patch("/{item_id}")
async def update_content_business_variable(
    item_id: str,
    payload: BusinessVariableUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_business_variable(db, item_id, payload)


@content_business_variables.delete("/{item_id}")
async def delete_content_business_variable(
    item_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_business_variable(db, item_id)
