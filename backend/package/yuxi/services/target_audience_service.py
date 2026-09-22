from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.target_audience_repository import TargetAudienceRepository
from yuxi.storage.postgres.models_business import User

DEFAULT_TARGET_AUDIENCES: tuple[tuple[str, bool], ...] = (
    ("毛坯", True),
    ("精装房", True),
    ("旧房改造", True),
    ("别墅", True),
    ("乡墅", False),
)


class TargetAudienceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True


class TargetAudienceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


def _normalize_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise _error(422, "TARGET_AUDIENCE_INVALID_FIELD", "目标人群类型不能为空")
    return normalized


async def ensure_default_target_audiences(db: AsyncSession) -> None:
    repo = TargetAudienceRepository(db)
    if await repo.has_any():
        return
    for name, enabled in DEFAULT_TARGET_AUDIENCES:
        await repo.create(
            {
                "id": str(uuid.uuid4()),
                "name": name,
                "enabled": enabled,
                "created_by": "system",
            }
        )


async def list_target_audiences(db: AsyncSession, keyword: str | None = None) -> dict[str, Any]:
    await ensure_default_target_audiences(db)
    items = await TargetAudienceRepository(db).list_items(keyword=keyword.strip() if keyword else None)
    return {"target_audiences": [item.to_dict() for item in items], "total": len(items)}


async def list_enabled_target_audience_names(db: AsyncSession) -> list[str]:
    await ensure_default_target_audiences(db)
    return await TargetAudienceRepository(db).list_enabled_names()


async def create_target_audience(db: AsyncSession, user: User, payload: TargetAudienceCreate) -> dict[str, Any]:
    await ensure_default_target_audiences(db)
    repo = TargetAudienceRepository(db)
    name = _normalize_name(payload.name)
    if await repo.get_by_name(name):
        raise _error(409, "TARGET_AUDIENCE_EXISTS", "该目标人群类型已存在")
    try:
        item = await repo.create(
            {
                "id": str(uuid.uuid4()),
                "name": name,
                "enabled": payload.enabled,
                "created_by": str(user.uid),
            }
        )
    except IntegrityError as exc:
        raise _error(409, "TARGET_AUDIENCE_EXISTS", "该目标人群类型已存在") from exc
    return {"target_audience": item.to_dict()}


async def update_target_audience(db: AsyncSession, item_id: str, payload: TargetAudienceUpdate) -> dict[str, Any]:
    repo = TargetAudienceRepository(db)
    item = await repo.get(item_id)
    if item is None:
        raise _error(404, "TARGET_AUDIENCE_NOT_FOUND", "目标人群类型不存在")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        data["name"] = _normalize_name(data["name"])
        existing = await repo.get_by_name(data["name"])
        if existing is not None and existing.id != item.id:
            raise _error(409, "TARGET_AUDIENCE_EXISTS", "该目标人群类型已存在")
    try:
        item = await repo.update(item, data)
    except IntegrityError as exc:
        raise _error(409, "TARGET_AUDIENCE_EXISTS", "该目标人群类型已存在") from exc
    return {"target_audience": item.to_dict()}


async def delete_target_audience(db: AsyncSession, item_id: str) -> dict[str, Any]:
    repo = TargetAudienceRepository(db)
    item = await repo.get(item_id)
    if item is None:
        raise _error(404, "TARGET_AUDIENCE_NOT_FOUND", "目标人群类型不存在")
    await repo.delete(item)
    return {"ok": True}
