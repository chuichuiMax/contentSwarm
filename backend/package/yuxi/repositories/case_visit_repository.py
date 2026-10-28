from __future__ import annotations

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_content import (
    ContentCaseVisitLead,
    ContentCaseVisitSubmission,
    ContentEmployee,
    ContentMaterialShare,
)
from yuxi.utils.datetime_utils import utc_now_naive


class CaseVisitRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def share(self, token: str, *, for_update: bool = False) -> ContentMaterialShare | None:
        query = select(ContentMaterialShare).where(ContentMaterialShare.token == token)
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self.db.scalar(query)

    async def upsert(self, values: dict) -> str:
        statement = insert(ContentCaseVisitLead).values(**values)
        statement = statement.on_conflict_do_update(
            constraint="uq_case_visit_visitor_share",
            set_={
                **{key: statement.excluded[key] for key in ("phone", "acquired_at")},
                "nickname": func.coalesce(statement.excluded.nickname, ContentCaseVisitLead.nickname),
                "avatar_data": func.coalesce(statement.excluded.avatar_data, ContentCaseVisitLead.avatar_data),
                "booking_revision": ContentCaseVisitLead.booking_revision + 1,
                "follow_status": "pending",
                "followed_at": None,
            },
        ).returning(ContentCaseVisitLead.id)
        return (await self.db.execute(statement)).scalar_one()

    async def submission(self, session_id: str) -> str | None:
        return await self.db.scalar(
            select(ContentCaseVisitSubmission.lead_id).where(ContentCaseVisitSubmission.session_id == session_id)
        )

    def record_submission(self, session_id: str, lead_id: str) -> None:
        self.db.add(ContentCaseVisitSubmission(session_id=session_id, lead_id=lead_id))

    async def unread_count(self, owner_uid: str) -> int:
        lead = ContentCaseVisitLead
        return (
            await self.db.scalar(
                select(func.count())
                .select_from(lead)
                .where(lead.sharer_uid == owner_uid, lead.read_revision < lead.booking_revision)
            )
            or 0
        )

    async def mp_listing(self, owner_uid: str, page: int, page_size: int, keyword: str, start, end):
        lead = ContentCaseVisitLead
        predicates = [lead.sharer_uid == owner_uid]
        if keyword:
            predicates.append(
                or_(lead.nickname.icontains(keyword, autoescape=True), lead.phone.contains(keyword, autoescape=True))
            )
        if start is not None:
            predicates.append(lead.acquired_at >= start)
        if end is not None:
            predicates.append(lead.acquired_at < end)
        total = await self.db.scalar(select(func.count()).select_from(lead).where(*predicates))
        rows = await self.db.execute(
            self.mp_query()
            .where(*predicates)
            .order_by(lead.acquired_at.desc(), lead.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return rows.all(), total or 0

    def mp_query(self):
        lead, share = ContentCaseVisitLead, ContentMaterialShare
        return select(
            lead.id,
            lead.nickname,
            lead.avatar_data.is_not(None).label("has_avatar"),
            lead.phone,
            lead.acquired_at,
            lead.booking_revision,
            lead.read_revision,
            lead.follow_status,
            lead.followed_at,
            share.token,
            share.title,
            share.building_name,
            share.design_style,
            share.area,
            share.source_platform,
            share.created_at.label("shared_at"),
        ).join(share, lead.share_id == share.id)

    async def mp_detail(self, owner_uid: str, lead_id: str):
        result = await self.db.execute(
            self.mp_query().where(ContentCaseVisitLead.sharer_uid == owner_uid, ContentCaseVisitLead.id == lead_id)
        )
        return result.one_or_none()

    async def update_mp_state(self, owner_uid: str, lead_id: str, revision: int, action: str) -> bool:
        lead = ContentCaseVisitLead
        values = (
            {"read_revision": revision}
            if action == "read"
            else {"follow_status": "followed", "followed_at": func.coalesce(lead.followed_at, utc_now_naive())}
        )
        result = await self.db.execute(
            update(lead)
            .where(lead.sharer_uid == owner_uid, lead.id == lead_id, lead.booking_revision == revision)
            .values(**values)
            .returning(lead.id)
        )
        return result.scalar_one_or_none() is not None

    def pc_query(self):
        lead, share, employee = ContentCaseVisitLead, ContentMaterialShare, ContentEmployee
        return (
            select(
                lead.id,
                lead.phone,
                lead.acquired_at,
                share.token,
                share.title,
                share.source_platform,
                employee.name.label("sharer_name"),
                employee.employee_code,
                employee.current_branch.label("branch"),
            )
            .select_from(lead)
            .outerjoin(share, lead.share_id == share.id)
            .outerjoin(employee, share.sharer_employee_id == employee.id)
        )

    async def listing(
        self,
        predicates: list,
        page: int,
        page_size: int | None,
        *,
        keyword: str = "",
        branch: str | None = None,
        source: str | None = None,
        start=None,
        end=None,
    ) -> tuple[list, int]:
        lead, share, employee = ContentCaseVisitLead, ContentMaterialShare, ContentEmployee
        conditions = list(predicates)
        if keyword:
            conditions.append(
                or_(
                    lead.phone.contains(keyword, autoescape=True),
                    employee.name.icontains(keyword, autoescape=True),
                    employee.employee_code.icontains(keyword, autoescape=True),
                    employee.current_branch.icontains(keyword, autoescape=True),
                )
            )
        if branch:
            conditions.append(employee.current_branch == branch)
        if source:
            conditions.append(share.source_platform == source)
        if start is not None:
            conditions.append(lead.acquired_at >= start)
        if end is not None:
            conditions.append(lead.acquired_at < end)
        query = self.pc_query().where(*conditions)
        total = await self.db.scalar(select(func.count()).select_from(query.subquery()))
        query = query.order_by(lead.acquired_at.desc(), lead.id.desc())
        if page_size is not None:
            query = query.offset((page - 1) * page_size).limit(page_size)
        rows = await self.db.execute(query)
        return rows.all(), total or 0

    async def summary(self, predicates: list, start, end) -> dict:
        lead = ContentCaseVisitLead
        total, today = (
            await self.db.execute(
                select(
                    func.count(),
                    func.count().filter(lead.created_at >= start, lead.created_at < end),
                )
                .select_from(lead)
                .where(*predicates)
            )
        ).one()
        # 门店选项与员工管理一致；仅客资统计受当前账号的数据范围限制。
        branches = await self.db.scalars(select(ContentEmployee.current_branch).distinct())
        return {
            "total": total,
            "today": today,
            "branches": sorted({value.strip() for value in branches if value.strip()}),
        }

    async def phone(self, lead_id: str, predicates: list) -> str | None:
        return await self.db.scalar(
            select(ContentCaseVisitLead.phone).where(ContentCaseVisitLead.id == lead_id, *predicates)
        )

    async def avatar(self, lead_id: str, predicates: list) -> bytes | None:
        return await self.db.scalar(
            select(ContentCaseVisitLead.avatar_data).where(ContentCaseVisitLead.id == lead_id, *predicates)
        )

    async def delete(self, lead_id: str, predicates: list) -> bool:
        result = await self.db.execute(
            delete(ContentCaseVisitLead)
            .where(ContentCaseVisitLead.id == lead_id, *predicates)
            .returning(ContentCaseVisitLead.id)
        )
        return result.scalar_one_or_none() is not None
