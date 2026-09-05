from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services.role_service import (
    RoleCreate,
    RolePermissionsUpdate,
    RoleUpdate,
    create_role,
    delete_role,
    get_role_permissions,
    list_role_members,
    list_roles,
    update_role,
    update_role_permissions,
)
from yuxi.storage.postgres.models_business import User

roles = APIRouter(prefix="/roles", tags=["roles"])


@roles.get("")
async def list_content_roles(
    keyword: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_roles(db, keyword, enabled)


@roles.get("/{role_pk}/employees")
async def list_content_role_employees(
    role_pk: str,
    keyword: str | None = Query(default=None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_role_members(db, role_pk, keyword)


@roles.get("/{role_pk}/permissions")
async def get_content_role_permissions(
    role_pk: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_role_permissions(db, role_pk)


@roles.put("/{role_pk}/permissions")
async def put_content_role_permissions(
    role_pk: str,
    payload: RolePermissionsUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_role_permissions(db, role_pk, payload)


@roles.post("")
async def create_content_role(
    payload: RoleCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_role(db, current_user, payload)


@roles.patch("/{role_pk}")
async def update_content_role(
    role_pk: str,
    payload: RoleUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_role(db, role_pk, payload)


@roles.delete("/{role_pk}")
async def delete_content_role(
    role_pk: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_role(db, role_pk)
