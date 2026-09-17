"""当家外部内容接入：契约解析、OBS 图片导入素材库、简报映射与生成编排。

入参契约与设计决策见 docs/vibe/2026-09-14-dangjia-external-content-api.md。
"""

from __future__ import annotations

import io
from typing import Any

import httpx
from fastapi import HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content.schemas import (
    ContentBriefPayload,
    ContentRunCreate,
    ContentTaskCreate,
    ContentVisualMaterialSelection,
)
from yuxi.content_cover.photo_composition import PhotoComposition, PhotoSlot
from yuxi.repositories.content_repository import ContentRepository
from yuxi.services.content_service import (
    create_content_run,
    create_content_task,
    get_content_run,
    get_content_task,
    save_content_brief,
)
from yuxi.services.material_library_service import import_material_images
from yuxi.services.run_queue_service import list_run_stream_events
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentTask

INDUSTRY_SLUG = "decoration"
CONTENT_GOAL = "acquire"
TYPE_NAME_TO_CT_CODE = {"施工报价": "CT02"}
MAX_IMAGE_COUNT = 9
MAX_IMAGE_BYTES = 20 * 1024 * 1024
OBS_DOWNLOAD_TIMEOUT = 30.0
COMPOSITION_LAYOUT_BY_COUNT = {2: "grid-2", 3: "grid-3", 4: "grid-4", 6: "grid-6", 9: "grid-9"}
EXTERNAL_SOURCE = "dangjia"


class DangjiaHonors(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ownerRecommendCount: str | None = None
    servedSiteCount: str | None = None


class DangjiaPersona(BaseModel):
    model_config = ConfigDict(extra="ignore")

    age: str | None = None
    workYears: str | None = None
    serviceCity: str | None = None
    introduction: str = Field(min_length=1, max_length=4000)
    skills: list[str] = Field(default_factory=list)
    honors: DangjiaHonors | None = None
    tone: str | None = None
    serviceAdvantages: list[str] = Field(default_factory=list)


class DangjiaQuotationInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    houseArea: str = Field(min_length=1, max_length=100)
    houseType: str = Field(min_length=1, max_length=100)


class DangjiaPrice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    format: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=8000)


class DangjiaRequirementType(BaseModel):
    model_config = ConfigDict(extra="ignore")

    typeName: str = Field(min_length=1, max_length=100)
    quotationInfo: DangjiaQuotationInfo
    prices: list[DangjiaPrice] = Field(min_length=1, max_length=20)
    mySite: str | None = Field(default=None, max_length=500)


class DangjiaImage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    templateId: str = Field(default="", max_length=64)
    objectKey: str = Field(default="", max_length=500)
    objectUrl: str = Field(min_length=1, max_length=2000)


class DangjiaContentCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    serialNo: str = Field(min_length=1, max_length=64)
    persona: DangjiaPersona
    requirementType: DangjiaRequirementType
    tags: list[str] = Field(default_factory=list, max_length=20)
    images: list[DangjiaImage] = Field(min_length=1, max_length=MAX_IMAGE_COUNT)


def _dj_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


def _clean(value: str | None) -> str:
    return (value or "").strip()


def build_persona_description(persona: DangjiaPersona) -> str:
    """把结构化人设字段拼成一段完整的人设描述，供 Skill 生成人设化文案。"""
    facts: list[str] = []
    if _clean(persona.age):
        facts.append(f"{_clean(persona.age)}岁")
    if _clean(persona.workYears):
        facts.append(f"{_clean(persona.workYears)}年装修工龄")
    if _clean(persona.serviceCity):
        facts.append(f"服务城市{_clean(persona.serviceCity)}")
    sentences = [f"{('，'.join(facts))}。"] if facts else []
    sentences.append(persona.introduction.strip().rstrip("。") + "。")
    if persona.skills:
        sentences.append(f"技能：{'、'.join(persona.skills)}。")
    honors = persona.honors
    honor_parts: list[str] = []
    if honors and _clean(honors.ownerRecommendCount):
        honor_parts.append(f"业主推荐{_clean(honors.ownerRecommendCount)}次")
    if honors and _clean(honors.servedSiteCount):
        honor_parts.append(f"累计服务工地{_clean(honors.servedSiteCount)}个")
    if honor_parts:
        sentences.append("，".join(honor_parts) + "。")
    if _clean(persona.tone):
        sentences.append(f"沟通语气：{_clean(persona.tone)}。")
    return "".join(sentences)


def _require_cover_image(images: list[DangjiaImage]) -> DangjiaImage:
    cover_candidates = [image for image in images if _clean(image.templateId)]
    if len(cover_candidates) != 1:
        raise _dj_error(
            422,
            "DANGJIA_COVER_IMAGE_INVALID",
            "images 中必须有且仅有一张 templateId 非空的封面图",
        )
    return cover_candidates[0]


def _composition_layout_id(image_count: int) -> str | None:
    if image_count <= 1:
        return None
    layout_id = COMPOSITION_LAYOUT_BY_COUNT.get(image_count)
    if layout_id is None:
        raise _dj_error(
            422,
            "DANGJIA_IMAGE_COUNT_UNSUPPORTED",
            "图片组合仅支持 2/3/4/6/9 张，请调整图片数量",
        )
    return layout_id


def _resolve_ct_code(type_name: str) -> str:
    ct_code = TYPE_NAME_TO_CT_CODE.get(type_name.strip())
    if ct_code is None:
        raise _dj_error(
            422,
            "DANGJIA_TYPE_NAME_UNMAPPED",
            f"暂不支持的需求类型：{type_name}",
        )
    return ct_code


def build_dangjia_form_values(payload: DangjiaContentCreate) -> dict[str, Any]:
    """映射为装修行业表单事实；brand_name/audience/pain 为平台必填，由真实入参推导。"""
    persona = payload.persona
    requirement = payload.requirementType
    city = _clean(persona.serviceCity)
    house_type = requirement.quotationInfo.houseType.strip()
    house_area = requirement.quotationInfo.houseArea.strip()
    skills = [item.strip() for item in persona.skills if item.strip()]
    advantages = [item.strip() for item in persona.serviceAdvantages if item.strip()]
    budget_text = "\n".join(f"【{price.format.strip()}】{price.content.strip()}" for price in requirement.prices)
    craft_parts: list[str] = []
    if skills:
        craft_parts.append(f"工种能力：{'、'.join(skills)}")
    if advantages:
        craft_parts.append(f"服务优势：{'、'.join(advantages)}")
    return {
        "external_serial_no": payload.serialNo.strip(),
        "external_source": EXTERNAL_SOURCE,
        "brand_name": f"{city}装修工长" if city else "当家装修工长",
        "audience": [f"{city}准备装修{house_type}的业主" if city else f"准备装修{house_type}的业主"],
        "pain": [f"想搞清楚{house_area}{house_type}的施工报价明细"],
        "advantage": advantages,
        "project_type": house_type,
        "area": house_area,
        "budget": budget_text,
        "craft_and_materials": "；".join(craft_parts),
        "project_site": _clean(requirement.mySite),
        "content_tags": [item.strip() for item in payload.tags if item.strip()],
        "type_name": requirement.typeName.strip(),
    }


def build_dangjia_brief(
    payload: DangjiaContentCreate,
    *,
    cover_item_id: str,
    ordered_item_ids: list[str],
) -> ContentBriefPayload:
    """把当家的入参映射为平台简报；视觉素材使用素材库导入后的 item id。"""
    cover_image = _require_cover_image(payload.images)
    layout_id = _composition_layout_id(len(payload.images))
    composition = None
    if layout_id is not None:
        composition = PhotoComposition(
            layout_id=layout_id,
            slots=[PhotoSlot(image_item_id=item_id) for item_id in ordered_item_ids],
        )
    try:
        visual_material = ContentVisualMaterialSelection(
            image_item_id=cover_item_id,
            hycanvas_template_id=_clean(cover_image.templateId),
            photo_composition=composition,
        )
    except ValueError as exc:
        raise _dj_error(422, "DANGJIA_TEMPLATE_ID_INVALID", "封面图 templateId 不是有效的 HyCanvas 模板 ID") from exc
    return ContentBriefPayload(
        brand={"name": ""},
        audience=[],
        persona={"description": build_persona_description(payload.persona)},
        form_values=build_dangjia_form_values(payload),
        visual_material=visual_material,
    )


async def _download_obs_image(image: DangjiaImage) -> UploadFile:
    url = image.objectUrl.strip()
    if not url.startswith(("http://", "https://")):
        raise _dj_error(422, "DANGJIA_IMAGE_URL_INVALID", f"图片地址不是合法的 URL：{url[:120]}")
    try:
        async with httpx.AsyncClient(timeout=OBS_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        raise _dj_error(502, "DANGJIA_IMAGE_DOWNLOAD_FAILED", f"图片下载失败：{url[:120]}") from exc
    data = response.content
    if not data:
        raise _dj_error(502, "DANGJIA_IMAGE_DOWNLOAD_FAILED", f"图片内容为空：{url[:120]}")
    if len(data) > MAX_IMAGE_BYTES:
        raise _dj_error(422, "DANGJIA_IMAGE_DOWNLOAD_FAILED", f"图片超过 20MB 限制：{url[:120]}")
    filename = _clean(image.objectKey) or "image.jpg"
    return UploadFile(file=io.BytesIO(data), filename=filename)


async def _import_images(db: AsyncSession, user: User, payload: DangjiaContentCreate) -> dict[str, str]:
    """按入参顺序下载并导入素材库，返回 objectUrl 到素材 item id 的映射。"""
    item_ids: dict[str, str] = {}
    for image in payload.images:
        upload = await _download_obs_image(image)
        imported = await import_material_images(db, user, [upload], category="uncategorized")
        item_ids[image.objectUrl] = imported["items"][0]["id"]
    return item_ids


async def _find_task_by_serial(db: AsyncSession, user: User, serial_no: str) -> ContentTask | None:
    serial = ContentTask.brief_json["form_values"]["external_serial_no"].as_string()
    result = await db.execute(
        select(ContentTask)
        .where(
            ContentTask.created_by == str(user.uid),
            ContentTask.deleted_at.is_(None),
            serial == serial_no,
        )
        .order_by(ContentTask.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _decoration_template(db: AsyncSession) -> dict[str, Any]:
    templates = await ContentRepository(db).list_templates()
    template = next((item for item in templates if item.get("slug") == INDUSTRY_SLUG), None)
    if template is None:
        raise _dj_error(503, "CONTENT_INDUSTRY_TEMPLATE_NOT_FOUND", "装修行业模板尚未发布")
    return template


def _task_response(task: ContentTask | dict[str, Any], *, run_id: str | None, idempotent: bool) -> dict[str, Any]:
    task_dict = task if isinstance(task, dict) else task.to_dict()
    brief = task_dict.get("brief") or {}
    form_values = brief.get("form_values") or {}
    return {
        "serial_no": form_values.get("external_serial_no") or "",
        "task_id": task_dict["id"],
        "run_id": run_id or task_dict.get("latest_run_id"),
        "task_status": task_dict.get("status"),
        "idempotent": idempotent,
    }


async def create_dangjia_content(db: AsyncSession, user: User, payload: DangjiaContentCreate) -> dict[str, Any]:
    serial_no = payload.serialNo.strip()
    existing = await _find_task_by_serial(db, user, serial_no)
    if existing is not None:
        return _task_response(existing, run_id=None, idempotent=True)

    ct_code = _resolve_ct_code(payload.requirementType.typeName)
    cover_image = _require_cover_image(payload.images)
    # 先校验图片数量是否落在组合布局支持范围内，避免下载后才报错。
    _composition_layout_id(len(payload.images))
    item_ids = await _import_images(db, user, payload)
    ordered_item_ids = [item_ids[image.objectUrl] for image in payload.images]
    cover_item_id = item_ids[cover_image.objectUrl]
    ordered_item_ids.remove(cover_item_id)
    ordered_item_ids.insert(0, cover_item_id)

    brief = build_dangjia_brief(payload, cover_item_id=cover_item_id, ordered_item_ids=ordered_item_ids)
    template = await _decoration_template(db)
    created = await create_content_task(
        db,
        user,
        ContentTaskCreate(
            industry_template_id=template["id"],
            mode="quick",
            content_goal=CONTENT_GOAL,
            content_type_code=ct_code,
            name=f"当家-{payload.requirementType.typeName.strip()}-{serial_no}",
        ),
    )
    task_id = created["task"]["id"]
    await save_content_brief(db, user, task_id, brief, compile_now=True)
    run = await create_content_run(
        db,
        user,
        task_id,
        ContentRunCreate(request_id=f"dangjia:{serial_no}", model_spec=None),
    )
    task = await get_content_task(db, user, task_id)
    return _task_response(task["task"], run_id=run["run_id"], idempotent=False)


async def get_dangjia_task(db: AsyncSession, user: User, task_id: str) -> dict[str, Any]:
    result = await get_content_task(db, user, task_id)
    task = result["task"]
    brief = task.get("brief") or {}
    form_values = brief.get("form_values") or {}
    if form_values.get("external_source") != EXTERNAL_SOURCE:
        raise _dj_error(404, "DANGJIA_TASK_NOT_FOUND", "内容任务不存在")
    artifact = result.get("artifact")
    response = {
        "serial_no": form_values.get("external_serial_no") or "",
        "task_id": task["id"],
        "task_status": task.get("status"),
        "latest_run_id": task.get("latest_run_id"),
        "artifact": None,
    }
    if artifact:
        cover_asset_id = artifact.get("cover_asset_id")
        response["artifact"] = {
            "title": artifact.get("title"),
            "body": artifact.get("body"),
            "topics": artifact.get("topics") or [],
            "status": artifact.get("status"),
            "current_version": artifact.get("current_version"),
            "cover_asset_id": cover_asset_id,
            "cover_file_url": f"/api/content/covers/assets/{cover_asset_id}/file" if cover_asset_id else None,
        }
    return response


async def get_dangjia_run(db: AsyncSession, user: User, run_id: str) -> dict[str, Any]:
    result = await get_content_run(db, user, run_id)
    run = result["run"]
    events = await list_run_stream_events(run_id, limit=500)
    interrupt = _extract_interrupt(events)
    return {
        "run_id": run["id"],
        "task_id": run.get("thread_id"),
        "status": run.get("status"),
        "interrupt": interrupt,
    }


def _extract_interrupt(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("event_type") != "interrupt":
            continue
        envelope = event.get("payload") or {}
        inner = envelope.get("payload") if isinstance(envelope, dict) else {}
        return inner if isinstance(inner, dict) else envelope if isinstance(envelope, dict) else None
    return None
