from __future__ import annotations

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_content import (
    ContentCaseVisitLead,
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
        share = ContentMaterialShare
        conditions = list(predicates)
        if keyword:
            conditions.append(
                or_(
                    share.sharer_employee_name.icontains(keyword, autoescape=True),
                    share.sharer_employee_code.icontains(keyword, autoescape=True),
                    share.sharer_branch.icontains(keyword, autoescape=True),
                )
            )
        if source:
            conditions.append(share.source_platform == source)
        if start is not None:
            conditions.append(share.created_at >= start)
        if end is not None:
            conditions.append(share.created_at < end)
        total = await self.db.scalar(select(func.count()).select_from(share).where(*conditions))
        query = select(share).where(*conditions).order_by(share.created_at.desc().nulls_last(), share.id.desc())
        if page_size is not None:
            query = query.offset((page - 1) * page_size).limit(page_size)
        return (await self.db.scalars(query)).all(), total or 0

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
