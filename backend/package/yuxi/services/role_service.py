from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.employee_repository import EmployeeRepository
from yuxi.repositories.role_repository import RoleRepository
from yuxi.repositories.user_repository import UserRepository
from yuxi.services.role_permissions import PERMISSION_CATALOG, normalize_grants
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentRole

DEFAULT_ROLES: tuple[tuple[str, str], ...] = (
    ("JS0001", "运营"),
    ("JS0002", "家装顾问"),
    ("JS0003", "楼盘经理"),
    ("JS0004", "新渠道"),
    ("JS0005", "网销"),
)

SYSTEM_ROLES: tuple[tuple[str, str], ...] = (
    ("user", "普通用户"),
    ("admin", "管理员"),
    ("superadmin", "超级管理员"),
)
SYSTEM_ROLE_CODES = {code for code, _ in SYSTEM_ROLES}
SYSTEM_ROLE_NAMES = {name for _, name in SYSTEM_ROLES}


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    role_code: str | None = Field(default=None, max_length=32)
    enabled: bool = True


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None


class RolePermissionsUpdate(BaseModel):
    grants: list[str] = Field(default_factory=list)


def _role_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


def _normalize_text(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise _role_error(422, "ROLE_INVALID_FIELD", f"{field} 不能为空")
    return normalized


def next_role_code(existing_codes: list[str]) -> str:
    max_n = 0
    for code in existing_codes:
        prefix, suffix = code[:2], code[2:]
        if prefix.upper() == "JS" and suffix.isdigit():
            max_n = max(max_n, int(suffix))
    return f"JS{max_n + 1:04d}"


def is_system_role(role: ContentRole) -> bool:
    return role.role_type == "系统" or role.role_code in SYSTEM_ROLE_CODES


def stored_user_role(role: ContentRole) -> str:
    return role.role_code if role.role_code in SYSTEM_ROLE_CODES else role.name


def role_binding_keys(role: ContentRole) -> list[str]:
    keys = [role.name]
    if role.role_code not in keys:
        keys.append(role.role_code)
    return keys


async def ensure_system_roles(db: AsyncSession) -> None:
    repo = RoleRepository(db)
    for role_code, name in SYSTEM_ROLES:
        if await repo.get_by_code(role_code):
            continue
        if await repo.get_by_name(name):
            continue
        await repo.create(
            {
                "id": str(uuid.uuid4()),
                "role_code": role_code,
                "name": name,
                "role_type": "系统",
                "enabled": True,
                "permissions": [],
                "created_by": "system",
            }
        )


async def ensure_default_roles(db: AsyncSession) -> None:
    repo = RoleRepository(db)
    if not await repo.has_any():
        for role_code, name in DEFAULT_ROLES:
            await repo.create(
                {
                    "id": str(uuid.uuid4()),
                    "role_code": role_code,
                    "name": name,
                    "role_type": "新增",
                    "enabled": True,
                    "permissions": [],
                    "created_by": "system",
                }
            )
    await ensure_system_roles(db)


async def require_role(db: AsyncSession, name: str, *, allow_disabled: bool = False) -> ContentRole:
    await ensure_default_roles(db)
    role = await RoleRepository(db).get_by_code_or_name(name)
    if role is None:
        raise _role_error(422, "ROLE_NOT_FOUND", "角色不存在")
    if not allow_disabled and not role.enabled:
        raise _role_error(422, "ROLE_DISABLED", "角色已禁用")
    return role


async def resolve_stored_user_role(db: AsyncSession, role_value: str) -> str:
    role = await require_role(db, role_value)
    return stored_user_role(role)


def _member_count(role: ContentRole, employee_counts: dict[str, int], user_counts: dict[str, int]) -> int:
    total = employee_counts.get(role.name, 0)
    seen: set[str] = set()
    for key in role_binding_keys(role):
        if key in seen:
            continue
        seen.add(key)
        total += user_counts.get(key, 0)
    return total


def _with_member_count(
    role: ContentRole, employee_counts: dict[str, int], user_counts: dict[str, int]
) -> dict[str, Any]:
    data = role.to_dict(member_count=_member_count(role, employee_counts, user_counts))
    data["is_system"] = is_system_role(role)
    return data


async def list_roles(
    db: AsyncSession, keyword: str | None = None, enabled: bool | None = None
) -> dict[str, Any]:
    await ensure_default_roles(db)
    items = await RoleRepository(db).list_roles(
        keyword=keyword.strip() if keyword else None,
        enabled=enabled,
    )
    employee_counts = await EmployeeRepository(db).count_by_role()
    user_counts = await UserRepository().count_by_role_with_db(db)
    return {
        "roles": [_with_member_count(item, employee_counts, user_counts) for item in items],
        "total": len(items),
    }


async def list_role_members(db: AsyncSession, role_pk: str, keyword: str | None = None) -> dict[str, Any]:
    role = await RoleRepository(db).get(role_pk)
    if role is None:
        raise _role_error(404, "ROLE_NOT_FOUND", "角色不存在")
    keyword = keyword.strip() if keyword else None
    employees = await EmployeeRepository(db).list_by_role(role.name, keyword=keyword)
    users = await UserRepository().list_by_roles_with_db(db, role_binding_keys(role), keyword=keyword)
    members = [item.to_dict() for item in employees]
    members.extend(
        {
            "id": f"user:{user.uid}",
            "name": user.username,
            "employee_code": user.uid,
            "role": role.name,
            "enabled": True,
        }
        for user in users
    )
    return {"employees": members, "total": len(members)}


async def create_role(db: AsyncSession, user: User, payload: RoleCreate) -> dict[str, Any]:
    await ensure_default_roles(db)
    repo = RoleRepository(db)
    name = _normalize_text(payload.name, field="角色名称")
    if name in SYSTEM_ROLE_NAMES:
        raise _role_error(409, "ROLE_SYSTEM_LOCKED", "不能新增与系统角色同名的角色")
    if payload.role_code:
        role_code = _normalize_text(payload.role_code, field="角色编码")
        if role_code in SYSTEM_ROLE_CODES:
            raise _role_error(409, "ROLE_SYSTEM_LOCKED", "不能占用系统角色编码")
    else:
        role_code = next_role_code(await repo.list_codes())
    if await repo.get_by_name(name):
        raise _role_error(409, "ROLE_NAME_EXISTS", "角色名称已存在")
    if await repo.get_by_code(role_code):
        raise _role_error(409, "ROLE_CODE_EXISTS", "角色编码已存在")
    try:
        role = await repo.create(
            {
                "id": str(uuid.uuid4()),
                "role_code": role_code,
                "name": name,
                "role_type": "新增",
                "enabled": payload.enabled,
                "permissions": [],
                "created_by": str(user.uid),
            }
        )
    except IntegrityError as exc:
        raise _role_error(409, "ROLE_DUPLICATE", "角色编码或角色名称已存在") from exc
    return {"role": role.to_dict(member_count=0)}


async def update_role(db: AsyncSession, role_pk: str, payload: RoleUpdate) -> dict[str, Any]:
    repo = RoleRepository(db)
    role = await repo.get(role_pk)
    if role is None:
        raise _role_error(404, "ROLE_NOT_FOUND", "角色不存在")
    if is_system_role(role) and payload.name is not None:
        raise _role_error(409, "ROLE_SYSTEM_LOCKED", "系统角色不能改名")
    data = payload.model_dump(exclude_unset=True)
    old_name = role.name
    if "name" in data:
        data["name"] = _normalize_text(data["name"], field="角色名称")
        existing = await repo.get_by_name(data["name"])
        if existing and existing.id != role.id:
            raise _role_error(409, "ROLE_NAME_EXISTS", "角色名称已存在")
    try:
        role = await repo.update(role, data)
    except IntegrityError as exc:
        raise _role_error(409, "ROLE_NAME_EXISTS", "角色名称已存在") from exc
    if role.name != old_name:
        await EmployeeRepository(db).rename_role(old_name, role.name)
        await UserRepository().rename_role_with_db(db, old_name, role.name)
    employee_counts = await EmployeeRepository(db).count_by_role()
    user_counts = await UserRepository().count_by_role_with_db(db)
    return {"role": _with_member_count(role, employee_counts, user_counts)}


async def get_role_permissions(db: AsyncSession, role_pk: str) -> dict[str, Any]:
    role = await RoleRepository(db).get(role_pk)
    if role is None:
        raise _role_error(404, "ROLE_NOT_FOUND", "角色不存在")
    grants = normalize_grants(role.permissions, strict=False)
    return {"role": role.to_dict(), "catalog": PERMISSION_CATALOG, "grants": grants}


async def update_role_permissions(
    db: AsyncSession, role_pk: str, payload: RolePermissionsUpdate
) -> dict[str, Any]:
    repo = RoleRepository(db)
    role = await repo.get(role_pk)
    if role is None:
        raise _role_error(404, "ROLE_NOT_FOUND", "角色不存在")
    try:
        grants = normalize_grants(payload.grants)
    except ValueError as exc:
        raise _role_error(422, "ROLE_PERMISSION_INVALID", "存在无效权限项") from exc
    role = await repo.update(role, {"permissions": grants})
    return {"role": role.to_dict(), "catalog": PERMISSION_CATALOG, "grants": grants}


async def delete_role(db: AsyncSession, role_pk: str) -> dict[str, Any]:
    repo = RoleRepository(db)
    role = await repo.get(role_pk)
    if role is None:
        raise _role_error(404, "ROLE_NOT_FOUND", "角色不存在")
    if is_system_role(role):
        raise _role_error(409, "ROLE_SYSTEM_LOCKED", "系统角色不能删除")
    members = await EmployeeRepository(db).list_by_role(role.name)
    users = await UserRepository().list_by_roles_with_db(db, role_binding_keys(role))
    if members or users:
        raise _role_error(409, "ROLE_IN_USE", "该角色已关联人员，无法删除")
    await repo.delete(role)
    return {"success": True, "id": role_pk}
