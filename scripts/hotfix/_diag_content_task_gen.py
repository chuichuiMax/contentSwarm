import asyncio
import json
import sys

from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import AgentRun
from yuxi.storage.postgres.models_content import ContentArtifact


async def main(task_id: str) -> None:
    pg_manager.initialize()
    async with pg_manager.get_async_session_context() as db:
        art = await db.scalar(select(ContentArtifact).where(ContentArtifact.task_id == task_id))
        run = await db.scalar(
            select(AgentRun)
            .where(AgentRun.thread_id == task_id)
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
        if art:
            log = (art.runtime_config_snapshot or {}).get("generation_log") or {}
            print("generation_log", json.dumps(log, ensure_ascii=False, indent=2)[:8000])
        if run:
            p = run.input_payload or {}
            print("model_spec", p.get("model_spec"))
            print("generation_prompt", (p.get("generation_prompt") or "")[:1500])
            print("user_request", (p.get("user_request") or "")[:2500])
            print("viral_source", json.dumps(p.get("viral_source") or {}, ensure_ascii=False)[:2000])


asyncio.run(main(sys.argv[1]))