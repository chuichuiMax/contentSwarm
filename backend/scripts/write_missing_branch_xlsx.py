"""在容器内查询并写出缺少分部/部门员工 xlsx。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
for import_path in (APP_ROOT, APP_ROOT / "package"):
    text = str(import_path)
    if text not in sys.path:
        sys.path.insert(0, text)

from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import text

OUTPUT = Path("/tmp/missing_branch_dept_employees.xlsx")


async def main() -> None:
    load_dotenv(APP_ROOT / ".env", override=False)
    from yuxi.storage.postgres.manager import pg_manager

    pg_manager.initialize()
    query = text(
        """
        SELECT employee_code, name, login_account, role,
               coalesce(current_branch, ''), coalesce(current_department, '')
        FROM content_employees
        WHERE btrim(coalesce(current_branch, '')) = ''
           OR btrim(coalesce(current_department, '')) = ''
        ORDER BY name
        """
    )
    async with pg_manager.get_async_session_context() as db:
        result = await db.execute(query)
        rows = [list(row) for row in result.all()]
    await pg_manager.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "缺少分部或部门"
    headers = ["员工编码", "姓名", "登录账号", "角色", "当前分部", "当前部门"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(row)
    for col, width in zip("ABCDEF", (16, 14, 16, 14, 18, 22), strict=True):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    wb.save(OUTPUT)
    print(f"rows={len(rows)} path={OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
