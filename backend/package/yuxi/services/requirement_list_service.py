"""需求清单提交记录。"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, time, timedelta

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.employee_repository import EmployeeRepository
from yuxi.services.employee_service import resolve_employee_for_user
from yuxi.services.mp_service import _wechat_credentials, _wechat_phone_by_code
from yuxi.services.run_queue_service import get_redis_client
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import RequirementListSubmission, RequirementListSubmissionShare
from yuxi.utils.datetime_utils import SHANGHAI_TZ, UTC, shanghai_now, utc_now_naive

REQUIREMENT_VISITOR_SESSION_TTL = 600

REQUIREMENT_TYPES = ("毛坯房", "精装房", "旧房焕新")
STORE_OPTIONS = ("长沙芙蓉店", "长沙袁家岭店", "长沙河西店", "长沙星沙店")
RANGE_OPTIONS = {"all", "today", "7", "30"}
# (字段, 题型, 是否有「其他」说明)。面积拆成面积和房型，设备题拆成 4 组。
_ANSWER_FIELDS = (
    ("q1", "radio", False),
    ("q2", "area", False),
    ("q3", "radio", True),
    ("q4", "radio", True),
    ("q5", "radio", False),
    ("q6", "radio", True),
    ("q7", "check", False),
    ("q8", "radio", True),
    ("q9", "check", True),
    ("q10", "radio", True),
    ("q11", "radio", True),
    ("q12", "check", False),
    ("q13", "radio", True),
    ("q14", "radio", True),
    ("q15", "check", True),
    ("q16", "radio", False),
    ("q17", "groups", False),
    ("q18", "radio", False),
)
# 精装房 EDC t13s87 字段（与小程序 requirement-list-form-decorated 同源）
_DECORATED_ANSWER_FIELDS = (
    ("field001", "check", True),
    ("field002", "check", False),
    ("field003", "check", False),
    ("field004", "check", True),
    ("field005", "check", True),
    ("field006", "check", False),
    ("field007", "check", False),
    ("field008", "check", False),
    ("field009", "check", False),
    ("field010", "check", True),
    ("field011", "check", True),
    ("field012", "check", False),
    ("field013", "check", True),
    ("field014", "check", True),
    ("field015", "check", True),
    ("field016", "check", True),
    ("field017", "check", True),
    ("field018", "check", True),
    ("field019", "check", True),
    ("field020", "check", True),
    ("field021", "check", True),
    ("field022", "check", True),
    ("field023", "check", True),
    ("field024", "check", True),
    ("field025", "check", True),
    ("field026", "check", True),
    ("field027", "check", True),
    ("field028", "check", True),
    ("field029", "check", True),
    ("field030", "check", True),
    ("field031", "check", True),
    ("field032", "check", True),
    ("field033", "check", True),
    ("field034", "check", True),
    ("field035", "check", True),
    ("field046", "text", False),
)
# 旧房焕新 EDC o6n08s（与 requirement-list-form-renovation 同源）
_RENOVATION_ANSWER_FIELDS = (
    ("field003", "text", False),
    ("field005", "check", False),
    ("field006", "check", False),
    ("field007", "text", False),
    ("field008", "check", True),
    ("field009", "check", False),
    ("field010", "check", False),
    ("field011", "check", False),
    ("field012", "check", True),
    ("field013", "check", False),
    ("field014", "check", False),
    ("field015", "check", False),
    ("field016", "check", False),
    ("field017", "check", False),
    ("field018", "check", False),
    ("field019", "check", False),
)


def _answer_field_schema(requirement_type: str):
    if requirement_type == "精装房":
        return _DECORATED_ANSWER_FIELDS
    if requirement_type == "旧房焕新":
        return _RENOVATION_ANSWER_FIELDS
    return _ANSWER_FIELDS


def _has_text(value) -> bool:
    if isinstance(value, list):
        return any(str(item).strip() for item in value)
    return bool(str(value or "").strip())


def completion_percent(
    answers: dict | None, *, name: str, phone: str, address: str, requirement_type: str = ""
) -> int:
    """已填项 / 全部填写项。客户姓名、手机、楼盘地址各算一项，分享人工号不计入。"""
    slots = [name, phone, address]
    payload = answers or {}
    for key, kind, other in _answer_field_schema(requirement_type):
        if kind == "area":
            slots.extend((payload.get("q2_area"), payload.get("q2_layout")))
        elif kind == "groups":
            slots.extend(payload.get(f"{key}_{index}") for index in range(4))
        elif other and _has_text(payload.get(f"{key}_other")):
            slots.append("filled")
        else:
            slots.append(payload.get(key))
    filled = sum(_has_text(item) for item in slots)
    return round(filled * 100 / len(slots)) if slots else 0


class SubmissionCreate(BaseModel):
    requirement_type: str = Field(min_length=1, max_length=32)
    customer_name: str = Field(default="", max_length=40)
    phone: str = Field(default="", max_length=20)
    address: str = Field(default="", max_length=200)
    staff_code: str = Field(default="", max_length=64)
    answers: dict = Field(default_factory=dict)

    @field_validator("answers")
    @classmethod
    def limit_answers(cls, value: dict) -> dict:
        if len(json.dumps(value, ensure_ascii=False)) > 20000:
            raise ValueError("问卷内容过长")
        return value

    def normalized(self) -> SubmissionCreate:
        name = self.customer_name.strip()
        phone = self.phone.strip()
        address = self.address.strip()
        staff_code = self.staff_code.strip()
        if self.requirement_type not in REQUIREMENT_TYPES:
            raise HTTPException(status_code=422, detail="需求清单类型不正确")
        if not name and not phone and not address:
            raise HTTPException(status_code=422, detail="请先填写客户姓名、手机号码或楼盘地址")
        return self.model_copy(
            update={"customer_name": name, "phone": phone, "address": address, "staff_code": staff_code}
        )


class RequirementVisitorSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=128)
    staff_code: str = Field(min_length=1, max_length=64)


class MpSharedSubmissionCreate(SubmissionCreate):
    visitor_session_id: str = Field(min_length=8, max_length=64)
    phone_code: str = Field(min_length=1, max_length=512)
    submitter_nickname: str = Field(min_length=1, max_length=32)


class SubmissionFilters(BaseModel):
    keyword: str = Field(default="", max_length=80)
    branch: str | None = Field(default=None, max_length=80)
    submitted_range: str = Field(default="all", max_length=16)
    shared_range: str = Field(default="all", max_length=16)


def mask_phone(phone: str) -> str:
    digits = phone.strip()
    if len(digits) < 7:
        return digits
    return f"{digits[:3]}****{digits[-4:]}"


def display_stamp(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.replace(tzinfo=UTC).astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")


def _range_bounds(kind: str) -> tuple[datetime, datetime] | None:
    if kind not in RANGE_OPTIONS:
        raise HTTPException(status_code=422, detail="时间范围不正确")
    if kind == "all":
        return None
    today = shanghai_now().date()
    start_day = today if kind == "today" else today - timedelta(days=int(kind))
    start = datetime.combine(start_day, time.min) - timedelta(hours=8)
    end = datetime.combine(today + timedelta(days=1), time.min) - timedelta(hours=8)
    return start, end


def _owns_submission(row: RequirementListSubmission, user: User) -> bool:
    return row.created_by == str(user.uid)


def _can_manage(row: RequirementListSubmission, user: User) -> bool:
    return user.role == "superadmin" or _owns_submission(row, user)


def _visible(stmt, user: User, *, employee_code: str | None = None):
    if user.role == "superadmin":
        return stmt
    uid = str(user.uid)
    shared = (
        select(RequirementListSubmissionShare.id)
        .where(
            RequirementListSubmissionShare.submission_id == RequirementListSubmission.id,
            RequirementListSubmissionShare.member_uid == uid,
        )
        .correlate(RequirementListSubmission)
        .exists()
    )
    clauses: list = [RequirementListSubmission.created_by == uid, shared]
    code = (employee_code or "").strip()
    if code:
        clauses.append(RequirementListSubmission.employee_code == code)
    return stmt.where(or_(*clauses))


async def _has_share_access(db: AsyncSession, row: RequirementListSubmission, user: User) -> bool:
    if user.role == "superadmin" or _owns_submission(row, user):
        return True
    found = await db.scalar(
        select(RequirementListSubmissionShare.id).where(
            RequirementListSubmissionShare.submission_id == row.id,
            RequirementListSubmissionShare.member_uid == str(user.uid),
        )
    )
    if found is not None:
        return True
    employee = await resolve_employee_for_user(db, user)
    return bool(
        employee
        and employee.employee_code
        and row.employee_code
        and row.employee_code == employee.employee_code
    )


def _member_rows(shares: list[RequirementListSubmissionShare]) -> list[dict[str, str]]:
    return [{"uid": item.member_uid, "name": item.member_name, "channel": "企微"} for item in shares]


async def _shares_by_submission(
    db: AsyncSession, submission_ids: list[str]
) -> dict[str, list[RequirementListSubmissionShare]]:
    if not submission_ids:
        return {}
    rows = (
        await db.scalars(
            select(RequirementListSubmissionShare)
            .where(RequirementListSubmissionShare.submission_id.in_(submission_ids))
            .order_by(RequirementListSubmissionShare.created_at.asc())
        )
    ).all()
    grouped: dict[str, list[RequirementListSubmissionShare]] = {}
    for item in rows:
        grouped.setdefault(item.submission_id, []).append(item)
    return grouped


def _filtered(filters: SubmissionFilters):
    stmt = select(RequirementListSubmission)
    keyword = filters.keyword.strip()
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            or_(
                RequirementListSubmission.requirement_type.ilike(like),
                RequirementListSubmission.customer_name.ilike(like),
                RequirementListSubmission.phone.ilike(like),
                RequirementListSubmission.address.ilike(like),
                RequirementListSubmission.sharer_name.ilike(like),
                RequirementListSubmission.submitter_name.ilike(like),
                RequirementListSubmission.employee_code.ilike(like),
                RequirementListSubmission.branch.ilike(like),
            )
        )
    if filters.branch and filters.branch != "all":
        stmt = stmt.where(RequirementListSubmission.branch == filters.branch.strip())
    submitted = _range_bounds(filters.submitted_range or "all")
    if submitted:
        stmt = stmt.where(
            RequirementListSubmission.submitted_at >= submitted[0],
            RequirementListSubmission.submitted_at < submitted[1],
        )
    shared = _range_bounds(filters.shared_range or "all")
    if shared:
        stmt = stmt.where(
            RequirementListSubmission.shared_at >= shared[0],
            RequirementListSubmission.shared_at < shared[1],
        )
    return stmt


def submission_data(
    row: RequirementListSubmission,
    *,
    reveal_phone: bool = False,
    with_answers: bool = False,
    user: User | None = None,
    shared_members: list[dict[str, str]] | None = None,
) -> dict:
    progress_value = completion_percent(
        dict(row.answers or {}),
        name=row.customer_name or "",
        phone=row.phone or "",
        address=row.address or "",
        requirement_type=row.requirement_type or "",
    )
    data = {
        "id": row.id,
        "requirement_type": row.requirement_type,
        "customer_name": row.customer_name,
        "phone": row.phone if reveal_phone else mask_phone(row.phone),
        "address": row.address,
        "sharer_name": row.sharer_name,
        "role": row.role,
        "employee_code": row.employee_code,
        "progress": f"{progress_value}%",
        "branch": row.branch,
        "shared_at": display_stamp(row.shared_at),
        "submitted_at": display_stamp(row.submitted_at),
        "submitter_name": row.submitter_name or "",
        "shared_members": shared_members if shared_members is not None else [],
    }
    if user is not None:
        data["can_share"] = _can_manage(row, user)
    if with_answers:
        data["answers"] = dict(row.answers or {})
    return data


async def create_submission(db: AsyncSession, user: User, payload: SubmissionCreate) -> dict:
    data = payload.normalized()
    employee = await resolve_employee_for_user(db, user)
    staff_code = data.staff_code or (employee.employee_code if employee else "")
    if not staff_code:
        raise HTTPException(status_code=422, detail="当前账号没有员工编码，无法提交需求清单")
    now = utc_now_naive()
    submitter_name = (employee.name if employee else "") or (user.username or "")
    row = RequirementListSubmission(
        id=f"rls_{uuid.uuid4().hex}",
        requirement_type=data.requirement_type,
        customer_name=data.customer_name,
        phone=data.phone,
        address=data.address,
        sharer_name=employee.name if employee else "",
        role=employee.role if employee else "",
        employee_code=staff_code,
        branch=employee.current_branch if employee else "",
        progress=completion_percent(
            data.answers,
            name=data.customer_name,
            phone=data.phone,
            address=data.address,
            requirement_type=data.requirement_type,
        ),
        answers=data.answers,
        shared_at=now,
        submitted_at=now,
        submitter_name=submitter_name,
        created_by=str(user.uid),
        created_at=now,
    )
    db.add(row)
    await db.flush()
    return submission_data(row, user=user)


async def _sharer_uid(db: AsyncSession, employee) -> str:
    phone = (employee.login_account or "").strip()
    if phone:
        found = await db.scalar(select(User.uid).where(User.phone_number == phone, User.is_deleted == 0))
        if found:
            return str(found)
    return f"mp_{employee.id.replace('-', '')[:17]}"


async def create_requirement_visitor_session(db: AsyncSession, payload: RequirementVisitorSessionCreate) -> dict:
    staff_code = payload.staff_code.strip()
    employee = await EmployeeRepository(db).get_by_code(staff_code)
    if employee is None or not employee.enabled:
        raise HTTPException(status_code=422, detail="分享人工号不存在")
    appid, secret = _wechat_credentials()
    if not appid or not secret:
        raise HTTPException(status_code=503, detail="微信服务未配置")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params={
                    "appid": appid,
                    "secret": secret,
                    "js_code": payload.code.strip(),
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            identity = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="微信身份服务暂时不可用，请稍后重试") from exc
    if identity.get("errcode") or not identity.get("openid"):
        raise HTTPException(status_code=422, detail="微信身份验证失败，请重新打开分享链接")
    session_id = uuid.uuid4().hex
    redis = await get_redis_client()
    await redis.setex(
        f"mp:rls:{session_id}",
        REQUIREMENT_VISITOR_SESSION_TTL,
        json.dumps({"appid": appid, "staff_code": staff_code, "openid": identity["openid"]}),
    )
    return {
        "session_id": session_id,
        "expires_in": REQUIREMENT_VISITOR_SESSION_TTL,
        "sharer_name": employee.name or "",
        "sharer_role": employee.role or "",
    }


async def _resolve_requirement_visitor_phone(
    *,
    visitor_session_id: str,
    phone_code: str,
    staff_code: str,
) -> str:
    staff_code = staff_code.strip()
    key = f"mp:rls:{visitor_session_id.strip()}"
    redis = await get_redis_client()
    raw = await redis.get(key)
    if raw is None:
        raise HTTPException(status_code=401, detail="微信授权会话已过期，请重新打开页面")
    session = json.loads(raw)
    appid, secret = _wechat_credentials()
    if session.get("staff_code") != staff_code or session.get("appid") != appid:
        raise HTTPException(status_code=403, detail="微信授权与分享员工不匹配")
    phone = str(session.get("phone") or "").strip()
    cached_code = session.get("phone_code")
    if not phone or cached_code != phone_code:
        try:
            phone = await _wechat_phone_by_code(appid, secret, phone_code)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail="微信手机号服务暂时不可用，请稍后重试") from exc
        session["phone"] = phone
        session["phone_code"] = phone_code
        await redis.setex(key, REQUIREMENT_VISITOR_SESSION_TTL, json.dumps(session))
    if not phone or not phone.isdigit() or len(phone) < 11:
        raise HTTPException(status_code=422, detail="未能获取有效手机号")
    return phone


def _guest_submitter_label(nickname: str, phone: str) -> str:
    nick = str(nickname or "").strip()
    digits = str(phone or "").strip()
    if nick and digits:
        return f"{nick} · {digits}"[:80]
    return (nick or digits)[:80]


async def create_mp_shared_submission(db: AsyncSession, payload: MpSharedSubmissionCreate) -> dict:
    """小程序访客分享：微信授权手机号 + 昵称写入提交人。"""
    nickname = payload.submitter_nickname.strip()
    visitor_phone = await _resolve_requirement_visitor_phone(
        visitor_session_id=payload.visitor_session_id,
        phone_code=payload.phone_code,
        staff_code=payload.staff_code,
    )
    base = SubmissionCreate(
        requirement_type=payload.requirement_type,
        customer_name=payload.customer_name,
        phone=visitor_phone,
        address=payload.address,
        staff_code=payload.staff_code,
        answers=payload.answers,
    )
    return await create_shared_submission(
        db, base, submitter_name=_guest_submitter_label(nickname, visitor_phone)
    )


async def create_shared_submission(
    db: AsyncSession,
    payload: SubmissionCreate,
    *,
    submitter_name: str | None = None,
) -> dict:
    """客户通过分享链接提交。分享人、岗位、工号和门店按链接中的员工固化。"""
    data = payload.normalized()
    if not data.staff_code:
        raise HTTPException(status_code=422, detail="分享链接缺少员工编码")
    employee = await EmployeeRepository(db).get_by_code(data.staff_code)
    if employee is None or not employee.enabled:
        raise HTTPException(status_code=422, detail="分享人工号不存在")
    now = utc_now_naive()
    row = RequirementListSubmission(
        id=f"rls_{uuid.uuid4().hex}",
        requirement_type=data.requirement_type,
        customer_name=data.customer_name,
        phone=data.phone,
        address=data.address,
        sharer_name=employee.name or "",
        role=employee.role or "",
        employee_code=employee.employee_code,
        branch=employee.current_branch or "",
        progress=completion_percent(
            data.answers,
            name=data.customer_name,
            phone=data.phone,
            address=data.address,
            requirement_type=data.requirement_type,
        ),
        answers=data.answers,
        shared_at=now,
        submitted_at=now,
        submitter_name=(submitter_name or data.customer_name or employee.name or "").strip(),
        created_by=await _sharer_uid(db, employee),
        created_at=now,
    )
    db.add(row)
    await db.flush()
    return submission_data(row)


async def list_mp_submissions(
    db: AsyncSession,
    user: User,
    *,
    page: int,
    page_size: int,
    keyword: str = "",
) -> dict:
    employee = await resolve_employee_for_user(db, user)
    filters = SubmissionFilters(keyword=keyword)
    staff_code = employee.employee_code if employee else None
    base = _visible(_filtered(filters), user, employee_code=staff_code)
    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    rows = (
        await db.scalars(
            base.order_by(RequirementListSubmission.submitted_at.desc(), RequirementListSubmission.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    share_map = await _shares_by_submission(db, [row.id for row in rows])
    return {
        "items": [
            submission_data(
                row,
                user=user,
                shared_members=_member_rows(share_map.get(row.id, [])),
            )
            for row in rows
        ],
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
    }


async def list_submissions(
    db: AsyncSession, user: User, page: int, page_size: int, filters: SubmissionFilters
) -> dict:
    employee = await resolve_employee_for_user(db, user)
    staff_code = employee.employee_code if employee else None
    base = _visible(_filtered(filters), user, employee_code=staff_code)
    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    rows = (
        await db.scalars(
            base.order_by(RequirementListSubmission.submitted_at.desc(), RequirementListSubmission.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    found = (
        await db.scalars(
            _visible(
                select(RequirementListSubmission.branch).where(RequirementListSubmission.branch != ""),
                user,
                employee_code=staff_code,
            )
            .distinct()
            .order_by(RequirementListSubmission.branch.asc())
        )
    ).all()
    branches = list(STORE_OPTIONS) + [item for item in found if item not in STORE_OPTIONS]
    share_map = await _shares_by_submission(db, [row.id for row in rows])
    return {
        "items": [
            submission_data(
                row,
                user=user,
                shared_members=_member_rows(share_map.get(row.id, [])),
            )
            for row in rows
        ],
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
        "branches": branches,
    }


class ShareMembersPayload(BaseModel):
    employee_ids: list[str] = Field(min_length=1, max_length=30)

    @field_validator("employee_ids")
    @classmethod
    def normalize_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item and item.strip()]
        if not normalized:
            raise ValueError("请至少选择一位员工")
        return list(dict.fromkeys(normalized))


async def list_share_employees(db: AsyncSession, *, keyword: str = "") -> dict:
    rows = await EmployeeRepository(db).list_employees(keyword=keyword or None)
    items = []
    for employee in rows[:50]:
        if not employee.enabled:
            continue
        items.append(
            {
                "id": employee.id,
                "name": employee.name,
                "employee_code": employee.employee_code,
                "branch": employee.current_branch,
            }
        )
    return {"items": items}


async def share_submission(db: AsyncSession, user: User, submission_id: str, payload: ShareMembersPayload) -> dict:
    row = await db.get(RequirementListSubmission, submission_id)
    if row is None or not _can_manage(row, user):
        raise HTTPException(status_code=404, detail="需求清单不存在")
    repo = EmployeeRepository(db)
    owner_uid = str(user.uid)
    for employee_id in payload.employee_ids:
        employee = await repo.get(employee_id)
        if employee is None or not employee.enabled:
            raise HTTPException(status_code=422, detail="所选员工不存在或已禁用")
        member_uid = await _sharer_uid(db, employee)
        if member_uid == row.created_by:
            continue
        exists = await db.scalar(
            select(RequirementListSubmissionShare.id).where(
                RequirementListSubmissionShare.submission_id == row.id,
                RequirementListSubmissionShare.member_uid == member_uid,
            )
        )
        if exists is not None:
            continue
        db.add(
            RequirementListSubmissionShare(
                id=f"rlss_{uuid.uuid4().hex}",
                submission_id=row.id,
                member_uid=member_uid,
                member_name=employee.name or "",
                shared_by_uid=owner_uid,
                created_at=utc_now_naive(),
            )
        )
    await db.flush()
    shares = (
        await db.scalars(
            select(RequirementListSubmissionShare)
            .where(RequirementListSubmissionShare.submission_id == row.id)
            .order_by(RequirementListSubmissionShare.created_at.asc())
        )
    ).all()
    return submission_data(row, user=user, reveal_phone=True, shared_members=_member_rows(list(shares)))


async def get_submission(db: AsyncSession, submission_id: str, user: User | None = None) -> dict:
    row = await db.get(RequirementListSubmission, submission_id)
    if row is None:
        raise HTTPException(status_code=404, detail="需求清单不存在")
    if user is not None and not await _has_share_access(db, row, user):
        raise HTTPException(status_code=404, detail="需求清单不存在")
    shares = (
        await db.scalars(
            select(RequirementListSubmissionShare)
            .where(RequirementListSubmissionShare.submission_id == row.id)
            .order_by(RequirementListSubmissionShare.created_at.asc())
        )
    ).all()
    return submission_data(
        row,
        reveal_phone=True,
        with_answers=True,
        user=user,
        shared_members=_member_rows(list(shares)),
    )


async def delete_submission(db: AsyncSession, submission_id: str, user: User) -> dict:
    row = await db.get(RequirementListSubmission, submission_id)
    if row is None or not _can_manage(row, user):
        raise HTTPException(status_code=404, detail="需求清单不存在")
    await db.delete(row)
    return {"id": submission_id}


async def export_submissions(db: AsyncSession, user: User, filters: SubmissionFilters) -> bytes:
    employee = await resolve_employee_for_user(db, user)
    staff_code = employee.employee_code if employee else None
    rows = (
        await db.scalars(
            _visible(_filtered(filters), user, employee_code=staff_code).order_by(
                RequirementListSubmission.submitted_at.desc(), RequirementListSubmission.id.desc()
            )
        )
    ).all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    header = [
        "序",
        "需求清单类型",
        "客户姓名",
        "手机号码",
        "楼盘地址",
        "分享人",
        "岗位",
        "工号",
        "完成度",
        "所属门店",
        "分享时间",
        "提交时间",
        "提交人",
        "共享成员",
    ]
    writer.writerow(header)
    share_map = await _shares_by_submission(db, [row.id for row in rows])
    for index, row in enumerate(rows, start=1):
        item = submission_data(row, reveal_phone=True, shared_members=_member_rows(share_map.get(row.id, [])))
        members = "、".join(member["name"] for member in item["shared_members"] if member.get("name")) or "未共享"
        writer.writerow(
            [
                index,
                item["requirement_type"],
                item["customer_name"],
                item["phone"],
                item["address"],
                item["sharer_name"],
                item["role"],
                item["employee_code"],
                item["progress"],
                item["branch"],
                item["shared_at"],
                item["submitted_at"],
                item["submitter_name"],
                members,
            ]
        )
    return buffer.getvalue().encode("utf-8-sig")
