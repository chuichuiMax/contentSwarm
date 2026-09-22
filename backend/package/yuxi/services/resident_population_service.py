from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.resident_population_repository import ResidentPopulationRepository
from yuxi.storage.postgres.models_business import User

DEFAULT_RESIDENT_POPULATIONS: tuple[tuple[str, bool], ...] = (
    ("三口之家", True),
    ("四口之家", True),
    ("五口之家", True),
    ("两代同堂", True),
    ("新婚婚房", True),
    ("养老房", True),
    ("人宠友好家", True),
)


class ResidentPopulationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True


class ResidentPopulationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


def _normalize_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise _error(422, "RESIDENT_POPULATION_INVALID_FIELD", "居住人口类型不能为空")
    return normalized


async def ensure_default_resident_populations(db: AsyncSession) -> None:
    repo = ResidentPopulationRepository(db)
    if await repo.has_any():
        return
    for name, enabled in DEFAULT_RESIDENT_POPULATIONS:
        await repo.create(
            {
                "id": str(uuid.uuid4()),
                "name": name,
                "enabled": enabled,
                "created_by": "system",
            }
        )


async def list_resident_populations(db: AsyncSession, keyword: str | None = None) -> dict[str, Any]:
    await ensure_default_resident_populations(db)
    items = await ResidentPopulationRepository(db).list_items(keyword=keyword.strip() if keyword else None)
    return {"resident_populations": [item.to_dict() for item in items], "total": len(items)}


async def list_enabled_resident_population_names(db: AsyncSession) -> list[str]:
    await ensure_default_resident_populations(db)
    return await ResidentPopulationRepository(db).list_enabled_names()


async def create_resident_population(db: AsyncSession, user: User, payload: ResidentPopulationCreate) -> dict[str, Any]:
    await ensure_default_resident_populations(db)
    repo = ResidentPopulationRepository(db)
    name = _normalize_name(payload.name)
    if await repo.get_by_name(name):
        raise _error(409, "RESIDENT_POPULATION_EXISTS", "该居住人口类型已存在")
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
        raise _error(409, "RESIDENT_POPULATION_EXISTS", "该居住人口类型已存在") from exc
    return {"resident_population": item.to_dict()}


async def update_resident_population(
    db: AsyncSession, item_id: str, payload: ResidentPopulationUpdate
) -> dict[str, Any]:
    repo = ResidentPopulationRepository(db)
    item = await repo.get(item_id)
    if item is None:
        raise _error(404, "RESIDENT_POPULATION_NOT_FOUND", "居住人口类型不存在")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        data["name"] = _normalize_name(data["name"])
        existing = await repo.get_by_name(data["name"])
        if existing is not None and existing.id != item.id:
            raise _error(409, "RESIDENT_POPULATION_EXISTS", "该居住人口类型已存在")
    try:
        item = await repo.update(item, data)
    except IntegrityError as exc:
        raise _error(409, "RESIDENT_POPULATION_EXISTS", "该居住人口类型已存在") from exc
    return {"resident_population": item.to_dict()}


async def delete_resident_population(db: AsyncSession, item_id: str) -> dict[str, Any]:
    repo = ResidentPopulationRepository(db)
    item = await repo.get(item_id)
    if item is None:
        raise _error(404, "RESIDENT_POPULATION_NOT_FOUND", "居住人口类型不存在")
    await repo.delete(item)
    return {"ok": True}
