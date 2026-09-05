from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.content_type_repository import ContentTypeRepository
from yuxi.storage.postgres.models_business import User

DEFAULT_CONTENT_TYPES: tuple[tuple[str, str, bool], ...] = (
    ("NRLX0001", "工艺施工展示", True),
    ("NRLX0002", "装修报价清单", True),
    ("NRLX0003", "装修避坑分享", True),
    ("NRLX0004", "装修省钱攻略", True),
    ("NRLX0005", "装修案例分享", False),
    ("NRLX0006", "装修知识科普", True),
    ("NRLX0007", "人设自荐", True),
)


class ContentTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type_code: str | None = Field(default=None, max_length=32)
    enabled: bool = True


class ContentTypeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None


def _type_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


def _normalize_text(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise _type_error(422, "CONTENT_TYPE_INVALID_FIELD", f"{field} 不能为空")
    return normalized


def next_type_code(existing_codes: list[str]) -> str:
    max_n = 0
    for code in existing_codes:
        prefix, suffix = code[:4], code[4:]
        if prefix.upper() == "NRLX" and suffix.isdigit():
            max_n = max(max_n, int(suffix))
    return f"NRLX{max_n + 1:04d}"


async def ensure_default_content_types(db: AsyncSession) -> None:
    repo = ContentTypeRepository(db)
    if await repo.has_any():
        return
    for type_code, name, enabled in DEFAULT_CONTENT_TYPES:
        await repo.create(
            {
                "id": str(uuid.uuid4()),
                "type_code": type_code,
                "name": name,
                "enabled": enabled,
                "created_by": "system",
            }
        )


async def list_content_types(db: AsyncSession, keyword: str | None = None) -> dict[str, Any]:
    await ensure_default_content_types(db)
    items = await ContentTypeRepository(db).list_types(keyword=keyword.strip() if keyword else None)
    return {"content_types": [item.to_dict() for item in items], "total": len(items)}


async def create_content_type(db: AsyncSession, user: User, payload: ContentTypeCreate) -> dict[str, Any]:
    await ensure_default_content_types(db)
    repo = ContentTypeRepository(db)
    name = _normalize_text(payload.name, field="内容类型名称")
    if payload.type_code:
        type_code = _normalize_text(payload.type_code, field="内容类型编码")
    else:
        type_code = next_type_code(await repo.list_codes())
    if await repo.get_by_name(name):
        raise _type_error(409, "CONTENT_TYPE_NAME_EXISTS", "内容类型已存在")
    if await repo.get_by_code(type_code):
        raise _type_error(409, "CONTENT_TYPE_CODE_EXISTS", "内容类型编码已存在")
    try:
        item = await repo.create(
            {
                "id": str(uuid.uuid4()),
                "type_code": type_code,
                "name": name,
                "enabled": payload.enabled,
                "created_by": str(user.uid),
            }
        )
    except IntegrityError as exc:
        raise _type_error(409, "CONTENT_TYPE_DUPLICATE", "内容类型或编码已存在") from exc
    return {"content_type": item.to_dict()}


async def update_content_type(db: AsyncSession, type_pk: str, payload: ContentTypeUpdate) -> dict[str, Any]:
    repo = ContentTypeRepository(db)
    item = await repo.get(type_pk)
    if item is None:
        raise _type_error(404, "CONTENT_TYPE_NOT_FOUND", "内容类型不存在")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        data["name"] = _normalize_text(data["name"], field="内容类型名称")
        existing = await repo.get_by_name(data["name"])
        if existing and existing.id != item.id:
            raise _type_error(409, "CONTENT_TYPE_NAME_EXISTS", "内容类型已存在")
    try:
        item = await repo.update(item, data)
    except IntegrityError as exc:
        raise _type_error(409, "CONTENT_TYPE_NAME_EXISTS", "内容类型已存在") from exc
    return {"content_type": item.to_dict()}


async def delete_content_type(db: AsyncSession, type_pk: str) -> dict[str, Any]:
    repo = ContentTypeRepository(db)
    item = await repo.get(type_pk)
    if item is None:
        raise _type_error(404, "CONTENT_TYPE_NOT_FOUND", "内容类型不存在")
    await repo.delete(item)
    return {"success": True, "id": type_pk}
