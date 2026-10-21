"""Bind succeeded cover jobs whose artifacts still lack cover_asset_id."""
from __future__ import annotations

import asyncio

import yuxi.content  # noqa: F401

from sqlalchemy import select, text

from yuxi.repositories.content_repository import ContentRepository
from yuxi.services.content_cover_service import set_current_cover
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User

ORPHAN_SQL = """
SELECT j.id AS job_id,
       j.owner_uid,
       j.content_task_id,
       j.result_json->'asset_ids'->>0 AS asset_id
FROM content_cover_jobs j
JOIN content_tasks t ON t.id = j.content_task_id AND t.deleted_at IS NULL
JOIN content_artifacts a ON a.task_id = t.id
WHERE j.status = 'succeeded'
  AND (a.cover_asset_id IS NULL OR a.cover_asset_id = '')
  AND j.result_json->'asset_ids'->>0 IS NOT NULL
ORDER BY j.updated_at DESC
"""


async def main() -> None:
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        rows = (await db.execute(text(ORPHAN_SQL))).mappings().all()
    print(f"orphans={len(rows)}")
    ok = 0
    failed: list[str] = []
    for row in rows:
        job_id = row["job_id"]
        asset_id = row["asset_id"]
        task_id = row["content_task_id"]
        owner_uid = row["owner_uid"]
        try:
            async with pg_manager.AsyncSession() as db:
                user = await db.scalar(select(User).where(User.uid == owner_uid, User.is_deleted == 0))
                if user is None:
                    raise RuntimeError(f"owner missing: {owner_uid}")
                await set_current_cover(db, user, job_id, asset_id=asset_id)
                task = await ContentRepository(db).get_task(task_id, for_update=True)
                if task is not None and task.status in {"failed", "running"}:
                    task.status = "generated"
                    task.error_json = None
                await db.commit()
            ok += 1
            print(f"ok {task_id} {job_id}")
        except Exception as exc:
            failed.append(f"{task_id}: {exc}")
            print(f"fail {task_id} {exc}")
    print(f"done ok={ok} failed={len(failed)}")
    for line in failed:
        print(line)


if __name__ == "__main__":
    asyncio.run(main())
