"""冻结文本块的完整性校验、展示派生和正文合成。"""

from __future__ import annotations

import hashlib
import re
from typing import Any

QUOTE_RENDER_POLICIES = ("semicolon-lines-v1", "checkmark-lines-v1")


def render_semicolon_lines(original_content: str) -> str:
    """仅在尚无换行的中文分号后增加展示换行，不改变其他字符。"""

    return re.sub(r"；(?!\r?\n)", "；\n", original_content)


def render_locked_quote(original_content: str, render_policy: str) -> str:
    """按冻结策略排版报价；仅增加换行和行首标记，保留报价原文。"""

    if render_policy not in QUOTE_RENDER_POLICIES:
        raise ValueError("quote_block 使用了未发布的渲染策略")
    rendered = render_semicolon_lines(original_content)
    if render_policy == "semicolon-lines-v1":
        return rendered
    return "".join(
        f"✅ {line}" if line.strip() and not line.lstrip().startswith("✅") else line
        for line in rendered.splitlines(keepends=True)
    )


def extract_locked_quote_block(production_pack: dict[str, Any]) -> dict[str, Any] | None:
    materials = [
        material
        for material in production_pack.get("materials") or []
        if "quote_block" in (material.get("variable_codes") or [])
    ]
    if not materials:
        return None
    if len(materials) != 1:
        raise ValueError("冻结生产包必须且只能包含一个 quote_block")
    material = materials[0]
    value = (material.get("payload") or {}).get("value") or {}
    original_content = value.get("original_content")
    if not isinstance(original_content, str) or not original_content:
        raise ValueError("quote_block.original_content 不能为空")
    content_hash = hashlib.sha256(original_content.encode("utf-8")).hexdigest()
    if content_hash != value.get("content_hash") or content_hash != (material.get("source") or {}).get("source_hash"):
        raise ValueError("quote_block 原文、内容 Hash 与来源 Hash 不一致")
    if value.get("insertion_policy") != "after-opening-paragraph-v1":
        raise ValueError("quote_block 使用了未发布的插入策略")
    rendered_content = render_locked_quote(original_content, value.get("render_policy"))
    replacement_diffs = []
    platform = (
        production_pack.get("content_rule_bundle", {}).get("runtime_rules", {}).get("viral-platform-expression", {})
    )
    if lexicon := platform.get("forbidden_lexicon"):
        from yuxi.content.model.forbidden_words import replace_forbidden_words

        adapted = replace_forbidden_words(rendered_content, platform["forbidden_replacements"])
        if adapted != rendered_content:
            replacement_diffs.append(
                {
                    "location": "quote_block",
                    "before": rendered_content,
                    "after": adapted,
                    "rule_id": lexicon["snapshot_hash"],
                }
            )
        rendered_content = adapted
    return {
        "material_id": material.get("id"),
        "evidence_ids": list(material.get("evidence_ids") or []),
        "content_hash": content_hash,
        "original_content": original_content,
        "rendered_content": rendered_content,
        "render_policy": value["render_policy"],
        "insertion_policy": value["insertion_policy"],
        "replacement_diffs": replacement_diffs,
    }


def quote_body_limits(production_pack: dict[str, Any], rendered_content: str) -> dict[str, int]:
    constraints = (production_pack.get("channel_profile") or {}).get("body_constraints") or {}
    final_max = int(constraints.get("max_length") or 1000)
    policy = production_pack.get("content_rule_bundle", {}).get("single_blueprint", {})
    creative_max = policy.get("creative_max_chars", 650)
    separators = 4
    return {
        "final_body_max_chars": final_max,
        "creative_body_min_chars": production_pack.get("content_rule_bundle", {})
        .get("single_blueprint", {})
        .get("creative_min_chars", 200),
        "creative_body_max_chars": min(creative_max or final_max, final_max - len(rendered_content) - separators),
    }


def compose_after_opening_paragraph(body: str, rendered_content: str) -> str:
    if not body.strip():
        raise ValueError("创作正文为空，无法插入锁定报价块")
    separator = re.search(r"\n\s*\n", body)
    if separator is None:
        return f"{body}\n\n{rendered_content}"
    return f"{body[: separator.start()]}\n\n{rendered_content}\n\n{body[separator.end() :]}"


__all__ = [
    "QUOTE_RENDER_POLICIES",
    "compose_after_opening_paragraph",
    "extract_locked_quote_block",
    "quote_body_limits",
    "render_locked_quote",
    "render_semicolon_lines",
]
