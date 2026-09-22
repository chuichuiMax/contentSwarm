from __future__ import annotations

import hashlib
import uuid
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.employee_repository import EmployeeRepository
from yuxi.services.role_service import SYSTEM_ROLES, require_role, resolve_stored_user_role
from yuxi.storage.postgres.models_business import Department, User
from yuxi.storage.postgres.models_content import ContentEmployee
from yuxi.utils.auth_utils import AuthUtils
from yuxi.utils.datetime_utils import format_utc_datetime, utc_now_naive

Gender = Literal["male", "female"]
LoginPort = Literal["pc", "app"]
LOGIN_PORT_ORDER: tuple[LoginPort, ...] = ("pc", "app")
DEFAULT_EMPLOYEE_PASSWORD = "123456"
SYSTEM_ROLE_LABELS = {code: name for code, name in SYSTEM_ROLES}


class EmployeeCreate(BaseModel):
    employee_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    login_account: str = Field(min_length=1, max_length=64)
    current_branch: str = Field(default="", max_length=80)
    current_department: str = Field(default="", max_length=128)
    gender: Gender
    age: int | None = Field(default=None, ge=1, le=120)
    login_port: list[LoginPort] = Field(min_length=1)
    role: str = Field(min_length=1, max_length=64)
    enabled: bool = True


class EmployeeUpdate(BaseModel):
    employee_code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    login_account: str | None = Field(default=None, min_length=1, max_length=64)
    current_branch: str | None = Field(default=None, max_length=80)
    current_department: str | None = Field(default=None, max_length=128)
    gender: Gender | None = None
    age: int | None = Field(default=None, ge=1, le=120)
    login_port: list[LoginPort] | None = Field(default=None, min_length=1)
    role: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None


def _employee_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


def _normalize_login_ports(value: list[str]) -> list[str]:
    ports = [item for item in LOGIN_PORT_ORDER if item in value]
    if not ports:
        raise _employee_error(422, "EMPLOYEE_INVALID_FIELD", "请选择登录端口")
    return ports


def _normalize_text(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise _employee_error(422, "EMPLOYEE_INVALID_FIELD", f"{field} 不能为空")
    return normalized


def _platform_uid(employee: ContentEmployee) -> str:
    return f"mp_{employee.id.replace('-', '')[:17]}"


async def resolve_employee_for_user(db: AsyncSession, user: User) -> ContentEmployee | None:
    repo = EmployeeRepository(db)
    phone = (user.phone_number or "").strip()
    if phone:
        employee = await repo.get_by_login_account(phone)
        if employee is not None:
            return employee
    uid = str(user.uid or "")
    if uid.startswith("mp_"):
        for item in await repo.list_employees():
            if _platform_uid(item) == uid:
                return item
    return None


async def get_current_employee(db: AsyncSession, user: User) -> dict[str, Any]:
    employee = await resolve_employee_for_user(db, user)
    return {"employee": employee.to_dict() if employee else None}


async def get_pc_login_employee(db: AsyncSession, identifier: str) -> ContentEmployee | None:
    login_id = identifier.strip()
    if not login_id:
        return None
    repo = EmployeeRepository(db)
    employee = await repo.get_by_login_account(login_id)
    if employee is None:
        employee = await repo.get_by_code(login_id)
    return employee


async def _phone_taken(db: AsyncSession, phone: str, *, exclude_user_id: int | None = None) -> bool:
    query = select(User.id).where(User.phone_number == phone, User.is_deleted == 0)
    if exclude_user_id is not None:
        query = query.where(User.id != exclude_user_id)
    result = await db.execute(query)
    return result.scalar_one_or_none() is not None


async def ensure_platform_user(db: AsyncSession, employee: ContentEmployee) -> User:
    phone = employee.login_account
    uid = _platform_uid(employee)
    result = await db.execute(select(User).where(User.uid == uid, User.is_deleted == 0))
    user = result.scalar_one_or_none()
    if user is not None:
        if user.last_login is None:
            user.password_hash = AuthUtils.hash_password(DEFAULT_EMPLOYEE_PASSWORD)
        user.role = await resolve_stored_user_role(db, employee.role)
        if user.phone_number != phone and not await _phone_taken(db, phone, exclude_user_id=user.id):
            user.phone_number = phone
        await db.flush()
        return user
    creator = await db.execute(select(User).where(User.uid == employee.created_by, User.is_deleted == 0))
    creator_user = creator.scalar_one_or_none()
    department_id = creator_user.department_id if creator_user else None
    if department_id is None:
        dept = await db.execute(select(Department).order_by(Department.id.asc()).limit(1))
        department = dept.scalar_one_or_none()
        if department is None:
            raise _employee_error(503, "EMPLOYEE_DEPARTMENT_MISSING", "系统尚未初始化部门")
        department_id = department.id
    username = (employee.name or "员工")[:20]
    clash = await db.execute(select(User.id).where(User.username == username))
    if clash.scalar_one_or_none() is not None:
        username = f"mp{phone[-8:]}"
    user = User(
        username=username,
        uid=uid,
        phone_number=None if await _phone_taken(db, phone) else phone,
        password_hash=AuthUtils.hash_password(DEFAULT_EMPLOYEE_PASSWORD),
        role=await resolve_stored_user_role(db, employee.role),
        department_id=department_id,
    )
    db.add(user)
    await db.flush()
    return user


async def _soft_delete_employee_user(db: AsyncSession, employee: ContentEmployee) -> None:
    result = await db.execute(select(User).where(User.uid == _platform_uid(employee), User.is_deleted == 0))
    user = result.scalar_one_or_none()
    if user is None or user.role == "superadmin":
        return
    hash_suffix = hashlib.sha256(f"{user.uid}:{user.id}".encode()).hexdigest()[:4]
    user.is_deleted = 1
    user.deleted_at = utc_now_naive()
    user.username = f"已注销用户-{hash_suffix}"
    user.phone_number = None
    user.password_hash = "DELETED"
    user.avatar = None
    await db.flush()


def _employee_row(employee: ContentEmployee) -> dict[str, Any]:
    data = employee.to_dict()
    data["source"] = "employee"
    return data


def _user_row(user: User) -> dict[str, Any]:
    return {
        "id": f"user:{user.uid}",
        "employee_code": user.uid,
        "name": user.username,
        "login_account": user.phone_number or user.uid,
        "current_branch": "",
        "current_department": "",
        "gender": "",
        "age": None,
        "login_port": ["pc"],
        "role": SYSTEM_ROLE_LABELS.get(user.role, user.role),
        "enabled": True,
        "source": "user",
        "avatar": user.avatar,
        "bio": "",
        "last_login_at": format_utc_datetime(user.last_login),
        "created_by": "",
        "created_at": format_utc_datetime(user.created_at),
        "updated_at": format_utc_datetime(user.created_at),
    }


async def list_employees(db: AsyncSession, keyword: str | None = None) -> dict[str, Any]:
    keyword = keyword.strip() if keyword else None
    employees = await EmployeeRepository(db).list_employees(keyword=keyword)
    # 员工开通的平台账号 uid 以 mp_ 开头，列表里已有对应员工行，避免重复加载。
    query = select(User).where(User.is_deleted == 0, ~User.uid.startswith("mp_"))
    if keyword:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.where(
            or_(
                User.username.ilike(pattern, escape="\\"),
                User.uid.ilike(pattern, escape="\\"),
                User.phone_number.ilike(pattern, escape="\\"),
            )
        )
    users = list((await db.execute(query)).scalars().all())
    rows: list[tuple[Any, dict[str, Any]]] = [(item.created_at, _employee_row(item)) for item in employees]
    rows.extend((user.created_at, _user_row(user)) for user in users)
    rows.sort(key=lambda item: item[0] or utc_now_naive(), reverse=True)
    items = [row for _, row in rows]
    return {"employees": items, "total": len(items)}


async def create_employee(db: AsyncSession, user: User, payload: EmployeeCreate) -> dict[str, Any]:
    repo = EmployeeRepository(db)
    employee_code = _normalize_text(payload.employee_code, field="员工编码")
    login_account = _normalize_text(payload.login_account, field="登录账号")
    role = _normalize_text(payload.role, field="角色")
    await require_role(db, role)
    if await repo.get_by_code(employee_code):
        raise _employee_error(409, "EMPLOYEE_CODE_EXISTS", "员工编码已存在")
    if await repo.get_by_login_account(login_account):
        raise _employee_error(409, "EMPLOYEE_LOGIN_EXISTS", "登录账号已存在")
    try:
        employee = await repo.create(
            {
                "id": str(uuid.uuid4()),
                "employee_code": employee_code,
                "name": _normalize_text(payload.name, field="姓名"),
                "login_account": login_account,
                "current_branch": (payload.current_branch or "").strip(),
                "current_department": (payload.current_department or "").strip(),
                "gender": payload.gender,
                "age": payload.age,
                "login_port": _normalize_login_ports(payload.login_port),
                "role": role,
                "enabled": payload.enabled,
                "created_by": str(user.uid),
            }
        )
        await ensure_platform_user(db, employee)
    except IntegrityError as exc:
        raise _employee_error(409, "EMPLOYEE_DUPLICATE", "员工编码或登录账号已存在") from exc
    return {"employee": employee.to_dict()}


async def update_employee(db: AsyncSession, employee_pk: str, payload: EmployeeUpdate) -> dict[str, Any]:
    repo = EmployeeRepository(db)
    employee = await repo.get(employee_pk)
    if employee is None:
        raise _employee_error(404, "EMPLOYEE_NOT_FOUND", "员工不存在")
    data = payload.model_dump(exclude_unset=True)
    if "employee_code" in data:
        data["employee_code"] = _normalize_text(data["employee_code"], field="员工编码")
        if await repo.get_by_code(data["employee_code"], exclude_id=employee.id):
            raise _employee_error(409, "EMPLOYEE_CODE_EXISTS", "员工编码已存在")
    if "name" in data:
        data["name"] = _normalize_text(data["name"], field="姓名")
    if "login_account" in data:
        data["login_account"] = _normalize_text(data["login_account"], field="登录账号")
        if await repo.get_by_login_account(data["login_account"], exclude_id=employee.id):
            raise _employee_error(409, "EMPLOYEE_LOGIN_EXISTS", "登录账号已存在")
    if "current_branch" in data:
        data["current_branch"] = (data["current_branch"] or "").strip()
    if "current_department" in data:
        data["current_department"] = (data["current_department"] or "").strip()
    if "login_port" in data:
        data["login_port"] = _normalize_login_ports(data["login_port"])
    if "role" in data:
        data["role"] = _normalize_text(data["role"], field="角色")
        await require_role(db, data["role"], allow_disabled=data["role"] == employee.role)
    try:
        employee = await repo.update(employee, data)
        await ensure_platform_user(db, employee)
    except IntegrityError as exc:
        raise _employee_error(409, "EMPLOYEE_DUPLICATE", "员工编码或登录账号已存在") from exc
    return {"employee": employee.to_dict()}


async def delete_employee(db: AsyncSession, employee_pk: str) -> dict[str, Any]:
    repo = EmployeeRepository(db)
    employee = await repo.get(employee_pk)
    if employee is None:
        raise _employee_error(404, "EMPLOYEE_NOT_FOUND", "员工不存在")
    await _soft_delete_employee_user(db, employee)
    await repo.delete(employee)
    return {"success": True, "id": employee_pk}
