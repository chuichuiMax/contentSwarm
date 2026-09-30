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
def _wrap_visual_text(value: str, *, max_chars_per_line: int, max_lines: int) -> str:
    if len(value) <= max_chars_per_line or "\n" in value:
        return value
    if max_lines == 2 and len(value) <= max_chars_per_line * 2:
        minimum = len(value) - max_chars_per_line
        candidates = range(minimum, max_chars_per_line + 1)
        preferred_endings = "，。！？；：、,.!?;:㎡%元万亿"
        breakpoint = min(
            candidates,
            key=lambda index: (value[index - 1] not in preferred_endings, abs(index * 2 - len(value))),
        )
        return f"{value[:breakpoint]}\n{value[breakpoint:]}"
    lines = [value[index : index + max_chars_per_line] for index in range(0, len(value), max_chars_per_line)]
    if len(lines) > max_lines:
        raise ValueError(f"文字超过模板限制的 {max_lines} 行")
    return "\n".join(lines)


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
        label = str(field["label"])
        field_key = str(field.get("key") or label)
        role = str(field.get("semanticRole") or "")
        if role == "label":
            continue
        value = str(template_fields.get(field_key) or sources.get(role) or "").strip()
        if not role:
            if field_key in template_fields:
                value = str(template_fields[field_key]).strip()
            elif "副标题" in label:
                value = sources["subtitle"]
            elif "标题" in label or "语录" in label:
                value = sources["title"]
            else:
                value = sources["body_excerpt"]
        if role == "project_area":
            match = re.search(r"\d+(?:\.\d+)?", value)
            value = match.group(0) if match else ""
        elif role == "completion_year":
            match = re.search(r"(?:19|20)\d{2}", value)
            value = match.group(0) if match else ""
        constraints = field.get("constraints") or {}
        if constraints.get("required") and not value:
            raise ValueError(f"封面模板必填字段“{label}”未完成自动适配")
        max_chars = constraints.get("maxChars")
        if isinstance(max_chars, int) and max_chars > 0 and len(value.replace("\n", "")) > max_chars:
            raise ValueError(f"封面字段“{label}”超过模板限制的 {max_chars} 个字符")
        max_chars_per_line = constraints.get("maxCharsPerLine")
        max_lines = constraints.get("maxLines")
        if (
            isinstance(max_chars_per_line, int)
            and max_chars_per_line > 0
            and isinstance(max_lines, int)
            and max_lines > 1
            and "\n" not in value
            and len(value) > max_chars_per_line
        ):
            try:
                value = _wrap_visual_text(
                    value,
                    max_chars_per_line=max_chars_per_line,
                    max_lines=max_lines,
                )
            except ValueError as exc:
                raise ValueError(f"封面字段“{label}”超过模板限制的 {max_lines} 行") from exc
        fields[field_key] = value
    return fields


__all__ = [
    "missing_required_template_fields",
    "resolve_hycanvas_template_fields",
    "template_fact_sources",
]
