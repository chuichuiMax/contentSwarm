from __future__ import annotations

import asyncio
import io
import json
import uuid
from datetime import date, datetime, time, timedelta

import httpx
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.case_visit_repository import CaseVisitRepository
from yuxi.services.mp_service import MpContext, _wechat_credentials, _wechat_phone_by_code
from yuxi.services.role_service import effective_permission_grants
from yuxi.services.run_queue_service import get_redis_client
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentCaseVisitLead
from yuxi.utils.datetime_utils import format_utc_datetime, utc_now_naive

VISITOR_SESSION_TTL = 600
MAX_AVATAR_BYTES = 2 * 1024 * 1024


def booking_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"error": {"code": code, "message": message}})


class VisitorSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=128)
    share_token: str = Field(min_length=1, max_length=64)


async def create_visitor_session(db: AsyncSession, payload: VisitorSessionCreate) -> dict:
    if await CaseVisitRepository(db).share(payload.share_token) is None:
        raise booking_error(404, "MATERIAL_SHARE_NOT_FOUND", "案例分享不存在")
    appid, secret = _wechat_credentials()
    if not appid or not secret:
        raise booking_error(503, "WECHAT_NOT_CONFIGURED", "微信预约服务未配置")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params={
                    "appid": appid,
                    "secret": secret,
                    "js_code": payload.code,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            identity = response.json()
    except (httpx.HTTPError, ValueError):
        raise booking_error(503, "WECHAT_UNAVAILABLE", "微信身份服务暂时不可用，请稍后重试") from None
    if identity.get("errcode") or not identity.get("openid"):
        raise booking_error(422, "WECHAT_CODE_INVALID", "微信身份验证失败，请关闭预约卡片后重试")
    session_id = uuid.uuid4().hex
    redis = await get_redis_client()
    await redis.setex(
        f"mp:visit:{session_id}",
        VISITOR_SESSION_TTL,
        json.dumps(
            {
                "appid": appid,
                "openid": identity["openid"],
                "share_token": payload.share_token,
            }
        ),
    )
    return {"session_id": session_id, "expires_in": VISITOR_SESSION_TTL}


def encode_booking_avatar(data: bytes) -> bytes:
    if not data or len(data) > MAX_AVATAR_BYTES:
        raise booking_error(422, "BOOKING_AVATAR_INVALID", "请选择不超过 2 MB 的头像")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 16_000_000:
                raise booking_error(422, "BOOKING_AVATAR_INVALID", "头像尺寸过大，请重新选择")
            avatar = ImageOps.exif_transpose(image).convert("RGB")
            avatar.thumbnail((256, 256))
            output = io.BytesIO()
            avatar.save(output, format="WEBP", quality=82)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise booking_error(422, "BOOKING_AVATAR_INVALID", "头像文件无法读取，请重新选择") from exc


async def submit_booking(
    db: AsyncSession,
    token: str,
    session_id: str,
    nickname: str,
    phone_code: str,
    avatar: bytes,
) -> dict:
    nickname = nickname.strip()
    if not nickname or len(nickname) > 32:
        raise booking_error(422, "BOOKING_NICKNAME_REQUIRED", "请填写不超过 32 字的昵称")
    redis = await get_redis_client()
    key = f"mp:visit:{session_id}"
    raw = await redis.get(key)
    if raw is None:
        raise booking_error(401, "VISITOR_SESSION_EXPIRED", "预约会话已过期，请关闭卡片后重新预约")
    session = json.loads(raw)
    appid, secret = _wechat_credentials()
    if session["share_token"] != token or session["appid"] != appid:
        raise booking_error(403, "VISITOR_SESSION_MISMATCH", "预约会话与案例不匹配")
    share = await CaseVisitRepository(db).share(token)
    if share is None:
        raise booking_error(404, "MATERIAL_SHARE_NOT_FOUND", "案例分享不存在")
    avatar_data = await asyncio.to_thread(encode_booking_avatar, avatar)
    # 同一会话串行消费微信的一次性授权码，保存失败后仍可重试。
    async with redis.lock(f"{key}:submit", timeout=45, blocking_timeout=2):
        session = json.loads(await redis.get(key) or "null")
        if session is None:
            raise booking_error(401, "VISITOR_SESSION_EXPIRED", "预约会话已过期，请重新预约")
        repo = CaseVisitRepository(db)
        submitted = await repo.submission(session_id)
        if submitted:
            return {"success": True, "id": submitted}
        if not session.get("phone") or session.get("phone_code") != phone_code:
            try:
                session["phone"] = await _wechat_phone_by_code(appid, secret, phone_code)
            except httpx.HTTPError:
                raise booking_error(503, "WECHAT_UNAVAILABLE", "微信手机号服务暂时不可用，请稍后重试") from None
            session["phone_code"] = phone_code
            await redis.setex(key, VISITOR_SESSION_TTL, json.dumps(session))
        lead_id = await repo.upsert(
            {
                "id": f"cvl_{uuid.uuid4().hex}",
                "share_id": share.id,
                "appid": appid,
                "openid": session["openid"],
                "nickname": nickname,
                "phone": session["phone"],
                "avatar_data": avatar_data,
                "sharer_uid": share.sharer_uid,
                "department_id": share.sharer_department_id,
                "created_at": utc_now_naive(),
                "acquired_at": utc_now_naive(),
            }
        )
        repo.record_submission(session_id, lead_id)
        await db.commit()
    return {"success": True, "id": lead_id}


class LeadRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)


def mp_lead_data(row, *, detail: bool = False) -> dict:
    area = (row.area or "").strip()
    if area.replace(".", "", 1).isdigit():
        area += "㎡"
    title = "-".join(str(value) for value in (row.building_name, row.design_style, area) if value) or row.title
    result = {
        "id": row.id,
        "nickname": row.nickname,
        "phone_masked": f"{row.phone[:3]}****{row.phone[-4:]}" if len(row.phone) >= 7 else "****",
        "avatar_url": f"/api/mp/customer-leads/{row.id}/avatar",
        "acquired_at": format_utc_datetime(row.acquired_at),
        "revision": row.booking_revision,
        "unread": row.read_revision < row.booking_revision,
        "follow_status": row.follow_status,
        "source_title": title,
        "source_platform": row.source_platform,
    }
    if detail:
        result.update(
            phone=row.phone,
            source_share_id=row.token,
            source_cover_url=f"/api/material-library/shares/{row.token}/images/1.webp",
            shared_at=format_utc_datetime(row.shared_at),
        )
    return result


async def list_mp_leads(
    db: AsyncSession,
    ctx: MpContext,
    page: int,
    page_size: int,
    keyword: str,
    start_date: date | None,
    end_date: date | None,
) -> dict:
    if start_date and end_date and start_date > end_date:
        raise booking_error(422, "LEAD_DATE_RANGE_INVALID", "开始日期不能晚于结束日期")
    # 日期选择按北京时间自然日，数据库保存 UTC naive；结束边界不含下一天。
    start = datetime.combine(start_date, time.min) - timedelta(hours=8) if start_date else None
    end = datetime.combine(end_date, time.min) + timedelta(days=1, hours=-8) if end_date else None
    rows, total = await CaseVisitRepository(db).mp_listing(
        str(ctx.user.uid), page, page_size, keyword.strip(), start, end
    )
    return {"items": [mp_lead_data(row) for row in rows], "total": total, "page": page, "page_size": page_size}


async def get_mp_lead(db: AsyncSession, ctx: MpContext, lead_id: str) -> dict:
    row = await CaseVisitRepository(db).mp_detail(str(ctx.user.uid), lead_id)
    if row is None:
        raise booking_error(404, "CUSTOMER_LEAD_NOT_FOUND", "客资不存在或无权访问")
    return mp_lead_data(row, detail=True)


async def change_mp_lead(db: AsyncSession, ctx: MpContext, lead_id: str, revision: int, action: str) -> dict:
    repo = CaseVisitRepository(db)
    if not await repo.update_mp_state(str(ctx.user.uid), lead_id, revision, action):
        if await repo.mp_detail(str(ctx.user.uid), lead_id) is None:
            raise booking_error(404, "CUSTOMER_LEAD_NOT_FOUND", "客资不存在或无权访问")
        raise booking_error(409, "LEAD_UPDATED", "业主有新的预约，请刷新后查看")
    await db.commit()
    return {"success": True, "unread_count": await repo.unread_count(str(ctx.user.uid))}


async def lead_scope(db: AsyncSession, user: User, action: str) -> list:
    grants = await effective_permission_grants(db, user)
    if grants is not None and f"customer_lead.{action}" not in grants:
        raise booking_error(403, "CUSTOMER_LEAD_FORBIDDEN", "没有客资操作权限")
    if user.role == "superadmin":
        return []
    if user.role == "admin":
        if user.department_id is None:
            raise booking_error(403, "CUSTOMER_LEAD_FORBIDDEN", "当前管理员未绑定部门")
        return [ContentCaseVisitLead.department_id == user.department_id]
    return [ContentCaseVisitLead.sharer_uid == str(user.uid)]


async def list_leads(db: AsyncSession, user: User, page: int, page_size: int, public_base_url: str) -> dict:
    predicates = await lead_scope(db, user, "view_list")
    rows, total = await CaseVisitRepository(db).listing(predicates, page, page_size)
    return {
        "items": [
            {
                "id": row.id,
                "nickname": row.nickname,
                "phone": row.phone,
                "avatar_url": f"/api/customer-leads/{row.id}/avatar",
                "source_title": row.title,
                "source_url": f"{public_base_url}/share/case/{row.token}",
                "acquired_at": format_utc_datetime(row.acquired_at),
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def read_lead_avatar(db: AsyncSession, user: User, lead_id: str) -> bytes:
    predicates = await lead_scope(db, user, "view_list")
    avatar = await CaseVisitRepository(db).avatar(lead_id, predicates)
    if avatar is None:
        raise booking_error(404, "CUSTOMER_LEAD_NOT_FOUND", "客资不存在或无权访问")
    return avatar


async def delete_lead(db: AsyncSession, user: User, lead_id: str) -> dict:
    predicates = await lead_scope(db, user, "delete")
    if not await CaseVisitRepository(db).delete(lead_id, predicates):
        raise booking_error(404, "CUSTOMER_LEAD_NOT_FOUND", "客资不存在或无权访问")
    await db.commit()
    return {"success": True}
