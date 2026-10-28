from __future__ import annotations

import io
import json
import logging
from datetime import date, datetime, time, timedelta
from typing import Literal

from fastapi import HTTPException
from openpyxl import Workbook
from openpyxl.styles import Font
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.case_share_repository import CaseShareRepository
from yuxi.services.case_visit_service import invalidate_mp_lead_unread_cache
from yuxi.services.material_share_images import delete_share_image_cache
from yuxi.services.role_service import effective_permission_grants
from yuxi.storage.postgres.models_business import OperationLog, User
from yuxi.storage.postgres.models_content import ContentMaterialShare
from yuxi.utils.datetime_utils import format_utc_datetime, shanghai_now

logger = logging.getLogger(__name__)


class ShareFilters(BaseModel):
    keyword: str = Field(default="", max_length=80)
    source: Literal["pc", "mp"] | None = None
    start_date: date | None = None
    end_date: date | None = None


async def share_scope(db: AsyncSession, user: User, action: str) -> list:
    grants = await effective_permission_grants(db, user)
    if grants is not None and f"customer_lead.{action}" not in grants:
        raise HTTPException(403, detail={"error": {"code": "CASE_SHARE_FORBIDDEN", "message": "没有案例分享操作权限"}})
    return [] if user.role == "superadmin" else [ContentMaterialShare.sharer_uid == str(user.uid)]


def share_filters(filters: ShareFilters) -> dict:
    if filters.start_date and filters.end_date and filters.start_date > filters.end_date:
        raise HTTPException(
            422, detail={"error": {"code": "SHARE_DATE_RANGE_INVALID", "message": "开始日期不能晚于结束日期"}}
        )
    return {
        "keyword": filters.keyword.strip(),
        "source": filters.source,
        "start": datetime.combine(filters.start_date, time.min) - timedelta(hours=8) if filters.start_date else None,
        "end": datetime.combine(filters.end_date, time.min) + timedelta(days=1, hours=-8) if filters.end_date else None,
    }


def share_data(row, public_base_url: str) -> dict:
    return {
        "id": row.id,
        "sharer_name": row.sharer_employee_name,
        "employee_code": row.sharer_employee_code,
        "branch": row.sharer_branch,
        "source_platform": row.source_platform,
        "source_url": f"{public_base_url.rstrip('/')}/share/case/{row.token}",
        "shared_at": format_utc_datetime(row.created_at),
    }


async def list_shares(
    db: AsyncSession, user: User, page: int, page_size: int, public_base_url: str, filters: ShareFilters
):
    predicates = await share_scope(db, user, "view_list")
    rows, total = await CaseShareRepository(db).listing(predicates, page, page_size, **share_filters(filters))
    return {
        "items": [share_data(row, public_base_url) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def share_summary(db: AsyncSession, user: User):
    predicates = await share_scope(db, user, "view_list")
    start = datetime.combine(shanghai_now().date(), time.min) - timedelta(hours=8)
    return await CaseShareRepository(db).summary(predicates, start, start + timedelta(days=1))


async def export_shares(db: AsyncSession, user: User, filters: ShareFilters) -> bytes:
    predicates = await share_scope(db, user, "view_list")
    rows, _ = await CaseShareRepository(db).listing(predicates, 1, None, **share_filters(filters))
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "案例分享"
    sheet.append(["序号", "来源", "分享人姓名", "工号", "所属门店", "分享时间"])
    for index, row in enumerate(rows, 1):
        sheet.append(
            [
                index,
                {"pc": "PC端案例分享", "mp": "小程序案例分享"}.get(row.source_platform, "来源未知"),
                row.sharer_employee_name or "—",
                row.sharer_employee_code or "—",
                row.sharer_branch or "—",
                (row.created_at + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") if row.created_at else "—",
            ]
        )
        for cell in sheet[index + 1]:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for column, width in {"A": 8, "B": 24, "C": 20, "D": 18, "E": 24, "F": 24}.items():
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = "A2"
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


async def delete_share(db: AsyncSession, user: User, share_id: str):
    predicates = await share_scope(db, user, "delete")
    result = await CaseShareRepository(db).delete(share_id, predicates)
    if result is None:
        raise HTTPException(
            404, detail={"error": {"code": "CASE_SHARE_NOT_FOUND", "message": "案例分享不存在或无权访问"}}
        )
    db.add(OperationLog(user_id=user.id, operation="material.share.delete", details=json.dumps({"share_id": share_id})))
    await db.commit()
    token, orders, owners = result
    await delete_share_image_cache(token, orders)
    try:
        for owner in owners:
            await invalidate_mp_lead_unread_cache(owner)
    except RedisError:
        logger.warning("Share deleted; unread cache will expire", exc_info=True)
    return {"success": True}
