"""One-off: rename superadmin employee/user tiechui -> admin (production ops)."""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import or_, select, update

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
    ContentMaterialShare,
    ContentEmployee,
    ContentTask,
)


async def _show() -> None:
    async with pg_manager.get_async_session_context() as db:
        users = (
            await db.execute(
                select(User).where(
                    or_(
                        User.role == "superadmin",
                        User.uid.ilike("%tiechui%"),
                        User.username.ilike("%tiechui%"),
                    )
                )
            )
        ).scalars()
        for user in users:
            print(f"user id={user.id} uid={user.uid!r} username={user.username!r} role={user.role}")
        employees = (
            await db.execute(
                select(ContentEmployee).where(
                    or_(
                        ContentEmployee.employee_code.ilike("%tiechui%"),
                        ContentEmployee.name.ilike("%tiechui%"),
                        ContentEmployee.login_account.ilike("%tiechui%"),
                        ContentEmployee.employee_code == "admin",
                    )
                )
            )
        ).scalars()
        for emp in employees:
            print(
                f"employee id={emp.id} code={emp.employee_code!r} name={emp.name!r} "
                f"login={emp.login_account!r} role={emp.role}"
            )


UID_REPLACEMENTS = (
    (ContentCoverAsset, "owner_uid"),
    (ContentMaterialCategory, "owner_uid"),
    (ContentMaterialLibraryItem, "owner_uid"),
    (ContentMaterialLibraryItem, "category_owner_uid"),
    (ContentMaterialShare, "owner_uid"),
    (ContentMaterialShare, "sharer_uid"),
    (ContentTask, "created_by"),
)


async def _apply(*, dry_run: bool) -> None:
    async with pg_manager.get_async_session_context() as db:
        user = await db.scalar(
            select(User).where(User.role == "superadmin", User.is_deleted == 0).order_by(User.id).limit(1)
        )
        if user is None:
            user = await db.scalar(select(User).where(User.uid.ilike("%tiechui%")).limit(1))
        if user is None:
            raise SystemExit("superadmin user not found")

        employee = await db.scalar(
            select(ContentEmployee).where(
                or_(
                    ContentEmployee.employee_code.ilike("%tiechui%"),
                    ContentEmployee.login_account == user.uid,
                    ContentEmployee.login_account == user.username,
                )
            ).limit(1)
        )
        if employee is None and user.uid:
            employee = await db.scalar(
                select(ContentEmployee).where(ContentEmployee.login_account == user.phone_number).limit(1)
            )

        existing_code = await db.scalar(
            select(ContentEmployee.id).where(
                ContentEmployee.employee_code == "admin", ContentEmployee.id != getattr(employee, "id", None)
            )
        )
        existing_user = await db.scalar(
            select(User.id).where(User.username == "admin", User.id != user.id, User.is_deleted == 0)
        )
        if existing_code:
            raise SystemExit(f"employee_code admin already used by employee {existing_code}")
        if existing_user:
            raise SystemExit(f"username admin already used by user {existing_user}")

        print(f"will update user id={user.id} uid={user.uid!r} username={user.username!r} -> admin")
        if employee:
            print(
                f"will update employee id={employee.id} code={employee.employee_code!r} name={employee.name!r} -> admin"
            )
        else:
            print("no matching content_employees row (user-only update)")

        old_uid = str(user.uid)
        if dry_run:
            print("dry-run, no changes")
            return

        for model, column in UID_REPLACEMENTS:
            col = getattr(model, column)
            result = await db.execute(update(model).where(col == old_uid).values({column: "admin"}))
            if result.rowcount:
                print(f"updated {result.rowcount} rows in {model.__tablename__}.{column}")

        user.username = "admin"
        user.uid = "admin"
        if employee:
            employee.employee_code = "admin"
            employee.name = "admin"
        await db.commit()
        print("committed — PC 登录账号由 tiechui 改为 admin，密码不变")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        asyncio.run(_apply(dry_run=False))
    else:
        asyncio.run(_show())


if __name__ == "__main__":
    main()
