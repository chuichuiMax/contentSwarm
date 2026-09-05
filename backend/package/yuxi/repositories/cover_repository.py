from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_content import ContentCover
from yuxi.utils.datetime_utils import utc_now_naive


class CoverRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, cover_pk: str) -> ContentCover | None:
        result = await self.db.execute(select(ContentCover).where(ContentCover.id == cover_pk))
        return result.scalar_one_or_none()

    async def get_by_image_name(self, category: str, image_name: str) -> ContentCover | None:
        result = await self.db.execute(
            select(ContentCover).where(
                ContentCover.category == category,
                func.lower(ContentCover.image_name) == image_name.lower(),
            )
        )
        return result.scalar_one_or_none()

    async def list_covers(self, *, category: str, keyword: str | None = None) -> list[ContentCover]:
        query = select(ContentCover).where(ContentCover.category == category)
        if keyword:
            escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(ContentCover.image_name.ilike(f"%{escaped}%", escape="\\"))
        result = await self.db.execute(query.order_by(ContentCover.created_at.desc()))
        return list(result.scalars().all())

    async def list_enabled(self) -> list[ContentCover]:
        result = await self.db.execute(
            select(ContentCover).where(ContentCover.enabled.is_(True)).order_by(ContentCover.created_at.desc())
        )
        return list(result.scalars().all())

    async def create(self, data: dict[str, Any]) -> ContentCover:
        cover = ContentCover(**data)
        self.db.add(cover)
        await self.db.flush()
        return cover

    async def update(self, cover: ContentCover, data: dict[str, Any]) -> ContentCover:
        for key, value in data.items():
            setattr(cover, key, value)
        cover.updated_at = utc_now_naive()
        await self.db.flush()
        return cover

    async def delete(self, cover: ContentCover) -> None:
        await self.db.delete(cover)
        await self.db.flush()
