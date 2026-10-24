from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, not_, or_, select, update
from sqlalchemy.orm import aliased
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.storage.postgres.models_business import User

from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentCoverPosterTemplate,
    ContentEmployee,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
    ContentMaterialShare,
    ContentMaterialShareItem,
    ContentTask,
    ImageDesignLibraryItem,
    RemoteMaterialLibrarySetting,
)
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.services.material_library_categories import PERSONAL_IMAGE_FOLDERS
from yuxi.services.material_upload_queue import (
    INGEST_COMPLETED,
    INGEST_EXPIRED,
    INGEST_FAILED,
    INGEST_PENDING,
    SHOW_AFTER_ORIGINAL,
)

# 排队中、生成中、生成成功占用图库图片。直生成功状态是 generated。
# 草稿、编译未开跑、失败、取消、审核拦截不占用，失败后可再次选择。
IMAGE_OCCUPANCY_ACTIVE_STATUSES = frozenset(
    {
        "queued",
        "running",
        "waiting_human",
        "waiting_external",
        "review_required",
        "reviewed",
        "generated",
        "completed",
    }
)
IMAGE_OCCUPANCY_RELEASED_STATUSES = frozenset({"failed", "cancelled"})
# 小程序/PC 图库「已使用」标记：占用中任务 + 已失败但仍引用图片的任务。
GALLERY_USED_DISPLAY_STATUSES = frozenset({*IMAGE_OCCUPANCY_ACTIVE_STATUSES, "failed", "brief_ready"})
ROUGH_CATEGORY_NAMES = frozenset({"毛坯房图库", "毛胚房图库"})


def _task_usage_scope_filters(
    owner_uids: set[str],
    *,
    tenant_id: str | None = None,
    enterprise_scope: bool = False,
) -> list[Any]:
    if enterprise_scope and tenant_id:
        return [ContentTask.tenant_id == tenant_id]
    if len(owner_uids) == 1:
        return [ContentTask.created_by == next(iter(owner_uids))]
    return [ContentTask.created_by.in_(owner_uids)]


class MaterialLibraryRepository:
    def __init__(self, db: AsyncSession, *, include_shared: bool = False):
        self.db = db
        self.include_shared = include_shared

    @staticmethod
    def private_rough_filters(owner_uid: str | None = None):
        filters = [
            ContentMaterialCategory.visibility == "private",
            ContentMaterialCategory.owner_uid == ContentMaterialLibraryItem.owner_uid,
            func.coalesce(
                ContentMaterialLibraryItem.category_owner_uid,
                ContentMaterialLibraryItem.owner_uid,
            )
            == ContentMaterialCategory.owner_uid,
            or_(
                ContentMaterialCategory.id == "mp-rough-private",
                ContentMaterialCategory.image_design_role == "rough",
                ContentMaterialCategory.name.in_(ROUGH_CATEGORY_NAMES),
            ),
        ]
        if owner_uid is not None:
            filters.append(ContentMaterialLibraryItem.owner_uid == owner_uid)
        return filters

    async def count_private_rough_images_by_owner(self, owner_uids: list[str]) -> dict[str, int]:
        if not owner_uids:
            return {}
        join = ContentMaterialLibraryItem.__table__.join(
            ContentCoverAsset, ContentCoverAsset.id == ContentMaterialLibraryItem.asset_id
        ).join(ContentMaterialCategory, self.category_join())
        rows = await self.db.execute(
            select(ContentMaterialLibraryItem.owner_uid, func.count(ContentMaterialLibraryItem.id))
            .select_from(join)
            .where(
                ContentMaterialLibraryItem.owner_uid.in_(owner_uids),
                ContentMaterialLibraryItem.material_type == "image",
                ContentMaterialLibraryItem.status == "enabled",
                ContentMaterialLibraryItem.deleted_at.is_(None),
                ContentCoverAsset.deleted_at.is_(None),
                *self.private_rough_filters(),
            )
            .group_by(ContentMaterialLibraryItem.owner_uid)
        )
        return {owner_uid: int(count) for owner_uid, count in rows.all()}

    @staticmethod
    def enterprise_rough_filters(owner_uid: str | None = None):
        filters = [
            ContentMaterialCategory.visibility == "enterprise",
            ContentMaterialCategory.material_type == "image",
            ContentMaterialCategory.parent_id.is_(None),
            or_(
                ContentMaterialCategory.image_design_role == "rough",
                ContentMaterialCategory.name.in_(ROUGH_CATEGORY_NAMES),
            ),
        ]
        if owner_uid is not None:
            filters.append(ContentMaterialLibraryItem.owner_uid == owner_uid)
        return filters

    async def count_enterprise_rough_images_by_owner(self, owner_uids: list[str]) -> dict[str, int]:
        if not owner_uids:
            return {}
        rows = await self.db.execute(
            select(ContentMaterialLibraryItem.owner_uid, func.count(ContentMaterialLibraryItem.id))
            .join(ContentCoverAsset, ContentCoverAsset.id == ContentMaterialLibraryItem.asset_id)
            .join(ContentMaterialCategory, self.category_join())
            .where(
                ContentMaterialLibraryItem.owner_uid.in_(owner_uids),
                ContentMaterialLibraryItem.material_type == "image",
                ContentMaterialLibraryItem.status == "enabled",
                ContentMaterialLibraryItem.deleted_at.is_(None),
                ContentCoverAsset.deleted_at.is_(None),
                *self.enterprise_rough_filters(),
            )
            .group_by(ContentMaterialLibraryItem.owner_uid)
        )
        return {owner_uid: int(count) for owner_uid, count in rows.all()}

    @staticmethod
    def _generated_root_clause(category):
        return and_(
            category.parent_id.is_(None),
            category.visibility == "enterprise",
            category.material_type == "image",
            or_(category.image_design_role == "generated", category.name == "生图图库"),
        )

    def _enterprise_generated_join(self):
        parent = aliased(ContentMaterialCategory)
        join = (
            ContentMaterialLibraryItem.__table__.join(
                ContentCoverAsset, ContentCoverAsset.id == ContentMaterialLibraryItem.asset_id
            )
            .join(ContentMaterialCategory, self.category_join())
            .outerjoin(parent, parent.id == ContentMaterialCategory.parent_id)
        )
        located = or_(self._generated_root_clause(ContentMaterialCategory), self._generated_root_clause(parent))
        return join, located

    async def count_personal_generated_images_by_owner(
        self,
        owner_uids: list[str],
        *,
        created_from: datetime | None = None,
        created_before: datetime | None = None,
    ) -> dict[str, int]:
        if not owner_uids:
            return {}
        names = ("AI生图图库", "AI生图图库（个人）", "生图图库")
        filters = [
            ContentMaterialLibraryItem.owner_uid.in_(owner_uids),
            ContentMaterialLibraryItem.material_type == "image",
            ContentMaterialLibraryItem.status == "enabled",
            ContentMaterialLibraryItem.deleted_at.is_(None),
            ContentCoverAsset.deleted_at.is_(None),
            ContentMaterialCategory.visibility == "private",
            ContentMaterialCategory.owner_uid == ContentMaterialLibraryItem.owner_uid,
            or_(
                ContentMaterialCategory.id == "mp-generated-private",
                ContentMaterialCategory.name.in_(names),
                and_(
                    ContentMaterialCategory.id.in_(["private-root", "uncategorized"]),
                    or_(
                        ContentMaterialLibraryItem.metadata_json["source_folder"].as_string() == "generated",
                        ContentMaterialLibraryItem.metadata_json["source"].as_string().in_(
                            ["image_design", "content_production"]
                        ),
                    ),
                ),
            ),
        ]
        if created_from is not None:
            filters.append(ContentCoverAsset.created_at >= created_from)
        if created_before is not None:
            filters.append(ContentCoverAsset.created_at < created_before)
        rows = await self.db.execute(
            select(ContentMaterialLibraryItem.owner_uid, func.count(ContentMaterialLibraryItem.id))
            .join(ContentCoverAsset, ContentCoverAsset.id == ContentMaterialLibraryItem.asset_id)
            .join(ContentMaterialCategory, self.category_join())
            .where(*filters)
            .group_by(ContentMaterialLibraryItem.owner_uid)
        )
        return {owner_uid: int(count) for owner_uid, count in rows.all()}

    async def count_enterprise_generated_images_by_owner(
        self,
        owner_uids: list[str],
        *,
        created_from: datetime | None = None,
        created_before: datetime | None = None,
    ) -> dict[str, int]:
        if not owner_uids:
            return {}
        join, located = self._enterprise_generated_join()
        filters = [
            ContentMaterialLibraryItem.owner_uid.in_(owner_uids),
            ContentMaterialLibraryItem.material_type == "image",
            ContentMaterialLibraryItem.status == "enabled",
            ContentMaterialLibraryItem.deleted_at.is_(None),
            ContentCoverAsset.deleted_at.is_(None),
            located,
        ]
        if created_from is not None:
            filters.append(ContentCoverAsset.created_at >= created_from)
        if created_before is not None:
            filters.append(ContentCoverAsset.created_at < created_before)
        rows = await self.db.execute(
            select(ContentMaterialLibraryItem.owner_uid, func.count(ContentMaterialLibraryItem.id))
            .select_from(join)
            .where(*filters)
            .group_by(ContentMaterialLibraryItem.owner_uid)
        )
        return {owner_uid: int(count) for owner_uid, count in rows.all()}

    async def count_case_shares_by_owner(
        self,
        owner_uids: list[str],
        *,
        created_from: datetime | None = None,
        created_before: datetime | None = None,
    ) -> dict[str, int]:
        if not owner_uids:
            return {}
        sharer_uid = func.coalesce(ContentMaterialShare.sharer_uid, ContentMaterialShare.owner_uid)
        filters = [sharer_uid.in_(owner_uids)]
        if created_from is not None:
            filters.append(ContentMaterialShare.created_at >= created_from)
        if created_before is not None:
            filters.append(ContentMaterialShare.created_at < created_before)
        rows = await self.db.execute(
            select(sharer_uid, func.count(ContentMaterialShare.id)).where(*filters).group_by(sharer_uid)
        )
        return {owner_uid: int(count) for owner_uid, count in rows.all()}

    async def get_remote_setting(self, *, for_update: bool = False) -> RemoteMaterialLibrarySetting | None:
        query = select(RemoteMaterialLibrarySetting).where(RemoteMaterialLibrarySetting.id == "global")
        if for_update:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalar_one_or_none()

    async def upsert_remote_setting(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        verification_status: str,
        verified_at,
        updated_by: int | None,
    ) -> RemoteMaterialLibrarySetting:
        setting = await self.get_remote_setting(for_update=True)
        if setting is None:
            setting = RemoteMaterialLibrarySetting(
                id="global",
                base_url=base_url,
                username=username,
                password=password,
                verification_status=verification_status,
                verified_at=verified_at,
                updated_by=updated_by,
            )
            self.db.add(setting)
        else:
            setting.base_url = base_url
            setting.username = username
            setting.password = password
            setting.verification_status = verification_status
            setting.verified_at = verified_at
            setting.updated_by = updated_by
            setting.updated_at = utc_now_naive()
        await self.db.flush()
        return setting

    def category_access(self, owner_uid: str):
        own = ContentMaterialCategory.owner_uid == owner_uid
        if not self.include_shared:
            return own
        admin_personal = and_(
            ContentMaterialCategory.material_type == "image",
            ContentMaterialCategory.visibility == "private",
            ContentMaterialCategory.is_global_personal.is_(True),
            ContentMaterialCategory.id.not_in([value[0] for value in PERSONAL_IMAGE_FOLDERS.values()]),
            ContentMaterialCategory.name.not_in(
                [name for _, name in PERSONAL_IMAGE_FOLDERS.values()]
                + [f"{name}（个人）" for _, name in PERSONAL_IMAGE_FOLDERS.values()]
                + ["生图图库", "毛胚房图库"]
            ),
        )
        return or_(own, ContentMaterialCategory.visibility == "enterprise", admin_personal)

    @staticmethod
    def visible_in_folder():
        """新上传等原图入库成功后才进入文件夹。没有标记的旧素材保持可见。"""
        waiting_for_original = ContentCoverAsset.metadata_json[SHOW_AFTER_ORIGINAL].as_boolean().is_(True)
        original_stored = (
            func.coalesce(ContentCoverAsset.metadata_json["ingest_status"].as_string(), INGEST_COMPLETED)
            == INGEST_COMPLETED
        )
        return or_(not_(waiting_for_original), original_stored)

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
                or_(
                    ContentMaterialCategory.visibility == "enterprise",
                    and_(
                        ContentMaterialCategory.visibility == "private",
                        ContentMaterialLibraryItem.owner_uid == owner_uid,
                    ),
                ),
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

    async def sync_system_categories(self, values: list[dict[str, Any]]) -> None:
        """Create or restore fixed galleries without touching user-created galleries."""
        for value in values:
            category = await self.db.scalar(
                select(ContentMaterialCategory).where(
                    ContentMaterialCategory.owner_uid == value["owner_uid"],
                    ContentMaterialCategory.material_type == value["material_type"],
                    ContentMaterialCategory.id == value["id"],
                )
            )
            if category is None:
                # Source folders and save targets are loaded in parallel by the mini-program.
                await self.db.execute(pg_insert(ContentMaterialCategory).values(value).on_conflict_do_nothing())
                continue
            for key in (
                "tenant_id",
                "visibility",
                "parent_id",
                "industry_slug",
                "description",
                "sort_order",
                "is_system",
            ):
                setattr(category, key, value[key])
            name_conflict = await self.db.scalar(
                select(ContentMaterialCategory.id).where(
                    ContentMaterialCategory.owner_uid == value["owner_uid"],
                    ContentMaterialCategory.material_type == value["material_type"],
                    ContentMaterialCategory.id != value["id"],
                    func.lower(ContentMaterialCategory.name) == value["name"].lower(),
                    ContentMaterialCategory.deleted_at.is_(None),
                )
            )
            if name_conflict is None:
                category.name = value["name"]
            category.deleted_at = None
        await self.db.flush()

    async def migrate_generated_private_root(self, owner_uid: str, root_id: str) -> None:
        """Move only explicitly recorded old root saves, preserving ordinary uncategorized images."""
        item = ContentMaterialLibraryItem
        rows = (
            (
                await self.db.execute(
                    select(item)
                    .where(
                        item.owner_uid == owner_uid,
                        or_(item.category_owner_uid == owner_uid, item.category_owner_uid.is_(None)),
                        item.material_type == "image",
                        item.category == "uncategorized",
                        item.metadata_json["source"].as_string() == "image_design",
                        item.metadata_json["resolved_save_target"]["scope"].as_string() == "private",
                        item.metadata_json["resolved_save_target"]["gallery_id"].as_string().is_(None),
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            metadata = row.metadata_json or {}
            saved = metadata.get("resolved_save_target") or {}
            if metadata.get("save_target_version") == 2 or "gallery_id" not in saved:
                continue
            row.category = root_id
            row.metadata_json = {**metadata, "save_target_version": 2}
            await self.db.execute(
                update(ImageDesignLibraryItem)
                .where(ImageDesignLibraryItem.source_material_item_id == row.id)
                .values(source_gallery_id=root_id)
            )
        await self.db.flush()

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

    async def get_category_exact(
        self,
        *,
        requester_uid: str,
        material_type: str,
        category_id: str,
        category_owner_uid: str | None = None,
        visibility: str | None = None,
        for_update: bool = False,
    ) -> ContentMaterialCategory | None:
        """Load one visible category without losing its owner when IDs overlap."""
        filters = [
            self.category_access(requester_uid),
            ContentMaterialCategory.material_type == material_type,
            ContentMaterialCategory.id == category_id,
            ContentMaterialCategory.deleted_at.is_(None),
        ]
        if category_owner_uid is not None:
            filters.append(ContentMaterialCategory.owner_uid == category_owner_uid)
        if visibility is not None:
            filters.append(ContentMaterialCategory.visibility == visibility)
        query = select(ContentMaterialCategory).where(*filters)
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

    async def get_private_rough_item_for_owner(
        self,
        item_id: str,
        owner_uid: str,
        *,
        for_update: bool = False,
    ) -> ContentMaterialLibraryItem | None:
        query = (
            select(ContentMaterialLibraryItem)
            .join(ContentMaterialCategory, self.category_join())
            .where(
                ContentMaterialLibraryItem.id == item_id,
                ContentMaterialLibraryItem.deleted_at.is_(None),
                *self.private_rough_filters(owner_uid),
            )
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

    async def create_share(self, share: ContentMaterialShare, items: list[ContentMaterialShareItem]) -> None:
        self.db.add(share)
        self.db.add_all(items)
        await self.db.flush()

    async def share_by_token(self, token: str) -> ContentMaterialShare | None:
        return await self.db.scalar(select(ContentMaterialShare).where(ContentMaterialShare.token == token))

    async def share_by_entry_code(self, code: str) -> ContentMaterialShare | None:
        return await self.db.scalar(select(ContentMaterialShare).where(ContentMaterialShare.mp_entry_code == code))

    async def ensure_share_entry_code(self, share_id: str, code: str) -> str:
        result = await self.db.execute(
            update(ContentMaterialShare).where(ContentMaterialShare.id == share_id)
            .values(mp_entry_code=func.coalesce(ContentMaterialShare.mp_entry_code, code))
            .returning(ContentMaterialShare.mp_entry_code)
        )
        return result.scalar_one()

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
        self,
        item_id: str,
        owner_uid: str | set[str],
        *,
        exclude_task_id: str | None = None,
        tenant_id: str | None = None,
        enterprise_scope: bool = False,
    ) -> bool:
        owner_uids = {owner_uid} if isinstance(owner_uid, str) else set(owner_uid)
        filters = [
            *_task_usage_scope_filters(owner_uids, tenant_id=tenant_id, enterprise_scope=enterprise_scope),
            ContentTask.selected_image_item_id == item_id,
            ContentTask.deleted_at.is_(None),
            ContentTask.status.in_(IMAGE_OCCUPANCY_ACTIVE_STATUSES),
        ]
        if exclude_task_id:
            filters.append(ContentTask.id != exclude_task_id)
        return bool((await self.db.execute(select(func.count(ContentTask.id)).where(*filters))).scalar_one())

    async def list_selected_image_item_ids(self, owner_uid: str, *, exclude_task_id: str | None = None) -> set[str]:
        filters = [
            ContentTask.created_by == owner_uid,
            ContentTask.selected_image_item_id.is_not(None),
            ContentTask.deleted_at.is_(None),
            ContentTask.status.in_(IMAGE_OCCUPANCY_ACTIVE_STATUSES),
        ]
        if exclude_task_id:
            filters.append(ContentTask.id != exclude_task_id)
        rows = await self.db.execute(select(ContentTask.selected_image_item_id).where(*filters))
        return {item_id for item_id in rows.scalars().all() if item_id}

    async def list_gallery_used_image_item_ids(
        self,
        owner_uid: str | set[str],
        *,
        exclude_task_id: str | None = None,
        tenant_id: str | None = None,
        enterprise_scope: bool = False,
    ) -> set[str]:
        owner_uids = {owner_uid} if isinstance(owner_uid, str) else set(owner_uid)
        filters = [
            *_task_usage_scope_filters(owner_uids, tenant_id=tenant_id, enterprise_scope=enterprise_scope),
            ContentTask.deleted_at.is_(None),
            ContentTask.status.in_(GALLERY_USED_DISPLAY_STATUSES),
        ]
        if exclude_task_id:
            filters.append(ContentTask.id != exclude_task_id)
        ids: set[str] = set()
        selected_rows = await self.db.execute(
            select(ContentTask.selected_image_item_id).where(
                *filters,
                ContentTask.selected_image_item_id.is_not(None),
            )
        )
        ids.update(item_id for item_id in selected_rows.scalars().all() if item_id)
        brief_image_item = ContentTask.brief_json["visual_material"]["image_item_id"].as_string()
        brief_rows = await self.db.execute(select(brief_image_item).where(*filters, brief_image_item.isnot(None)))
        ids.update(item_id for item_id in brief_rows.scalars().all() if item_id)
        runtime_image_item = ContentTask.runtime_config_snapshot_json["visual_material"]["image_item_id"].as_string()
        runtime_rows = await self.db.execute(
            select(runtime_image_item).where(*filters, runtime_image_item.isnot(None))
        )
        ids.update(item_id for item_id in runtime_rows.scalars().all() if item_id)
        return ids

    async def _owner_uids_for_uploader_query(self, pattern: str) -> list[str]:
        escaped = pattern.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        ilike_pattern = f"%{escaped}%"
        uids: set[str] = set(
            (
                await self.db.execute(
                    select(User.uid).where(
                        or_(
                            User.username.ilike(ilike_pattern, escape="\\"),
                            User.phone_number.ilike(ilike_pattern, escape="\\"),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        employee_ids = (
            (
                await self.db.execute(
                    select(ContentEmployee.id).where(
                        or_(
                            ContentEmployee.name.ilike(ilike_pattern, escape="\\"),
                            ContentEmployee.employee_code.ilike(ilike_pattern, escape="\\"),
                            ContentEmployee.login_account.ilike(ilike_pattern, escape="\\"),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        uids.update(f"mp_{employee_id.replace('-', '')[:17]}" for employee_id in employee_ids)
        return list(uids)

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
        category_owner_uid: str | None = None,
        private_rough_only: bool = False,
        enterprise_rough_owner_uid: str | None = None,
        enterprise_generated_owner_uid: str | None = None,
        uploaded_from: datetime | None = None,
        uploaded_before: datetime | None = None,
    ) -> tuple[list[tuple[ContentMaterialLibraryItem, ContentCoverAsset, ContentMaterialCategory]], int]:
        filters = [
            self.item_access(owner_uid),
            ContentMaterialLibraryItem.material_type == material_type,
            ContentMaterialLibraryItem.deleted_at.is_(None),
            ContentCoverAsset.deleted_at.is_(None),
            self.visible_in_folder(),
        ]
        if scope:
            filters.append(ContentMaterialCategory.visibility == scope)
        if category_ids:
            filters.append(ContentMaterialLibraryItem.category.in_(category_ids))
        elif category:
            filters.append(ContentMaterialLibraryItem.category == category)
        if category_owner_uid is not None:
            filters.append(
                func.coalesce(ContentMaterialLibraryItem.category_owner_uid, ContentMaterialLibraryItem.owner_uid)
                == category_owner_uid
            )
        if private_rough_only:
            filters.extend(self.private_rough_filters(owner_uid))
        if enterprise_rough_owner_uid is not None:
            filters.extend(self.enterprise_rough_filters(enterprise_rough_owner_uid))
        generated_parent = None
        if enterprise_generated_owner_uid is not None:
            generated_parent = aliased(ContentMaterialCategory)
            filters.append(ContentMaterialLibraryItem.owner_uid == enterprise_generated_owner_uid)
            filters.append(
                or_(
                    self._generated_root_clause(ContentMaterialCategory),
                    self._generated_root_clause(generated_parent),
                )
            )
        if uploaded_from is not None:
            filters.append(ContentCoverAsset.created_at >= uploaded_from)
        if uploaded_before is not None:
            filters.append(ContentCoverAsset.created_at < uploaded_before)
        if status:
            filters.append(ContentMaterialLibraryItem.status == status)
        if query_text:
            pattern = f"%{query_text.strip()}%"
            uploader_uids = await self._owner_uids_for_uploader_query(query_text)
            search_clauses = [
                ContentMaterialLibraryItem.display_name.ilike(pattern),
                ContentMaterialCategory.name.ilike(pattern),
            ]
            if uploader_uids:
                search_clauses.append(ContentMaterialLibraryItem.owner_uid.in_(uploader_uids))
            filters.append(or_(*search_clauses))
        join = ContentMaterialLibraryItem.__table__.join(
            ContentCoverAsset, ContentCoverAsset.id == ContentMaterialLibraryItem.asset_id
        ).join(
            ContentMaterialCategory,
            self.category_join(),
        )
        if generated_parent is not None:
            join = join.outerjoin(generated_parent, generated_parent.id == ContentMaterialCategory.parent_id)
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

    async def list_upload_notices(
        self,
        owner_uid: str,
        *,
        material_type: str,
        category: str | None,
        category_ids: list[str] | None = None,
        category_owner_uid: str | None = None,
        limit: int = 50,
    ) -> tuple[list[tuple[ContentMaterialLibraryItem, ContentCoverAsset]], int]:
        """当前用户在该文件夹里尚未成功入库的新上传。失败项用于提示，pending 用于上传后继续等待。"""
        filters = [
            self.item_access(owner_uid),
            ContentMaterialLibraryItem.owner_uid == owner_uid,
            ContentMaterialLibraryItem.material_type == material_type,
            ContentMaterialLibraryItem.deleted_at.is_(None),
            ContentCoverAsset.deleted_at.is_(None),
            ContentCoverAsset.metadata_json[SHOW_AFTER_ORIGINAL].as_boolean().is_(True),
        ]
        if category_ids:
            filters.append(ContentMaterialLibraryItem.category.in_(category_ids))
        elif category:
            filters.append(ContentMaterialLibraryItem.category == category)
        else:
            return [], 0
        if category_owner_uid is not None:
            filters.append(
                func.coalesce(ContentMaterialLibraryItem.category_owner_uid, ContentMaterialLibraryItem.owner_uid)
                == category_owner_uid
            )
        status = ContentCoverAsset.metadata_json["ingest_status"].as_string()
        join = ContentMaterialLibraryItem.__table__.join(
            ContentCoverAsset, ContentCoverAsset.id == ContentMaterialLibraryItem.asset_id
        )
        failed = (
            await self.db.execute(
                select(ContentMaterialLibraryItem, ContentCoverAsset)
                .select_from(join)
                .where(*filters, status.in_([INGEST_FAILED, INGEST_EXPIRED]))
                .order_by(ContentMaterialLibraryItem.created_at.desc())
                .limit(limit)
            )
        ).all()
        pending = int(
            (
                await self.db.execute(
                    select(func.count(ContentMaterialLibraryItem.id))
                    .select_from(join)
                    .where(*filters, status == INGEST_PENDING)
                )
            ).scalar_one()
        )
        return list(failed), pending

    async def uploader_names(self, owner_uids: list[str]) -> dict[str, str]:
        rows = await self.db.execute(select(User.uid, User.username).where(User.uid.in_(owner_uids)))
        return dict(rows.all())

    async def category_summaries(
        self, owner_uid: str, *, material_type: str
    ) -> dict[tuple[str, str], tuple[int, ContentMaterialLibraryItem | None]]:
        filters = [
            self.item_access(owner_uid),
            ContentMaterialLibraryItem.material_type == material_type,
            ContentMaterialLibraryItem.deleted_at.is_(None),
            ContentCoverAsset.deleted_at.is_(None),
            self.visible_in_folder(),
        ]
        owner = func.coalesce(ContentMaterialLibraryItem.category_owner_uid, ContentMaterialLibraryItem.owner_uid)
        counts = (
            await self.db.execute(
                select(owner, ContentMaterialLibraryItem.category, func.count(ContentMaterialLibraryItem.id))
                .join(ContentCoverAsset, ContentCoverAsset.id == ContentMaterialLibraryItem.asset_id)
                .where(*filters)
                .group_by(owner, ContentMaterialLibraryItem.category)
            )
        ).all()
        result = {}
        for category_owner, category_id, count in counts:
            latest = await self.db.scalar(
                select(ContentMaterialLibraryItem)
                .join(ContentCoverAsset, ContentCoverAsset.id == ContentMaterialLibraryItem.asset_id)
                .where(*filters, owner == category_owner, ContentMaterialLibraryItem.category == category_id)
                .order_by(ContentMaterialLibraryItem.created_at.desc())
                .limit(1)
            )
            result[(category_owner, category_id)] = (int(count), latest)
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
        return bool(
            (
                await self.db.execute(
                    select(ContentMaterialLibraryItem.id)
                    .where(
                        ContentMaterialLibraryItem.asset_id == asset_id,
                        ContentMaterialLibraryItem.metadata_json["ever_shared"].as_boolean().is_(True),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
        )

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
