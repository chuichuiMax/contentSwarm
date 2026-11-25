"""Smoke-test requirement-list share visibility inside api-dev (production)."""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.manager import pg_manager
from yuxi.services.requirement_list_service import (
    ShareMembersPayload,
    SubmissionFilters,
    _sharer_uid,
    list_share_employees,
    list_submissions,
    share_submission,
)
from yuxi.repositories.employee_repository import EmployeeRepository
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import RequirementListSubmission, RequirementListSubmissionShare


async def run() -> int:
    pg_manager.initialize()
    async with pg_manager.get_async_session_context() as db:
        share_table = await db.scalar(
            select(func.to_regclass("public.requirement_list_submission_shares"))
        )
        print("shares_table", share_table)

        owner_row = (
            await db.scalars(
                select(RequirementListSubmission)
                .order_by(RequirementListSubmission.shared_at.desc().nullslast())
                .limit(1)
            )
        ).first()
        if owner_row is None:
            print("no_submissions")
            return 0

        owner_user = (
            await db.scalars(select(User).where(User.uid == owner_row.created_by).limit(1))
        ).first()
        if owner_user is None:
            print("owner_user_missing", owner_row.created_by)
            return 1

        repo = EmployeeRepository(db)
        employees = await list_share_employees(db)
        candidates = [item for item in employees["items"] if item.get("id")]
        recipient = None
        for item in candidates:
            emp = await repo.get(item["id"])
            if emp is None:
                continue
            member_uid = await _sharer_uid(db, emp)
            if member_uid != str(owner_user.uid):
                recipient = (emp, member_uid)
                break
        if recipient is None:
            print("no_recipient_employee", len(candidates))
            return 0

        emp, member_uid = recipient
        recipient_user = (await db.scalars(select(User).where(User.uid == member_uid).limit(1))).first()
        if recipient_user is None:
            print("recipient_user_missing", member_uid)
            return 1

        before = await db.scalar(
            select(func.count())
            .select_from(RequirementListSubmissionShare)
            .where(
                RequirementListSubmissionShare.submission_id == owner_row.id,
                RequirementListSubmissionShare.member_uid == member_uid,
            )
        )
        shared = await share_submission(
            db,
            owner_user,
            owner_row.id,
            ShareMembersPayload(employee_ids=[emp.id]),
        )
        await db.commit()
        print("share_ok", owner_row.id, "member", emp.name, "shared_members", len(shared.get("shared_members") or []))

        owner_list = await list_submissions(db, owner_user, 1, 20, SubmissionFilters())
        owner_ids = {item["id"] for item in owner_list.get("items", [])}
        recipient_list = await list_submissions(db, recipient_user, 1, 50, SubmissionFilters())
        recipient_ids = {item["id"] for item in recipient_list.get("items", [])}
        visible = owner_row.id in recipient_ids
        print("owner_sees", owner_row.id in owner_ids)
        print("recipient_sees", visible, "before_share_rows", before)
        return 0 if visible and owner_row.id in owner_ids else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))