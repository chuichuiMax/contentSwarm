"""Delete reviewed private product/产品商品 galleries and their image files.

Dry-run by default. Stop writers before --apply: database transactions cannot
roll back object storage deletions. On storage failure, rerun the same command
after fixing storage; missing objects are safe to delete again.
"""

# ruff: noqa: E402 -- CLI adds the package source directory before project imports.

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import func, select

APP_ROOT = Path(__file__).resolve().parents[1]
for path in (APP_ROOT, APP_ROOT / "package"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.material_upload_queue import delete_material_display_cache, material_thumb_object_name
from yuxi.storage.minio import get_minio_client
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
    ImageDesignJob,
    ImageDesignLibraryItem,
)
from yuxi.utils.datetime_utils import utc_now_naive


async def purge(db, *, owner_uids: list[str], apply: bool = False) -> dict:
    """Validate the entire plan before touching storage; never move images elsewhere."""
    if not owner_uids:
        raise ValueError("必须通过 --owner-uid 明确指定账号")
    owners = set(owner_uids)
    found = set(await db.scalars(select(User.uid).where(User.uid.in_(owners), User.is_deleted == 0)))
    if owners != found:
        raise ValueError(f"指定账号不存在或已注销：{','.join(sorted(owners - found))}")
    galleries = list(
        await db.scalars(
            select(ContentMaterialCategory)
            .where(
                ContentMaterialCategory.owner_uid.in_(owners),
                ContentMaterialCategory.material_type == "image",
                ContentMaterialCategory.id == "product",
                ContentMaterialCategory.name == "产品商品",
                ContentMaterialCategory.visibility == "private",
                ContentMaterialCategory.parent_id.is_(None),
            )
            .with_for_update()
        )
    )
    repo = MaterialLibraryRepository(db)
    cover_repo = ContentCoverRepository(db)
    plan, records, blockers = [], [], []
    for gallery in galleries:
        children = await db.scalar(
            select(ContentMaterialCategory.id)
            .where(
                ContentMaterialCategory.owner_uid == gallery.owner_uid,
                ContentMaterialCategory.material_type == "image",
                ContentMaterialCategory.parent_id == gallery.id,
                ContentMaterialCategory.deleted_at.is_(None),
            )
            .limit(1)
        )
        if children:
            blockers.append(f"{gallery.owner_uid}: 图库仍有子图库")
        # Include previously removed items: those may still retain original files.
        items = list(
            await db.scalars(
                select(ContentMaterialLibraryItem)
                .where(
                    func.coalesce(ContentMaterialLibraryItem.category_owner_uid, ContentMaterialLibraryItem.owner_uid)
                    == gallery.owner_uid,
                    ContentMaterialLibraryItem.material_type == "image",
                    ContentMaterialLibraryItem.category == gallery.id,
                )
                .with_for_update()
            )
        )
        planned_images = []
        active_jobs = list(
            await db.scalars(
                select(ImageDesignJob.request_json).where(
                    ImageDesignJob.owner_uid == gallery.owner_uid,
                    ImageDesignJob.status.not_in(("succeeded", "failed", "cancelled")),
                )
            )
        )
        for item in items:
            asset = await db.scalar(
                select(ContentCoverAsset)
                .where(
                    ContentCoverAsset.id == item.asset_id,
                )
                .with_for_update()
            )
            if asset is None or item.owner_uid != gallery.owner_uid or asset.owner_uid != gallery.owner_uid:
                blockers.append(f"{item.id}: 文件缺失或图片归属其他账号")
                continue
            outside_item = await db.scalar(
                select(ContentMaterialLibraryItem.id)
                .where(
                    ContentMaterialLibraryItem.asset_id == asset.id,
                    ContentMaterialLibraryItem.id != item.id,
                    ContentMaterialLibraryItem.deleted_at.is_(None),
                )
                .limit(1)
            )
            outside_asset = await db.scalar(
                select(ContentCoverAsset.id)
                .where(
                    ContentCoverAsset.bucket_name == asset.bucket_name,
                    ContentCoverAsset.object_name == asset.object_name,
                    ContentCoverAsset.id != asset.id,
                    ContentCoverAsset.deleted_at.is_(None),
                )
                .limit(1)
            )
            refs = list(
                await db.scalars(
                    select(ImageDesignLibraryItem)
                    .where(
                        ImageDesignLibraryItem.asset_id == asset.id,
                    )
                    .with_for_update()
                )
            )
            outside_ref = any(
                ref.hidden_at is None
                and (
                    ref.owner_uid != gallery.owner_uid
                    or ref.source_material_item_id not in (None, item.id)
                    or ref.source_gallery_id not in (None, gallery.id)
                )
                for ref in refs
            )
            if outside_item or outside_asset or outside_ref or await repo.get_poster_template_by_asset(asset.id):
                blockers.append(f"{item.id}: 图片仍被其他图库或模板引用")
            in_design_job = any(
                json.dumps(item.id) in json.dumps(request) or json.dumps(asset.id) in json.dumps(request)
                for request in active_jobs
            )
            if (
                await cover_repo.asset_is_in_active_job(asset.id, gallery.owner_uid)
                or await repo.item_is_selected_by_task(item.id, gallery.owner_uid)
                or in_design_job
            ):
                blockers.append(f"{item.id}: 图片正在被任务使用")
            planned_images.append(
                {
                    "item_id": item.id,
                    "asset_id": asset.id,
                    "bucket": asset.bucket_name,
                    "object": asset.object_name,
                    "thumbnail": material_thumb_object_name(asset.object_name),
                }
            )
            records.append((item, asset, refs))
        plan.append({"owner_uid": gallery.owner_uid, "gallery_id": gallery.id, "images": planned_images})
    result = {"apply": apply, "galleries": plan, "blockers": blockers}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not apply:
        await db.rollback()
        return result
    if blockers:
        raise ValueError("清理计划存在阻止项，未删除任何文件")
    storage = get_minio_client() if records else None
    for item, asset, refs in records:
        # Deliberately ignore ever_shared/retain_asset_on_delete: this purge deletes
        # originals too. Independent share snapshots are separate stored objects.
        await storage.adelete_file(asset.bucket_name, asset.object_name)
        await storage.adelete_file(asset.bucket_name, material_thumb_object_name(asset.object_name))
        await delete_material_display_cache(asset.id)
        item.deleted_at = asset.deleted_at = utc_now_naive()
        for ref in refs:
            ref.hidden_at = item.deleted_at
    for gallery in galleries:
        await db.delete(gallery)
    await db.commit()
    return result


async def run(*, owner_uids: list[str], apply: bool) -> None:
    from yuxi.storage.postgres.manager import pg_manager

    pg_manager.initialize()
    try:
        async with pg_manager.AsyncSession() as db:
            await purge(db, owner_uids=owner_uids, apply=apply)
    finally:
        await pg_manager.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-uid", action="append", required=True, help="Reviewed account UID; repeat per account")
    parser.add_argument(
        "--apply", action="store_true", help="Delete gallery, originals and thumbnails; default dry-run"
    )
    args = parser.parse_args()
    load_dotenv(APP_ROOT.parent / ".env", override=False)
    if not os.getenv("POSTGRES_URL"):
        parser.error("POSTGRES_URL is required")
    asyncio.run(run(owner_uids=args.owner_uid, apply=args.apply))
