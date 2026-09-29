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


def historical_destination(asset, item, task, old_category) -> tuple[str, str | None] | None:
    """Classify only sources that can be identified from stored records."""
    asset_meta = asset.metadata_json or {}
    item_meta = (item.metadata_json or {}) if item else {}
    if asset.role == "image_design_input":
        return "uploads", "mp"
    if item and item_meta.get("source") == "image_design":
        return "generated", None
    if asset.role == "output" and task is not None:
        values = (task.brief_json or {}).get("form_values") or {}
        if not (values.get("mp_content_code") or values.get("mp_service_entry")):
            return "generated", None
    if item and asset.role == "library_image":
        channel = item_meta.get("source_channel") or asset_meta.get("source_channel")
        source = item_meta.get("source_folder") or asset_meta.get("source_folder")
        if channel in {"pc", "mp"}:
            if source in {"rough", "uploads"}:
                return source, channel
            if old_category and (
                old_category.image_design_role == "rough" or old_category.name in {"毛坯房图库", "毛胚房图库"}
            ):
                return "rough", channel
        if old_category and old_category.id == "private-root" and old_category.visibility == "private":
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
        ImageDesignLibraryItem,
    )

    pg_manager.initialize()
    changed = 0
    review = []
    try:
        async with pg_manager.get_async_session_context() as db:
            users = (
                (await db.execute(select(User).where(User.is_deleted == 0, User.deleted_at.is_(None)))).scalars().all()
            )
            users = [user for user in users if owner_uid is None or str(user.uid) == owner_uid]
            for user in users:
                uid = str(user.uid)
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
                    destination = historical_destination(asset, item, tasks.get(asset.content_task_id), old)
                    if destination is None:
                        continue
                    folder, channel = destination
                    if folder == "review":
                        review.append((uid, asset.id, item.id, old.name))
                        continue
                    target = (
                        folders["generated"][0]
                        if folder == "generated"
                        else await upload_category(db, user, folder, channel)
                    )
                    if item is None:
                        print(f"CREATE owner={uid} asset={asset.id} folder={target.name} scope={target.visibility}")
                        if apply:
                            item = await create_library_item_for_asset(
                                db,
                                asset=asset,
                                material_type="image",
                                name=Path(asset.original_file_name).stem,
                                category=target.id,
                                category_owner_uid=target.owner_uid,
                                metadata={
                                    "source_channel": "mp" if asset.role == "image_design_input" else "pc",
                                    "source_folder": "uploads" if asset.role == "image_design_input" else "generated",
                                },
                            )
                            if asset.role == "image_design_input":
                                item.metadata_json = {**(item.metadata_json or {}), "retain_asset_on_delete": True}
                    elif (
                        item.category != target.id
                        or (item.category_owner_uid or item.owner_uid) != target.owner_uid
                    ):
                        print(
                            f"MOVE owner={uid} item={item.id} from={item.category} "
                            f"to={target.id} scope={target.visibility}"
                        )
                        if apply:
                            item.category = target.id
                            item.category_owner_uid = target.owner_uid
                            metadata = {
                                **item_meta,
                                "ever_shared": target.visibility == "enterprise" or item_meta.get("ever_shared", False),
                            }
                            if old and old.id == "private-root" and asset.role == "library_image":
                                metadata.update(source_channel="pc", source_folder="uploads")
                                asset.metadata_json = {**asset_meta, "source_channel": "pc", "source_folder": "uploads"}
                            if metadata.get("source") == "image_design":
                                metadata["resolved_save_target"] = {"scope": "enterprise", "gallery_id": target.id}
                                asset.metadata_json = {
                                    **asset_meta,
                                    "resolved_save_target": metadata["resolved_save_target"],
                                }
                            item.metadata_json = metadata
                            await db.execute(
                                update(ImageDesignLibraryItem)
                                .where(ImageDesignLibraryItem.source_material_item_id == item.id)
                                .values(source_gallery_id=target.id)
                            )
                    else:
                        continue
                    changed += 1
            if not apply:
                await db.rollback()
    finally:
        await pg_manager.close()
    for uid, asset_id, item_id, name in review:
        print(f"REVIEW_PRIVATE owner={uid} asset={asset_id} item={item_id} gallery={name}")
    print(f"SUMMARY apply={apply} changes={changed} ambiguous_private={len(review)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply the reviewed plan; default is dry-run")
    parser.add_argument("--owner-uid", help="Restrict to one account")
    args = parser.parse_args()
    load_dotenv(APP_ROOT.parent / ".env", override=False)
    if not os.getenv("POSTGRES_URL"):
        parser.error("POSTGRES_URL is required; run with the deployment database configuration")
    asyncio.run(run(apply=args.apply, owner_uid=args.owner_uid))
