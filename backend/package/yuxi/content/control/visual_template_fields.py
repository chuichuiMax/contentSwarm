from __future__ import annotations

import json
import re
from typing import Any

from yuxi.content.rules import brief_variable_map
from yuxi.content.v3.title_formula_slots import process_title_options

FACT_TEMPLATE_ROLES = frozenset(
    {
        "project_name",
        "project_name_en",
        "project_area",
        "designer",
        "completion_year",
        "brand_name",
        "design_style",
        "layout",
        "service_region",
        "target_audience",
        "process_type",
        "service_years",
        "job_position",
    }
)
_FACT_ROLES = FACT_TEMPLATE_ROLES
_NARRATIVE_ROLES = frozenset({"title", "subtitle", "body_excerpt"})
_UNSUPPORTED_CLAIMS = ("免费", "保证", "保价", "最低", "第一", "省钱", "零风险")
# 与 HyCanvas SaveAsTemplateDialog semanticRoles 一致：semanticRole -> 业务变量名
SEMANTIC_ROLE_BUSINESS_LABEL: dict[str, str] = {
    "title": "内容标题",
    "subtitle": "内容副标题",
    "project_name": "项目名称",
    "project_name_en": "项目英文名",
    "project_area": "项目面积",
    "designer": "设计师",
    "completion_year": "完成年份",
    "brand_name": "品牌名称",
    "design_style": "设计风格",
    "layout": "户型",
    "service_region": "所在区域",
    "target_audience": "目标人群",
    "process_type": "工艺类型",
    "service_years": "年限",
    "job_position": "岗位",
    "body_excerpt": "正文摘要",
}
SEMANTIC_ROLE_VARIABLE_KEYS: dict[str, tuple[str, ...]] = {
    "project_name": (
        "project_name",
        "community_name",
        "楼盘信息",
        "项目名称",
        "case_background",
        "project_site",
    ),
    "project_name_en": ("project_name_en", "community_name_en", "项目英文名"),
    "project_area": ("project_area", "area", "area_sqm", "quantity", "外框面积", "项目面积"),
    "designer": ("designer", "designer_name", "设计师"),
    "completion_year": ("completion_year", "year", "完成年份"),
    "brand_name": ("brand_name", "品牌名称"),
    "design_style": ("设计风格", "design_style", "scene"),
    "layout": ("户型", "房屋布局", "户型布局", "layout"),
    "service_region": ("所在区域", "region", "city", "location", "serviceCity"),
    "target_audience": ("目标人群", "target_audience", "audience"),
    "process_type": ("工艺类型", "process_type", "工艺名称", "process"),
    "service_years": ("从业年限", "年限", "service_years"),
    "job_position": ("岗位", "job", "job_title", "position"),
}
# 图层 label 与业务变量名一致时可无 semanticRole 直读简报
_BUSINESS_VARIABLE_LABELS = frozenset(
    key for keys in SEMANTIC_ROLE_VARIABLE_KEYS.values() for key in keys
) | frozenset(SEMANTIC_ROLE_BUSINESS_LABEL.values())
_CHINESE_SEMANTIC_ROLE: dict[str, str] = {label: role for role, label in SEMANTIC_ROLE_BUSINESS_LABEL.items()}
# 封面模板上常见的人群角标文案（无 semanticRole 时的兜底）
_TARGET_AUDIENCE_COVER_LABELS = frozenset(
    {
        "毛坯",
        "毛坯房",
        "精装房",
        "旧房改造",
        "旧房翻新",
        "新房装修",
        "局部改造",
        "别墅",
        "乡墅",
        "改善型",
    }
)


def _label_maps_to_target_audience(label: str, role: str) -> bool:
    if role == "target_audience":
        return True
    text = str(label or "").strip()
    if not text:
        return False
    if "目标人群" in text:
        return True
    return text in _TARGET_AUDIENCE_COVER_LABELS


def _clean_field_label(label: str) -> str:
    text = str(label or "").strip()
    if len(text) >= 2 and text.startswith("<") and text.endswith(">"):
        return text[1:-1].strip()
    return text


def _infer_template_field_role(declared_role: str, label: str) -> str:
    role = normalize_semantic_role(declared_role)
    if role in _FACT_ROLES | _NARRATIVE_ROLES or role == "label":
        return role
    for candidate in (_clean_field_label(label), str(label or "").strip()):
        if not candidate:
            continue
        inferred = normalize_semantic_role(candidate)
        if inferred in _FACT_ROLES | _NARRATIVE_ROLES:
            return inferred
    return role


def normalize_semantic_role(role: str) -> str:
    text = str(role or "").strip()
    if not text:
        return ""
    if text in _FACT_ROLES or text in _NARRATIVE_ROLES or text == "label":
        return text
    mapped = _CHINESE_SEMANTIC_ROLE.get(text)
    if mapped:
        return mapped
    for normalized, keys in SEMANTIC_ROLE_VARIABLE_KEYS.items():
        if text in keys:
            return normalized
    return text


def business_variable_label_for_role(role: str) -> str:
    normalized = normalize_semantic_role(role)
    return SEMANTIC_ROLE_BUSINESS_LABEL.get(normalized, normalized or "业务变量")


def _pick_brief_variable(variables: dict[str, Any], *keys: str) -> str:
    for key in keys:
        raw = variables.get(key)
        if raw is None or raw == "" or raw == []:
            continue
        if isinstance(raw, (list, tuple)):
            text = "、".join(str(item).strip() for item in raw if str(item).strip())
        else:
            text = str(raw).strip()
        if text:
            return text
    return ""


def _brief_text(brief: dict[str, Any], *keys: str) -> str:
    sources = [brief.get("form_values"), brief.get("business_variables")]
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            raw = source.get(key)
            if isinstance(raw, (list, tuple)):
                text = "、".join(str(item).strip() for item in raw if str(item).strip())
            else:
                text = str(raw or "").strip()
            if text:
                return text
    return ""


def _service_years(brief: dict[str, Any]) -> str:
    years = _brief_text(brief, "从业年限", "年限", "service_years")
    if years and "年" not in years:
        return f"{years}年"
    return years


def _production_pack_values(brief: dict[str, Any]) -> dict[str, Any]:
    """内容需求生产包里的业务变量，封面生成时和表单字段同一套来源。"""

    form_values = brief.get("form_values") if isinstance(brief.get("form_values"), dict) else {}
    merged: dict[str, Any] = {}
    for raw in (brief.get("user_request"), form_values.get("user_request")):
        if not isinstance(raw, str) or not raw.strip().startswith("{"):
            continue
        try:
            payload = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        for name in ("businessVariables", "business_variables", "facts"):
            section = payload.get(name)
            if not isinstance(section, dict):
                continue
            for key, value in section.items():
                if value in (None, "", [], {}):
                    continue
                merged.setdefault(str(key), value)
    return merged


def template_fact_sources(brief: dict[str, Any]) -> dict[str, str]:
    variables = brief_variable_map(brief)
    for key, value in _production_pack_values(brief).items():
        if not str(variables.get(key) or "").strip():
            variables[key] = value
    brand = brief.get("brand") if isinstance(brief.get("brand"), dict) else {}
    brand_name = str(brand.get("name") or "").strip() or _pick_brief_variable(variables, "brand_name")
    sources = {
        role: _pick_brief_variable(variables, *keys)
        for role, keys in SEMANTIC_ROLE_VARIABLE_KEYS.items()
    }
    sources["brand_name"] = brand_name or sources.get("brand_name", "")
    years = sources.get("service_years") or _service_years(brief)
    if years and "年" not in years:
        years = f"{years}年"
    sources["service_years"] = years
    return sources


def _value_from_semantic_role(
    role: str,
    *,
    sources: dict[str, str],
    brief: dict[str, Any],
    template_fields: dict[str, str],
    field_key: str,
    label: str,
) -> str:
    normalized = normalize_semantic_role(role)
    if field_key in template_fields and str(template_fields[field_key]).strip():
        return str(template_fields[field_key]).strip()
    if normalized in sources and str(sources[normalized]).strip():
        return str(sources[normalized]).strip()
    if normalized in SEMANTIC_ROLE_VARIABLE_KEYS:
        variables = brief_variable_map(brief)
        picked = _pick_brief_variable(variables, *SEMANTIC_ROLE_VARIABLE_KEYS[normalized])
        if picked:
            return picked
    if not role and _label_maps_to_target_audience(label, role):
        return str(sources.get("target_audience") or "").strip()
    if normalized in _NARRATIVE_ROLES:
        return str(sources.get(normalized) or "").strip()
    if not role:
        if "副标题" in label:
            return sources.get("subtitle", "")
        if "标题" in label or "语录" in label:
            return sources.get("title", "")
        return sources.get("body_excerpt", "")
    return ""


def _value_from_field_label(label: str, *, brief: dict[str, Any], sources: dict[str, str]) -> str:
    variables = brief_variable_map(brief)
    for candidate in (_clean_field_label(label), str(label or "").strip()):
        if not candidate:
            continue
        if candidate in _BUSINESS_VARIABLE_LABELS:
            picked = _pick_brief_variable(variables, candidate)
            if picked:
                return picked
        inferred = normalize_semantic_role(candidate)
        if inferred in sources and str(sources[inferred]).strip():
            return str(sources[inferred]).strip()
        if inferred in SEMANTIC_ROLE_VARIABLE_KEYS:
            picked = _pick_brief_variable(variables, *SEMANTIC_ROLE_VARIABLE_KEYS[inferred])
            if picked:
                return picked
    return ""


def missing_required_template_fields(
    declarations: list[dict[str, Any]], brief: dict[str, Any]
) -> dict[str, dict[str, int]]:
    sources = template_fact_sources(brief)
    missing: dict[str, dict[str, int]] = {}
    for field in declarations:
        label = str(field.get("label") or "").strip()
        field_key = str(field.get("key") or label).strip()
        role = normalize_semantic_role(str(field.get("semanticRole") or "").strip())
        constraints = field.get("constraints") or {}
        if field.get("kind") != "text" or not label or role in {"", "label", *_NARRATIVE_ROLES, *_FACT_ROLES}:
            continue
        value = _value_from_semantic_role(
            role,
            sources=sources,
            brief=brief,
            template_fields={},
            field_key=field_key,
            label=label,
        )
        if role == "project_area" and not re.search(r"\d+(?:\.\d+)?", value):
            value = ""
        elif role == "completion_year" and not re.search(r"(?:19|20)\d{2}", value):
            value = ""
        if constraints.get("required") and not value:
            missing[field_key] = {
                key: int(constraints[key])
                for key in ("maxChars", "maxCharsPerLine", "maxLines")
                if isinstance(constraints.get(key), int) and constraints[key] > 0
            }
    return missing


def is_decorative_cover_label(value: object) -> bool:
    """序号角标（1 / 01）不是封面主标题。"""

    text = str(value or "").strip()
    if not text:
        return True
    return bool(re.fullmatch(r"0?\d{1,2}", text))


def is_ordinal_badge_template_field(field: dict[str, Any]) -> bool:
    """模板里的 01/1 短序号框不能当叙事标题，否则会把整页 title 上限压成 1～2 字。"""

    role = str(field.get("semanticRole") or "").strip()
    if role == "label":
        return True
    label = str(field.get("label") or "").strip()
    if is_decorative_cover_label(label):
        return True
    constraints = field.get("constraints") or {}
    max_chars = constraints.get("maxChars")
    return role in _NARRATIVE_ROLES and isinstance(max_chars, int) and 0 < max_chars <= 2


def ordinal_badge_fill_value(field: dict[str, Any]) -> str:
    """给序号角标填一个不超过 maxChars 的装饰值。label=01 且 maxChars=1 时写成 1。"""

    label = str(field.get("label") or "").strip()
    value = label if is_decorative_cover_label(label) else "1"
    if not value:
        value = "1"
    max_chars = (field.get("constraints") or {}).get("maxChars")
    if isinstance(max_chars, int) and max_chars > 0:
        value = value[-max_chars:]
    return value


def _normalize_cover_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def _fit_cover_text(value: str, max_chars: int | None) -> str:
    text = re.sub(r"[\s？?！!。，,、：:]+", "", str(value or "").strip())
    if any(term in text for term in _UNSUPPORTED_CLAIMS):
        return ""
    if max_chars and len(text) > max_chars:
        text = text[:max_chars]
    return text


def compile_cover_narrative_fields(
    *,
    declarations: list[dict[str, Any]],
    title: str,
    process_values: list[str] | tuple[str, ...] = (),
    audience: list[str] | tuple[str, ...] = (),
) -> dict[str, str]:
    """按锁定标题、工艺和人群为封面叙事框生成不重复且不超长的文案。"""

    keys: list[str] = []
    limits: dict[str, int | None] = {}
    for field in declarations:
        if field.get("kind") != "text":
            continue
        role = str(field.get("semanticRole") or "")
        if role not in _NARRATIVE_ROLES or is_ordinal_badge_template_field(field):
            continue
        key = str(field.get("key") or field.get("label") or "").strip()
        if not key:
            continue
        keys.append(key)
        max_chars = (field.get("constraints") or {}).get("maxChars")
        limits[key] = int(max_chars) if isinstance(max_chars, int) and max_chars > 0 else None

    candidates = [
        *process_title_options(process_values),
        *(str(item).strip() for item in audience if str(item).strip()),
        str(title or "").strip(),
    ]
    assigned: dict[str, str] = {}
    used: set[str] = set()
    for key in keys:
        for candidate in candidates:
            fitted = _fit_cover_text(candidate, limits[key])
            normalized = _normalize_cover_text(fitted)
            if not normalized or normalized in used:
                continue
            assigned[key] = fitted
            used.add(normalized)
            break
    return assigned


def prepare_visual_plan_template_fields(
    *,
    supplied: dict[str, str],
    allowed: dict[str, dict[str, int]],
    required: dict[str, dict[str, int]],
    compiled: dict[str, str],
) -> dict[str, str]:
    """只保留授权叙事框，缺项或超长项用锁定文案补齐。"""

    authorized = {**allowed, **required}
    prepared: dict[str, str] = {}
    used: set[str] = set()
    for key, raw in (supplied or {}).items():
        if key not in authorized:
            continue
        value = str(raw).strip()
        max_chars = authorized[key].get("maxChars")
        normalized = _normalize_cover_text(value)
        if (
            not value
            or not normalized
            or normalized in used
            or any(term in value for term in _UNSUPPORTED_CLAIMS)
            or (isinstance(max_chars, int) and max_chars > 0 and len(value.replace("\n", "")) > max_chars)
        ):
            continue
        prepared[key] = value
        used.add(normalized)
    for key, constraints in authorized.items():
        if key in prepared:
            continue
        max_chars = constraints.get("maxChars")
        for candidate in (compiled.get(key), *(compiled or {}).values()):
            fitted = _fit_cover_text(str(candidate or ""), max_chars if isinstance(max_chars, int) else None)
            token = _normalize_cover_text(fitted)
            if not token or token in used:
                continue
            prepared[key] = fitted
            used.add(token)
            break
    return prepared


def resolve_hycanvas_template_fields(
    declarations: list[dict[str, Any]],
    *,
    visual_text: list[str],
    brief: dict[str, Any],
    template_fields: dict[str, str] | None = None,
) -> dict[str, str]:
    """根据模板语义声明从已锁定内容中解析 HyCanvas 字段。"""

    template_fields = template_fields or {}
    sources = {
        "title": visual_text[0] if visual_text else "",
        "subtitle": visual_text[1] if len(visual_text) > 1 else "",
        "body_excerpt": visual_text[1] if len(visual_text) > 1 else (visual_text[0] if visual_text else ""),
        **template_fact_sources(brief),
    }
    fields: dict[str, str] = {}
    for field in declarations:
        if field.get("kind") != "text" or not field.get("label"):
            continue
        label = str(field["label"]).strip()
        field_key = str(field.get("key") or label)
        declared_role = str(field.get("semanticRole") or "").strip()
        role = _infer_template_field_role(declared_role, label)
        if role == "label":
            continue
        value = _value_from_semantic_role(
            role,
            sources=sources,
            brief=brief,
            template_fields=template_fields,
            field_key=field_key,
            label=label,
        ).strip()
        if not value:
            value = _value_from_field_label(label, brief=brief, sources=sources).strip()
        if role == "project_area":
            match = re.search(r"\d+(?:\.\d+)?", value)
            value = match.group(0) if match else ""
            if value and "㎡" in label:
                value = f"{value}㎡"
        elif role == "completion_year":
            match = re.search(r"(?:19|20)\d{2}", value)
            value = match.group(0) if match else ""
        elif role == "service_years" and value and "年" not in value:
            value = f"{value}年"
        constraints = field.get("constraints") or {}
        if constraints.get("required") and not value:
            if role in _FACT_ROLES:
                continue
            biz = business_variable_label_for_role(role)
            raise ValueError(
                f"封面模板必填字段“{label}”未完成自动适配"
                + (f"（已绑定业务变量「{biz}」，请确认任务已填写）" if biz else "")
            )
        if role in _FACT_ROLES or role in _NARRATIVE_ROLES:
            value = re.sub(r"[\r\n]+", "", value)
        if not value and role in _FACT_ROLES and not constraints.get("required"):
            continue
        fields[field_key] = value
    return fields


__all__ = [
    "FACT_TEMPLATE_ROLES",
    "SEMANTIC_ROLE_BUSINESS_LABEL",
    "business_variable_label_for_role",
    "compile_cover_narrative_fields",
    "is_decorative_cover_label",
    "is_ordinal_badge_template_field",
    "missing_required_template_fields",
    "normalize_semantic_role",
    "ordinal_badge_fill_value",
    "prepare_visual_plan_template_fields",
    "resolve_hycanvas_template_fields",
    "template_fact_sources",
]
