import asyncio
import sys
from sqlalchemy import select
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import ContentArtifact

async def main(tid: str) -> None:
    pg_manager.initialize()
    async with pg_manager.get_async_session_context() as db:
        a = await db.scalar(select(ContentArtifact).where(ContentArtifact.task_id == tid))
        if not a:
            print("not_found")
            return
        print("TITLE:", a.title)
        print("BODY:", a.body)

asyncio.run(main(sys.argv[1]))