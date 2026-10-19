"""从集团总部通讯录（Excel 导出的 JSON）导入员工到当前环境。

表头：姓名、编号、分部、部门、岗位、移动电话
- 已存在相同登录账号（手机号）则跳过
- 角色 = 岗位（与 Excel 一致）
- 岗位名称包含「设计师」→ 登录端口 PC+APP，其余仅 APP
- 初始密码由 ensure_platform_user 写入（默认 123456）

用法：
  docker exec api-dev python scripts/import_group_hq_roster.py /tmp/group-hq-roster.json
  docker exec api-dev python scripts/import_group_hq_roster.py /tmp/group-hq-roster.json --dry-run
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

HEADER = ("姓名", "编号", "分部", "部门", "岗位", "移动电话")
GENDER_DEFAULT = "male"
def load_project_env() -> None:
    load_dotenv(APP_ROOT / ".env", override=False)
    load_dotenv(APP_ROOT.parent / ".env", override=False)


def _normalize_matrix(raw: object) -> list[list[str]]:
    if not isinstance(raw, list) or not raw:
        raise SystemExit("名单文件为空")
    rows: list[list[str]] = []
    for item in raw:
        if isinstance(item, dict) and "value" in item:
            rows.append([str(cell).strip() for cell in item["value"]])
        elif isinstance(item, list):
            rows.append([str(cell).strip() for cell in item])
        else:
            raise SystemExit(f"无法识别的行格式: {type(item)!r}")
    return rows


def parse_rows(path: Path) -> list[dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    matrix = _normalize_matrix(raw)
    header = matrix[0]
    if tuple(header) != HEADER:
        raise SystemExit(f"表头不匹配，期望 {HEADER}，实际 {tuple(header)}")
    rows: list[dict[str, str]] = []
    for values in matrix[1:]:
        if len(values) < len(HEADER):
            continue
        row = dict(zip(HEADER, values, strict=False))
        if not row["编号"] or not row["姓名"] or not row["移动电话"]:
            continue
        rows.append(row)
    return rows


def map_role(position: str) -> str:
    return (position or "").strip() or "运营"


def map_login_ports(position: str) -> list[str]:
    if "设计师" in (position or ""):
        return ["pc", "app"]
    return ["app"]


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


async def align_existing_from_roster(path: Path, *, dry_run: bool) -> None:
    from yuxi.repositories.employee_repository import EmployeeRepository
    from yuxi.services.employee_service import EmployeeUpdate, update_employee
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_business import User

    load_project_env()
    rows = parse_rows(path)
    pg_manager.initialize()
    await pg_manager.create_tables()
    await pg_manager.ensure_business_schema()
    await pg_manager.ensure_content_schema()

    updated = skipped = failed = 0
    async with pg_manager.get_async_session_context() as db:
        repo = EmployeeRepository(db)
        actor = (
            await db.execute(select(User).where(User.role == "superadmin", User.is_deleted == 0).limit(1))
        ).scalar_one_or_none()
        if actor is None:
            raise SystemExit("系统中没有可用账号")

        for row in rows:
            phone = row["移动电话"].strip()
            role = map_role(row["岗位"])
            ports = map_login_ports(row["岗位"])
            employee = await repo.get_by_login_account(phone)
            if employee is None:
                skipped += 1
                continue
            if employee.role == role and employee.login_port == ports:
                skipped += 1
                continue
            if dry_run:
                print(f"DRY {row['编号']} {row['姓名']} role {employee.role!r} -> {role!r} ports {employee.login_port} -> {ports}")
                updated += 1
                continue
            try:
                await ensure_role(db, role, str(actor.uid))
                await update_employee(
                    db,
                    employee.id,
                    EmployeeUpdate(role=role, login_port=ports),
                )
                await db.commit()
                print(f"OK {row['编号']} {row['姓名']} role={role} ports={ports}")
                updated += 1
            except Exception as exc:
                await db.rollback()
                failed += 1
                print(f"FAIL {row['编号']} {row['姓名']}: {exc}")
    await pg_manager.close()
    label = "预检对齐" if dry_run else "对齐"
    print(f"{label}完成 updated={updated} skipped={skipped} failed={failed} total={len(rows)}")
    if failed:
        raise SystemExit(1)


async def import_roster(path: Path, *, dry_run: bool) -> None:
    from yuxi.repositories.employee_repository import EmployeeRepository
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
        repo = EmployeeRepository(db)
        creator = (
            await db.execute(select(User).where(User.role == "superadmin", User.is_deleted == 0).limit(1))
        ).scalar_one_or_none()
        if creator is None:
            creator = (await db.execute(select(User).where(User.is_deleted == 0).limit(1))).scalar_one_or_none()
        if creator is None:
            raise SystemExit("系统中没有可用账号，无法导入员工")

        for row in rows:
            phone = row["移动电话"].strip()
            code = row["编号"].strip()
            role = map_role(row["岗位"])
            ports = map_login_ports(row["岗位"])
            if await repo.get_by_login_account(phone):
                print(f"SKIP {code} {row['姓名']} 手机号已存在")
                skipped += 1
                continue
            if await repo.get_by_code(code):
                print(f"SKIP {code} {row['姓名']} 员工编码已存在")
                skipped += 1
                continue
            if dry_run:
                print(f"DRY {code} {row['姓名']} role={role} ports={ports} phone={phone}")
                created += 1
                continue
            try:
                await ensure_role(db, role, str(creator.uid))
                await create_employee(
                    db,
                    creator,
                    EmployeeCreate(
                        employee_code=code,
                        name=row["姓名"],
                        login_account=phone,
                        current_branch=row["分部"],
                        current_department=row["部门"],
                        gender=GENDER_DEFAULT,
                        age=None,
                        login_port=ports,
                        role=role,
                    ),
                )
                await db.commit()
                print(f"OK {code} {row['姓名']} role={role} ports={ports}")
                created += 1
            except HTTPException as exc:
                await db.rollback()
                code_err = exc.detail.get("error", {}).get("code") if isinstance(exc.detail, dict) else None
                if code_err in {"EMPLOYEE_CODE_EXISTS", "EMPLOYEE_LOGIN_EXISTS", "EMPLOYEE_DUPLICATE"}:
                    print(f"SKIP {code} {row['姓名']} 已存在")
                    skipped += 1
                    continue
                failed += 1
                print(f"FAIL {code} {row['姓名']}: {exc.detail}")
            except Exception as exc:
                await db.rollback()
                failed += 1
                print(f"FAIL {code} {row['姓名']}: {exc}")
    await pg_manager.close()
    label = "预检" if dry_run else "导入"
    print(f"{label}完成 created={created} skipped={skipped} failed={failed} total={len(rows)}")
    if failed:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="导入集团总部通讯录员工")
    parser.add_argument("path", type=Path, help="Excel 导出的 JSON 路径")
    parser.add_argument("--dry-run", action="store_true", help="只校验与打印，不写入数据库")
    parser.add_argument(
        "--align-existing",
        action="store_true",
        help="按名单更新已存在员工的角色（岗位）与登录端口，不新建",
    )
    args = parser.parse_args()
    if not args.path.exists():
        raise SystemExit(f"找不到名单文件: {args.path}")
    if args.align_existing:
        asyncio.run(align_existing_from_roster(args.path, dry_run=args.dry_run))
    else:
        asyncio.run(import_roster(args.path, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
