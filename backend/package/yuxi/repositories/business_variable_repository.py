from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_content import ContentBusinessVariable
from yuxi.utils.datetime_utils import utc_now_naive


class BusinessVariableRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, item_id: str) -> ContentBusinessVariable | None:
        result = await self.db.execute(
            select(ContentBusinessVariable).where(ContentBusinessVariable.id == item_id)
        )
        return result.scalar_one_or_none()

    async def get_by_key(
        self,
        *,
        service_entry: str,
        content_type_id: str,
        variable_id: str,
    ) -> ContentBusinessVariable | None:
        result = await self.db.execute(
            select(ContentBusinessVariable).where(
                ContentBusinessVariable.service_entry == service_entry,
                ContentBusinessVariable.content_type_id == content_type_id,
                ContentBusinessVariable.variable_id == variable_id,
            )
        )
        return result.scalar_one_or_none()

    async def has_any(self) -> bool:
        result = await self.db.execute(select(ContentBusinessVariable.id).limit(1))
        return result.scalar_one_or_none() is not None

    async def list_items(self) -> list[ContentBusinessVariable]:
        result = await self.db.execute(
            select(ContentBusinessVariable).order_by(
                ContentBusinessVariable.created_at.asc(),
                ContentBusinessVariable.id.asc(),
            )
        )
        return list(result.scalars().all())

    async def create(self, data: dict[str, Any]) -> ContentBusinessVariable:
        item = ContentBusinessVariable(**data)
        self.db.add(item)
        await self.db.flush()
        return item

    async def update(self, item: ContentBusinessVariable, data: dict[str, Any]) -> ContentBusinessVariable:
        for key, value in data.items():
            setattr(item, key, value)
        item.updated_at = utc_now_naive()
        await self.db.flush()
        return item

    async def delete(self, item: ContentBusinessVariable) -> None:
        await self.db.delete(item)
        await self.db.flush()

    async def delete_by_variable_id(self, variable_id: str) -> None:
        await self.db.execute(
            delete(ContentBusinessVariable).where(ContentBusinessVariable.variable_id == variable_id)
        )
        await self.db.flush()
