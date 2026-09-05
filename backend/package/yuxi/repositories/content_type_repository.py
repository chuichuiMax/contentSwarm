from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_content import ContentType
from yuxi.utils.datetime_utils import utc_now_naive


class ContentTypeRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, type_pk: str) -> ContentType | None:
        result = await self.db.execute(select(ContentType).where(ContentType.id == type_pk))
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> ContentType | None:
        result = await self.db.execute(select(ContentType).where(ContentType.name == name))
        return result.scalar_one_or_none()

    async def get_by_code(self, type_code: str) -> ContentType | None:
        result = await self.db.execute(select(ContentType).where(ContentType.type_code == type_code))
        return result.scalar_one_or_none()

    async def has_any(self) -> bool:
        result = await self.db.execute(select(ContentType.id).limit(1))
        return result.scalar_one_or_none() is not None

    async def list_codes(self) -> list[str]:
        result = await self.db.execute(select(ContentType.type_code))
        return [code for (code,) in result.all()]

    async def list_types(self, *, keyword: str | None = None) -> list[ContentType]:
        query = select(ContentType)
        if keyword:
            escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            query = query.where(
                or_(
                    ContentType.type_code.ilike(pattern, escape="\\"),
                    ContentType.name.ilike(pattern, escape="\\"),
                )
            )
        result = await self.db.execute(query.order_by(ContentType.type_code.asc(), ContentType.created_at.asc()))
        return list(result.scalars().all())

    async def create(self, data: dict[str, Any]) -> ContentType:
        item = ContentType(**data)
        self.db.add(item)
        await self.db.flush()
        return item

    async def update(self, item: ContentType, data: dict[str, Any]) -> ContentType:
        for key, value in data.items():
            setattr(item, key, value)
        item.updated_at = utc_now_naive()
        await self.db.flush()
        return item

    async def delete(self, item: ContentType) -> None:
        await self.db.delete(item)
        await self.db.flush()
