from datetime import date

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from redis.exceptions import LockError
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_mp_context, get_required_user
from yuxi.repositories.case_visit_repository import CaseVisitRepository
from yuxi.services.mp_service import MpContext
from yuxi.storage.postgres.models_content import ContentCaseVisitLead
from server.utils.public_url import request_public_base_url
from yuxi.services.case_visit_service import (
    MAX_AVATAR_BYTES,
    LeadRevision,
    LeadFilters,
    VisitorSessionCreate,
    booking_error,
    create_visitor_session,
    delete_lead,
    list_leads,
    read_lead_avatar,
    submit_booking,
    list_mp_leads,
    get_mp_lead,
    change_mp_lead,
    mp_lead_summary,
    pc_lead_summary,
    read_lead_phone,
    export_leads,
)
from yuxi.storage.postgres.models_business import User

case_visits = APIRouter(tags=["case-visits"])


@case_visits.get("/mp/customer-leads/summary")
async def mp_customer_lead_summary(
    authorization: str | None = Header(None),
    access_token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    return await mp_lead_summary(db, authorization, access_token=access_token)


@case_visits.get("/mp/customer-leads")
async def mp_lead_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    keyword: str = Query("", max_length=80),
    start_date: date | None = None,
    end_date: date | None = None,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await list_mp_leads(db, ctx, page, page_size, keyword, start_date, end_date)


@case_visits.get("/mp/customer-leads/{lead_id}")
async def mp_lead_detail(lead_id: str, ctx: MpContext = Depends(get_mp_context), db: AsyncSession = Depends(get_db)):
    return await get_mp_lead(db, ctx, lead_id)


@case_visits.get("/mp/customer-leads/{lead_id}/avatar")
async def mp_lead_avatar(lead_id: str, ctx: MpContext = Depends(get_mp_context), db: AsyncSession = Depends(get_db)):
    avatar = await CaseVisitRepository(db).avatar(lead_id, [ContentCaseVisitLead.sharer_uid == str(ctx.user.uid)])
    if avatar is None:
        raise booking_error(404, "CUSTOMER_LEAD_NOT_FOUND", "客资不存在或无权访问")
    return Response(avatar, media_type="image/webp", headers={"Cache-Control": "private, no-store"})


@case_visits.post("/mp/customer-leads/{lead_id}/read")
async def mp_lead_read(
    lead_id: str, payload: LeadRevision, ctx: MpContext = Depends(get_mp_context), db: AsyncSession = Depends(get_db)
):
    return await change_mp_lead(db, ctx, lead_id, payload.revision, "read")


@case_visits.post("/mp/customer-leads/{lead_id}/follow")
async def mp_lead_follow(
    lead_id: str, payload: LeadRevision, ctx: MpContext = Depends(get_mp_context), db: AsyncSession = Depends(get_db)
):
    return await change_mp_lead(db, ctx, lead_id, payload.revision, "follow")


@case_visits.post("/mp/visitor-sessions")
async def visitor_session(payload: VisitorSessionCreate, db: AsyncSession = Depends(get_db)):
    return await create_visitor_session(db, payload)


@case_visits.post("/mp/share/cases/{token}/appointments")
async def appointment(
    token: str,
    session_id: str = Form(min_length=1, max_length=64),
    nickname: str | None = Form(None, min_length=1, max_length=32),
    phone_code: str = Form(min_length=1, max_length=256),
    consent: bool = Form(),
    avatar: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
):
    if not consent:
        raise booking_error(422, "BOOKING_CONSENT_REQUIRED", "请先同意预约信息使用说明")
    data = await avatar.read(MAX_AVATAR_BYTES + 1) if avatar else None
    try:
        return await submit_booking(db, token, session_id, nickname, phone_code, data)
    except LockError:
        raise booking_error(409, "BOOKING_BUSY", "预约正在提交，请稍后重试") from None


@case_visits.get("/customer-leads")
async def customer_leads(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    filters: LeadFilters = Depends(),
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return JSONResponse(
        await list_leads(db, user, page, page_size, request_public_base_url(request), filters),
        headers={"Cache-Control": "private, no-store"},
    )


@case_visits.get("/customer-leads/summary")
async def customer_lead_summary(user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    return JSONResponse(await pc_lead_summary(db, user), headers={"Cache-Control": "private, no-store"})


@case_visits.get("/customer-leads/export")
async def customer_lead_export(
    filters: LeadFilters = Depends(),
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return Response(
        await export_leads(db, user, filters),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="customer-leads.xlsx"',
            "Cache-Control": "private, no-store",
        },
    )


@case_visits.get("/customer-leads/{lead_id}/phone")
async def customer_lead_phone(
    lead_id: str,
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return JSONResponse(await read_lead_phone(db, user, lead_id), headers={"Cache-Control": "private, no-store"})


@case_visits.get("/customer-leads/{lead_id}/avatar")
async def customer_lead_avatar(
    lead_id: str,
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return Response(
        await read_lead_avatar(db, user, lead_id),
        media_type="image/webp",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@case_visits.delete("/customer-leads/{lead_id}")
async def customer_lead_delete(
    lead_id: str,
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_lead(db, user, lead_id)
