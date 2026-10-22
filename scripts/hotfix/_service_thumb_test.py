import asyncio
import sys

from sqlalchemy import select, text

from yuxi.services.material_library_service import get_material_thumbnail
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User

ITEM = sys.argv[1] if len(sys.argv) > 1 else "mli_1f0e4947f9fe42a19dcf4a846d0b3fe6"


async def main() -> None:
    async with pg_manager.get_async_session_context() as db:
        uid = (
            await db.execute(
                text("select owner_uid from content_material_library_items where id=:id"),
                {"id": ITEM},
            )
        ).scalar_one()
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one()
        data, name = await get_material_thumbnail(db, user, ITEM)
        print("service_ok", name, len(data))


asyncio.run(main())
