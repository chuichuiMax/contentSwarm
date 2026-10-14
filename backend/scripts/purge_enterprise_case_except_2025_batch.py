"""Remove enterprise case-gallery folders/images except OSS 2025 batch imports.

Keeps items whose cover asset metadata ``source_object`` starts with
``image/material-library/2025/``. Deletes other images under the enterprise
案例图库 tree, then removes pre-batch secondary galleries that are empty.

Dry-run by default; pass ``--apply`` to execute. Prefer pausing conflicting
writers; storage deletes are not rolled back on commit failure.
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import HTTPException
from sqlalchemy import select, text

APP_ROOT = Path(__file__).resolve().parents[1]
for path in (APP_ROOT, APP_ROOT / "package"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from yuxi.services.material_library_service import (  # noqa: E402
    MaterialCategoryDelete,
    delete_material_category,
    delete_material_item,
)
from yuxi.storage.postgres.models_business import User  # noqa: E402

CASE_ROOT_ID = "mlc_a228707511984df3b34c9b7beafd66af"
KEEP_SOURCE_PREFIX = "image/material-library/2025/"
BATCH_CATEGORY_CUTOFF = datetime(2026, 10, 13, 6, 23, 0)
ADMIN_UID = "tiechui"

ITEMS_SQL = text(
    """
    SELECT i.id AS item_id, i.category AS category_id, c.name AS category_name,
           a.metadata_json->>'source_object' AS source_object
    FROM content_material_library_items i
    JOIN content_material_categories c
      ON c.id = i.category AND c.material_type = i.material_type
    JOIN content_cover_assets a ON a.id = i.asset_id
    WHERE i.deleted_at IS NULL
      AND a.deleted_at IS NULL
      AND i.material_type = 'image'
      AND c.parent_id = :case_root
      AND COALESCE(a.metadata_json->>'source_object', '') NOT LIKE :keep_prefix
    ORDER BY i.id
    """
)

EMPTY_OLD_GALLERIES_SQL = text(
    """
    SELECT c.id, c.name, c.created_at
    FROM content_material_categories c
    WHERE c.parent_id = :case_root
      AND c.deleted_at IS NULL
      AND c.material_type = 'image'
      AND c.created_at < :cutoff
      AND NOT EXISTS (
        SELECT 1 FROM content_material_library_items i
        WHERE i.deleted_at IS NULL
          AND i.material_type = 'image'
          AND i.category = c.id
      )
    ORDER BY c.created_at
    """
)


async def run(*, apply: bool) -> None:
    from yuxi.storage.postgres.manager import pg_manager

    pg_manager.initialize()
    try:
        async with pg_manager.AsyncSession() as db:
            admin = (
                await db.execute(select(User).where(User.uid == ADMIN_UID, User.is_deleted == 0))
            ).scalar_one_or_none()
            if admin is None or admin.role not in {"superadmin", "admin"}:
                raise RuntimeError(f"管理员账号 {ADMIN_UID!r} 不可用")

            rows = (
                await db.execute(
                    ITEMS_SQL,
                    {"case_root": CASE_ROOT_ID, "keep_prefix": f"{KEEP_SOURCE_PREFIX}%"},
                )
            ).mappings().all()

            plan = {
                "apply": apply,
                "delete_items": len(rows),
                "sample_items": [dict(row) for row in rows[:5]],
            }
            print(json.dumps(plan, ensure_ascii=False, indent=2, default=str))

            if not apply:
                galleries = (
                    await db.execute(
                        EMPTY_OLD_GALLERIES_SQL,
                        {"case_root": CASE_ROOT_ID, "cutoff": BATCH_CATEGORY_CUTOFF},
                    )
                ).mappings().all()
                print(
                    json.dumps(
                        {
                            "note": "空旧图库数量需在删图后统计；当前仍含待删图片的旧图库不会出现在列表",
                            "delete_empty_old_galleries_if_ran_after_items": len(galleries),
                        },
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    )
                )
                await db.rollback()
                return

            deleted_items: list[str] = []
            blocked_items: list[dict[str, str]] = []
            for row in rows:
                item_id = row["item_id"]
                try:
                    await delete_material_item(db, admin, item_id, actor_user=admin)
                    deleted_items.append(item_id)
                except HTTPException as exc:
                    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                    blocked_items.append({"item_id": item_id, "status": exc.status_code, "detail": detail})

            galleries = (
                await db.execute(
                    EMPTY_OLD_GALLERIES_SQL,
                    {"case_root": CASE_ROOT_ID, "cutoff": BATCH_CATEGORY_CUTOFF},
                )
            ).mappings().all()

            deleted_galleries: list[str] = []
            blocked_galleries: list[dict[str, str]] = []
            payload = MaterialCategoryDelete()
            for row in galleries:
                category_id = row["id"]
                try:
                    await delete_material_category(
                        db,
                        admin,
                        "image",
                        category_id,
                        payload,
                        actor_user=admin,
                    )
                    deleted_galleries.append(category_id)
                except HTTPException as exc:
                    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                    blocked_galleries.append({"category_id": category_id, "status": exc.status_code, "detail": detail})

            summary = {
                "deleted_items": len(deleted_items),
                "blocked_items": blocked_items,
                "deleted_galleries": len(deleted_galleries),
                "blocked_galleries": blocked_galleries,
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    finally:
        await pg_manager.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Execute deletions")
    args = parser.parse_args()
    load_dotenv(APP_ROOT.parent / ".env", override=False)
    if not os.getenv("POSTGRES_URL"):
        parser.error("POSTGRES_URL is required")
    asyncio.run(run(apply=args.apply))
