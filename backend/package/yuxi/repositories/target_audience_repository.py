from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_content import ContentTargetAudience
from yuxi.utils.datetime_utils import utc_now_naive


class TargetAudienceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, item_id: str) -> ContentTargetAudience | None:
        result = await self.db.execute(select(ContentTargetAudience).where(ContentTargetAudience.id == item_id))
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> ContentTargetAudience | None:
        result = await self.db.execute(select(ContentTargetAudience).where(ContentTargetAudience.name == name))
        return result.scalar_one_or_none()

    async def has_any(self) -> bool:
        result = await self.db.execute(select(ContentTargetAudience.id).limit(1))
        return result.scalar_one_or_none() is not None

    async def list_items(self, *, keyword: str | None = None) -> list[ContentTargetAudience]:
        query = select(ContentTargetAudience)
        if keyword:
            escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(ContentTargetAudience.name.ilike(f"%{escaped}%", escape="\\"))
        result = await self.db.execute(
            query.order_by(ContentTargetAudience.created_at.asc(), ContentTargetAudience.id.asc())
        )
        return list(result.scalars().all())

    async def list_enabled_names(self) -> list[str]:
        result = await self.db.execute(
            select(ContentTargetAudience.name)
            .where(ContentTargetAudience.enabled.is_(True))
            .order_by(ContentTargetAudience.created_at.asc(), ContentTargetAudience.id.asc())
        )
        return [name for (name,) in result.all() if name]

    async def create(self, data: dict[str, Any]) -> ContentTargetAudience:
        item = ContentTargetAudience(**data)
        self.db.add(item)
        await self.db.flush()
        return item

    async def update(self, item: ContentTargetAudience, data: dict[str, Any]) -> ContentTargetAudience:
        for key, value in data.items():
            setattr(item, key, value)
        item.updated_at = utc_now_naive()
        await self.db.flush()
        return item

    async def delete(self, item: ContentTargetAudience) -> None:
        await self.db.delete(item)
        await self.db.flush()
