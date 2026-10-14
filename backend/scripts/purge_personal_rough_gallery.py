"""Soft-delete all images in personal 毛坯房图库 folders (not enterprise shared).

Matches the same scope as employee 「毛坯图上传数（我的素材）」: private visibility,
``mp-rough-private`` / ``image_design_role=rough`` / 毛坯房图库 name on private roots.

Dry-run by default; pass ``--apply`` to execute. Run inside worker-dev on production.
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import HTTPException
from sqlalchemy import select, text

APP_ROOT = Path(__file__).resolve().parents[1]
for path in (APP_ROOT, APP_ROOT / "package"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from yuxi.repositories.material_library_repository import ROUGH_CATEGORY_NAMES  # noqa: E402
from yuxi.services.material_library_service import delete_material_item  # noqa: E402
from yuxi.storage.postgres.manager import pg_manager  # noqa: E402
from yuxi.storage.postgres.models_business import User  # noqa: E402

ADMIN_UID = "tiechui"

ITEMS_SQL = text(
    """
    SELECT i.id AS item_id, i.owner_uid, c.id AS category_id, c.name AS category_name
    FROM content_material_library_items i
    JOIN content_material_categories c
      ON c.id = i.category
     AND c.material_type = i.material_type
     AND c.owner_uid = COALESCE(i.category_owner_uid, i.owner_uid)
     AND c.deleted_at IS NULL
    JOIN content_cover_assets a ON a.id = i.asset_id
    WHERE i.deleted_at IS NULL
      AND a.deleted_at IS NULL
      AND i.material_type = 'image'
      AND c.visibility = 'private'
      AND c.parent_id IS NULL
      AND (
        c.id = 'mp-rough-private'
        OR c.image_design_role = 'rough'
        OR c.name = ANY(:rough_names)
      )
    ORDER BY i.owner_uid, i.id
    """
)


async def run(*, apply: bool) -> None:
    pg_manager.initialize()
    try:
        async with pg_manager.AsyncSession() as db:
            admin = (
                await db.execute(select(User).where(User.uid == ADMIN_UID, User.is_deleted == 0))
            ).scalar_one_or_none()
            if admin is None or admin.role not in {"superadmin", "admin"}:
                raise RuntimeError(f"管理员账号 {ADMIN_UID!r} 不可用")

            rows = (
                await db.execute(ITEMS_SQL, {"rough_names": list(ROUGH_CATEGORY_NAMES)})
            ).mappings().all()

            owners = sorted({row["owner_uid"] for row in rows})
            plan = {
                "apply": apply,
                "delete_items": len(rows),
                "owners": len(owners),
                "sample_items": [dict(row) for row in rows[:8]],
            }
            print(json.dumps(plan, ensure_ascii=False, indent=2, default=str))

            if not apply:
                await db.rollback()
                return

            deleted: list[str] = []
            blocked: list[dict] = []
            for row in rows:
                item_id = row["item_id"]
                owner = User(uid=row["owner_uid"], role="user")
                try:
                    await delete_material_item(
                        db,
                        owner,
                        item_id,
                        actor_user=admin,
                        target_private=True,
                    )
                    deleted.append(item_id)
                except HTTPException as exc:
                    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                    blocked.append({"item_id": item_id, "status": exc.status_code, "detail": detail})

            print(
                json.dumps(
                    {"deleted_items": len(deleted), "blocked_items": blocked},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
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
