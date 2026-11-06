from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.content_type_repository import ContentTypeRepository
from yuxi.repositories.creative_style_repository import CreativeStyleRepository
from yuxi.services.content_type_service import ensure_default_content_types
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import CreativeStyle

_PROMPT_CATALOG_PATH = Path(__file__).resolve().parents[1] / "content" / "data" / "content_style_prompts.json"


class CreativeStyleCreate(BaseModel):
    content_type_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=255)
    prompt: str = Field(default="", max_length=20000)
    enabled: bool = True


class CreativeStyleUpdate(BaseModel):
    content_type_id: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=255)
    prompt: str | None = Field(default=None, max_length=20000)
    enabled: bool | None = None


def _style_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


def _normalize_text(value: str, *, field: str, required: bool = True) -> str:
    normalized = value.strip()
    if required and not normalized:
        raise _style_error(422, "CREATIVE_STYLE_INVALID_FIELD", f"{field} 不能为空")
    return normalized


def bundled_creative_style_rows() -> list[dict[str, Any]]:
    """提示词表里的创作风格。内容类型名称必须与配置中的内容类型一致。"""

    catalog = json.loads(_PROMPT_CATALOG_PATH.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for type_name, items in catalog.items():
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            name = str(item.get("label") or "").strip()
            if not name:
                continue
            rows.append(
                {
                    "content_type_name": str(type_name).strip(),
                    "name": name,
                    "description": str(item.get("description") or "").strip(),
                    "prompt": str(item.get("prompt") or "").strip(),
                    "sort_order": index,
                }
            )
    return rows


def _serialize(item: CreativeStyle, content_type_name: str) -> dict[str, Any]:
    data = item.to_dict()
    data["content_type_name"] = content_type_name
    return data


async def ensure_default_creative_styles(db: AsyncSession) -> None:
    await ensure_default_content_types(db)
    repo = CreativeStyleRepository(db)
    if await repo.has_any():
        return
    type_repo = ContentTypeRepository(db)
    for row in bundled_creative_style_rows():
        content_type = await type_repo.get_by_name(row["content_type_name"])
        if content_type is None:
            continue
        await repo.create(
            {
                "id": str(uuid.uuid4()),
                "content_type_id": content_type.id,
                "name": row["name"],
                "description": row["description"],
                "prompt": row["prompt"],
                "enabled": True,
                "sort_order": row["sort_order"],
                "created_by": "system",
            }
        )


async def list_creative_styles(
    db: AsyncSession,
    *,
    keyword: str | None = None,
    content_type_id: str | None = None,
    enabled_only: bool = False,
) -> dict[str, Any]:
    await ensure_default_creative_styles(db)
    rows = await CreativeStyleRepository(db).list_styles(
        keyword=keyword.strip() if keyword else None,
        content_type_id=content_type_id or None,
        enabled_only=enabled_only,
    )
    items = [_serialize(item, type_name) for item, type_name in rows]
    return {"creative_styles": items, "total": len(items)}


async def _require_content_type(db: AsyncSession, content_type_id: str):
    await ensure_default_content_types(db)
    content_type = await ContentTypeRepository(db).get(content_type_id)
    if content_type is None:
        raise _style_error(422, "CREATIVE_STYLE_CONTENT_TYPE_MISSING", "内容类型不存在")
    return content_type


async def create_creative_style(db: AsyncSession, user: User, payload: CreativeStyleCreate) -> dict[str, Any]:
    content_type = await _require_content_type(db, payload.content_type_id.strip())
    name = _normalize_text(payload.name, field="创作风格")
    prompt = _normalize_text(payload.prompt, field="提示词")
    repo = CreativeStyleRepository(db)
    if await repo.get_by_type_and_name(content_type.id, name):
        raise _style_error(409, "CREATIVE_STYLE_DUPLICATE", "该内容类型下已有同名创作风格")
    try:
        item = await repo.create(
            {
                "id": str(uuid.uuid4()),
                "content_type_id": content_type.id,
                "name": name,
                "description": _normalize_text(payload.description, field="说明", required=False),
                "prompt": prompt,
                "enabled": payload.enabled,
                "sort_order": await repo.next_sort_order(content_type.id),
                "created_by": user.uid,
            }
        )
    except IntegrityError as exc:
        raise _style_error(409, "CREATIVE_STYLE_DUPLICATE", "该内容类型下已有同名创作风格") from exc
    return _serialize(item, content_type.name)


async def update_creative_style(db: AsyncSession, style_id: str, payload: CreativeStyleUpdate) -> dict[str, Any]:
    repo = CreativeStyleRepository(db)
    item = await repo.get(style_id)
    if item is None:
        raise _style_error(404, "CREATIVE_STYLE_NOT_FOUND", "创作风格不存在")
    data: dict[str, Any] = {}
    content_type_name = ""
    if payload.content_type_id is not None:
        content_type = await _require_content_type(db, payload.content_type_id.strip())
        data["content_type_id"] = content_type.id
        content_type_name = content_type.name
    else:
        current = await ContentTypeRepository(db).get(item.content_type_id)
        content_type_name = current.name if current else ""
    if payload.name is not None:
        data["name"] = _normalize_text(payload.name, field="创作风格")
    if payload.description is not None:
        data["description"] = _normalize_text(payload.description, field="说明", required=False)
    if payload.prompt is not None:
        data["prompt"] = payload.prompt.strip()
    if payload.enabled is not None:
        data["enabled"] = payload.enabled
    target_type_id = data.get("content_type_id", item.content_type_id)
    target_name = data.get("name", item.name)
    existing = await repo.get_by_type_and_name(target_type_id, target_name)
    if existing is not None and existing.id != item.id:
        raise _style_error(409, "CREATIVE_STYLE_DUPLICATE", "该内容类型下已有同名创作风格")
    if data:
        try:
            item = await repo.update(item, data)
        except IntegrityError as exc:
            raise _style_error(409, "CREATIVE_STYLE_DUPLICATE", "该内容类型下已有同名创作风格") from exc
    return _serialize(item, content_type_name)


async def delete_creative_style(db: AsyncSession, style_id: str) -> dict[str, Any]:
    repo = CreativeStyleRepository(db)
    item = await repo.get(style_id)
    if item is None:
        raise _style_error(404, "CREATIVE_STYLE_NOT_FOUND", "创作风格不存在")
    await repo.delete(item)
    return {"deleted": True}
