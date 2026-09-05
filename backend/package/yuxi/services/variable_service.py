from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.variable_repository import VariableRepository
from yuxi.storage.postgres.models_business import User

SERVICE_ENTRIES: tuple[str, ...] = ("装修家居", "好评笔记")
VariablePort = Literal["pc", "app"]
VariableEdition = Literal["quick", "pro"]
VARIABLE_PORT_ORDER: tuple[VariablePort, ...] = ("pc", "app")
VARIABLE_EDITION_ORDER: tuple[VariableEdition, ...] = ("quick", "pro")
DEFAULT_PORTS: list[str] = ["pc", "app"]
DEFAULT_EDITIONS: list[str] = ["quick", "pro"]

DEFAULT_VARIABLES: tuple[tuple[str, str, str, bool], ...] = (
    ("FWTD0001", "设计师", "好评笔记", True),
    ("FWTD0002", "预算师", "好评笔记", True),
    ("FWTD0003", "项目经理", "好评笔记", True),
    ("FWTD0004", "客户经理", "好评笔记", True),
    ("FWTD0005", "工匠", "好评笔记", False),
    ("FWTD0006", "楼盘信息", "装修家居", True),
    ("FWTD0007", "基础", "装修家居", True),
    ("FWTD0008", "木制品", "装修家居", True),
    ("FWTD0009", "主材", "装修家居", True),
)


class VariableCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    service_entry: str = Field(min_length=1, max_length=64)
    variable_code: str | None = Field(default=None, max_length=32)
    ports: list[VariablePort] = Field(default_factory=lambda: list(DEFAULT_PORTS), min_length=1)
    editions: list[VariableEdition] = Field(default_factory=lambda: list(DEFAULT_EDITIONS), min_length=1)
    enabled: bool = True


class VariableUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    service_entry: str | None = Field(default=None, min_length=1, max_length=64)
    ports: list[VariablePort] | None = Field(default=None, min_length=1)
    editions: list[VariableEdition] | None = Field(default=None, min_length=1)
    enabled: bool | None = None


def _variable_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


def _normalize_text(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise _variable_error(422, "VARIABLE_INVALID_FIELD", f"{field} 不能为空")
    return normalized


def next_variable_code(existing_codes: list[str]) -> str:
    max_n = 0
    for code in existing_codes:
        prefix, suffix = code[:4], code[4:]
        if prefix.upper() == "FWTD" and suffix.isdigit():
            max_n = max(max_n, int(suffix))
    return f"FWTD{max_n + 1:04d}"


async def ensure_default_variables(db: AsyncSession) -> None:
    repo = VariableRepository(db)
    for variable_code, name, service_entry, enabled in DEFAULT_VARIABLES:
        if await repo.get_by_code(variable_code) or await repo.get_by_name(name):
            continue
        await repo.create(
            {
                "id": str(uuid.uuid4()),
                "variable_code": variable_code,
                "name": name,
                "service_entry": service_entry,
                "ports": list(DEFAULT_PORTS),
                "editions": list(DEFAULT_EDITIONS),
                "enabled": enabled,
                "created_by": "system",
            }
        )


def _require_service_entry(service_entry: str) -> str:
    if service_entry not in SERVICE_ENTRIES:
        raise _variable_error(422, "VARIABLE_SERVICE_ENTRY_NOT_FOUND", "服务入口不存在")
    return service_entry


def _normalize_ports(value: list[str]) -> list[str]:
    ports = [item for item in VARIABLE_PORT_ORDER if item in value]
    if not ports:
        raise _variable_error(422, "VARIABLE_INVALID_FIELD", "请选择端口")
    return ports


def _normalize_editions(value: list[str]) -> list[str]:
    editions = [item for item in VARIABLE_EDITION_ORDER if item in value]
    if not editions:
        raise _variable_error(422, "VARIABLE_INVALID_FIELD", "请选择版本")
    return editions


async def list_variables(db: AsyncSession, keyword: str | None = None) -> dict[str, Any]:
    await ensure_default_variables(db)
    items = await VariableRepository(db).list_variables(keyword=keyword.strip() if keyword else None)
    return {"variables": [item.to_dict() for item in items], "total": len(items)}


async def create_variable(db: AsyncSession, user: User, payload: VariableCreate) -> dict[str, Any]:
    await ensure_default_variables(db)
    repo = VariableRepository(db)
    name = _normalize_text(payload.name, field="变量")
    service_entry = _require_service_entry(_normalize_text(payload.service_entry, field="服务入口"))
    if payload.variable_code:
        variable_code = _normalize_text(payload.variable_code, field="编码")
    else:
        variable_code = next_variable_code(await repo.list_codes())
    if await repo.get_by_name(name):
        raise _variable_error(409, "VARIABLE_NAME_EXISTS", "变量名称已存在")
    if await repo.get_by_code(variable_code):
        raise _variable_error(409, "VARIABLE_CODE_EXISTS", "编码已存在")
    try:
        item = await repo.create(
            {
                "id": str(uuid.uuid4()),
                "variable_code": variable_code,
                "name": name,
                "service_entry": service_entry,
                "ports": _normalize_ports(payload.ports),
                "editions": _normalize_editions(payload.editions),
                "enabled": payload.enabled,
                "created_by": str(user.uid),
            }
        )
    except IntegrityError as exc:
        raise _variable_error(409, "VARIABLE_DUPLICATE", "变量名称或编码已存在") from exc
    return {"variable": item.to_dict()}


async def update_variable(db: AsyncSession, variable_pk: str, payload: VariableUpdate) -> dict[str, Any]:
    repo = VariableRepository(db)
    item = await repo.get(variable_pk)
    if item is None:
        raise _variable_error(404, "VARIABLE_NOT_FOUND", "变量不存在")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        data["name"] = _normalize_text(data["name"], field="变量")
        existing = await repo.get_by_name(data["name"])
        if existing and existing.id != item.id:
            raise _variable_error(409, "VARIABLE_NAME_EXISTS", "变量名称已存在")
    if "service_entry" in data:
        data["service_entry"] = _require_service_entry(_normalize_text(data["service_entry"], field="服务入口"))
    if "ports" in data:
        data["ports"] = _normalize_ports(data["ports"])
    if "editions" in data:
        data["editions"] = _normalize_editions(data["editions"])
    try:
        item = await repo.update(item, data)
    except IntegrityError as exc:
        raise _variable_error(409, "VARIABLE_NAME_EXISTS", "变量名称已存在") from exc
    return {"variable": item.to_dict()}


async def delete_variable(db: AsyncSession, variable_pk: str) -> dict[str, Any]:
    repo = VariableRepository(db)
    item = await repo.get(variable_pk)
    if item is None:
        raise _variable_error(404, "VARIABLE_NOT_FOUND", "变量不存在")
    await repo.delete(item)
    return {"success": True, "id": variable_pk}
