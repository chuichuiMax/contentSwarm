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


__all__ = ["missing_required_template_fields", "template_fact_sources"]
