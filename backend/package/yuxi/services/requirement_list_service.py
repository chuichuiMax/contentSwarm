"""需求清单提交记录。"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, time, timedelta

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.employee_repository import EmployeeRepository
from yuxi.services.employee_service import resolve_employee_for_user
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import RequirementListSubmission
from yuxi.utils.datetime_utils import SHANGHAI_TZ, UTC, shanghai_now, utc_now_naive

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
    return value.replace(tzinfo=UTC).astimezone(SHANGHAI_TZ).strftime("%Y%m%d %H:%M")


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


def _can_see(row: RequirementListSubmission, user: User) -> bool:
    return user.role == "superadmin" or row.created_by == str(user.uid)


def _visible(stmt, user: User):
    if user.role == "superadmin":
        return stmt
    return stmt.where(RequirementListSubmission.created_by == str(user.uid))


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


def submission_data(row: RequirementListSubmission, *, reveal_phone: bool = False, with_answers: bool = False) -> dict:
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
    }
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
        created_by=str(user.uid),
        created_at=now,
    )
    db.add(row)
    await db.flush()
    return submission_data(row)


async def _sharer_uid(db: AsyncSession, employee) -> str:
    phone = (employee.login_account or "").strip()
    if phone:
        found = await db.scalar(select(User.uid).where(User.phone_number == phone, User.is_deleted == 0))
        if found:
            return str(found)
    return f"mp_{employee.id.replace('-', '')[:17]}"


async def create_shared_submission(db: AsyncSession, payload: SubmissionCreate) -> dict:
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
    base = _filtered(filters)
    uid = str(user.uid)
    if employee and employee.employee_code:
        base = base.where(
            or_(
                RequirementListSubmission.created_by == uid,
                RequirementListSubmission.employee_code == employee.employee_code,
            )
        )
    else:
        base = base.where(RequirementListSubmission.created_by == uid)
    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    rows = (
        await db.scalars(
            base.order_by(RequirementListSubmission.submitted_at.desc(), RequirementListSubmission.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [submission_data(row) for row in rows],
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
    }


async def list_submissions(
    db: AsyncSession, user: User, page: int, page_size: int, filters: SubmissionFilters
) -> dict:
    base = _visible(_filtered(filters), user)
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
            )
            .distinct()
            .order_by(RequirementListSubmission.branch.asc())
        )
    ).all()
    branches = list(STORE_OPTIONS) + [item for item in found if item not in STORE_OPTIONS]
    return {
        "items": [submission_data(row) for row in rows],
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
        "branches": branches,
    }


async def get_submission(db: AsyncSession, submission_id: str, user: User | None = None) -> dict:
    row = await db.get(RequirementListSubmission, submission_id)
    if row is None or (user is not None and not _can_see(row, user)):
        raise HTTPException(status_code=404, detail="需求清单不存在")
    return submission_data(row, reveal_phone=True, with_answers=True)


async def delete_submission(db: AsyncSession, submission_id: str, user: User) -> dict:
    row = await db.get(RequirementListSubmission, submission_id)
    if row is None or not _can_see(row, user):
        raise HTTPException(status_code=404, detail="需求清单不存在")
    await db.delete(row)
    return {"id": submission_id}


async def export_submissions(db: AsyncSession, user: User, filters: SubmissionFilters) -> bytes:
    rows = (
        await db.scalars(
            _visible(_filtered(filters), user).order_by(
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
    ]
    writer.writerow(header)
    for index, row in enumerate(rows, start=1):
        item = submission_data(row, reveal_phone=True)
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
            ]
        )
    return buffer.getvalue().encode("utf-8-sig")
