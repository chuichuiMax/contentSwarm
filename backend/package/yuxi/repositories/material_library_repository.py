from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentCoverPosterTemplate,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
    ContentTask,
)


class MaterialLibraryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_item(self, **values: Any) -> ContentMaterialLibraryItem:
        item = ContentMaterialLibraryItem(**values)
        self.db.add(item)
        await self.db.flush()
        return item

    async def create_category(self, **values: Any) -> ContentMaterialCategory:
        category = ContentMaterialCategory(**values)
        self.db.add(category)
        await self.db.flush()
        return category

    async def ensure_default_categories(self, values: list[dict[str, Any]]) -> None:
        await self.db.execute(pg_insert(ContentMaterialCategory).values(values).on_conflict_do_nothing())

    async def list_categories(self, owner_uid: str, material_type: str) -> list[ContentMaterialCategory]:
        return list(
            (
                await self.db.execute(
                    select(ContentMaterialCategory)
                    .where(
                        ContentMaterialCategory.owner_uid == owner_uid,
                        ContentMaterialCategory.material_type == material_type,
                        ContentMaterialCategory.deleted_at.is_(None),
                    )
                    .order_by(ContentMaterialCategory.sort_order, ContentMaterialCategory.created_at)
                )
            ).scalars()
        )

    async def get_category(
        self,
        owner_uid: str,
        material_type: str,
        category_id: str,
        *,
        for_update: bool = False,
    ) -> ContentMaterialCategory | None:
        query = select(ContentMaterialCategory).where(
            ContentMaterialCategory.owner_uid == owner_uid,
            ContentMaterialCategory.material_type == material_type,
            ContentMaterialCategory.id == category_id,
            ContentMaterialCategory.deleted_at.is_(None),
        )
        if for_update:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalar_one_or_none()

    async def list_child_categories(
        self,
        owner_uid: str,
        material_type: str,
        parent_id: str,
    ) -> list[ContentMaterialCategory]:
        return list(
            (
                await self.db.execute(
                    select(ContentMaterialCategory)
                    .where(
                        ContentMaterialCategory.owner_uid == owner_uid,
                        ContentMaterialCategory.material_type == material_type,
                        ContentMaterialCategory.parent_id == parent_id,
                        ContentMaterialCategory.deleted_at.is_(None),
                    )
                    .order_by(ContentMaterialCategory.sort_order, ContentMaterialCategory.created_at)
                )
            ).scalars()
        )

    async def get_item_for_user(
        self, item_id: str, owner_uid: str, *, for_update: bool = False
    ) -> ContentMaterialLibraryItem | None:
        query = select(ContentMaterialLibraryItem).where(
            ContentMaterialLibraryItem.id == item_id,
            ContentMaterialLibraryItem.owner_uid == owner_uid,
            ContentMaterialLibraryItem.deleted_at.is_(None),
        )
        if for_update:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalar_one_or_none()

    async def get_item_by_asset(self, asset_id: str) -> ContentMaterialLibraryItem | None:
        return (
            await self.db.execute(
                select(ContentMaterialLibraryItem).where(
                    ContentMaterialLibraryItem.asset_id == asset_id,
                    ContentMaterialLibraryItem.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def list_items_by_asset_ids(
        self,
        owner_uid: str,
        asset_ids: list[str],
    ) -> list[ContentMaterialLibraryItem]:
        if not asset_ids:
            return []
        return list(
            (
                await self.db.execute(
                    select(ContentMaterialLibraryItem).where(
                        ContentMaterialLibraryItem.owner_uid == owner_uid,
                        ContentMaterialLibraryItem.asset_id.in_(asset_ids),
                        ContentMaterialLibraryItem.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )

    async def item_is_selected_by_task(self, item_id: str, owner_uid: str) -> bool:
        return bool(
            (
                await self.db.execute(
                    select(func.count(ContentTask.id)).where(
                        ContentTask.created_by == owner_uid,
                        ContentTask.selected_image_item_id == item_id,
                        ContentTask.deleted_at.is_(None),
                    )
                )
            ).scalar_one()
        )

    async def list_items(
        self,
        owner_uid: str,
        *,
        material_type: str,
        category: str | None,
        status: str | None,
        query_text: str | None,
        page: int,
        page_size: int,
        sort: str = "newest",
    ) -> tuple[list[tuple[ContentMaterialLibraryItem, ContentCoverAsset, ContentMaterialCategory]], int]:
        filters = [
            ContentMaterialLibraryItem.owner_uid == owner_uid,
            ContentMaterialLibraryItem.material_type == material_type,
            ContentMaterialLibraryItem.deleted_at.is_(None),
            ContentCoverAsset.deleted_at.is_(None),
        ]
        if category:
            filters.append(ContentMaterialLibraryItem.category == category)
        if status:
            filters.append(ContentMaterialLibraryItem.status == status)
        if query_text:
            pattern = f"%{query_text.strip()}%"
            filters.append(
                or_(
                    ContentMaterialLibraryItem.display_name.ilike(pattern),
                    ContentMaterialCategory.name.ilike(pattern),
                )
            )
        join = ContentMaterialLibraryItem.__table__.join(
            ContentCoverAsset, ContentCoverAsset.id == ContentMaterialLibraryItem.asset_id
        ).join(
            ContentMaterialCategory,
            (ContentMaterialCategory.owner_uid == ContentMaterialLibraryItem.owner_uid)
            & (ContentMaterialCategory.material_type == ContentMaterialLibraryItem.material_type)
            & (ContentMaterialCategory.id == ContentMaterialLibraryItem.category)
            & ContentMaterialCategory.deleted_at.is_(None),
        )
        total = int(
            (
                await self.db.execute(
                    select(func.count(ContentMaterialLibraryItem.id)).select_from(join).where(*filters)
                )
            ).scalar_one()
        )
        order_by = {
            "newest": ContentMaterialLibraryItem.created_at.desc(),
            "oldest": ContentMaterialLibraryItem.created_at.asc(),
            "name": ContentMaterialLibraryItem.display_name.asc(),
        }[sort]
        rows = (
            await self.db.execute(
                select(ContentMaterialLibraryItem, ContentCoverAsset, ContentMaterialCategory)
                .select_from(join)
                .where(*filters)
                .order_by(order_by)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return list(rows), total

    async def category_summaries(
        self, owner_uid: str, *, material_type: str
    ) -> dict[str, tuple[int, ContentMaterialLibraryItem | None]]:
        filters = [
            ContentMaterialLibraryItem.owner_uid == owner_uid,
            ContentMaterialLibraryItem.material_type == material_type,
            ContentMaterialLibraryItem.deleted_at.is_(None),
            ContentCoverAsset.deleted_at.is_(None),
        ]
        counts = (
            await self.db.execute(
                select(ContentMaterialLibraryItem.category, func.count(ContentMaterialLibraryItem.id))
                .join(ContentCoverAsset, ContentCoverAsset.id == ContentMaterialLibraryItem.asset_id)
                .where(*filters)
                .group_by(ContentMaterialLibraryItem.category)
            )
        ).all()
        result: dict[str, tuple[int, ContentMaterialLibraryItem | None]] = {}
        for category, count in counts:
            latest = (
                await self.db.execute(
                    select(ContentMaterialLibraryItem)
                    .join(ContentCoverAsset, ContentCoverAsset.id == ContentMaterialLibraryItem.asset_id)
                    .where(*filters, ContentMaterialLibraryItem.category == category)
                    .order_by(ContentMaterialLibraryItem.updated_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            result[category] = (int(count), latest)
        return result

    async def category_item_count(self, owner_uid: str, material_type: str, category_id: str) -> int:
        return int(
            (
                await self.db.execute(
                    select(func.count(ContentMaterialLibraryItem.id)).where(
                        ContentMaterialLibraryItem.owner_uid == owner_uid,
                        ContentMaterialLibraryItem.material_type == material_type,
                        ContentMaterialLibraryItem.category == category_id,
                        ContentMaterialLibraryItem.deleted_at.is_(None),
                    )
                )
            ).scalar_one()
        )

    async def reassign_category_items(
        self,
        owner_uid: str,
        material_type: str,
        source_category_id: str,
        target_category_id: str,
    ) -> None:
        await self.db.execute(
            update(ContentMaterialLibraryItem)
            .where(
                ContentMaterialLibraryItem.owner_uid == owner_uid,
                ContentMaterialLibraryItem.material_type == material_type,
                ContentMaterialLibraryItem.category == source_category_id,
                ContentMaterialLibraryItem.deleted_at.is_(None),
            )
            .values(category=target_category_id)
        )
        if material_type == "cover_template":
            await self.db.execute(
                update(ContentCoverPosterTemplate)
                .where(
                    ContentCoverPosterTemplate.owner_uid == owner_uid,
                    ContentCoverPosterTemplate.category == source_category_id,
                    ContentCoverPosterTemplate.deleted_at.is_(None),
                )
                .values(category=target_category_id)
            )

    async def update_child_category_industry(
        self,
        owner_uid: str,
        material_type: str,
        parent_id: str,
        industry_slug: str | None,
    ) -> None:
        await self.db.execute(
            update(ContentMaterialCategory)
            .where(
                ContentMaterialCategory.owner_uid == owner_uid,
                ContentMaterialCategory.material_type == material_type,
                ContentMaterialCategory.parent_id == parent_id,
                ContentMaterialCategory.deleted_at.is_(None),
            )
            .values(industry_slug=industry_slug)
        )

    async def normalize_orphan_categories(
        self,
        owner_uid: str,
        material_type: str,
        active_category_ids: list[str],
        fallback_category_id: str,
    ) -> None:
        await self.db.execute(
            update(ContentMaterialLibraryItem)
            .where(
                ContentMaterialLibraryItem.owner_uid == owner_uid,
                ContentMaterialLibraryItem.material_type == material_type,
                ContentMaterialLibraryItem.deleted_at.is_(None),
                ContentMaterialLibraryItem.category.not_in(active_category_ids),
            )
            .values(category=fallback_category_id)
        )
        if material_type == "cover_template":
            await self.db.execute(
                update(ContentCoverPosterTemplate)
                .where(
                    ContentCoverPosterTemplate.owner_uid == owner_uid,
                    ContentCoverPosterTemplate.deleted_at.is_(None),
                    ContentCoverPosterTemplate.category.not_in(active_category_ids),
                )
                .values(category=fallback_category_id)
            )

    async def get_asset(self, asset_id: str, owner_uid: str, *, for_update: bool = False) -> ContentCoverAsset | None:
        query = select(ContentCoverAsset).where(
            ContentCoverAsset.id == asset_id,
            ContentCoverAsset.owner_uid == owner_uid,
            ContentCoverAsset.deleted_at.is_(None),
        )
        if for_update:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalar_one_or_none()

    async def get_poster_template_by_asset(self, asset_id: str) -> ContentCoverPosterTemplate | None:
        return (
            await self.db.execute(
                select(ContentCoverPosterTemplate).where(
                    ContentCoverPosterTemplate.asset_id == asset_id,
                    ContentCoverPosterTemplate.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def list_poster_templates_by_asset_ids(
        self,
        owner_uid: str,
        asset_ids: list[str],
    ) -> list[ContentCoverPosterTemplate]:
        if not asset_ids:
            return []
        return list(
            (
                await self.db.execute(
                    select(ContentCoverPosterTemplate).where(
                        ContentCoverPosterTemplate.owner_uid == owner_uid,
                        ContentCoverPosterTemplate.asset_id.in_(asset_ids),
                        ContentCoverPosterTemplate.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )
