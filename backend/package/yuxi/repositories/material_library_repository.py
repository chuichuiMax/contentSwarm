from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentCoverPosterTemplate,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
    ContentMaterialShare,
    ContentMaterialShareItem,
    ContentTask,
)

# 失败/取消的任务释放图库占用，便于重新选图生产；删除任务本来就不计入占用。
IMAGE_OCCUPANCY_RELEASED_STATUSES = frozenset({"failed", "cancelled"})


class MaterialLibraryRepository:
    def __init__(self, db: AsyncSession, *, include_shared: bool = False):
        self.db = db
        self.include_shared = include_shared

    def category_access(self, owner_uid: str):
        own = ContentMaterialCategory.owner_uid == owner_uid
        if not self.include_shared:
            return own
        return or_(own, ContentMaterialCategory.visibility == "enterprise")

    @staticmethod
    def category_join():
        return (
            (
                ContentMaterialCategory.owner_uid
                == func.coalesce(ContentMaterialLibraryItem.category_owner_uid, ContentMaterialLibraryItem.owner_uid)
            )
            & (ContentMaterialCategory.id == ContentMaterialLibraryItem.category)
            & (ContentMaterialCategory.material_type == ContentMaterialLibraryItem.material_type)
            & ContentMaterialCategory.deleted_at.is_(None)
        )

    def item_access(self, owner_uid: str):
        if not self.include_shared:
            return ContentMaterialLibraryItem.owner_uid == owner_uid
        return (
            select(ContentMaterialCategory.id)
            .where(
                self.category_join(),
                self.category_access(owner_uid),
            )
            .correlate(ContentMaterialLibraryItem)
            .exists()
        )

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
                        self.category_access(owner_uid),
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
            self.category_access(owner_uid),
            ContentMaterialCategory.material_type == material_type,
            ContentMaterialCategory.id == category_id,
            ContentMaterialCategory.deleted_at.is_(None),
        )
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
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
                        self.category_access(owner_uid),
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
            self.item_access(owner_uid),
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
                        self.item_access(owner_uid),
                        ContentMaterialLibraryItem.asset_id.in_(asset_ids),
                        ContentMaterialLibraryItem.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )

    async def list_image_items_with_assets_and_categories(
        self, owner_uid: str, item_ids: list[str]
    ) -> list[tuple[ContentMaterialLibraryItem, ContentCoverAsset, ContentMaterialCategory]]:
        if not item_ids:
            return []
        join = ContentMaterialLibraryItem.__table__.join(
            ContentCoverAsset, ContentCoverAsset.id == ContentMaterialLibraryItem.asset_id
        ).join(
            ContentMaterialCategory,
            self.category_join(),
        )
        return list(
            (
                await self.db.execute(
                    select(ContentMaterialLibraryItem, ContentCoverAsset, ContentMaterialCategory)
                    .select_from(join)
                    .where(
                        self.item_access(owner_uid),
                        ContentMaterialLibraryItem.id.in_(item_ids),
                        ContentMaterialLibraryItem.material_type == "image",
                        ContentMaterialLibraryItem.status == "enabled",
                        ContentMaterialLibraryItem.deleted_at.is_(None),
                        ContentCoverAsset.deleted_at.is_(None),
                        ContentMaterialCategory.deleted_at.is_(None),
                    )
                )
            ).all()
        )

    async def create_share(
        self, share: ContentMaterialShare, items: list[ContentMaterialShareItem]
    ) -> None:
        self.db.add(share)
        self.db.add_all(items)
        await self.db.flush()

    async def get_share_with_items(
        self, token: str
    ) -> tuple[ContentMaterialShare | None, list[ContentMaterialShareItem]]:
        share = (
            await self.db.execute(select(ContentMaterialShare).where(ContentMaterialShare.token == token))
        ).scalar_one_or_none()
        if share is None:
            return None, []
        items = list(
            (
                await self.db.execute(
                    select(ContentMaterialShareItem)
                    .where(ContentMaterialShareItem.share_id == share.id)
                    .order_by(ContentMaterialShareItem.display_order)
                )
            ).scalars()
        )
        return share, items

    async def item_is_selected_by_task(
        self, item_id: str, owner_uid: str, *, exclude_task_id: str | None = None
    ) -> bool:
        filters = [
            ContentTask.created_by == owner_uid,
            ContentTask.selected_image_item_id == item_id,
            ContentTask.deleted_at.is_(None),
            ContentTask.status.notin_(IMAGE_OCCUPANCY_RELEASED_STATUSES),
        ]
        if exclude_task_id:
            filters.append(ContentTask.id != exclude_task_id)
        return bool((await self.db.execute(select(func.count(ContentTask.id)).where(*filters))).scalar_one())

    async def list_selected_image_item_ids(
        self, owner_uid: str, *, exclude_task_id: str | None = None
    ) -> set[str]:
        filters = [
            ContentTask.created_by == owner_uid,
            ContentTask.selected_image_item_id.is_not(None),
            ContentTask.deleted_at.is_(None),
            ContentTask.status.notin_(IMAGE_OCCUPANCY_RELEASED_STATUSES),
        ]
        if exclude_task_id:
            filters.append(ContentTask.id != exclude_task_id)
        rows = await self.db.execute(select(ContentTask.selected_image_item_id).where(*filters))
        return {item_id for item_id in rows.scalars().all() if item_id}

    async def list_items(
        self,
        owner_uid: str,
        *,
        material_type: str,
        category: str | None,
        category_ids: list[str] | None = None,
        status: str | None,
        query_text: str | None,
        page: int,
        page_size: int,
        sort: str = "newest",
        scope: str | None = None,
    ) -> tuple[list[tuple[ContentMaterialLibraryItem, ContentCoverAsset, ContentMaterialCategory]], int]:
        filters = [
            self.item_access(owner_uid),
            ContentMaterialLibraryItem.material_type == material_type,
            ContentMaterialLibraryItem.deleted_at.is_(None),
            ContentCoverAsset.deleted_at.is_(None),
        ]
        if scope:
            filters.append(ContentMaterialCategory.visibility == scope)
        if category_ids:
            filters.append(ContentMaterialLibraryItem.category.in_(category_ids))
        elif category:
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
            self.category_join(),
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

    async def uploader_names(self, owner_uids: list[str]) -> dict[str, str]:
        rows = await self.db.execute(select(User.uid, User.username).where(User.uid.in_(owner_uids)))
        return dict(rows.all())

    async def category_summaries(
        self, owner_uid: str, *, material_type: str
    ) -> dict[str, tuple[int, ContentMaterialLibraryItem | None]]:
        filters = [
            self.item_access(owner_uid),
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
                    .order_by(ContentMaterialLibraryItem.created_at.desc())
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
                        self.item_access(owner_uid),
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
        target_owner_uid: str | None = None,
    ) -> None:
        await self.db.execute(
            update(ContentMaterialLibraryItem)
            .where(
                self.item_access(owner_uid),
                ContentMaterialLibraryItem.material_type == material_type,
                ContentMaterialLibraryItem.category == source_category_id,
                ContentMaterialLibraryItem.deleted_at.is_(None),
            )
            .values(category=target_category_id, category_owner_uid=target_owner_uid or owner_uid)
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
                self.category_access(owner_uid),
                ContentMaterialCategory.material_type == material_type,
                ContentMaterialCategory.parent_id == parent_id,
                ContentMaterialCategory.deleted_at.is_(None),
            )
            .values(industry_slug=industry_slug)
        )

    async def category_items(self, category: ContentMaterialCategory):
        return list(
            (
                await self.db.execute(
                    select(ContentMaterialLibraryItem).where(
                        func.coalesce(
                            ContentMaterialLibraryItem.category_owner_uid, ContentMaterialLibraryItem.owner_uid
                        )
                        == category.owner_uid,
                        ContentMaterialLibraryItem.material_type == category.material_type,
                        ContentMaterialLibraryItem.category == category.id,
                    )
                )
            ).scalars()
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
                self.item_access(owner_uid),
                ContentMaterialLibraryItem.material_type == material_type,
                ContentMaterialLibraryItem.deleted_at.is_(None),
                ~select(ContentMaterialCategory.id)
                .where(self.category_join())
                .correlate(ContentMaterialLibraryItem)
                .exists(),
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

    async def asset_was_shared(self, asset_id: str) -> bool:
        return bool((await self.db.execute(select(ContentMaterialLibraryItem.id).where(
            ContentMaterialLibraryItem.asset_id == asset_id,
            ContentMaterialLibraryItem.metadata_json["ever_shared"].as_boolean().is_(True),
        ).limit(1))).scalar_one_or_none())

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
