from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_content import ContentProcessStandard
from yuxi.utils.datetime_utils import utc_now_naive


class ProcessStandardRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, item_id: str) -> ContentProcessStandard | None:
        result = await self.db.execute(
            select(ContentProcessStandard).where(ContentProcessStandard.id == item_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name_detail(self, name: str, detail: str) -> ContentProcessStandard | None:
        result = await self.db.execute(
            select(ContentProcessStandard).where(
                ContentProcessStandard.name == name,
                ContentProcessStandard.detail == detail,
            )
        )
        return result.scalar_one_or_none()

    async def has_any(self) -> bool:
        result = await self.db.execute(select(ContentProcessStandard.id).limit(1))
        return result.scalar_one_or_none() is not None

    async def list_names(self) -> list[str]:
        result = await self.db.execute(
            select(ContentProcessStandard.name)
            .distinct()
            .order_by(ContentProcessStandard.name.asc())
        )
        return [name for (name,) in result.all() if name]

    async def list_enabled_names(self) -> list[str]:
        result = await self.db.execute(
            select(ContentProcessStandard.name)
            .where(ContentProcessStandard.enabled.is_(True))
            .distinct()
            .order_by(ContentProcessStandard.name.asc())
        )
        return [name for (name,) in result.all() if name]

    async def list_enabled_names_by_type(self) -> dict[str, list[str]]:
        result = await self.db.execute(
            select(ContentProcessStandard.name, ContentProcessStandard.detail)
            .where(ContentProcessStandard.enabled.is_(True))
            .order_by(ContentProcessStandard.name.asc(), ContentProcessStandard.detail.asc())
        )
        mapping: dict[str, list[str]] = {}
        for type_name, detail in result.all():
            if not type_name or not detail:
                continue
            bucket = mapping.setdefault(type_name, [])
            if detail not in bucket:
                bucket.append(detail)
        return mapping

    async def list_items(
        self, *, keyword: str | None = None, name: str | None = None
    ) -> list[ContentProcessStandard]:
        query = select(ContentProcessStandard)
        if name:
            query = query.where(ContentProcessStandard.name == name)
        if keyword:
            escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            query = query.where(
                or_(
                    ContentProcessStandard.name.ilike(pattern, escape="\\"),
                    ContentProcessStandard.detail.ilike(pattern, escape="\\"),
                )
            )
        result = await self.db.execute(
            query.order_by(ContentProcessStandard.created_at.asc(), ContentProcessStandard.id.asc())
        )
        return list(result.scalars().all())

    async def create(self, data: dict[str, Any]) -> ContentProcessStandard:
        item = ContentProcessStandard(**data)
        self.db.add(item)
        await self.db.flush()
        return item

    async def update(self, item: ContentProcessStandard, data: dict[str, Any]) -> ContentProcessStandard:
        for key, value in data.items():
            setattr(item, key, value)
        item.updated_at = utc_now_naive()
        await self.db.flush()
        return item

    async def delete(self, item: ContentProcessStandard) -> None:
        await self.db.delete(item)
        await self.db.flush()
