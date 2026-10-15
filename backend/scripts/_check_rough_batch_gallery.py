"""One-off: count items in 毛坯批量生图图库 and recent image-design jobs with save target."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
for path in (APP_ROOT, APP_ROOT / "package"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from sqlalchemy import desc, select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import (
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
    ImageDesignJob,
)


async def main() -> None:
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        categories = (
            await db.execute(
                select(ContentMaterialCategory).where(ContentMaterialCategory.deleted_at.is_(None))
            )
        ).scalars().all()
        batch = [c for c in categories if c.name and "批量" in c.name]
        print("batch_galleries:", [(c.id, c.name, c.parent_id, c.visibility) for c in batch])
        for cat in batch:
            items = (
                await db.execute(
                    select(ContentMaterialLibraryItem).where(
                        ContentMaterialLibraryItem.category == cat.id,
                        ContentMaterialLibraryItem.deleted_at.is_(None),
                    )
                )
            ).scalars().all()
            print(f"  items in {cat.name}: {len(items)}")

        jobs = (
            await db.execute(select(ImageDesignJob).order_by(desc(ImageDesignJob.created_at)).limit(5))
        ).scalars().all()
        print("recent_jobs:")
        for job in jobs:
            req = job.request_json or {}
            print(
                job.id,
                job.status,
                req.get("requested_save_target"),
                (job.result_json or {}).get("library_item_ids"),
            )


if __name__ == "__main__":
    asyncio.run(main())
