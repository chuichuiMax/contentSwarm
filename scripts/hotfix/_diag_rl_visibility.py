"""Diagnose requirement-list visibility for 徐迎港 vs 翟伟."""
from __future__ import annotations

import asyncio

from sqlalchemy import or_, select

from yuxi.repositories.employee_repository import EmployeeRepository
from yuxi.services.employee_service import _platform_uid, resolve_employee_for_user
from yuxi.services.requirement_list_service import _visible, SubmissionFilters, _filtered
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import RequirementListSubmission, RequirementListSubmissionShare


async def main() -> None:
    pg_manager.initialize()
    async with pg_manager.get_async_session_context() as db:
        repo = EmployeeRepository(db)
        for code in ("H04454",):
            emp = await repo.get_by_code(code)
            if not emp:
                print("no employee", code)
                continue
            user = (
                await db.scalars(
                    select(User).where(
                        or_(
                            User.uid == _platform_uid(emp),
                            User.phone_number == (emp.login_account or ""),
                        ),
                        User.is_deleted == 0,
                    )
                )
            ).first()
            print("employee", emp.name, emp.employee_code, "login", emp.login_account)
            print("platform_uid", _platform_uid(emp))
            print("user", user.uid if user else None, user.username if user else None, user.role if user else None)
            if user:
                resolved = await resolve_employee_for_user(db, user)
                print("resolved_employee", resolved.name if resolved else None)
                mp = (
                    await db.scalars(
                        _filtered(SubmissionFilters())
                        .where(
                            or_(
                                RequirementListSubmission.created_by == str(user.uid),
                                RequirementListSubmission.employee_code == emp.employee_code,
                            )
                        )
                        .order_by(RequirementListSubmission.submitted_at.desc())
                        .limit(20)
                    )
                ).all()
                pc = (
                    await db.scalars(
                        _visible(_filtered(SubmissionFilters()), user, employee_code=emp.employee_code)
                        .order_by(RequirementListSubmission.submitted_at.desc())
                        .limit(20)
                    )
                ).all()
                mp_ids = [r.id for r in mp]
                pc_ids = [r.id for r in pc]
                print("mp_count", len(mp), "pc_count", len(pc))
                print("only_mp", [r.id for r in mp if r.id not in pc_ids])
                for r in mp:
                    print(
                        "row",
                        r.id[:12],
                        r.requirement_type,
                        "sharer",
                        r.sharer_name,
                        "emp_code",
                        r.employee_code,
                        "created_by",
                        r.created_by[:20] if r.created_by else "",
                    )
                shares = (
                    await db.scalars(
                        select(RequirementListSubmissionShare).where(
                            RequirementListSubmissionShare.member_uid.in_(
                                [str(user.uid), _platform_uid(emp)]
                            )
                        )
                    )
                ).all()
                print("share_rows_for_user", len(shares))
                for s in shares:
                    print(" share", s.submission_id[:12], s.member_uid[:24], s.member_name)


if __name__ == "__main__":
    asyncio.run(main())