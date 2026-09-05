from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.employee_repository import EmployeeRepository
from yuxi.services.role_service import require_role
from yuxi.storage.postgres.models_business import User

Gender = Literal["male", "female"]
LoginPort = Literal["pc", "app"]
LOGIN_PORT_ORDER: tuple[LoginPort, ...] = ("pc", "app")


class EmployeeCreate(BaseModel):
    employee_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    login_account: str = Field(min_length=1, max_length=64)
    gender: Gender
    login_port: list[LoginPort] = Field(min_length=1)
    role: str = Field(min_length=1, max_length=64)
    enabled: bool = True


class EmployeeUpdate(BaseModel):
    employee_code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    login_account: str | None = Field(default=None, min_length=1, max_length=64)
    gender: Gender | None = None
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


async def list_employees(db: AsyncSession, keyword: str | None = None) -> dict[str, Any]:
    items = await EmployeeRepository(db).list_employees(keyword=keyword.strip() if keyword else None)
    return {"employees": [item.to_dict() for item in items], "total": len(items)}


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
                "gender": payload.gender,
                "login_port": _normalize_login_ports(payload.login_port),
                "role": role,
                "enabled": payload.enabled,
                "created_by": str(user.uid),
            }
        )
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
    if "login_port" in data:
        data["login_port"] = _normalize_login_ports(data["login_port"])
    if "role" in data:
        data["role"] = _normalize_text(data["role"], field="角色")
        await require_role(db, data["role"], allow_disabled=data["role"] == employee.role)
    try:
        employee = await repo.update(employee, data)
    except IntegrityError as exc:
        raise _employee_error(409, "EMPLOYEE_DUPLICATE", "员工编码或登录账号已存在") from exc
    return {"employee": employee.to_dict()}


async def delete_employee(db: AsyncSession, employee_pk: str) -> dict[str, Any]:
    repo = EmployeeRepository(db)
    employee = await repo.get(employee_pk)
    if employee is None:
        raise _employee_error(404, "EMPLOYEE_NOT_FOUND", "员工不存在")
    await repo.delete(employee)
    return {"success": True, "id": employee_pk}
