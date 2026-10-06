from __future__ import annotations

import base64
import json
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from fastapi import HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content.catalog import CONTENT_TYPES
from yuxi.content.generation import DEFAULT_DIRECT_GENERATION_PROMPT
from yuxi.content.mp_studio_direct import MP_DECORATION_DIRECT_UI, build_mp_decoration_direct_defaults
from yuxi.content.mp_workflow import MP_REVIEW_NOTES_ENTRY, MP_REVIEW_NOTES_UI, mp_service_entry_from_brief
from yuxi.content.schemas import (
    ContentBriefPayload,
    ContentDirectGenerateCreate,
    ContentRunCreate,
    ContentRunResume,
    ContentTaskCreate,
    ContentVisualMaterialSelection,
)
from yuxi.content.service_entry_form import (
    BRAND_NAME,
    CONTENT_TYPE_NAME_TO_DIRECTION,
    FIELD_SELECT_OPTIONS,
    catalog_select_options,
    configured_business_variable_fields,
    map_service_entry_form_values,
)
from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.repositories.content_repository import ContentRepository
from yuxi.repositories.cover_repository import CoverRepository
from yuxi.repositories.employee_repository import EmployeeRepository
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.agent_run_service import stream_agent_run_events
from yuxi.services.business_variable_service import list_business_variables
from yuxi.services.content_cover_service import get_cover_asset_file, serialize_asset
from yuxi.services.content_service import (
    create_content_run,
    create_content_task,
    create_direct_content_run,
    delete_content_task,
    duplicate_content_task,
    get_content_run,
    get_content_task,
    get_task_artifact,
    resume_content_run,
    retry_content_node,
    save_content_brief,
)
from yuxi.services.content_type_service import ensure_default_content_types, list_content_types
from yuxi.services.content_viral_assets import list_viral_assets
from yuxi.services.material_library_service import (
    delete_material_item,
    get_material_file,
    get_material_thumbnail,
    import_material_images,
    list_image_galleries,
    list_material_items,
)
from yuxi.services.mp_works_logic import visible_work_items
from yuxi.services.process_standard_service import (
    list_enabled_process_names_by_type,
    list_enabled_process_type_names,
)
from yuxi.services.resident_population_service import list_enabled_resident_population_names
from yuxi.services.run_queue_service import get_redis_client, list_run_stream_events
from yuxi.services.target_audience_service import list_enabled_target_audience_names
from yuxi.services.user_identity_service import is_valid_phone_number, normalize_phone_number
from yuxi.services.variable_service import SERVICE_ENTRIES, ensure_default_variables
from yuxi.storage.minio.client import StorageError, get_minio_client
from yuxi.storage.postgres.models_business import Department, User
from yuxi.storage.postgres.models_content import ContentArtifact, ContentEmployee, ContentMpFavorite, ContentTask
from yuxi.utils.auth_utils import AuthUtils
from yuxi.utils.datetime_utils import SHANGHAI_TZ, ensure_utc, format_utc_datetime, shanghai_now, utc_now_naive

INDUSTRY_SLUG = "decoration"
SMS_TTL_SECONDS = 300
WECHAT_SESSION_TTL_SECONDS = 600
NRLX_TO_CT = {
    "NRLX0001": "CT06",  # 工艺施工展示 → 工厂工艺展示方向（非 CT05 施工报价）
    "NRLX0002": "CT02",
    "NRLX0003": "CT03",
    "NRLX0004": "CT04",
    "NRLX0005": "CT01",
    "NRLX0006": "CT06",
    "NRLX0007": "CT07",
}
NAME_TO_CT = CONTENT_TYPE_NAME_TO_DIRECTION
FRAME_AREA_PRICING: tuple[dict[str, Any], ...] = (
    {"value": "50-70㎡", "label": "50-70㎡", "min": 50, "max": 70, "quotes": {"基础": "4-5万", "木制品": "2-3万", "主材": "2-3万"}},
    {"value": "90-110㎡", "label": "90-110㎡", "min": 90, "max": 110, "quotes": {"基础": "7-8万", "木制品": "3-4万", "主材": "4-5万"}},
    {"value": "110-130㎡", "label": "110-130㎡", "min": 110, "max": 130, "quotes": {"基础": "9-11万", "木制品": "4-5万", "主材": "5-6万"}},
    {"value": "130-150㎡", "label": "130-150㎡", "min": 130, "max": 150, "quotes": {"基础": "11-12万", "木制品": "5-6万", "主材": "6-7万"}},
    {"value": "150-200㎡", "label": "150-200㎡", "min": 150, "max": 200, "quotes": {"基础": "15-18万", "木制品": "6-8万", "主材": "7-8万"}},
    {"value": "200-300㎡", "label": "200-300㎡", "min": 200, "max": 300, "quotes": {"基础": "20-30万", "木制品": "9-11万", "主材": "8-10万"}},
    {
        "value": "300㎡以上",
        "label": "300㎡以上",
        "min": 300,
        "max": None,
        "quotes": {"基础": "30万以上", "木制品": "11万以上", "主材": "16万以上"},
    },
)
_AREA_SQM = re.compile(r"^(\d+(?:\.\d+)?)\s*(?:㎡|m²|m2|平米|平)?$", re.I)
_QUOTE_RANGE = re.compile(r"^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)万$")
_QUOTE_FLOOR = re.compile(r"^(\d+(?:\.\d+)?)万以上$")
_HYCANVAS_TEMPLATE_ID = re.compile(
    r"^(?:(?:xiaohongshu|system-cover)-[a-z0-9-]+|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$"
)
_OPEN_QUOTE_SPAN = 10
DESIGN_STYLES: tuple[str, ...] = tuple(FIELD_SELECT_OPTIONS["设计风格"])
REGION_TREE: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "长沙市",
        ("芙蓉区", "天心区", "岳麓区", "开福区", "雨花区", "望城区", "长沙县", "浏阳市", "宁乡市"),
    ),
    (
        "株洲市",
        ("茶陵县", "荷塘区", "渌口区", "芦淞区", "醴陵市", "石峰区", "天元区", "云龙示范区", "攸县", "炎陵县"),
    ),
    ("湘潭市", ("雨湖区", "岳塘区", "湘潭县", "湘乡市", "韶山市")),
    (
        "衡阳市",
        (
            "珠晖区",
            "雁峰区",
            "石鼓区",
            "蒸湘区",
            "南岳区",
            "衡阳县",
            "衡南县",
            "衡山县",
            "衡东县",
            "祁东县",
            "耒阳市",
            "常宁市",
        ),
    ),
    (
        "邵阳市",
        (
            "双清区",
            "大祥区",
            "北塔区",
            "邵东市",
            "新邵县",
            "邵阳县",
            "隆回县",
            "洞口县",
            "绥宁县",
            "新宁县",
            "城步苗族自治县",
            "武冈市",
        ),
    ),
    (
        "岳阳市",
        ("岳阳楼区", "云溪区", "君山区", "岳阳县", "华容县", "湘阴县", "平江县", "汨罗市", "临湘市"),
    ),
    (
        "常德市",
        ("武陵区", "鼎城区", "安乡县", "汉寿县", "澧县", "临澧县", "桃源县", "石门县", "津市市"),
    ),
    ("张家界市", ("永定区", "武陵源区", "慈利县", "桑植县")),
    ("益阳市", ("资阳区", "赫山区", "南县", "桃江县", "安化县", "沅江市")),
    (
        "郴州市",
        (
            "北湖区",
            "苏仙区",
            "桂阳县",
            "宜章县",
            "永兴县",
            "嘉禾县",
            "临武县",
            "汝城县",
            "桂东县",
            "安仁县",
            "资兴市",
        ),
    ),
    (
        "永州市",
        (
            "零陵区",
            "冷水滩区",
            "东安县",
            "双牌县",
            "道县",
            "江永县",
            "宁远县",
            "蓝山县",
            "新田县",
            "江华瑶族自治县",
            "祁阳市",
        ),
    ),
    (
        "怀化市",
        (
            "鹤城区",
            "中方县",
            "沅陵县",
            "辰溪县",
            "溆浦县",
            "会同县",
            "麻阳苗族自治县",
            "新晃侗族自治县",
            "芷江侗族自治县",
            "靖州苗族侗族自治县",
            "通道侗族自治县",
            "洪江市",
        ),
    ),
    ("娄底市", ("娄星区", "双峰县", "新化县", "冷水江市", "涟源市")),
    (
        "湘西土家族苗族自治州",
        ("吉首市", "泸溪县", "凤凰县", "花垣县", "保靖县", "古丈县", "永顺县", "龙山县"),
    ),
)
REGIONS: tuple[str, ...] = tuple(city for city, _ in REGION_TREE)
ServiceEntry = Literal["装修家居", "好评笔记"]


class SmsSendPayload(BaseModel):
    phone: str = Field(min_length=11, max_length=20)


class SmsLoginPayload(BaseModel):
    phone: str = Field(min_length=11, max_length=20)
    code: str = Field(min_length=4, max_length=8)


class WechatCodePayload(BaseModel):
    code: str = Field(min_length=1, max_length=128)


class WechatPhonePayload(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    code: str | None = None
    encrypted_data: str | None = None
    iv: str | None = None
    phone: str | None = None


class AuthConfirmPayload(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    phone: str | None = None
    avatar: str | None = Field(default=None, max_length=1024)


class AuthCancelPayload(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)


class MeUpdatePayload(BaseModel):
    bio: str = Field(default="", max_length=200)


class MpCompileBriefPayload(BaseModel):
    service_entry: ServiceEntry
    content_type_code: str | None = None
    form_values: dict[str, Any] = Field(default_factory=dict)
    cover_asset_id: str | None = Field(default=None, max_length=64)
    cover_asset_ids: list[str] = Field(default_factory=list, max_length=3)
    cover_template_id: str | None = None
    cover_mode: Literal["builtin", "ai"] | None = None
    hycanvas_template_id: str | None = None
    image_item_id: str | None = Field(default=None, max_length=64)


class MpRunCreatePayload(BaseModel):
    request_id: str | None = None
    model_spec: str | None = None


class MpRunResumePayload(BaseModel):
    request_id: str | None = None
    resume: dict[str, Any]


class MpRunRetryPayload(BaseModel):
    request_id: str | None = None
    node_id: str | None = None


@dataclass
class MpContext:
    employee: ContentEmployee
    user: User


def _mp_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


def _is_production() -> bool:
    return os.environ.get("YUXI_ENV", "development").strip().lower() in {"prod", "production"}


def _require_phone(phone: str) -> str:
    normalized = normalize_phone_number(phone)
    if not is_valid_phone_number(normalized):
        raise _mp_error(422, "MP_PHONE_INVALID", "手机号格式不正确")
    return normalized


def mask_phone(phone: str) -> str:
    if len(phone) < 7:
        return phone
    return f"{phone[:3]}****{phone[-4:]}"


def _wechat_credentials() -> tuple[str, str]:
    return os.environ.get("WECHAT_MP_APPID", "").strip(), os.environ.get("WECHAT_MP_SECRET", "").strip()


async def require_app_employee(db: AsyncSession, phone: str) -> ContentEmployee:
    employee = await EmployeeRepository(db).get_by_login_account(phone)
    if employee is None:
        raise _mp_error(404, "EMPLOYEE_NOT_FOUND", "未找到对应员工账号")
    if not employee.enabled:
        raise _mp_error(403, "EMPLOYEE_DISABLED", "员工已停用")
    if "app" not in (employee.login_port or []):
        raise _mp_error(403, "EMPLOYEE_APP_FORBIDDEN", "该员工未开通小程序登录")
    return employee


def map_nrlx_to_ct_code(type_code: str | None, name: str | None) -> str:
    if type_code and type_code in NRLX_TO_CT:
        return NRLX_TO_CT[type_code]
    if name and name.strip() in NAME_TO_CT:
        return NAME_TO_CT[name.strip()]
    raise _mp_error(422, "MP_CONTENT_TYPE_UNMAPPED", "内容类型无法映射到生产方向")


def resolve_content_goal(service_entry: str, ct_code: str) -> str:
    supported = next((item["supported_goals"] for item in CONTENT_TYPES if item["code"] == ct_code), ["acquire"])
    # 好评笔记是业主评价项目成员的口碑记录，禁止落入获客转化目标。
    preferred = ("brand", "educate") if service_entry == "好评笔记" else ("acquire", "educate", "brand")
    for goal in preferred:
        if goal in supported:
            return goal
    return next((item for item in supported if item != "acquire"), supported[0])


def _format_wan(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value)}万"
    return f"{value}万"


def expand_quote_range(spec: str) -> tuple[str, ...]:
    text = spec.strip()
    ranged = _QUOTE_RANGE.fullmatch(text)
    if ranged:
        return _discrete_quote_values(float(ranged.group(1)), float(ranged.group(2)))
    floor = _QUOTE_FLOOR.fullmatch(text)
    if floor:
        low = float(floor.group(1))
        return _discrete_quote_values(low, low + _OPEN_QUOTE_SPAN)
    raise _mp_error(422, "MP_QUOTE_RANGE_INVALID", f"报价范围无法展开：{spec}")


def _discrete_quote_values(low: float, high: float) -> tuple[str, ...]:
    values = [low]
    half = low + 0.5
    if half <= high:
        values.append(half)
    integer = int(low) + 1
    while integer <= high:
        values.append(float(integer))
        integer += 1
    return tuple(_format_wan(value) for value in values)


def _frame_area_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "value": item["value"],
        "label": item["label"],
        "min": item["min"],
        "max": item["max"],
        "quotes": dict(item["quotes"]),
        "quote_choices": {key: list(expand_quote_range(value)) for key, value in item["quotes"].items()},
    }


def match_frame_area_pricing(frame_area: str) -> dict[str, Any] | None:
    raw = str(frame_area or "").strip()
    for item in FRAME_AREA_PRICING:
        if item["value"] == raw:
            return item
    matched = _AREA_SQM.fullmatch(raw)
    if matched is None:
        return None
    number = float(matched.group(1))
    hits = [
        item
        for item in FRAME_AREA_PRICING
        if number >= item["min"] and (item["max"] is None or number <= item["max"])
    ]
    if not hits:
        return None
    return max(hits, key=lambda item: item["min"])


def lookup_frame_area_pricing(frame_area: str) -> dict[str, Any]:
    item = match_frame_area_pricing(frame_area)
    if item is None:
        raise _mp_error(422, "MP_FRAME_AREA_INVALID", "外框面积不在报价范围内")
    return item


def next_mp_content_code(existing_codes: list[str], *, day: str) -> str:
    prefix = f"NR{day}"
    max_seq = 0
    for code in existing_codes:
        if code.startswith(prefix) and code[len(prefix) :].isdigit():
            max_seq = max(max_seq, int(code[len(prefix) :]))
    return f"{prefix}{max_seq + 1:03d}"


def _cover_asset_ids(cover_asset_id: str, extra: list[str] | None = None) -> list[str]:
    ids = [cover_asset_id]
    for item in extra or []:
        if item and item not in ids:
            ids.append(item)
    if len(ids) > 3:
        raise _mp_error(422, "MP_COVER_LIMIT", "最多上传3张图片")
    return ids


def build_mp_brief_payload(
    *,
    service_entry: str,
    form_values: dict[str, Any],
    content_type_name: str,
    content_type_id: str | None,
    cover_asset_id: str | None,
    cover_template_id: str | None,
    content_code: str,
    cover_asset_ids: list[str] | None = None,
    visual_material: ContentVisualMaterialSelection | None = None,
    user_request: str = "",
    content_type_code: str | None = None,
) -> ContentBriefPayload:
    values = {str(key): value for key, value in form_values.items()}
    values["mp_service_entry"] = service_entry
    values["mp_content_code"] = content_code
    values["mp_content_type_id"] = content_type_id or ""
    values["mp_content_type_name"] = content_type_name
    photo_ids: list[str] = []
    if service_entry != "好评笔记" and str(cover_asset_id or "").strip():
        photo_ids = _cover_asset_ids(str(cover_asset_id).strip(), cover_asset_ids)
        values["cover_asset_id"] = photo_ids[0]
        values["cover_asset_ids"] = photo_ids
    else:
        values.pop("cover_asset_id", None)
        values.pop("cover_asset_ids", None)
    if cover_template_id:
        values["cover_template_id"] = cover_template_id
    if visual_material is not None:
        values["hycanvas_template_id"] = visual_material.hycanvas_template_id

    mapped = map_service_entry_form_values(service_entry, values)
    persona_text = str(mapped.get("persona") or "")
    audience = mapped.get("audience") or []
    business_variables = {"persona_fact": persona_text} if persona_text else {}
    return ContentBriefPayload(
        user_request=str(user_request or mapped.get("user_request") or "").strip(),
        content_type_code=content_type_code,
        brand={"name": BRAND_NAME},
        audience=audience,
        business_variables=business_variables,
        form_values=mapped,
        attachments=[
            {"asset_id": item, "role": "cover" if index == 0 else "photo"} for index, item in enumerate(photo_ids)
        ],
        visual_material=visual_material,
    )


def _mp_direct_run_response(direct: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": direct["run_id"],
        "task_id": direct["task_id"],
        "status": direct["status"],
        "request_id": direct["request_id"],
        "error_message": None,
        "interrupt": None,
        "stream_url": f"/api/mp/content/runs/{direct['run_id']}/events",
    }


def _extract_interrupt(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("event_type") != "interrupt":
            continue
        envelope = event.get("payload") or {}
        inner = envelope.get("payload") if isinstance(envelope, dict) else {}
        return inner if isinstance(inner, dict) else envelope if isinstance(envelope, dict) else None
    return None


def _compact_run(run_result: dict[str, Any], interrupt: dict[str, Any] | None) -> dict[str, Any]:
    run = run_result["run"]
    return {
        "run_id": run["id"],
        "task_id": run["thread_id"],
        "status": run["status"],
        "request_id": run["request_id"],
        "error_message": run.get("error_message"),
        "interrupt": interrupt,
        "stream_url": f"/api/mp/content/runs/{run['id']}/events",
    }


async def _backfill_cover_selection_assets(
    db: AsyncSession,
    *,
    task_id: str,
    owner_uid: str,
    interrupt: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not interrupt or interrupt.get("interrupt_type") != "cover_selection":
        return interrupt
    existing = [str(item) for item in (interrupt.get("asset_ids") or []) if item]
    if existing:
        return interrupt
    jobs, _ = await ContentCoverRepository(db).list_jobs(
        owner_uid,
        content_task_id=task_id,
        page=1,
        page_size=10,
    )
    for job in jobs:
        if job.status != "succeeded":
            continue
        asset_ids = [str(item) for item in ((job.result_json or {}).get("asset_ids") or []) if item]
        if asset_ids:
            return {
                **interrupt,
                "asset_ids": asset_ids,
                "cover_job_id": interrupt.get("cover_job_id") or job.id,
            }
    return interrupt


def _cover_auto_resume_payload(interrupt: dict[str, Any] | None, run_id: str) -> dict[str, Any] | None:
    if not interrupt or interrupt.get("interrupt_type") != "cover_selection":
        return None
    asset_ids = [str(item) for item in (interrupt.get("asset_ids") or []) if item]
    version = interrupt.get("expected_state_version")
    if not asset_ids or version is None:
        return None
    return {
        "run_id": str(interrupt.get("run_id") or run_id),
        "node_id": str(interrupt.get("node_id") or "select_cover"),
        "expected_state_version": version,
        "asset_id": asset_ids[0],
    }


def _content_approval_auto_resume_payload(interrupt: dict[str, Any] | None, run_id: str) -> dict[str, Any] | None:
    if not interrupt or interrupt.get("interrupt_type") != "content_approval":
        return None
    version = interrupt.get("expected_state_version")
    if version is None:
        return None
    return {
        "run_id": str(interrupt.get("run_id") or run_id),
        "node_id": str(interrupt.get("node_id") or "human_content_approval"),
        "expected_state_version": version,
        "decision": "approved",
        "note": "小程序自动审批",
    }


def _continuation_runs(run_result: dict[str, Any]) -> list[dict[str, Any]]:
    items = [item for item in (run_result.get("continuations") or []) if isinstance(item, dict) and item.get("id")]
    return items or [run_result["run"]]


def _visible_mp_run(run_result: dict[str, Any], *, task_status: str | None) -> dict[str, Any]:
    """封面续跑会换新 run；轮询旧 run 时仍要跟到最新续跑，成品已保存则视为 completed。"""
    requested = run_result["run"]
    runs = _continuation_runs(run_result)
    latest = runs[-1]
    if str(task_status or "") in {"reviewed", "completed"}:
        done = next((item for item in reversed(runs) if item.get("status") == "completed"), latest)
        return {
            **done,
            "status": "completed",
            "thread_id": requested.get("thread_id") or done.get("thread_id"),
        }
    return latest


async def authenticate_mp_request(
    db: AsyncSession, authorization: str | None, *, access_token: str | None = None
) -> MpContext:
    token = ""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split("Bearer ", 1)[1].strip()
    elif access_token:
        token = access_token.strip()
    if not token:
        raise _mp_error(401, "MP_UNAUTHORIZED", "请登录后再访问")
    try:
        payload = AuthUtils.verify_access_token(token)
    except ValueError as exc:
        raise _mp_error(401, "MP_UNAUTHORIZED", str(exc)) from exc
    if payload.get("typ") != "mp":
        raise _mp_error(401, "MP_UNAUTHORIZED", "无效的小程序凭证")
    employee_id = payload.get("sub")
    if not employee_id:
        raise _mp_error(401, "MP_UNAUTHORIZED", "无效的小程序凭证")
    employee = await EmployeeRepository(db).get(str(employee_id))
    if employee is None or not employee.enabled or "app" not in (employee.login_port or []):
        raise _mp_error(401, "MP_UNAUTHORIZED", "员工账号不可用")
    user = await ensure_platform_user(db, employee)
    return MpContext(employee=employee, user=user)


async def ensure_platform_user(db: AsyncSession, employee: ContentEmployee) -> User:
    phone = employee.login_account
    result = await db.execute(select(User).where(User.phone_number == phone, User.is_deleted == 0))
    user = result.scalar_one_or_none()
    if user is not None:
        return user
    uid = f"mp_{employee.id.replace('-', '')[:17]}"
    existing_uid = await db.execute(select(User).where(User.uid == uid, User.is_deleted == 0))
    user = existing_uid.scalar_one_or_none()
    if user is not None:
        if not user.phone_number:
            user.phone_number = phone
            await db.flush()
        return user
    creator = await db.execute(select(User).where(User.uid == employee.created_by, User.is_deleted == 0))
    creator_user = creator.scalar_one_or_none()
    department_id = creator_user.department_id if creator_user else None
    if department_id is None:
        dept = await db.execute(select(Department).order_by(Department.id.asc()).limit(1))
        department = dept.scalar_one_or_none()
        if department is None:
            raise _mp_error(503, "MP_DEPARTMENT_MISSING", "系统尚未初始化部门")
        department_id = department.id
    username = (employee.name or "员工")[:20]
    clash = await db.execute(select(User.id).where(User.username == username))
    if clash.scalar_one_or_none() is not None:
        username = f"mp{phone[-8:]}"
    user = User(
        username=username,
        uid=uid,
        phone_number=phone,
        password_hash=AuthUtils.hash_password(secrets.token_urlsafe(32)),
        role=employee.role,
        department_id=department_id,
    )
    db.add(user)
    await db.flush()
    return user


async def _issue_token(db: AsyncSession, employee: ContentEmployee) -> dict[str, Any]:
    employee = await require_app_employee(db, employee.login_account)
    employee.last_login_at = utc_now_naive()
    user = await ensure_platform_user(db, employee)
    token = AuthUtils.create_access_token({"sub": employee.id, "typ": "mp", "uid": user.uid})
    await db.commit()
    return {"access_token": token, "token_type": "bearer", "employee": _me_dict(employee)}


def _me_dict(employee: ContentEmployee) -> dict[str, Any]:
    data = employee.to_dict()
    return {
        "id": data["id"],
        "name": data["name"],
        "login_account": data["login_account"],
        "role": data["role"],
        "avatar": data["avatar"],
        "bio": data["bio"],
        "last_login_at": data["last_login_at"],
        "gender": data["gender"],
    }


async def send_sms_code(db: AsyncSession, phone: str) -> dict[str, Any]:
    normalized = _require_phone(phone)
    await require_app_employee(db, normalized)
    if _is_production() and not os.environ.get("SMS_PROVIDER", "").strip():
        raise _mp_error(503, "SMS_NOT_CONFIGURED", "短信服务未配置")
    code = f"{secrets.randbelow(1_000_000):06d}"
    redis = await get_redis_client()
    await redis.setex(f"mp:sms:{normalized}", SMS_TTL_SECONDS, code)
    payload: dict[str, Any] = {"sent": True, "expires_in": SMS_TTL_SECONDS}
    if not _is_production():
        payload["debug_code"] = code
    return payload


async def login_by_sms(db: AsyncSession, payload: SmsLoginPayload) -> dict[str, Any]:
    phone = _require_phone(payload.phone)
    redis = await get_redis_client()
    stored = await redis.get(f"mp:sms:{phone}")
    if stored is None:
        raise _mp_error(422, "SMS_CODE_EXPIRED", "验证码无效或已过期")
    expected = stored.decode() if isinstance(stored, bytes) else str(stored)
    if expected != payload.code.strip():
        raise _mp_error(422, "SMS_CODE_INVALID", "验证码错误")
    await redis.delete(f"mp:sms:{phone}")
    employee = await require_app_employee(db, phone)
    return await _issue_token(db, employee)


_WECHAT_ACCESS_TOKEN_INVALID = {40001, 40014, 42001}


async def _invalidate_wechat_access_token() -> None:
    redis = await get_redis_client()
    await redis.delete("mp:wx:access_token")


async def _wechat_access_token(appid: str, secret: str, *, force_refresh: bool = False) -> str:
    redis = await get_redis_client()
    if not force_refresh:
        cached = await redis.get("mp:wx:access_token")
        if cached:
            return cached.decode() if isinstance(cached, bytes) else str(cached)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            "https://api.weixin.qq.com/cgi-bin/stable_token",
            json={
                "grant_type": "client_credential",
                "appid": appid,
                "secret": secret,
                "force_refresh": force_refresh,
            },
        )
    body = response.json()
    token = body.get("access_token")
    if not token:
        raise _mp_error(503, "WECHAT_TOKEN_FAILED", body.get("errmsg") or "获取微信凭证失败")
    expires = max(int(body.get("expires_in") or 7200) - 200, 60)
    await redis.setex("mp:wx:access_token", expires, token)
    return token


async def _wechat_phone_by_code(appid: str, secret: str, phone_code: str) -> str:
    token = await _wechat_access_token(appid, secret)
    body = await _request_wechat_phone_number(token, phone_code)
    errcode = int(body.get("errcode") or 0)
    if errcode in _WECHAT_ACCESS_TOKEN_INVALID:
        await _invalidate_wechat_access_token()
        token = await _wechat_access_token(appid, secret, force_refresh=True)
        body = await _request_wechat_phone_number(token, phone_code)
        errcode = int(body.get("errcode") or 0)
    if errcode:
        raise _mp_error(422, "WECHAT_PHONE_INVALID", body.get("errmsg") or "微信手机号授权失败")
    info = body.get("phone_info") or {}
    phone = info.get("purePhoneNumber") or info.get("phoneNumber")
    if not phone:
        raise _mp_error(422, "WECHAT_PHONE_REQUIRED", "微信未返回手机号")
    return _require_phone(str(phone))


async def _request_wechat_phone_number(access_token: str, phone_code: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            "https://api.weixin.qq.com/wxa/business/getuserphonenumber",
            params={"access_token": access_token},
            json={"code": phone_code},
        )
    return response.json()


def _decrypt_wechat_phone(session_key: str, encrypted_data: str, iv: str) -> str:
    try:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise _mp_error(503, "WECHAT_NOT_CONFIGURED", "微信手机号解密不可用") from exc
    decryptor = Cipher(
        algorithms.AES(base64.b64decode(session_key)),
        modes.CBC(base64.b64decode(iv)),
        backend=default_backend(),
    ).decryptor()
    decrypted = decryptor.update(base64.b64decode(encrypted_data)) + decryptor.finalize()
    pad = decrypted[-1]
    payload = json.loads(decrypted[:-pad].decode("utf-8"))
    phone = payload.get("purePhoneNumber") or payload.get("phoneNumber")
    if not phone:
        raise _mp_error(422, "WECHAT_PHONE_REQUIRED", "微信未返回手机号")
    return _require_phone(str(phone))


async def _resolve_wechat_phone(payload: WechatPhonePayload, session: dict[str, Any]) -> str:
    appid, secret = _wechat_credentials()
    code = (payload.code or "").strip()
    fallback_phone = (payload.phone or "").strip()
    if code:
        if not (appid and secret):
            if _is_production():
                raise _mp_error(503, "WECHAT_NOT_CONFIGURED", "微信登录未配置")
            if fallback_phone:
                return _require_phone(fallback_phone)
            raise _mp_error(422, "WECHAT_PHONE_REQUIRED", "未读取到微信手机号")
        try:
            return await _wechat_phone_by_code(appid, secret, code)
        except HTTPException as exc:
            if fallback_phone and not _is_production():
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                error = detail.get("error") if isinstance(detail.get("error"), dict) else {}
                if error.get("code") == "WECHAT_PHONE_INVALID":
                    return _require_phone(fallback_phone)
            raise
    if payload.encrypted_data and payload.iv and session.get("session_key"):
        return _decrypt_wechat_phone(str(session["session_key"]), payload.encrypted_data, payload.iv)
    if fallback_phone and not _is_production():
        return _require_phone(fallback_phone)
    if _is_production() and not (appid and secret):
        raise _mp_error(503, "WECHAT_NOT_CONFIGURED", "微信登录未配置")
    raise _mp_error(422, "WECHAT_PHONE_REQUIRED", "请授权手机号")


async def login_by_wechat_code(payload: WechatCodePayload) -> dict[str, Any]:
    appid, secret = _wechat_credentials()
    session: dict[str, Any] = {"js_code": payload.code}
    if appid and secret:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params={
                    "appid": appid,
                    "secret": secret,
                    "js_code": payload.code,
                    "grant_type": "authorization_code",
                },
            )
        body = response.json()
        if body.get("errcode"):
            raise _mp_error(422, "WECHAT_CODE_INVALID", body.get("errmsg") or "微信登录失败")
        session["openid"] = body.get("openid")
        session["session_key"] = body.get("session_key")
        session["unionid"] = body.get("unionid")
    elif _is_production():
        raise _mp_error(503, "WECHAT_NOT_CONFIGURED", "微信登录未配置")
    session_id = str(uuid.uuid4())
    redis = await get_redis_client()
    await redis.setex(f"mp:wx:{session_id}", WECHAT_SESSION_TTL_SECONDS, json.dumps(session, ensure_ascii=False))
    return {"session_id": session_id, "need_confirm": True, "bound": False}


async def bind_wechat_phone(db: AsyncSession, payload: WechatPhonePayload) -> dict[str, Any]:
    redis = await get_redis_client()
    raw = await redis.get(f"mp:wx:{payload.session_id}")
    if raw is None:
        raise _mp_error(422, "WECHAT_SESSION_EXPIRED", "微信登录会话已过期")
    session = json.loads(raw)
    phone = await _resolve_wechat_phone(payload, session)
    employee = await require_app_employee(db, phone)
    session["phone"] = phone
    session["employee_id"] = employee.id
    await redis.setex(
        f"mp:wx:{payload.session_id}", WECHAT_SESSION_TTL_SECONDS, json.dumps(session, ensure_ascii=False)
    )
    return {
        "session_id": payload.session_id,
        "phone_masked": mask_phone(phone),
        "name": employee.name,
        "need_confirm": True,
    }


async def confirm_login(db: AsyncSession, payload: AuthConfirmPayload) -> dict[str, Any]:
    redis = await get_redis_client()
    raw = await redis.get(f"mp:wx:{payload.session_id}")
    if raw is None:
        raise _mp_error(422, "WECHAT_SESSION_EXPIRED", "登录会话已过期")
    session = json.loads(raw)
    phone = session.get("phone")
    if not phone:
        raise _mp_error(422, "WECHAT_PHONE_REQUIRED", "请先授权手机号")
    employee = await require_app_employee(db, str(phone))
    if payload.avatar:
        employee.avatar = payload.avatar.strip()
    await redis.delete(f"mp:wx:{payload.session_id}")
    return await _issue_token(db, employee)


async def cancel_login(payload: AuthCancelPayload) -> dict[str, bool]:
    redis = await get_redis_client()
    await redis.delete(f"mp:wx:{payload.session_id}")
    return {"cancelled": True}


async def logout() -> dict[str, bool]:
    return {"success": True}


async def get_me(ctx: MpContext) -> dict[str, Any]:
    return {"employee": _me_dict(ctx.employee)}


async def update_me(db: AsyncSession, ctx: MpContext, payload: MeUpdatePayload) -> dict[str, Any]:
    ctx.employee.bio = payload.bio.strip()
    await db.commit()
    return {"employee": _me_dict(ctx.employee)}


def _cover_template_item(cover) -> dict[str, Any]:
    data = cover.to_dict()
    data["file_url"] = f"/api/mp/content/cover-templates/{cover.id}/file"
    return data


def _mp_hycanvas_template_item(item: dict[str, Any]) -> dict[str, Any]:
    template_id = str(item["id"])
    return {
        **item,
        "preview_urls": [f"/api/mp/content/hycanvas-templates/{template_id}/preview"],
        "overlay_url": f"/api/mp/content/hycanvas-templates/{template_id}/overlay",
    }


async def _list_mp_hycanvas_templates() -> list[dict[str, Any]]:
    from yuxi.services.hycanvas_service import HyCanvasClient

    try:
        catalog = await HyCanvasClient.from_env().list_xiaohongshu_templates()
    except HTTPException:
        return []
    return [_mp_hycanvas_template_item(item) for item in catalog.get("templates") or []]


def _reject_review_notes_media(payload: MpCompileBriefPayload) -> None:
    if str(payload.cover_asset_id or "").strip() or payload.cover_asset_ids:
        raise _mp_error(422, "MP_REVIEW_NOTES_NO_MEDIA", "好评笔记不需要上传图片")
    if str(payload.image_item_id or "").strip():
        raise _mp_error(422, "MP_REVIEW_NOTES_NO_MEDIA", "好评笔记不需要上传图片")
    if payload.cover_mode or payload.hycanvas_template_id or payload.cover_template_id:
        raise _mp_error(422, "MP_REVIEW_NOTES_NO_COVER", "好评笔记不生成封面")


async def _lock_decoration_visual_material(
    db: AsyncSession,
    user: User,
    *,
    image_item_id: str | None = None,
    cover_asset_id: str | None = None,
    hycanvas_template_id: str | None = None,
    cover_mode: Literal["builtin", "ai"] | None = None,
) -> tuple[ContentVisualMaterialSelection, str]:
    mode = cover_mode if cover_mode in {"builtin", "ai"} else "builtin"
    template_id = str(hycanvas_template_id or "").strip()
    if mode == "ai":
        if template_id:
            raise _mp_error(422, "MP_AI_COVER_TEMPLATE_CONFLICT", "AI 封面只使用图库原图，不能选择封面模板")
        template_id = None
    elif not _HYCANVAS_TEMPLATE_ID.fullmatch(template_id or ""):
        raise _mp_error(422, "MP_HYCANVAS_TEMPLATE_REQUIRED", "请选择小红书封面模板")
    repo = MaterialLibraryRepository(db, include_shared=True)
    owner_uid = str(user.uid)
    item = None
    if str(image_item_id or "").strip():
        item = await repo.get_item_for_user(str(image_item_id).strip(), owner_uid)
    elif str(cover_asset_id or "").strip():
        candidate = await repo.get_item_by_asset(str(cover_asset_id).strip())
        if candidate is not None:
            item = await repo.get_item_for_user(candidate.id, owner_uid)
    else:
        raise _mp_error(422, "MP_COVER_REQUIRED", "请选择图库图片或上传封面图")
    if item is None or item.material_type != "image" or item.status != "enabled":
        raise _mp_error(422, "MP_COVER_LIBRARY_ITEM_MISSING", "封面图不存在、已停用或不在当前账号图库中")
    if await repo.item_is_selected_by_task(item.id, owner_uid):
        raise _mp_error(409, "MP_COVER_IN_USE", "该图库图片已被其他内容任务使用")
    return (
        ContentVisualMaterialSelection(
            cover_mode=mode,
            image_item_id=item.id,
            hycanvas_template_id=template_id,
        ),
        item.asset_id,
    )


async def get_form_schema(
    db: AsyncSession, service_entry: str, *, include_hycanvas_templates: bool = True
) -> dict[str, Any]:
    if service_entry not in SERVICE_ENTRIES:
        raise _mp_error(422, "MP_SERVICE_ENTRY_INVALID", "服务入口不存在")
    await ensure_default_content_types(db)
    await ensure_default_variables(db)
    bindings = (await list_business_variables(db))["business_variables"]
    types = [item for item in (await list_content_types(db))["content_types"] if item["enabled"]]
    content_types: list[dict[str, Any]] = []
    flat_variables: list[dict[str, Any]] = []
    requires_content_type = service_entry == "装修家居"
    target_audiences = await list_enabled_target_audience_names(db) if service_entry == "装修家居" else []
    resident_populations = await list_enabled_resident_population_names(db) if service_entry == "装修家居" else []
    process_types = await list_enabled_process_type_names(db) if service_entry == "装修家居" else []
    process_names_by_type = await list_enabled_process_names_by_type(db) if service_entry == "装修家居" else {}
    select_options = catalog_select_options(
        target_audiences=target_audiences,
        resident_populations=resident_populations,
        process_types=process_types,
        process_names_by_type=process_names_by_type,
    )
    if requires_content_type:
        for content_type in types:
            fields = configured_business_variable_fields(
                bindings,
                service_entry=service_entry,
                content_type_id=content_type["id"],
                port="app",
                select_options=select_options,
            )
            for field in fields:
                field["content_type_code"] = content_type["type_code"]
            content_types.append({**content_type, "variables": fields})
            flat_variables.extend(fields)
    else:
        flat_variables = configured_business_variable_fields(
            bindings,
            service_entry=service_entry,
            content_type_id=None,
            port="app",
            select_options=select_options,
        )
    covers = (
        [_cover_template_item(item) for item in await CoverRepository(db).list_enabled()]
        if service_entry == "装修家居"
        else []
    )
    hycanvas_templates = (
        await _list_mp_hycanvas_templates() if include_hycanvas_templates and service_entry == "装修家居" else []
    )
    return {
        "service_entry": service_entry,
        "requires_content_type": requires_content_type,
        "service_entries": [
            {"value": "装修家居", "label": "装修家居", "goal": "acquire"},
            {"value": "好评笔记", "label": "好评笔记", "goal": "brand"},
        ],
        "content_types": content_types,
        "variables": flat_variables,
        "business_variable_bindings": [
            item
            for item in bindings
            if item.get("service_entry") == service_entry and item.get("enabled") and "app" in (item.get("ports") or [])
        ],
        "frame_areas": [_frame_area_payload(item) for item in FRAME_AREA_PRICING]
        if service_entry == "装修家居"
        else [],
        "design_styles": list(DESIGN_STYLES) if service_entry == "装修家居" else [],
        "project_stages": (list(FIELD_SELECT_OPTIONS["项目阶段"]) if service_entry == "装修家居" else []),
        "target_audiences": target_audiences,
        "resident_populations": resident_populations,
        "process_types": process_types,
        "process_names_by_type": process_names_by_type,
        "regions": list(REGIONS),
        "region_tree": [{"city": city, "districts": list(districts)} for city, districts in REGION_TREE],
        "cover_templates": covers,
        "hycanvas_templates": hycanvas_templates,
        "requires_cover": service_entry == "装修家居",
        "requires_photo_upload": service_entry == "装修家居",
        "direct_production": MP_DECORATION_DIRECT_UI if service_entry == "装修家居" else None,
        "review_notes_production": MP_REVIEW_NOTES_UI if service_entry == MP_REVIEW_NOTES_ENTRY else None,
    }


async def get_pricing(frame_area: str) -> dict[str, Any]:
    item = _frame_area_payload(lookup_frame_area_pricing(frame_area))
    return {"frame_area": item["value"], "quotes": item["quotes"], "quote_choices": item["quote_choices"]}


async def list_cover_templates(db: AsyncSession) -> dict[str, Any]:
    covers = [_cover_template_item(item) for item in await CoverRepository(db).list_enabled()]
    return {"cover_templates": covers, "total": len(covers)}


async def list_hycanvas_templates() -> dict[str, Any]:
    return {"hycanvas_templates": await _list_mp_hycanvas_templates()}


async def list_mp_viral_assets(
    db: AsyncSession,
    ctx: MpContext,
    *,
    industry_slug: str | None = None,
    ready_only: bool = False,
    content_type_code: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    return await list_viral_assets(
        db,
        ctx.user,
        industry_slug=industry_slug,
        ready_only=ready_only,
        content_type_code=content_type_code,
        limit=limit,
    )


def _mp_gallery_item(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("work_asset_id"):
        return {
            **item,
            "file_url": f"/api/mp/image/works/{item['work_asset_id']}/file",
            "thumbnail_file_url": f"/api/mp/image/works/{item['work_asset_id']}/file",
        }
    item_id = str(item["id"])
    return {
        **item,
        "in_use": bool(item.get("in_use")),
        "file_url": f"/api/mp/content/gallery-items/{item_id}/file",
        "thumbnail_file_url": f"/api/mp/content/gallery-items/{item_id}/thumbnail",
    }


async def list_mp_galleries(
    db: AsyncSession,
    ctx: MpContext,
    scope: Literal["private", "enterprise"] | None = None,
) -> dict[str, Any]:
    result = await list_image_galleries(db, ctx.user)
    galleries = []
    for item in result.get("galleries") or []:
        visibility = item.get("visibility") or "private"
        if scope and visibility != scope:
            continue
        cover_item_id = item.get("cover_item_id")
        cover_work_id = item.get("cover_work_asset_id")
        galleries.append(
            {
                **item,
                "visibility": visibility,
                "cover_file_url": f"/api/mp/image/works/{cover_work_id}/file"
                if cover_work_id
                else (f"/api/mp/content/gallery-items/{cover_item_id}/file" if cover_item_id else None),
                "cover_thumbnail_file_url": (
                    f"/api/mp/image/works/{cover_work_id}/file"
                    if cover_work_id
                    else (f"/api/mp/content/gallery-items/{cover_item_id}/thumbnail" if cover_item_id else None)
                ),
            }
        )
    return {"galleries": galleries}


async def list_mp_gallery_items(
    db: AsyncSession,
    ctx: MpContext,
    category: str,
    scope: Literal["private", "enterprise"] | None = None,
    include_descendants: bool = False,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    result = await list_material_items(
        db,
        ctx.user,
        material_type="image",
        category=category,
        status="enabled",
        query=None,
        page=page,
        page_size=page_size,
        sort="newest",
        scope=scope,
        include_descendants=include_descendants,
    )
    items = [_mp_gallery_item(item) for item in result.get("items") or []]
    return {
        "items": items,
        "total": result.get("total") or 0,
        "page": result.get("page") or page,
        "page_size": result.get("page_size") or page_size,
    }


async def read_mp_gallery_item_file(db: AsyncSession, ctx: MpContext, item_id: str) -> tuple[bytes, str, str]:
    return await get_material_file(db, ctx.user, item_id)


async def read_mp_gallery_item_thumbnail(db: AsyncSession, ctx: MpContext, item_id: str) -> bytes:
    data, _ = await get_material_thumbnail(db, ctx.user, item_id)
    return data


async def delete_mp_gallery_item(db: AsyncSession, ctx: MpContext, item_id: str) -> dict[str, Any]:
    return await delete_material_item(db, ctx.user, item_id)


async def visible_mp_works(db: AsyncSession, owner_uid: str) -> list[dict[str, Any]]:
    repo = ContentCoverRepository(db)
    jobs = await repo.list_succeeded_jobs_for_user(owner_uid)
    asset_ids = [asset_id for job in jobs for asset_id in ((job.result_json or {}).get("asset_ids") or [])]
    assets = await repo.get_assets_for_user(list(dict.fromkeys(asset_ids)), owner_uid)
    return visible_work_items(jobs, {asset.id: asset for asset in assets})


async def list_mp_works(db: AsyncSession, ctx: MpContext, *, page: int, page_size: int) -> dict[str, Any]:
    from yuxi.services.personal_materials import list_folder

    return await list_folder(db, ctx.user, "works", page=page, page_size=page_size)


async def hide_mp_work(db: AsyncSession, ctx: MpContext, asset_id: str) -> dict[str, bool]:
    owner_uid = str(ctx.user.uid)
    repo = ContentCoverRepository(db)
    asset = await repo.get_output_asset_for_user(asset_id, owner_uid, for_update=True)
    if asset is None:
        raise _mp_error(404, "MP_WORK_NOT_FOUND", "作品不存在")
    jobs = await repo.list_succeeded_jobs_for_user(owner_uid)
    if not any(asset_id in ((job.result_json or {}).get("asset_ids") or []) for job in jobs):
        raise _mp_error(404, "MP_WORK_NOT_FOUND", "作品不存在")
    asset.hidden_from_works_at = utc_now_naive()
    await db.commit()
    return {"success": True}


async def read_mp_work_file(db: AsyncSession, ctx: MpContext, asset_id: str) -> tuple[bytes, str, str]:
    asset = await ContentCoverRepository(db).get_output_asset_for_user(asset_id, str(ctx.user.uid))
    if asset is None:
        raise _mp_error(404, "MP_WORK_NOT_FOUND", "作品不存在")
    return await get_cover_asset_file(db, ctx.user, asset_id)


async def upload_cover(
    db: AsyncSession,
    ctx: MpContext,
    file: UploadFile,
    *,
    category: str | None = None,
    design_style: str | None = None,
    folder: Literal["rough", "uploads"] = "uploads",
) -> dict[str, Any]:
    resolved_category = (category or "uncategorized").strip() or "uncategorized"
    try:
        imported = await import_material_images(
            db,
            ctx.user,
            [file],
            category=resolved_category,
            design_style=design_style,
            source_channel="mp",
            source_folder=folder,
        )
    except HTTPException:
        raise
    item = (imported.get("items") or [None])[0]
    if not item:
        raise _mp_error(500, "MATERIAL_UPLOAD_EMPTY", "素材上传失败")
    asset = await ContentCoverRepository(db).get_asset(str(item["asset_id"]))
    if asset is None:
        raise _mp_error(500, "MATERIAL_ASSET_MISSING", "素材上传后无法读取文件")
    payload_asset = serialize_asset(asset)
    payload_asset["file_url"] = f"/api/mp/content/covers/{asset.id}/file"
    return {
        "asset": payload_asset,
        "library_item_id": item["id"],
        "category": item.get("category") or resolved_category,
        "category_name": item.get("category_name") or "",
    }


async def read_cover_file(db: AsyncSession, ctx: MpContext, asset_id: str) -> tuple[bytes, str, str]:
    return await get_cover_asset_file(db, ctx.user, asset_id)


async def read_cover_template_file(db: AsyncSession, cover_pk: str) -> tuple[bytes, str, str]:
    cover = await CoverRepository(db).get(cover_pk)
    if cover is None or not cover.enabled:
        raise _mp_error(404, "COVER_NOT_FOUND", "封面模板不存在")
    image_url = cover.image_url or ""
    if not image_url.startswith("/public/"):
        raise _mp_error(404, "COVER_TEMPLATE_FILE_MISSING", "封面模板文件不可用")
    object_name = image_url[len("/public/") :]
    try:
        data = await get_minio_client().adownload_file("public", object_name)
    except StorageError as exc:
        raise _mp_error(404, "COVER_TEMPLATE_FILE_MISSING", "封面模板文件不可用") from exc
    content_type = "image/png" if object_name.lower().endswith(".png") else "image/jpeg"
    return data, content_type, cover.image_name


async def read_hycanvas_template_preview(template_id: str) -> tuple[bytes, str]:
    if not _HYCANVAS_TEMPLATE_ID.fullmatch(template_id):
        raise _mp_error(404, "HYCANVAS_TEMPLATE_NOT_FOUND", "封面模板不存在")
    from yuxi.services.hycanvas_service import HyCanvasClient

    try:
        return await HyCanvasClient.from_env().fetch_template_preview(template_id)
    except HTTPException as exc:
        if exc.status_code == 503:
            raise _mp_error(503, "HYCANVAS_NOT_CONFIGURED", "封面模板服务尚未配置") from exc
        raise _mp_error(404, "HYCANVAS_TEMPLATE_PREVIEW_MISSING", "封面模板预览不可用") from exc


async def read_hycanvas_template_overlay(template_id: str) -> tuple[bytes, str]:
    if not _HYCANVAS_TEMPLATE_ID.fullmatch(template_id):
        raise _mp_error(404, "HYCANVAS_TEMPLATE_NOT_FOUND", "封面模板不存在")
    from yuxi.services.hycanvas_service import HyCanvasClient

    try:
        return await HyCanvasClient.from_env().render_template_overlay_png(template_id)
    except HTTPException as exc:
        if exc.status_code == 503:
            raise _mp_error(503, "HYCANVAS_NOT_CONFIGURED", "封面模板服务尚未配置") from exc
        raise _mp_error(404, "HYCANVAS_TEMPLATE_OVERLAY_MISSING", "封面模板透明图不可用") from exc


async def _next_code_for_user(db: AsyncSession, user: User) -> str:
    day = shanghai_now().strftime("%Y%m%d")
    result = await db.execute(
        select(ContentTask.brief_json).where(
            ContentTask.created_by == str(user.uid),
            ContentTask.deleted_at.is_(None),
        )
    )
    codes = []
    for (brief,) in result.all():
        code = ((brief or {}).get("form_values") or {}).get("mp_content_code")
        if code:
            codes.append(str(code))
    return next_mp_content_code(codes, day=day)


async def _decoration_template(db: AsyncSession) -> dict[str, Any]:
    templates = await ContentRepository(db).list_templates()
    template = next((item for item in templates if item.get("slug") == INDUSTRY_SLUG), None)
    if template is None:
        raise _mp_error(503, "CONTENT_INDUSTRY_TEMPLATE_NOT_FOUND", "装修行业模板尚未发布")
    return template


async def _resolve_content_type(db: AsyncSession, service_entry: str, type_code: str | None) -> dict[str, Any]:
    await ensure_default_content_types(db)
    types = [item for item in (await list_content_types(db))["content_types"] if item["enabled"]]
    if service_entry == "好评笔记" and not type_code:
        selected = next((item for item in types if item["name"] == "人设自荐"), None) or next(
            (item for item in types if item["type_code"] == "NRLX0007"), None
        )
        if selected is None:
            raise _mp_error(422, "MP_CONTENT_TYPE_REQUIRED", "未配置人设自荐内容类型")
        return selected
    if not type_code:
        raise _mp_error(422, "MP_CONTENT_TYPE_REQUIRED", "请选择内容类型")
    selected = next((item for item in types if item["type_code"] == type_code), None)
    if selected is None:
        raise _mp_error(422, "MP_CONTENT_TYPE_INVALID", "内容类型不存在或未启用")
    return selected


async def compile_brief(db: AsyncSession, ctx: MpContext, payload: MpCompileBriefPayload) -> dict[str, Any]:
    selected_type = await _resolve_content_type(db, payload.service_entry, payload.content_type_code)
    ct_code = map_nrlx_to_ct_code(selected_type["type_code"], selected_type["name"])
    goal = resolve_content_goal(payload.service_entry, ct_code)
    template = await _decoration_template(db)
    target_audiences = await list_enabled_target_audience_names(db) if payload.service_entry == "装修家居" else []
    resident_populations = (
        await list_enabled_resident_population_names(db) if payload.service_entry == "装修家居" else []
    )
    process_types = await list_enabled_process_type_names(db) if payload.service_entry == "装修家居" else []
    process_names_by_type = await list_enabled_process_names_by_type(db) if payload.service_entry == "装修家居" else {}
    form_fields = configured_business_variable_fields(
        (await list_business_variables(db))["business_variables"],
        service_entry=payload.service_entry,
        content_type_id=selected_type["id"] if payload.service_entry == "装修家居" else None,
        port="app",
        select_options=catalog_select_options(
            target_audiences=target_audiences,
            resident_populations=resident_populations,
            process_types=process_types,
            process_names_by_type=process_names_by_type,
        ),
    )
    for field in form_fields:
        if not field.get("required"):
            continue
        if str((payload.form_values or {}).get(field["key"]) or "").strip():
            continue
        raise _mp_error(422, "MP_VARIABLE_REQUIRED", f"请填写{field['label']}")
    visual_material = None
    cover_asset_id = payload.cover_asset_id
    if payload.service_entry == "装修家居":
        visual_material, cover_asset_id = await _lock_decoration_visual_material(
            db,
            ctx.user,
            image_item_id=payload.image_item_id,
            cover_asset_id=payload.cover_asset_id,
            hycanvas_template_id=payload.hycanvas_template_id,
            cover_mode=payload.cover_mode,
        )
    else:
        _reject_review_notes_media(payload)
        cover_asset_id = None
    content_code = await _next_code_for_user(db, ctx.user)
    merged_form_values = dict(payload.form_values or {})
    merged_form_values["mp_content_type_id"] = selected_type["id"]
    merged_form_values["mp_content_type_name"] = selected_type["name"]
    if payload.service_entry == "装修家居":
        try:
            merged_form_values.update(
                await build_mp_decoration_direct_defaults(
                    db,
                    ctx.employee,
                    ctx.user,
                    content_type_name=selected_type["name"],
                    content_type_id=selected_type["id"],
                    content_type_code=ct_code,
                    business_variables=merged_form_values,
                    industry_slug=INDUSTRY_SLUG,
                )
            )
        except LookupError as exc:
            raise _mp_error(
                422,
                "MP_VIRAL_ASSET_UNAVAILABLE",
                "该内容类型暂无可用爆款原文，请联系运营准备",
            ) from exc
        except ValueError as exc:
            raise _mp_error(422, "MP_CREATIVE_STYLE_UNAVAILABLE", "该内容类型暂无可用创作风格") from exc
    created = await create_content_task(
        db,
        ctx.user,
        ContentTaskCreate(
            industry_template_id=template["id"],
            mode="quick",
            content_goal=goal,
            content_type_code=ct_code,
            creation_mode="viral_rewrite",
            name=f"{payload.service_entry}-{selected_type['name']}-{content_code}",
        ),
    )
    task_id = created["task"]["id"]
    brief = build_mp_brief_payload(
        service_entry=payload.service_entry,
        form_values=merged_form_values,
        content_type_name=selected_type["name"],
        content_type_id=selected_type["id"],
        cover_asset_id=cover_asset_id,
        cover_asset_ids=payload.cover_asset_ids,
        cover_template_id=payload.cover_template_id,
        content_code=content_code,
        visual_material=visual_material,
        user_request=str(merged_form_values.get("user_request") or ""),
        content_type_code=ct_code if payload.service_entry == "装修家居" else None,
    )
    saved = await save_content_brief(db, ctx.user, task_id, brief, compile_now=True)
    result = {
        "task_id": task_id,
        "status": "strategy_evidence_locked",
        "task_status": saved["task"]["status"],
        "content_code": content_code,
        "task": saved["task"],
    }
    if payload.service_entry == "好评笔记":
        run = await start_run(db, ctx, task_id, MpRunCreatePayload())
        result["run_id"] = run["run_id"]
        result["run_status"] = run["status"]
    elif payload.service_entry == "装修家居":
        viral_asset_id = str(merged_form_values.get("viral_asset_id") or "").strip()
        direct = await create_direct_content_run(
            db,
            ctx.user,
            task_id,
            ContentDirectGenerateCreate(
                request_id=str(uuid.uuid4()),
                viral_asset_id=viral_asset_id,
                generation_prompt=str(merged_form_values.get("generation_prompt") or DEFAULT_DIRECT_GENERATION_PROMPT),
                user_request=str(merged_form_values.get("user_request") or ""),
                creative_style=merged_form_values.get("creative_style"),
            ),
        )
        result.update(_mp_direct_run_response(direct))
        result["status"] = "direct_run_queued"
        result["task_status"] = "queued"
    return result


async def get_task(db: AsyncSession, ctx: MpContext, task_id: str) -> dict[str, Any]:
    result = await get_content_task(db, ctx.user, task_id)
    task = result["task"]
    locked = task["status"] in {"brief_ready", "queued", "waiting_human", "waiting_external", "completed"} or bool(
        task.get("brief")
    )
    return {
        **result,
        "locked": task["status"] != "draft" and bool(task.get("brief")),
        "status_label": "策略和证据已锁定" if locked else "待编译简报",
    }


async def start_run(db: AsyncSession, ctx: MpContext, task_id: str, payload: MpRunCreatePayload) -> dict[str, Any]:
    request_id = payload.request_id or str(uuid.uuid4())
    task_result = await get_content_task(db, ctx.user, task_id)
    brief = task_result["task"].get("brief") or {}
    form_values = brief.get("form_values") or {}
    viral_asset_id = str(form_values.get("viral_asset_id") or "").strip()
    if viral_asset_id:
        direct = await create_direct_content_run(
            db,
            ctx.user,
            task_id,
            ContentDirectGenerateCreate(
                request_id=request_id,
                viral_asset_id=viral_asset_id,
                model_spec=payload.model_spec,
                user_request=str(brief.get("user_request") or form_values.get("user_request") or ""),
                generation_prompt=str(form_values.get("generation_prompt") or DEFAULT_DIRECT_GENERATION_PROMPT),
                creative_style=form_values.get("creative_style"),
            ),
        )
        return _mp_direct_run_response(direct)
    return await create_content_run(
        db, ctx.user, task_id, ContentRunCreate(request_id=request_id, model_spec=payload.model_spec)
    )


async def get_run(db: AsyncSession, ctx: MpContext, run_id: str) -> dict[str, Any]:
    result = await get_content_run(db, ctx.user, run_id)
    task_id = str(result["run"]["thread_id"])
    task = await db.get(ContentTask, task_id)
    visible = _visible_mp_run(result, task_status=None if task is None else task.status)
    if visible.get("status") == "completed":
        return _compact_run({"run": visible}, None)
    events = await list_run_stream_events(str(visible["id"]), limit=500)
    interrupt = await _backfill_cover_selection_assets(
        db,
        task_id=task_id,
        owner_uid=str(ctx.user.uid),
        interrupt=_extract_interrupt(events),
    )
    brief_json = None if task is None else task.brief_json
    service_entry = mp_service_entry_from_brief(brief_json)
    resume_payload = None
    resume_key = "mp-cover-auto"
    if service_entry != MP_REVIEW_NOTES_ENTRY:
        resume_payload = _cover_auto_resume_payload(interrupt, str(visible["id"]))
    if resume_payload is None and service_entry == MP_REVIEW_NOTES_ENTRY:
        resume_payload = _content_approval_auto_resume_payload(interrupt, str(visible["id"]))
        resume_key = "mp-approval-auto"
    elif resume_payload is None and has_mp_content_code(brief_json):
        resume_payload = _content_approval_auto_resume_payload(interrupt, str(visible["id"]))
        resume_key = "mp-approval-auto"
    if resume_payload:
        try:
            queued = await resume_content_run(
                db,
                ctx.user,
                str(visible["id"]),
                ContentRunResume(request_id=f"{resume_key}:{visible['id']}", resume=resume_payload),
            )
        except HTTPException:
            queued = None
        if queued:
            return {
                "run_id": queued["run_id"],
                "task_id": queued["task_id"],
                "status": queued["status"],
                "request_id": queued["request_id"],
                "error_message": None,
                "interrupt": None,
                "stream_url": f"/api/mp/content/runs/{queued['run_id']}/events",
            }
    return _compact_run({"run": visible}, interrupt)


async def stream_run_events(run_id: str, after_seq: str, ctx: MpContext):
    async for chunk in stream_agent_run_events(run_id=run_id, after_seq=after_seq, current_uid=str(ctx.user.uid)):
        yield chunk


async def resume_run(db: AsyncSession, ctx: MpContext, run_id: str, payload: MpRunResumePayload) -> dict[str, Any]:
    request_id = payload.request_id or str(uuid.uuid4())
    return await resume_content_run(
        db, ctx.user, run_id, ContentRunResume(request_id=request_id, resume=payload.resume)
    )


async def retry_run(db: AsyncSession, ctx: MpContext, run_id: str, payload: MpRunRetryPayload) -> dict[str, Any]:
    request_id = payload.request_id or str(uuid.uuid4())
    return await retry_content_node(
        db,
        ctx.user,
        run_id,
        request_id=request_id,
        node_id=payload.node_id,
        model_spec=None,
    )


async def get_artifact(db: AsyncSession, ctx: MpContext, task_id: str) -> dict[str, Any]:
    result = await get_task_artifact(db, ctx.user, task_id)
    artifact = result.get("artifact")
    if artifact and artifact.get("cover_asset_id"):
        artifact["cover_file_url"] = f"/api/mp/content/covers/{artifact['cover_asset_id']}/file"
    return result


def _publish_status_label(status: str | None) -> str:
    if status in {"reviewed", "completed"}:
        return "已发布"
    return "未发布"


def _format_mp_created_at(value) -> str:
    if value is None:
        return ""
    local = ensure_utc(value).astimezone(SHANGHAI_TZ)
    return local.strftime("%Y%m%d %H:%M:%S")


def _creation_methods_label(snapshot: dict[str, Any]) -> str:
    methods = snapshot.get("creation_methods") or snapshot.get("methods") or snapshot.get("method_codes") or []
    if not isinstance(methods, list):
        return str(methods or "")
    definitions = snapshot.get("creation_method_definitions") or []
    name_by_code: dict[str, str] = {}
    if isinstance(definitions, list):
        for item in definitions:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").strip()
            name = str(item.get("name") or item.get("label") or "").strip()
            if code and name:
                name_by_code[code] = name
    labels = [name_by_code.get(str(code), str(code)) for code in methods if str(code).strip()]
    return "+".join(labels)


def _list_item(
    task: ContentTask, artifact: ContentArtifact | None, favorited: bool, cover_asset_id: str | None
) -> dict[str, Any]:
    brief = task.brief_json or {}
    form_values = brief.get("form_values") or {}
    snapshot = (artifact.strategy_snapshot if artifact else None) or task.strategy_json or {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    title_formula = snapshot.get("title_formula") if isinstance(snapshot.get("title_formula"), dict) else {}
    body_formula = snapshot.get("body_formula") if isinstance(snapshot.get("body_formula"), dict) else {}
    cover_id = cover_asset_id or form_values.get("cover_asset_id")
    service_entry = str(form_values.get("mp_service_entry") or "")
    return {
        "task_id": task.id,
        "content_code": form_values.get("mp_content_code") or "",
        "service_entry": service_entry,
        "content_type_name": form_values.get("mp_content_type_name") or "",
        "creation_methods": _creation_methods_label(snapshot),
        "method": _creation_methods_label(snapshot),
        "viral_title_formula": str(title_formula.get("name") or title_formula.get("code") or ""),
        "title": artifact.title if artifact else "",
        "content_formula": str(body_formula.get("name") or body_formula.get("code") or ""),
        "formula": str(body_formula.get("name") or body_formula.get("code") or ""),
        "status": task.status,
        "status_label": _publish_status_label(task.status),
        "created_at": format_utc_datetime(task.created_at),
        "created_at_display": _format_mp_created_at(task.created_at),
        "favorited": favorited,
        "cover_asset_id": cover_id,
        "cover_file_url": (f"/api/mp/content/covers/{cover_id}/file" if cover_id else None),
    }


def has_mp_content_code(brief_json: dict | None) -> bool:
    values = (brief_json or {}).get("form_values") or {}
    return bool(str(values.get("mp_content_code") or "").strip())


async def list_contents(
    db: AsyncSession, ctx: MpContext, *, service_entry: str | None, page: int, page_size: int
) -> dict[str, Any]:
    mp_code = ContentTask.brief_json["form_values"]["mp_content_code"].as_string()
    filters = [
        ContentTask.deleted_at.is_(None),
        ContentTask.created_by == str(ctx.user.uid),
        mp_code.is_not(None),
        mp_code != "",
    ]
    query = select(ContentTask).where(*filters)
    if service_entry:
        if service_entry not in SERVICE_ENTRIES:
            raise _mp_error(422, "MP_SERVICE_ENTRY_INVALID", "服务入口不存在")
        query = query.where(ContentTask.brief_json["form_values"]["mp_service_entry"].as_string() == service_entry)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    items = list(
        (
            await db.execute(
                query.order_by(ContentTask.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        ).scalars()
    )
    task_ids = [item.id for item in items]
    artifacts = {}
    if task_ids:
        rows = await db.execute(select(ContentArtifact).where(ContentArtifact.task_id.in_(task_ids)))
        artifacts = {row.task_id: row for row in rows.scalars()}
    favorite_ids: set[str] = set()
    if task_ids:
        fav_rows = await db.execute(
            select(ContentMpFavorite.task_id).where(
                ContentMpFavorite.employee_id == ctx.employee.id, ContentMpFavorite.task_id.in_(task_ids)
            )
        )
        favorite_ids = {task_id for (task_id,) in fav_rows.all()}
    return {
        "items": [
            _list_item(
                item,
                artifacts.get(item.id),
                item.id in favorite_ids,
                artifacts.get(item.id).cover_asset_id if artifacts.get(item.id) else None,
            )
            for item in items
            if has_mp_content_code(item.brief_json)
        ],
        "total": int(total),
        "page": page,
        "page_size": page_size,
    }


async def add_favorite(db: AsyncSession, ctx: MpContext, task_id: str) -> dict[str, Any]:
    task = await ContentRepository(db).get_task_for_user(task_id, ctx.user)
    if task is None:
        raise _mp_error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    existing = await db.execute(
        select(ContentMpFavorite).where(
            ContentMpFavorite.employee_id == ctx.employee.id, ContentMpFavorite.task_id == task_id
        )
    )
    if existing.scalar_one_or_none() is None:
        db.add(ContentMpFavorite(id=str(uuid.uuid4()), employee_id=ctx.employee.id, task_id=task_id))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
    return {"favorited": True, "task_id": task_id}


async def remove_favorite(db: AsyncSession, ctx: MpContext, task_id: str) -> dict[str, Any]:
    result = await db.execute(
        select(ContentMpFavorite).where(
            ContentMpFavorite.employee_id == ctx.employee.id, ContentMpFavorite.task_id == task_id
        )
    )
    item = result.scalar_one_or_none()
    if item is not None:
        await db.delete(item)
        await db.commit()
    return {"favorited": False, "task_id": task_id}


async def duplicate_content(db: AsyncSession, ctx: MpContext, task_id: str) -> dict[str, Any]:
    copied = await duplicate_content_task(db, ctx.user, task_id)
    task = await ContentRepository(db).get_task_for_user(copied["task"]["id"], ctx.user, for_update=True)
    if task is None:
        raise _mp_error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    brief = dict(task.brief_json or {})
    form_values = dict(brief.get("form_values") or {})
    form_values["mp_content_code"] = await _next_code_for_user(db, ctx.user)
    brief["form_values"] = form_values
    task.brief_json = brief
    await db.commit()
    return {"task": task.to_dict()}


async def delete_content(db: AsyncSession, ctx: MpContext, task_id: str) -> dict[str, Any]:
    return await delete_content_task(db, ctx.user, task_id)
