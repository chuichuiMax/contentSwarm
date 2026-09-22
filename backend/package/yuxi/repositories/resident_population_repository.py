from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_content import ContentResidentPopulation
from yuxi.utils.datetime_utils import utc_now_naive


class ResidentPopulationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, item_id: str) -> ContentResidentPopulation | None:
        result = await self.db.execute(select(ContentResidentPopulation).where(ContentResidentPopulation.id == item_id))
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> ContentResidentPopulation | None:
        result = await self.db.execute(select(ContentResidentPopulation).where(ContentResidentPopulation.name == name))
        return result.scalar_one_or_none()

    async def has_any(self) -> bool:
        result = await self.db.execute(select(ContentResidentPopulation.id).limit(1))
        return result.scalar_one_or_none() is not None

    async def list_items(self, *, keyword: str | None = None) -> list[ContentResidentPopulation]:
        query = select(ContentResidentPopulation)
        if keyword:
            escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(ContentResidentPopulation.name.ilike(f"%{escaped}%", escape="\\"))
        result = await self.db.execute(
            query.order_by(ContentResidentPopulation.created_at.asc(), ContentResidentPopulation.id.asc())
        )
        return list(result.scalars().all())

    async def list_enabled_names(self) -> list[str]:
        result = await self.db.execute(
            select(ContentResidentPopulation.name)
            .where(ContentResidentPopulation.enabled.is_(True))
            .order_by(ContentResidentPopulation.created_at.asc(), ContentResidentPopulation.id.asc())
        )
        return [name for (name,) in result.all() if name]

    async def create(self, data: dict[str, Any]) -> ContentResidentPopulation:
        item = ContentResidentPopulation(**data)
        self.db.add(item)
        await self.db.flush()
        return item

    async def update(self, item: ContentResidentPopulation, data: dict[str, Any]) -> ContentResidentPopulation:
        for key, value in data.items():
            setattr(item, key, value)
        item.updated_at = utc_now_naive()
        await self.db.flush()
        return item

    async def delete(self, item: ContentResidentPopulation) -> None:
        await self.db.delete(item)
        await self.db.flush()
