from __future__ import annotations

import re
from typing import Any


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
        if field.get("kind") != "text" or not label or role in {"", "label", "title", "subtitle", "body_excerpt"}:
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
