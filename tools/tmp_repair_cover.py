import asyncio

import yuxi.content  # noqa: F401

from sqlalchemy import select

from yuxi.repositories.content_repository import ContentRepository
from yuxi.services.content_cover_service import set_current_cover
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User

TASK_ID = "ct_fa520ee6fd30473b95b81f5408bcf22e"
JOB_ID = "ccj_321c4d6abd4440c59403ecc9257f8f73"
ASSET_ID = "cca_bc5c8d8529954ed6a1edeeb977b09b10"


async def main() -> None:
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        user = await db.scalar(select(User).where(User.uid == "mp_40ad32e6369e4094a", User.is_deleted == 0))
        if user is None:
            user = await db.scalar(select(User).where(User.role == "superadmin", User.is_deleted == 0).limit(1))
        if user is None:
            raise RuntimeError("task owner or superadmin not found")
        result = await set_current_cover(db, user, JOB_ID, asset_id=ASSET_ID)
        task = await ContentRepository(db).get_task(TASK_ID, for_update=True)
        if task is not None:
            task.status = "generated"
            task.error_json = None
        await db.commit()
        print(result["artifact"]["cover_asset_id"], task.status if task else None)


asyncio.run(main())
