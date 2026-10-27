"""List non-completed image design analyses."""
from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import ImageDesignAnalysis


async def main() -> None:
    async with pg_manager.get_async_session_context() as db:
        for status in ("running", "failed", "pending"):
            count = await db.scalar(
                select(func.count()).select_from(ImageDesignAnalysis).where(ImageDesignAnalysis.status == status)
            )
            print(status, int(count or 0))
        rows = (
            await db.execute(
                select(ImageDesignAnalysis)
                .where(ImageDesignAnalysis.status != "completed")
                .order_by(ImageDesignAnalysis.updated_at.desc())
                .limit(10)
            )
        ).scalars()
        for row in rows:
            print(
                row.id,
                row.status,
                row.updated_at,
                row.error_code,
                row.material_item_id,
                row.owner_uid,
            )


if __name__ == "__main__":
    asyncio.run(main())
