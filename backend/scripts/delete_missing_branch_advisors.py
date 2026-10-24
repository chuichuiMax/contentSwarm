"""删除缺少分部/部门且角色为家装顾问的员工（生产运维脚本）。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

APP_ROOT = Path(__file__).resolve().parents[1]
for import_path in (APP_ROOT, APP_ROOT / "package"):
    p = str(import_path)
    if p not in sys.path:
        sys.path.insert(0, p)


async def main() -> None:
    load_dotenv(APP_ROOT / ".env", override=False)
    from yuxi.services.employee_service import delete_employee
    from yuxi.storage.postgres.manager import pg_manager

    pg_manager.initialize()
    query = text(
        """
        SELECT id, employee_code, name, login_account
        FROM content_employees
        WHERE role = '家装顾问'
          AND (
            btrim(coalesce(current_branch, '')) = ''
            OR btrim(coalesce(current_department, '')) = ''
          )
        ORDER BY name
        """
    )
    async with pg_manager.get_async_session_context() as db:
        rows = (await db.execute(query)).all()

    deleted = 0
    for employee_id, code, name, phone in rows:
        async with pg_manager.get_async_session_context() as db:
            await delete_employee(db, str(employee_id))
            await db.commit()
        deleted += 1
        print(f"DELETED {code} {name} {phone}")

    await pg_manager.close()
    print(f"done deleted={deleted}")


if __name__ == "__main__":
    asyncio.run(main())
