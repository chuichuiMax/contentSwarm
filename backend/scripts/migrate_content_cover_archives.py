"""Move content-production covers from private AI galleries to works; never delete files."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select

APP_ROOT = Path(__file__).resolve().parents[1]
for path in (APP_ROOT, APP_ROOT / "package"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


async def migrate_archives(
    db, owner_uids: list[str], *, apply: bool = False, manifest_path: Path | None = None,
) -> dict:
    from yuxi.repositories.material_library_repository import MaterialLibraryRepository
    from yuxi.services.material_library_categories import PERSONAL_IMAGE_FOLDERS, personal_folder_key
    from yuxi.services.personal_gallery_settings import load_personal_gallery_settings, require_personal_gallery
    from yuxi.services.personal_materials import _private_category_name
    from yuxi.storage.postgres.models_business import User
    from yuxi.storage.postgres.models_content import (
        ContentCoverAsset, ContentCoverJob, ContentMaterialCategory, ContentMaterialLibraryItem, ContentTask,
    )

    users = list(await db.scalars(select(User).where(
        User.uid.in_(owner_uids), User.is_deleted == 0, User.deleted_at.is_(None),
    )))
    if {str(user.uid) for user in users} != set(owner_uids):
        raise ValueError("指定账号不存在或已注销")
    repo = MaterialLibraryRepository(db, include_shared=True)
    item, asset, category, task = ContentMaterialLibraryItem, ContentCoverAsset, ContentMaterialCategory, ContentTask
    rows = (await db.execute(select(item, asset, category, task).join(
        asset, asset.id == item.asset_id,
    ).join(category, repo.category_join()).join(task, task.id == asset.content_task_id).where(
        item.owner_uid.in_(owner_uids), item.material_type == "image", item.deleted_at.is_(None),
        asset.deleted_at.is_(None), asset.role == "output", category.visibility == "private",
        category.deleted_at.is_(None),
    ).order_by(item.owner_uid, item.id).with_for_update(of=item))).all()
    jobs = await db.scalars(select(ContentCoverJob).where(
        ContentCoverJob.owner_uid.in_(owner_uids), ContentCoverJob.status == "succeeded",
        ContentCoverJob.content_task_id.is_not(None),
    ))
    completed = {(job.owner_uid, job.content_task_id, asset_id)
                 for job in jobs for asset_id in (job.result_json or {}).get("asset_ids", [])}
    user_by_uid = {str(user.uid): user for user in users}
    targets = {}
    changes, skipped = [], []
    planned = []
    for entry, source, gallery, content_task in rows:
        if personal_folder_key(gallery) != "generated" and gallery.id not in {"private-root", "uncategorized"}:
            continue
        if (source.metadata_json or {}).get("domain") == "image_design" or (
            entry.metadata_json or {}
        ).get("source") == "image_design":
            continue
        if source.owner_uid != entry.owner_uid or (
            entry.owner_uid, source.content_task_id, source.id
        ) not in completed:
            skipped.append({"item_id": entry.id, "reason": "缺少同账号成功封面任务的关联证据"})
            continue
        owner_uid = entry.owner_uid
        if owner_uid not in targets:
            require_personal_gallery(await load_personal_gallery_settings(db), "works")
            categories = await repo.list_categories(owner_uid, "image")
            works = [c for c in categories if c.owner_uid == owner_uid and personal_folder_key(c) == "works"]
            if len(works) > 1:
                raise ValueError(f"账号 {owner_uid} 的我的作品存在多个候选")
            target_id = works[0].id if works else PERSONAL_IMAGE_FOLDERS["works"][0]
            targets[owner_uid] = (target_id, works, categories)
        values = (content_task.brief_json or {}).get("form_values") or {}
        channel = "mp" if values.get("mp_content_code") or values.get("mp_service_entry") else "pc"
        changes.append({
            "owner_uid": owner_uid, "item_id": entry.id, "asset_id": source.id,
            "category": entry.category, "category_owner_uid": entry.category_owner_uid,
            "item_metadata": entry.metadata_json or {}, "asset_metadata": source.metadata_json or {},
            "target_category": targets[owner_uid][0], "source_channel": channel,
        })
        planned.append((entry, source, channel))
    if apply and changes:
        if manifest_path is None:
            raise ValueError("应用迁移前必须通过 --manifest-path 保存原始归档记录")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation keeps an earlier rollback manifest intact.
        with manifest_path.open("x", encoding="utf-8") as output:
            json.dump({"changes": changes}, output, ensure_ascii=False, indent=2)
        for owner_uid, (target_id, works, categories) in targets.items():
            if not works:
                owner = user_by_uid[owner_uid]
                await repo.ensure_default_categories([dict(
                    owner_uid=owner_uid, id=target_id, material_type="image", visibility="private",
                    tenant_id=str(owner.department_id) if owner.department_id is not None else None,
                    parent_id=None, industry_slug="decoration",
                    name=_private_category_name("works", categories, owner_uid),
                    description="", sort_order=30, is_system=True,
                )])
        for entry, source, channel in planned:
            entry.category = targets[entry.owner_uid][0]
            entry.category_owner_uid = entry.owner_uid
            entry.metadata_json = {**(entry.metadata_json or {}), "source": "content_production",
                                   "source_channel": channel, "source_folder": "works", "retain_asset_on_delete": True}
            source.metadata_json = {**(source.metadata_json or {}), "source": "content_production",
                                    "source_channel": channel, "source_folder": "works"}
        await db.flush()
    return {"apply": apply, "changes": changes, "skipped": skipped, "count": len(changes)}


async def run(args) -> None:
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_business import User

    pg_manager.initialize()
    try:
        async with pg_manager.get_async_session_context() as db:
            owners = args.owner_uid or list(await db.scalars(select(User.uid).where(
                User.is_deleted == 0, User.deleted_at.is_(None),
            )))
            result = await migrate_archives(db, owners, apply=args.apply, manifest_path=args.manifest_path)
            if not args.apply:
                await db.rollback()
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await pg_manager.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--owner-uid", action="append", help="Account UID; repeat to select multiple accounts")
    scope.add_argument("--all-owners", action="store_true", help="Select all active accounts")
    parser.add_argument("--apply", action="store_true", help="Apply migration; default is dry-run")
    parser.add_argument("--manifest-path", type=Path, help="New JSON file containing original records for rollback")
    args = parser.parse_args()
    if args.apply and args.manifest_path is None:
        parser.error("--apply requires --manifest-path")
    load_dotenv(APP_ROOT.parent / ".env", override=False)
    if not os.getenv("POSTGRES_URL"):
        parser.error("POSTGRES_URL is required")
    asyncio.run(run(args))
