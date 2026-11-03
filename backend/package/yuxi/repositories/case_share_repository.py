from __future__ import annotations

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import (
    ContentCaseVisitLead,
    ContentEmployee,
    ContentMaterialShare,
    ContentMaterialShareItem,
    ContentMaterialShareJob,
)


class CaseShareRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def listing(
        self, predicates: list, page: int, page_size: int | None, *, keyword="", source=None, start=None, end=None
    ):
        share, employee = ContentMaterialShare, ContentEmployee
        # Preserve sharing-time snapshots; older shares resolve through the actual operator.
        query = (
            select(
                share.id,
                share.token,
                share.source_platform,
                share.created_at,
                func.coalesce(share.sharer_employee_name, employee.name, User.username).label("sharer_employee_name"),
                func.coalesce(share.sharer_employee_code, employee.employee_code).label("sharer_employee_code"),
                func.coalesce(share.sharer_branch, employee.current_branch).label("sharer_branch"),
            )
            .select_from(share)
            .outerjoin(User, and_(User.uid == share.sharer_uid, User.is_deleted == 0))
            .outerjoin(
                employee,
                or_(
                    share.sharer_employee_id == employee.id,
                    and_(share.sharer_employee_id.is_(None), employee.login_account == User.phone_number),
                ),
            )
            .where(*predicates)
            .subquery()
        )
        fields = query.c
        conditions = []
        if keyword:
            conditions.append(
                or_(
                    fields.sharer_employee_name.icontains(keyword, autoescape=True),
                    fields.sharer_employee_code.icontains(keyword, autoescape=True),
                    fields.sharer_branch.icontains(keyword, autoescape=True),
                )
            )
        if source:
            conditions.append(fields.source_platform == source)
        if start is not None:
            conditions.append(fields.created_at >= start)
        if end is not None:
            conditions.append(fields.created_at < end)
        total = await self.db.scalar(select(func.count()).select_from(query).where(*conditions))
        query = select(query).where(*conditions).order_by(fields.created_at.desc().nulls_last(), fields.id.desc())
        if page_size is not None:
            query = query.offset((page - 1) * page_size).limit(page_size)
        return (await self.db.execute(query)).all(), total or 0

    async def summary(self, predicates: list, start, end) -> dict:
        share = ContentMaterialShare
        total, today = (
            await self.db.execute(
                select(func.count(), func.count().filter(share.created_at >= start, share.created_at < end))
                .select_from(share)
                .where(*predicates)
            )
        ).one()
        return {"total": total, "today": today}

    async def delete(self, share_id: str, predicates: list):
        share = ContentMaterialShare
        token = await self.db.scalar(select(share.token).where(share.id == share_id, *predicates))
        if token is None:
            return None
        # Match worker lock order. Keep the job as a tombstone against token replay.
        job = await self.db.scalar(
            select(ContentMaterialShareJob)
            .where(
                ContentMaterialShareJob.token == token,
            )
            .with_for_update()
        )
        row = await self.db.scalar(select(share).where(share.id == share_id, *predicates).with_for_update())
        if row is None:
            return None
        orders = list(
            await self.db.scalars(
                select(ContentMaterialShareItem.display_order).where(
                    ContentMaterialShareItem.share_id == share_id,
                )
            )
        )
        owners = set(
            await self.db.scalars(
                select(ContentCaseVisitLead.sharer_uid).where(
                    ContentCaseVisitLead.share_id == share_id,
                )
            )
        )
        # Submission receipts cascade with their leads; snapshot item rows cascade with the share.
        await self.db.execute(delete(ContentCaseVisitLead).where(ContentCaseVisitLead.share_id == share_id))
        await self.db.execute(delete(share).where(share.id == share_id))
        if job is not None:
            job.status = "deleted"
        return token, orders, owners
