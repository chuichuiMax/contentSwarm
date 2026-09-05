from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services.variable_service import (
    VariableCreate,
    VariableUpdate,
    create_variable,
    delete_variable,
    list_variables,
    update_variable,
)
from yuxi.storage.postgres.models_business import User

content_variables = APIRouter(prefix="/content-variables", tags=["content-variables"])


@content_variables.get("")
async def list_content_variables(
    keyword: str | None = Query(default=None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_variables(db, keyword)


@content_variables.post("")
async def create_content_variable(
    payload: VariableCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_variable(db, current_user, payload)


@content_variables.patch("/{variable_pk}")
async def update_content_variable(
    variable_pk: str,
    payload: VariableUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_variable(db, variable_pk, payload)


@content_variables.delete("/{variable_pk}")
async def delete_content_variable(
    variable_pk: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_variable(db, variable_pk)
