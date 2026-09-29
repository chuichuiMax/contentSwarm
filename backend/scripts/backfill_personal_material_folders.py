"""Backfill four mini-program folders. Dry-run by default; never deletes files."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select, update

APP_ROOT = Path(__file__).resolve().parents[1]
for path in (APP_ROOT, APP_ROOT / "package"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def historical_destination(asset, item, task, old_category, image_job=None) -> tuple[str, str | None] | None:
    """Classify only sources that can be identified from stored records."""
    asset_meta = asset.metadata_json or {}
    item_meta = (item.metadata_json or {}) if item else {}
    if asset.role == "image_design_input":
        return "uploads", "mp"
    if item_meta.get("source") == "image_design" or asset_meta.get("domain") == "image_design":
        if (image_job and (image_job.request_json or {}).get("mp_fixed_target")) or (
            item_meta.get("source_channel") == "mp" and item_meta.get("source_folder") == "generated"
        ):
            return "generated", "mp"
        return None
    if item_meta.get("source") == "content_production":
        return "generated", "pc"
    if asset.role == "output" and task is not None:
        values = (task.brief_json or {}).get("form_values") or {}
        if not (values.get("mp_content_code") or values.get("mp_service_entry")):
            return "generated", "pc"
    if item and asset.role == "library_image":
        channel = item_meta.get("source_channel") or asset_meta.get("source_channel")
        source = item_meta.get("source_folder") or asset_meta.get("source_folder")
        if old_category and old_category.visibility == "enterprise":
            if source in {"rough", "uploads"} and channel == "pc":
                return "review_shared", None
            return None
        if channel in {"pc", "mp"}:
            if source in {"rough", "uploads"}:
                return source, channel
            if (
                channel == "pc"
                and old_category
                and (
                    old_category.id == "mp-rough-private"
                    or old_category.image_design_role == "rough"
                    or old_category.name in {"毛坯房图库", "毛胚房图库"}
                )
            ):
                return "rough", channel
            if (
                channel == "pc"
                and old_category
                and (old_category.id == "mp-uploads-private" or old_category.name == "我的上传")
            ):
                return "uploads", channel
        if (
            old_category
            and old_category.id == "private-root"
            and old_category.visibility == "private"
            and channel != "mp"
        ):
            return "uploads", "pc"
        if old_category and old_category.visibility == "private":
            return "review", None
    return None


async def run(*, apply: bool, owner_uid: str | None) -> None:
    from yuxi.services.material_library_service import create_library_item_for_asset
    from yuxi.services.personal_materials import folder_categories, upload_category
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_business import User
    from yuxi.storage.postgres.models_content import (
        ContentCoverAsset,
        ContentMaterialCategory,
        ContentMaterialLibraryItem,
        ContentTask,
        ImageDesignJob,
        ImageDesignLibraryItem,
    )

    pg_manager.initialize()
    changed = 0
    review = []
    review_shared = []
    try:
        async with pg_manager.get_async_session_context() as db:
            users = (
                (await db.execute(select(User).where(User.is_deleted == 0, User.deleted_at.is_(None)))).scalars().all()
            )
            users = [user for user in users if owner_uid is None or str(user.uid) == owner_uid]
            for user in users:
                uid = str(user.uid)
                tenant_id = str(user.department_id) if user.department_id is not None else None
                folders = await folder_categories(db, user)
                items = (
                    (
                        await db.execute(
                            select(ContentMaterialLibraryItem).where(
                                ContentMaterialLibraryItem.owner_uid == uid,
                                ContentMaterialLibraryItem.material_type == "image",
                                ContentMaterialLibraryItem.deleted_at.is_(None),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                by_asset = {item.asset_id: item for item in items}
                assets = (
                    (
                        await db.execute(
                            select(ContentCoverAsset).where(
                                ContentCoverAsset.owner_uid == uid, ContentCoverAsset.deleted_at.is_(None)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                tasks = {
                    task.id: task
                    for task in (await db.execute(select(ContentTask).where(ContentTask.created_by == uid)))
                    .scalars()
                    .all()
                }
                image_jobs = {
                    job.id: job
                    for job in (await db.execute(select(ImageDesignJob).where(ImageDesignJob.owner_uid == uid)))
                    .scalars()
                    .all()
                }
                categories = {
                    (category.owner_uid, category.id): category
                    for category in (
                        await db.execute(
                            select(ContentMaterialCategory).where(
                                ContentMaterialCategory.deleted_at.is_(None),
                                ContentMaterialCategory.material_type == "image",
                            )
                        )
                    )
                    .scalars()
                    .all()
                }
                for asset in assets:
                    item = by_asset.get(asset.id)
                    asset_meta = asset.metadata_json or {}
                    item_meta = (item.metadata_json or {}) if item else {}
                    old = categories.get((item.category_owner_uid or item.owner_uid, item.category)) if item else None
                    image_job_id = item_meta.get("image_design_job_id") or asset_meta.get("image_design_job_id")
                    destination = historical_destination(
                        asset, item, tasks.get(asset.content_task_id), old, image_jobs.get(image_job_id)
                    )
                    if destination is None:
                        continue
                    folder, channel = destination
                    if folder == "review":
                        review.append((uid, asset.id, item.id, old.name if old else "unknown"))
                        continue
                    if folder == "review_shared":
                        review_shared.append((uid, asset.id, item.id, old.name if old else "unknown"))
                        continue
                    target = (
                        folders["generated"][0]
                        if folder == "generated"
                        else await upload_category(db, user, folder, channel)
                    )
                    metadata = {**item_meta, "source_channel": channel, "source_folder": folder}
                    asset_metadata = {**asset_meta, "source_channel": channel, "source_folder": folder}
                    if old and old.visibility == "enterprise":
                        metadata["ever_shared"] = True
                    if asset.role == "image_design_input":
                        metadata["retain_asset_on_delete"] = True
                    if folder == "generated":
                        metadata["source"] = "image_design" if channel == "mp" else "content_production"
                    if folder == "generated" and channel == "mp":
                        saved_target = {"scope": "private", "gallery_id": None}
                        metadata.update(resolved_save_target=saved_target, save_target_version=2)
                        asset_metadata["resolved_save_target"] = saved_target
                    moved = item is not None and (
                        item.category != target.id or (item.category_owner_uid or item.owner_uid) != target.owner_uid
                    )
                    tenant_changed = tenant_id is not None and (
                        asset.tenant_id != tenant_id or (item is not None and item.tenant_id != tenant_id)
                    )
                    if (
                        item is not None
                        and not moved
                        and not tenant_changed
                        and metadata == item_meta
                        and asset_metadata == asset_meta
                    ):
                        continue
                    action = "CREATE" if item is None else "MOVE" if moved else "TAG"
                    print(
                        f"{action} owner={uid} asset={asset.id} from={item.category if item else '-'} "
                        f"to={target.id} source={channel}/{folder}"
                    )
                    if apply:
                        if tenant_id is not None:
                            asset.tenant_id = tenant_id
                        if item is None:
                            item = await create_library_item_for_asset(
                                db,
                                asset=asset,
                                material_type="image",
                                name=Path(asset.original_file_name).stem,
                                category=target.id,
                                category_owner_uid=target.owner_uid,
                                metadata=metadata,
                            )
                        else:
                            item.category = target.id
                            item.category_owner_uid = target.owner_uid
                            item.metadata_json = metadata
                            if tenant_id is not None:
                                item.tenant_id = tenant_id
                        asset.metadata_json = asset_metadata
                        if folder == "generated" and channel == "mp":
                            await db.execute(
                                update(ImageDesignLibraryItem)
                                .where(ImageDesignLibraryItem.source_material_item_id == item.id)
                                .values(source_gallery_id=target.id)
                            )
                    changed += 1
            if not apply:
                await db.rollback()
    finally:
        await pg_manager.close()
    for uid, asset_id, item_id, name in review:
        print(f"REVIEW_PRIVATE owner={uid} asset={asset_id} item={item_id} gallery={name}")
    for uid, asset_id, item_id, name in review_shared:
        print(f"REVIEW_SHARED owner={uid} asset={asset_id} item={item_id} gallery={name}")
    print(
        f"SUMMARY apply={apply} changes={changed} ambiguous_private={len(review)} ambiguous_shared={len(review_shared)}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply the reviewed plan; default is dry-run")
    parser.add_argument("--owner-uid", help="Restrict to one account")
    args = parser.parse_args()
    load_dotenv(APP_ROOT.parent / ".env", override=False)
    if not os.getenv("POSTGRES_URL"):
        parser.error("POSTGRES_URL is required; run with the deployment database configuration")
    asyncio.run(run(apply=args.apply, owner_uid=args.owner_uid))
