from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_content import ContentVariable
from yuxi.utils.datetime_utils import utc_now_naive


class VariableRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, variable_pk: str) -> ContentVariable | None:
        result = await self.db.execute(select(ContentVariable).where(ContentVariable.id == variable_pk))
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> ContentVariable | None:
        result = await self.db.execute(select(ContentVariable).where(ContentVariable.name == name))
        return result.scalar_one_or_none()

    async def get_by_service_entry_and_name(self, service_entry: str, name: str) -> ContentVariable | None:
        result = await self.db.execute(
            select(ContentVariable).where(
                ContentVariable.service_entry == service_entry,
                ContentVariable.name == name,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_code(self, variable_code: str) -> ContentVariable | None:
        result = await self.db.execute(select(ContentVariable).where(ContentVariable.variable_code == variable_code))
        return result.scalar_one_or_none()

    async def has_any(self) -> bool:
        result = await self.db.execute(select(ContentVariable.id).limit(1))
        return result.scalar_one_or_none() is not None

    async def list_codes(self) -> list[str]:
        result = await self.db.execute(select(ContentVariable.variable_code))
        return [code for (code,) in result.all()]

    async def list_by_service_entry(self, service_entry: str) -> list[ContentVariable]:
        result = await self.db.execute(
            select(ContentVariable).where(ContentVariable.service_entry == service_entry)
        )
        return list(result.scalars().all())

    async def list_variables(self, *, keyword: str | None = None) -> list[ContentVariable]:
        query = select(ContentVariable)
        if keyword:
            escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            query = query.where(
                or_(
                    ContentVariable.variable_code.ilike(pattern, escape="\\"),
                    ContentVariable.name.ilike(pattern, escape="\\"),
                    ContentVariable.service_entry.ilike(pattern, escape="\\"),
                )
            )
        result = await self.db.execute(
            query.order_by(ContentVariable.variable_code.asc(), ContentVariable.created_at.asc())
        )
        return list(result.scalars().all())

    async def rename_service_entry(self, old_name: str, new_name: str) -> None:
        await self.db.execute(
            update(ContentVariable)
            .where(ContentVariable.service_entry == old_name)
            .values(service_entry=new_name, updated_at=utc_now_naive())
        )

    async def create(self, data: dict[str, Any]) -> ContentVariable:
        item = ContentVariable(**data)
        self.db.add(item)
        await self.db.flush()
        return item

    async def update(self, item: ContentVariable, data: dict[str, Any]) -> ContentVariable:
        for key, value in data.items():
            setattr(item, key, value)
        item.updated_at = utc_now_naive()
        await self.db.flush()
        return item

    async def delete(self, item: ContentVariable) -> None:
        await self.db.delete(item)
        await self.db.flush()
