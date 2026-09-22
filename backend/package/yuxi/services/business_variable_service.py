from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.business_variable_repository import BusinessVariableRepository
from yuxi.repositories.content_type_repository import ContentTypeRepository
from yuxi.repositories.variable_repository import VariableRepository
from yuxi.services.content_type_service import ensure_default_content_types
from yuxi.services.variable_service import SERVICE_ENTRIES, ensure_default_variables
from yuxi.storage.postgres.models_business import User

PORT_LABELS = {"pc": "PC", "app": "小程序"}
PORT_ORDER: tuple[str, ...] = ("pc", "app")
ServiceEntry = Literal["装修家居", "好评笔记"]
VariablePort = Literal["pc", "app"]
REVIEW_NOTES_ENTRY = "好评笔记"
NO_CONTENT_TYPE_ID = ""
DEFAULT_PORTS: list[str] = ["pc", "app"]

# (service_entry, content_type_name|None, variable_name, required, enabled)
DEFAULT_BUSINESS_VARIABLES: tuple[tuple[str, str | None, str, bool, bool], ...] = (
    ("装修家居", "工艺施工展示", "目标人群", True, True),
    ("装修家居", "工艺施工展示", "居住人口", True, True),
    ("装修家居", "工艺施工展示", "工艺类型", True, True),
    ("装修家居", "工艺施工展示", "工艺名称", True, True),
    ("装修家居", "工艺施工展示", "楼盘信息", False, True),
    ("装修家居", "工艺施工展示", "外框面积", True, True),
    ("装修家居", "工艺施工展示", "项目阶段", True, True),
    ("装修家居", "装修报价清单", "目标人群", True, True),
    ("装修家居", "装修报价清单", "居住人口", True, True),
    ("装修家居", "装修报价清单", "楼盘信息", True, True),
    ("装修家居", "装修报价清单", "外框面积", True, True),
    ("装修家居", "装修报价清单", "基础", False, True),
    ("装修家居", "装修报价清单", "木制品", False, True),
    ("装修家居", "装修避坑分享", "目标人群", False, True),
    ("装修家居", "装修避坑分享", "楼盘信息", True, True),
    ("装修家居", "装修避坑分享", "外框面积", True, True),
    ("装修家居", "装修避坑分享", "基础", True, True),
    ("装修家居", "装修避坑分享", "木制品", True, True),
    ("装修家居", "装修避坑分享", "主材", True, True),
    ("装修家居", "装修避坑分享", "设计风格", True, True),
    ("装修家居", "装修避坑分享", "所在区域", True, True),
    ("装修家居", "装修案例分享", "目标人群", True, True),
    ("装修家居", "装修案例分享", "楼盘信息", True, True),
    ("装修家居", "装修案例分享", "外框面积", True, True),
    ("装修家居", "装修案例分享", "基础", True, True),
    ("装修家居", "装修案例分享", "木制品", True, True),
    ("装修家居", "装修案例分享", "主材", True, True),
    ("装修家居", "装修案例分享", "设计风格", True, True),
    ("装修家居", "装修案例分享", "所在区域", True, True),
    ("装修家居", "装修案例分享", "居住人口", False, True),
    ("装修家居", "装修知识科普", "目标人群", False, True),
    ("装修家居", "装修知识科普", "工艺类型", True, True),
    ("装修家居", "装修知识科普", "设计风格", True, True),
    ("装修家居", "装修知识科普", "工艺名称", True, True),
    ("装修家居", "人设自荐", "目标人群", False, True),
    ("装修家居", "人设自荐", "岗位", True, True),
    ("装修家居", "人设自荐", "从业年限", True, True),
    ("装修家居", "人设自荐", "所在区域", True, True),
    ("好评笔记", None, "设计师", True, True),
    ("好评笔记", None, "预算师", True, True),
    ("好评笔记", None, "项目经理", True, True),
    ("好评笔记", None, "客户经理", True, True),
    ("好评笔记", None, "工匠", False, True),
)


class BusinessVariableCreate(BaseModel):
    service_entry: ServiceEntry = "装修家居"
    content_type_id: str | None = Field(default=None, max_length=64)
    variable_id: str = Field(min_length=1, max_length=64)
    ports: list[VariablePort] = Field(default_factory=lambda: list(DEFAULT_PORTS), min_length=1)
    required: bool = True
    enabled: bool = True

    @model_validator(mode="after")
    def validate_content_type_by_service(self):
        content_type_id = (self.content_type_id or "").strip()
        if self.service_entry == REVIEW_NOTES_ENTRY:
            self.content_type_id = None
            return self
        if not content_type_id:
            raise ValueError("装修家居必须选择内容类型")
        self.content_type_id = content_type_id
        return self


class BusinessVariableUpdate(BaseModel):
    content_type_id: str | None = Field(default=None, max_length=64)
    variable_id: str | None = Field(default=None, min_length=1, max_length=64)
    ports: list[VariablePort] | None = Field(default=None, min_length=1)
    required: bool | None = None
    enabled: bool | None = None


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


def _port_label(ports: list[str] | None) -> str:
    labels = [PORT_LABELS.get(port, port) for port in (ports or []) if port]
    return "，".join(labels) if labels else "-"


def _normalize_ports(value: list[str] | None) -> list[str]:
    ports = [item for item in PORT_ORDER if item in (value or [])]
    if not ports:
        raise _error(422, "BUSINESS_VARIABLE_PORTS_REQUIRED", "请选择端口")
    return ports


def _normalize_content_type_id(content_type_id: str | None) -> str:
    return (content_type_id or "").strip()


def _binding_ports(item, variable) -> list[str]:
    ports = [port for port in PORT_ORDER if port in list(getattr(item, "ports", None) or [])]
    if ports:
        return ports
    return [port for port in PORT_ORDER if port in list(variable.ports or [])] or list(DEFAULT_PORTS)


async def ensure_default_business_variables(db: AsyncSession) -> None:
    await ensure_default_content_types(db)
    await ensure_default_variables(db)
    repo = BusinessVariableRepository(db)
    content_types = {item.name: item for item in await ContentTypeRepository(db).list_types()}
    variables = {item.name: item for item in await VariableRepository(db).list_variables()}
    for service_entry, content_type_name, variable_name, required, enabled in DEFAULT_BUSINESS_VARIABLES:
        variable = variables.get(variable_name)
        if variable is None:
            continue
        content_type_id = NO_CONTENT_TYPE_ID
        if content_type_name:
            content_type = content_types.get(content_type_name)
            if content_type is None:
                continue
            content_type_id = content_type.id
        if await repo.get_by_key(
            service_entry=service_entry,
            content_type_id=content_type_id,
            variable_id=variable.id,
        ):
            continue
        await repo.create(
            {
                "id": str(uuid.uuid4()),
                "service_entry": service_entry,
                "content_type_id": content_type_id,
                "variable_id": variable.id,
                "ports": list(DEFAULT_PORTS),
                "required": required,
                "enabled": enabled,
                "created_by": "system",
            }
        )


def serialize_binding(
    item,
    *,
    content_type_name: str,
    variable_name: str,
    variable_code: str,
    service_entry: str,
    ports: list[str],
) -> dict[str, Any]:
    payload = item.to_dict()
    payload.update(
        {
            "content_type_name": content_type_name,
            "variable_name": variable_name,
            "variable_code": variable_code,
            "service_entry": service_entry,
            "ports": list(ports or []),
            "ports_label": _port_label(ports),
        }
    )
    return payload


async def list_business_variables(
    db: AsyncSession,
    keyword: str | None = None,
    *,
    content_type_id: str | None = None,
) -> dict[str, Any]:
    await ensure_default_business_variables(db)
    bindings = await BusinessVariableRepository(db).list_items()
    content_types = {item.id: item for item in await ContentTypeRepository(db).list_types()}
    variables = {item.id: item for item in await VariableRepository(db).list_variables()}
    rows: list[dict[str, Any]] = []
    needle = (keyword or "").strip().lower()
    content_type_filter = (content_type_id or "").strip()
    for binding in bindings:
        if content_type_filter and binding.content_type_id != content_type_filter:
            continue
        variable = variables.get(binding.variable_id)
        if variable is None:
            continue
        content_type = content_types.get(binding.content_type_id) if binding.content_type_id else None
        content_type_name = content_type.name if content_type else ("-" if not binding.content_type_id else None)
        if binding.content_type_id and content_type is None:
            continue
        service_entry = binding.service_entry or variable.service_entry
        row = serialize_binding(
            binding,
            content_type_name=content_type_name or "-",
            variable_name=variable.name,
            variable_code=variable.variable_code,
            service_entry=service_entry,
            ports=_binding_ports(binding, variable),
        )
        if needle:
            haystack = " ".join(
                [
                    content_type_name or "",
                    variable.name,
                    service_entry or "",
                    row["ports_label"],
                    "是" if binding.required else "否",
                ]
            ).lower()
            if needle not in haystack:
                continue
        rows.append(row)
    return {"business_variables": rows, "total": len(rows)}


async def create_business_variable(db: AsyncSession, user: User, payload: BusinessVariableCreate) -> dict[str, Any]:
    await ensure_default_business_variables(db)
    if payload.service_entry not in SERVICE_ENTRIES:
        raise _error(422, "BUSINESS_VARIABLE_SERVICE_ENTRY_INVALID", "服务类型不存在")
    variable = await VariableRepository(db).get(payload.variable_id)
    if variable is None:
        raise _error(404, "VARIABLE_NOT_FOUND", "业务参数不存在")

    content_type_id = _normalize_content_type_id(payload.content_type_id)
    content_type_name = "-"
    ports = _normalize_ports(payload.ports)
    if payload.service_entry == REVIEW_NOTES_ENTRY:
        content_type_id = NO_CONTENT_TYPE_ID
    else:
        content_type = await ContentTypeRepository(db).get(content_type_id)
        if content_type is None:
            raise _error(404, "CONTENT_TYPE_NOT_FOUND", "内容类型不存在")
        content_type_name = content_type.name

    repo = BusinessVariableRepository(db)
    if await repo.get_by_key(
        service_entry=payload.service_entry,
        content_type_id=content_type_id,
        variable_id=payload.variable_id,
    ):
        raise _error(409, "BUSINESS_VARIABLE_EXISTS", "当前服务入口下该业务变量绑定已存在")
    try:
        item = await repo.create(
            {
                "id": str(uuid.uuid4()),
                "service_entry": payload.service_entry,
                "content_type_id": content_type_id,
                "variable_id": payload.variable_id,
                "ports": ports,
                "required": payload.required,
                "enabled": payload.enabled,
                "created_by": str(user.uid),
            }
        )
    except IntegrityError as exc:
        raise _error(409, "BUSINESS_VARIABLE_EXISTS", "当前服务入口下该业务变量绑定已存在") from exc
    return {
        "business_variable": serialize_binding(
            item,
            content_type_name=content_type_name,
            variable_name=variable.name,
            variable_code=variable.variable_code,
            service_entry=payload.service_entry,
            ports=ports,
        )
    }


async def update_business_variable(db: AsyncSession, item_id: str, payload: BusinessVariableUpdate) -> dict[str, Any]:
    repo = BusinessVariableRepository(db)
    item = await repo.get(item_id)
    if item is None:
        raise _error(404, "BUSINESS_VARIABLE_NOT_FOUND", "业务变量不存在")
    data = payload.model_dump(exclude_unset=True)
    service_entry = item.service_entry or "装修家居"

    if "ports" in data:
        data["ports"] = _normalize_ports(data["ports"])

    if "variable_id" in data:
        variable = await VariableRepository(db).get(data["variable_id"])
        if variable is None:
            raise _error(404, "VARIABLE_NOT_FOUND", "业务参数不存在")

    if "content_type_id" in data or service_entry == REVIEW_NOTES_ENTRY:
        if service_entry == REVIEW_NOTES_ENTRY:
            data["content_type_id"] = NO_CONTENT_TYPE_ID
        else:
            content_type_id = _normalize_content_type_id(data.get("content_type_id", item.content_type_id))
            if not content_type_id:
                raise _error(422, "CONTENT_TYPE_REQUIRED", "装修家居必须选择内容类型")
            content_type = await ContentTypeRepository(db).get(content_type_id)
            if content_type is None:
                raise _error(404, "CONTENT_TYPE_NOT_FOUND", "内容类型不存在")
            data["content_type_id"] = content_type_id

    next_content_type_id = data.get("content_type_id", item.content_type_id) or NO_CONTENT_TYPE_ID
    next_variable_id = data.get("variable_id", item.variable_id)
    if next_content_type_id != (item.content_type_id or NO_CONTENT_TYPE_ID) or next_variable_id != item.variable_id:
        existing = await repo.get_by_key(
            service_entry=service_entry,
            content_type_id=next_content_type_id,
            variable_id=next_variable_id,
        )
        if existing is not None and existing.id != item.id:
            raise _error(409, "BUSINESS_VARIABLE_EXISTS", "当前服务入口下该业务变量绑定已存在")

    try:
        item = await repo.update(item, data)
    except IntegrityError as exc:
        raise _error(409, "BUSINESS_VARIABLE_EXISTS", "当前服务入口下该业务变量绑定已存在") from exc

    variable = await VariableRepository(db).get(item.variable_id)
    if variable is None:
        raise _error(404, "BUSINESS_VARIABLE_REF_MISSING", "关联的业务参数已不存在")
    content_type_name = "-"
    if item.content_type_id:
        content_type = await ContentTypeRepository(db).get(item.content_type_id)
        if content_type is None:
            raise _error(404, "BUSINESS_VARIABLE_REF_MISSING", "关联的内容类型已不存在")
        content_type_name = content_type.name
    return {
        "business_variable": serialize_binding(
            item,
            content_type_name=content_type_name,
            variable_name=variable.name,
            variable_code=variable.variable_code,
            service_entry=item.service_entry or variable.service_entry,
            ports=_binding_ports(item, variable),
        )
    }


async def delete_business_variable(db: AsyncSession, item_id: str) -> dict[str, Any]:
    repo = BusinessVariableRepository(db)
    item = await repo.get(item_id)
    if item is None:
        raise _error(404, "BUSINESS_VARIABLE_NOT_FOUND", "业务变量不存在")
    await repo.delete(item)
    return {"success": True, "id": item_id}
