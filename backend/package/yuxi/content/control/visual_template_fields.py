from __future__ import annotations

import re
from typing import Any

from yuxi.content.v3.title_formula_slots import process_title_options

FACT_TEMPLATE_ROLES = frozenset(
    {"project_name", "project_name_en", "project_area", "designer", "completion_year", "brand_name"}
)
_FACT_ROLES = FACT_TEMPLATE_ROLES
_NARRATIVE_ROLES = frozenset({"title", "subtitle", "body_excerpt"})
_UNSUPPORTED_CLAIMS = ("免费", "保证", "保价", "最低", "第一", "省钱", "零风险")


def template_fact_sources(brief: dict[str, Any]) -> dict[str, str]:
    form_values = brief.get("form_values") or {}
    brand = brief.get("brand") or {}
    return {
        "project_name": str(form_values.get("project_name") or form_values.get("community_name") or "").strip(),
        "project_name_en": str(
            form_values.get("project_name_en") or form_values.get("community_name_en") or ""
        ).strip(),
        "project_area": str(
            form_values.get("project_area") or form_values.get("area") or form_values.get("area_sqm") or ""
        ).strip(),
        "designer": str(form_values.get("designer") or form_values.get("designer_name") or "").strip(),
        "completion_year": str(form_values.get("completion_year") or form_values.get("year") or "").strip(),
        "brand_name": str(brand.get("name") or form_values.get("brand_name") or "").strip(),
    }


def missing_required_template_fields(
    declarations: list[dict[str, Any]], brief: dict[str, Any]
) -> dict[str, dict[str, int]]:
    sources = template_fact_sources(brief)
    missing: dict[str, dict[str, int]] = {}
    for field in declarations:
        label = str(field.get("label") or "").strip()
        field_key = str(field.get("key") or label).strip()
        role = str(field.get("semanticRole") or "").strip()
        constraints = field.get("constraints") or {}
        if field.get("kind") != "text" or not label or role in {"", "label", *_NARRATIVE_ROLES, *_FACT_ROLES}:
            continue
        value = sources.get(role, "")
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


__all__ = [
    "FACT_TEMPLATE_ROLES",
    "compile_cover_narrative_fields",
    "is_decorative_cover_label",
    "is_ordinal_badge_template_field",
    "missing_required_template_fields",
    "ordinal_badge_fill_value",
    "prepare_visual_plan_template_fields",
    "template_fact_sources",
]
