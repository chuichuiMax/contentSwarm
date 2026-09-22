"""从测试名单导入员工：写入当前分部/当前部门，登录端口 APP，初始密码 123456。

用法：
  docker exec api-dev python scripts/import_employee_roster.py /tmp/test-list-918.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import HTTPException
from sqlalchemy import select

APP_ROOT = Path(__file__).resolve().parents[1]
for import_path in (APP_ROOT, APP_ROOT / "package"):
    text = str(import_path)
    if text not in sys.path:
        sys.path.insert(0, text)

GENDER_MAP = {"男": "male", "女": "female"}
HEADER = ("序号", "当前分部", "当前部门", "员工工号", "员工姓名", "手机号码", "角色", "年龄", "员工性别")


def load_project_env() -> None:
    load_dotenv(APP_ROOT / ".env", override=False)
    load_dotenv(APP_ROOT.parent / ".env", override=False)


def parse_rows(path: Path) -> list[dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not raw:
        raise SystemExit("名单文件为空")
    header = [str(item).strip() for item in raw[0]]
    if tuple(header) != HEADER:
        raise SystemExit(f"名单表头不匹配: {header}")
    rows: list[dict[str, str]] = []
    for item in raw[1:]:
        values = [str(cell).strip() for cell in item]
        if len(values) < len(HEADER) or not values[3] or not values[4] or not values[5]:
            continue
        rows.append(dict(zip(HEADER, values, strict=False)))
    return rows


async def ensure_role(db, name: str, created_by: str) -> None:
    from yuxi.repositories.role_repository import RoleRepository
    from yuxi.services.role_service import ensure_default_roles, next_role_code

    await ensure_default_roles(db)
    repo = RoleRepository(db)
    if await repo.get_by_name(name):
        return
    await repo.create(
        {
            "id": str(uuid.uuid4()),
            "role_code": next_role_code(await repo.list_codes()),
            "name": name,
            "role_type": "新增",
            "enabled": True,
            "permissions": [],
            "created_by": created_by,
        }
    )


async def import_roster(path: Path) -> None:
    from yuxi.services.employee_service import EmployeeCreate, create_employee
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_business import User

    load_project_env()
    rows = parse_rows(path)
    pg_manager.initialize()
    await pg_manager.create_tables()
    await pg_manager.ensure_business_schema()
    await pg_manager.ensure_content_schema()

    created = skipped = failed = 0
    async with pg_manager.get_async_session_context() as db:
        creator = (
            await db.execute(select(User).where(User.role == "superadmin", User.is_deleted == 0).limit(1))
        ).scalar_one_or_none()
        if creator is None:
            creator = (await db.execute(select(User).where(User.is_deleted == 0).limit(1))).scalar_one_or_none()
        if creator is None:
            raise SystemExit("系统中没有可用账号，无法导入员工")

        for row in rows:
            gender = GENDER_MAP.get(row["员工性别"])
            if gender is None:
                print(f"SKIP {row['员工工号']} 未知性别 {row['员工性别']}")
                skipped += 1
                continue
            age = int(row["年龄"]) if row["年龄"].isdigit() else None
            try:
                await ensure_role(db, row["角色"], str(creator.uid))
                await create_employee(
                    db,
                    creator,
                    EmployeeCreate(
                        employee_code=row["员工工号"],
                        name=row["员工姓名"],
                        login_account=row["手机号码"],
                        current_branch=row["当前分部"],
                        current_department=row["当前部门"],
                        gender=gender,
                        age=age,
                        login_port=["app"],
                        role=row["角色"],
                    ),
                )
                await db.commit()
                created += 1
            except HTTPException as exc:
                await db.rollback()
                code = exc.detail.get("error", {}).get("code") if isinstance(exc.detail, dict) else None
                if code in {"EMPLOYEE_CODE_EXISTS", "EMPLOYEE_LOGIN_EXISTS", "EMPLOYEE_DUPLICATE"}:
                    print(f"SKIP {row['员工工号']} {row['员工姓名']} 已存在")
                    skipped += 1
                    continue
                failed += 1
                print(f"FAIL {row['员工工号']} {row['员工姓名']}: {exc.detail}")
            except Exception as exc:
                await db.rollback()
                failed += 1
                print(f"FAIL {row['员工工号']} {row['员工姓名']}: {exc}")
    await pg_manager.close()
    print(f"导入完成 created={created} skipped={skipped} failed={failed} total={len(rows)}")
    if failed:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="导入测试名单员工")
    parser.add_argument("path", type=Path, help="名单 JSON 路径")
    args = parser.parse_args()
    if not args.path.exists():
        raise SystemExit(f"找不到名单文件: {args.path}")
    asyncio.run(import_roster(args.path))


if __name__ == "__main__":
    main()
