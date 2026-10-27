"""Mark stuck image_design_analyses rows so /polish can retry."""
from __future__ import annotations

import asyncio

from sqlalchemy import update

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import ImageDesignAnalysis
from yuxi.utils.datetime_utils import utc_now_naive


async def main() -> None:
    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(
            update(ImageDesignAnalysis)
            .where(ImageDesignAnalysis.status == "running")
            .values(
                status="failed",
                error_code="ANALYSIS_STUCK_RESET",
                error_message="cleared stuck running",
                updated_at=utc_now_naive(),
            )
        )
        await db.commit()
        print("reset", result.rowcount)


if __name__ == "__main__":
    asyncio.run(main())
