from __future__ import annotations

import json
import random
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content.generation import DEFAULT_DIRECT_GENERATION_PROMPT
from yuxi.content.service_entry_form import CONTENT_TYPE_NAME_TO_DIRECTION
from yuxi.services.content_viral_assets import list_viral_assets
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentEmployee

MP_DECORATION_DIRECT_UI = {
    "production_mode": "direct",
    "hidden_sections": ["creative_style", "viral_reference", "generation_prompt", "content_request"],
}

_GENDER_LABEL = {"male": "男", "female": "女"}
_BRAND_NAME = "鸿扬家装"
_CONSTRUCTION_BRAND = "鸿扬家装"
_BRAND_POSITIONING = "定制化家装"
_QUOTE_KEYS = ("基础", "木制品", "主材")
_STAGE_CRAFT_TOPICS = {
    "水电阶段": "水电施工与隐蔽验收",
    "拆改阶段": "拆改施工",
    "泥木阶段": "泥瓦施工",
    "油漆阶段": "油漆施工",
    "竣工交付": "竣工验收",
}

_STYLE_CATALOG: dict[str, dict[str, str]] = {
    "项目经理掏心窝": {
        "label": "项目经理掏心窝",
        "description": "用真诚的语气表达价格透明、团队优势",
    },
    "本地信任型": {
        "label": "本地信任型",
        "description": "本地服务，建立地域信任",
    },
    "案例证明型": {"label": "案例证明型", "description": "用真实案例和落地实际费用建立用户信任"},
    "专业干货型": {
        "label": "专业干货型",
        "description": "工艺讲解、材料对比、验收标准（比如水电、防水、瓷砖铺贴）",
    },
    "极简美学设计型": {
        "label": "极简美学设计型",
        "description": "用色彩搭配、软装、灯光设计、收纳设计，温柔舒缓，偏审美科普，不讲硬工艺",
    },
    "实景案例拆解型": {
        "label": "实景案例拆解型",
        "description": "讲故事，结合真实房子案例，代入感强",
    },
    "痛点共鸣型": {
        "label": "痛点共鸣型",
        "description": "先说明装修会遇到的坑和痛点，再给解决方案",
    },
    "理性设计师": {
        "label": "理性设计师",
        "description": "专业设计，不只为好看，兼顾实用，收纳，动线的设计师",
    },
}

_HEARTFELT_PRICE_SHOW = "用真诚的语气表达价格秀明、团队优势"
_STYLES_BY_TYPE_NAME: dict[str, tuple[str, ...]] = {
    "工艺施工展示": ("项目经理掏心窝", "本地信任型", "专业干货型", "极简美学设计型"),
    "工艺展示": ("项目经理掏心窝", "本地信任型", "专业干货型", "极简美学设计型"),
    "装修报价清单": ("项目经理掏心窝", "本地信任型", "案例证明型", "痛点共鸣型"),
    "报价清单": ("项目经理掏心窝", "本地信任型", "案例证明型", "痛点共鸣型"),
    "装修案例分享": ("项目经理掏心窝", "本地信任型", "案例证明型", "痛点共鸣型", "实景案例拆解型"),
    "案例分享": ("项目经理掏心窝", "本地信任型", "案例证明型", "痛点共鸣型", "实景案例拆解型"),
    "装修知识科普": ("实景案例拆解型", "极简美学设计型"),
    "知识科普": ("实景案例拆解型", "极简美学设计型"),
    "人设自荐": ("理性设计师",),
    "装修人设自荐": ("理性设计师",),
}
_DESCRIPTION_OVERRIDES: dict[str, dict[str, str]] = {
    "工艺施工展示": {"项目经理掏心窝": _HEARTFELT_PRICE_SHOW},
    "工艺展示": {"项目经理掏心窝": _HEARTFELT_PRICE_SHOW},
    "装修案例分享": {"项目经理掏心窝": _HEARTFELT_PRICE_SHOW},
    "案例分享": {"项目经理掏心窝": _HEARTFELT_PRICE_SHOW},
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _region_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_text(value.get(key)) for key in ("province", "city", "district") if _text(value.get(key)))
    return _text(value)


def _compact_list(*values: Any) -> list[str] | None:
    items: list[str] = []
    for value in values:
        if isinstance(value, list):
            items.extend(_text(item) for item in value if _text(item))
        else:
            text = _text(value)
            if text:
                items.append(text)
    if not items:
        return None
    return list(dict.fromkeys(items))


def _compact_facts(facts: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in facts.items() if value not in (None, "", [], {})}


def _lock_frame_area(raw: str, seed: str) -> str | None:
    value = _text(raw)
    if not value:
        return None
    ranged = re.match(r"^(\d+)\s*[-~～到至]\s*(\d+)", value)
    if ranged:
        low, high = int(ranged.group(1)), int(ranged.group(2))
        key = f"{seed}|{low}-{high}"
        digest = 0
        for char in key:
            digest = (digest * 31 + ord(char)) & 0xFFFFFFFF
        return f"{low + (digest % (high - low + 1))}㎡"
    plus = re.match(r"^(\d+)\s*(?:㎡|m²|m2|平米|平)?\s*(?:以上|起)$", value, re.I)
    if plus:
        return f"{int(plus.group(1)) + 1}㎡"
    concrete = re.match(r"^(\d+)\s*(?:㎡|m²|m2|平米|平)?$", value)
    return f"{concrete.group(1)}㎡" if concrete else value


def creative_style_options(content_type_code: str | None, content_type_name: str) -> list[dict[str, str]]:
    name = _text(content_type_name)
    keys = _STYLES_BY_TYPE_NAME.get(name)
    if not keys:
        name = {"CT01": "装修案例分享", "CT02": "装修报价清单", "CT07": "人设自荐"}.get(
            _text(content_type_code), ""
        )
        keys = _STYLES_BY_TYPE_NAME.get(name)
    if not keys:
        return []
    overrides = _DESCRIPTION_OVERRIDES.get(name, {})
    options: list[dict[str, str]] = []
    for key in keys:
        item = _STYLE_CATALOG.get(key)
        if item is None:
            continue
        if key in overrides:
            item = {**item, "description": overrides[key]}
        options.append(item)
    return options


def pick_random_creative_style(content_type_code: str | None, content_type_name: str) -> dict[str, str]:
    options = creative_style_options(content_type_code, content_type_name)
    if not options:
        raise ValueError("no creative styles for content type")
    picked = random.choice(options)
    return {"name": picked["label"], "instruction": picked["description"]}


def _build_persona(employee: ContentEmployee, user: User) -> dict[str, Any]:
    gender = _GENDER_LABEL.get(str(employee.gender or ""), str(employee.gender or ""))
    age = "" if employee.age is None else str(employee.age)
    department = ""
    if user.department_id:
        department = str(getattr(user, "department_name", "") or "")
    return {
        "name": employee.name or user.username or "",
        "employeeCode": employee.employee_code or "",
        "loginAccount": employee.login_account or user.phone_number or "",
        "gender": gender,
        "age": age,
        "role": employee.role or user.role or "",
        "currentBranch": employee.current_branch or "",
        "currentDepartment": department,
    }


def _format_service_years(raw: Any) -> str:
    value = _text(raw)
    if not value:
        return ""
    return value if "年" in value else f"{value}年"


def _build_persona_fact(persona: dict[str, Any], *, job: str = "", years: str = "", region: str = "") -> str:
    org = f"{persona.get('currentBranch', '')}{persona.get('currentDepartment', '')}"
    parts = [
        persona.get("name") or "",
        f"{persona['age']}岁" if persona.get("age") else "",
        job or persona.get("role") or "",
        f"从业{years}" if years else "",
        f"服务{region}" if region else "",
        org,
        f"工号{persona['employeeCode']}" if persona.get("employeeCode") else "",
    ]
    return "，".join(part for part in parts if part)


def _build_facts(type_name: str, persona: dict[str, Any], business_variables: dict[str, Any]) -> dict[str, Any]:
    community = _text(business_variables.get("楼盘信息"))
    frame_area = _text(business_variables.get("外框面积"))
    style = _text(business_variables.get("设计风格"))
    audience_target = _text(business_variables.get("目标人群"))
    household = _text(business_variables.get("居住人口"))
    process_type = _text(business_variables.get("工艺类型"))
    process_name = _text(business_variables.get("工艺名称"))
    project_stage = _text(business_variables.get("项目阶段"))
    stage_topic = _STAGE_CRAFT_TOPICS.get(project_stage, project_stage)
    budget_text = "；".join(
        f"{key} {_text(business_variables.get(key))}" for key in _QUOTE_KEYS if _text(business_variables.get(key))
    )
    is_craft = type_name in {"工艺施工展示", "工艺展示"}
    is_quote = type_name in {"装修报价清单", "报价清单"}
    is_case = type_name in {"装修案例分享", "案例分享"}
    is_knowledge = type_name in {"装修知识科普", "知识科普"}
    is_persona = type_name in {"人设自荐", "装修人设自荐"}
    region = _region_text(business_variables.get("所在区域"))
    job = _text(business_variables.get("岗位"))
    years = _format_service_years(business_variables.get("从业年限"))
    craft_parts = _compact_list(process_type, process_name, "" if process_type else stage_topic)
    locked_area = _lock_frame_area(frame_area, persona.get("employeeCode") or community)
    price_items = [f"{key} {_text(business_variables.get(key))}" for key in _QUOTE_KEYS if _text(business_variables.get(key))]
    process: list[str] | None
    result = ""
    if is_quote:
        process = _compact_list(f"{_BRAND_POSITIONING}交付", style)
        result = " ".join(part for part in (community, locked_area, style, f"施工{_CONSTRUCTION_BRAND}") if part)
    elif is_case:
        process = _compact_list(f"{_BRAND_POSITIONING}交付")
        result = " ".join(part for part in (community, locked_area, style, f"施工{_CONSTRUCTION_BRAND}") if part)
    elif is_knowledge:
        process = craft_parts or _compact_list(f"{_BRAND_POSITIONING}交付")
        result = (
            f"看懂{process_name}的判断标准与验收要点"
            if process_name
            else f"看懂{process_type or style or '装修工艺'}该怎么判断"
        )
    elif is_persona:
        process = _compact_list(f"{job}服务" if job else f"{_BRAND_POSITIONING}服务")
        result = "，".join(
            part
            for part in (
                f"{region}{job}" if region and job else job or region,
                f"从业{years}" if years else "",
                "可对接咨询",
            )
            if part
        )
    elif is_craft:
        process = craft_parts
        result = "".join(part for part in (project_stage, f"按工艺规范落实{process_name}" if process_name else "") if part)
    else:
        process = _compact_list(style, frame_area) or _compact_list(f"{_BRAND_POSITIONING}交付")
        result = " ".join(
            part for part in (community, locked_area or frame_area, style, f"施工{_CONSTRUCTION_BRAND}") if part
        )
    persona_fact = _build_persona_fact(persona, job=job, years=years, region=region) if is_persona else _build_persona_fact(persona)
    audience = _compact_list(household, audience_target) or _compact_list("装修业主")
    return _compact_facts(
        {
            "persona_fact": f"{persona_fact}。" if persona_fact else None,
            "process": process,
            "craft_role": audience if is_knowledge else None,
            "result": _text(result) or None,
            "product": (
                f"{_BRAND_NAME}{job or _BRAND_POSITIONING}服务"
                if is_persona
                else process_name
                or process_type
                or (f"{community}{_BRAND_POSITIONING}项目" if community else f"{_BRAND_POSITIONING}项目")
                if is_knowledge
                else f"{community}{_BRAND_POSITIONING}项目"
                if community
                else f"{_BRAND_POSITIONING}项目"
            ),
            "location": " · ".join(part for part in (region, community) if part) or None,
            "case_background": community or None,
            "scene": (project_stage or style) if is_craft else style or project_stage or None,
            "audience": audience,
            "quantity": locked_area or frame_area or None,
            "price": (_compact_list(*price_items) or _compact_list(budget_text)) if is_quote or is_case else None,
            "quote_type": "budget" if is_quote or (is_case and price_items) else None,
        }
    )


def build_content_request_payload(
    *,
    employee: ContentEmployee,
    user: User,
    content_type_name: str,
    content_type_id: str,
    content_type_code: str,
    business_variables: dict[str, Any],
) -> dict[str, Any]:
    persona = _build_persona(employee, user)
    filled = {
        key: value
        for key, value in business_variables.items()
        if (isinstance(value, list) and any(_text(item) for item in value)) or _text(value)
    }
    type_code = CONTENT_TYPE_NAME_TO_DIRECTION.get(content_type_name) or content_type_code
    return {
        "serialNo": persona.get("employeeCode") or user.uid or "",
        "contentType": {
            "typeName": content_type_name,
            "contentTypeId": content_type_id,
            "contentTypeCode": type_code,
        },
        "persona": persona,
        "businessVariables": filled,
        "facts": _build_facts(content_type_name, persona, filled),
    }


def format_content_request_json(**kwargs: Any) -> str:
    return f"{json.dumps(build_content_request_payload(**kwargs), ensure_ascii=False, indent=2)}\n"


async def pick_random_ready_viral_asset_id(
    db: AsyncSession,
    user: User,
    *,
    industry_slug: str,
    content_type_code: str,
) -> str:
    result = await list_viral_assets(
        db,
        user,
        industry_slug=industry_slug,
        ready_only=True,
        content_type_code=content_type_code,
        limit=100,
    )
    items = [
        item
        for item in result.get("items") or []
        if (item.get("reference_card") or {}).get("content_type_code") == content_type_code
    ]
    if not items:
        raise LookupError("no ready viral assets")
    return str(random.choice(items)["id"])


async def build_mp_decoration_direct_defaults(
    db: AsyncSession,
    employee: ContentEmployee,
    user: User,
    *,
    content_type_name: str,
    content_type_id: str,
    content_type_code: str,
    business_variables: dict[str, Any],
    industry_slug: str = "decoration",
) -> dict[str, Any]:
    creative_style = pick_random_creative_style(content_type_code, content_type_name)
    viral_asset_id = await pick_random_ready_viral_asset_id(
        db,
        user,
        industry_slug=industry_slug,
        content_type_code=content_type_code,
    )
    user_request = format_content_request_json(
        employee=employee,
        user=user,
        content_type_name=content_type_name,
        content_type_id=content_type_id,
        content_type_code=content_type_code,
        business_variables=business_variables,
    )
    return {
        "generation_prompt": DEFAULT_DIRECT_GENERATION_PROMPT,
        "creative_style": creative_style,
        "viral_asset_id": viral_asset_id,
        "user_request": user_request,
    }
