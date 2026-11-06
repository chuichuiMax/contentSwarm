from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_content import ContentType, CreativeStyle
from yuxi.utils.datetime_utils import utc_now_naive


class CreativeStyleRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def has_any(self) -> bool:
        result = await self.db.execute(select(CreativeStyle.id).limit(1))
        return result.scalar_one_or_none() is not None

    async def get(self, style_id: str) -> CreativeStyle | None:
        result = await self.db.execute(select(CreativeStyle).where(CreativeStyle.id == style_id))
        return result.scalar_one_or_none()

    async def get_by_type_and_name(self, content_type_id: str, name: str) -> CreativeStyle | None:
        result = await self.db.execute(
            select(CreativeStyle).where(
                CreativeStyle.content_type_id == content_type_id,
                CreativeStyle.name == name,
            )
        )
        return result.scalar_one_or_none()

    async def list_styles(
        self,
        *,
        keyword: str | None = None,
        content_type_id: str | None = None,
        enabled_only: bool = False,
    ) -> list[tuple[CreativeStyle, str]]:
        query = select(CreativeStyle, ContentType.name).join(
            ContentType, ContentType.id == CreativeStyle.content_type_id
        )
        if content_type_id:
            query = query.where(CreativeStyle.content_type_id == content_type_id)
        if enabled_only:
            query = query.where(CreativeStyle.enabled.is_(True))
        if keyword:
            escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            query = query.where(
                or_(
                    CreativeStyle.name.ilike(pattern, escape="\\"),
                    ContentType.name.ilike(pattern, escape="\\"),
                )
            )
        result = await self.db.execute(
            query.order_by(ContentType.type_code.asc(), CreativeStyle.sort_order.asc(), CreativeStyle.created_at.asc())
        )
        return list(result.all())

    async def next_sort_order(self, content_type_id: str) -> int:
        result = await self.db.execute(
            select(CreativeStyle.sort_order)
            .where(CreativeStyle.content_type_id == content_type_id)
            .order_by(CreativeStyle.sort_order.desc())
            .limit(1)
        )
        current = result.scalar_one_or_none()
        return 0 if current is None else int(current) + 1

    async def create(self, data: dict[str, Any]) -> CreativeStyle:
        item = CreativeStyle(**data)
        self.db.add(item)
        await self.db.flush()
        return item

    async def update(self, item: CreativeStyle, data: dict[str, Any]) -> CreativeStyle:
        for key, value in data.items():
            setattr(item, key, value)
        item.updated_at = utc_now_naive()
        await self.db.flush()
        return item

    async def delete(self, item: CreativeStyle) -> None:
        await self.db.delete(item)
        await self.db.flush()
