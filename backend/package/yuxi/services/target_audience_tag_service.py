"""目标人群标签知识库：按配置名称读取「目标人群-{segment}-标签」中的标签行。"""

from __future__ import annotations

import json
import re
from typing import Any

from yuxi.services.content_forbidden_words_service import load_knowledge_base_text

# 目标人群配置名与知识库目录名不一致时使用（如毛坯 → 目标人群-通用-标签）
TARGET_AUDIENCE_KB_SEGMENT_ALIASES: dict[str, str] = {
    "毛坯": "通用",
}
GENERIC_TARGET_AUDIENCE_TAG_KB = "目标人群-通用-标签"

_BULLET_PREFIX = re.compile(r"^[-*•\u2022]\s+")
_NUMBER_PREFIX = re.compile(r"^\d+[\.\)、]\s*")
# 小红书话题行：#装修 #家装（排除 markdown 标题连续的 #####）
_HASHTAG_TOKEN = re.compile(r"#(?!#)([^\s#,，;；|]+)")


def target_audience_tag_knowledge_base_name(audience_name: str) -> str:
    name = str(audience_name or "").strip()
    if not name:
        raise ValueError("目标人群不能为空")
    segment = TARGET_AUDIENCE_KB_SEGMENT_ALIASES.get(name, name)
    return f"目标人群-{segment}-标签"


def _append_unique_tags(tags: list[str], seen: set[str], items: list[str]) -> None:
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        tags.append(text)


def parse_audience_tag_lines(text: str) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for line in str(text or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        hashtag_items = _HASHTAG_TOKEN.findall(raw)
        if hashtag_items:
            _append_unique_tags(tags, seen, hashtag_items)
            continue
        if raw.startswith("#"):
            continue
        parts = re.split(r"[、,，;；|]", raw) if raw else []
        for part in parts:
            item = part.strip()
            item = _BULLET_PREFIX.sub("", item)
            item = _NUMBER_PREFIX.sub("", item)
            _append_unique_tags(tags, seen, [item])
    return tags


async def _load_tags_from_knowledge_base(uid: str, kb_name: str) -> list[str]:
    text = await load_knowledge_base_text(uid, kb_name)
    tags = parse_audience_tag_lines(text)
    if not tags:
        raise ValueError(f"“{kb_name}”未解析到可用标签")
    return tags


async def list_target_audience_tags(uid: str, audience_name: str) -> tuple[list[str], str]:
    """返回 (标签列表, 实际使用的知识库名称)。专属库不存在或为空时回退到通用库。"""
    primary_kb = target_audience_tag_knowledge_base_name(audience_name)

    async def _load_or_empty(kb_name: str) -> list[str]:
        try:
            return await _load_tags_from_knowledge_base(uid, kb_name)
        except ValueError:
            return []

    if primary_kb == GENERIC_TARGET_AUDIENCE_TAG_KB:
        return await _load_or_empty(primary_kb), primary_kb
    tags = await _load_or_empty(primary_kb)
    if tags:
        return tags, primary_kb
    generic_tags = await _load_or_empty(GENERIC_TARGET_AUDIENCE_TAG_KB)
    return generic_tags, GENERIC_TARGET_AUDIENCE_TAG_KB


async def enrich_content_request_payload(uid: str, payload: dict[str, Any]) -> dict[str, Any]:
    """按 businessVariables.目标人群 重写 facts.audience（及知识科普 craft_role）。"""
    from yuxi.content.mp_studio_direct import _build_facts, _build_persona_from_payload

    business_variables = payload.get("businessVariables")
    if not isinstance(business_variables, dict):
        return payload
    audience_name = str(business_variables.get("目标人群") or "").strip()
    if not audience_name:
        return payload
    tags, _ = await list_target_audience_tags(uid, audience_name)
    content_type = payload.get("contentType") if isinstance(payload.get("contentType"), dict) else {}
    type_name = str(content_type.get("typeName") or "").strip()
    persona = _build_persona_from_payload(payload)
    enriched = dict(payload)
    enriched["facts"] = _build_facts(type_name, persona, business_variables, audience_tags=tags)
    return enriched


async def enrich_studio_user_request_json(uid: str, user_request: str) -> str:
    try:
        payload = json.loads(user_request)
    except (TypeError, ValueError):
        return user_request
    if not isinstance(payload, dict):
        return user_request
    business_variables = payload.get("businessVariables")
    if not isinstance(business_variables, dict) or not str(business_variables.get("目标人群") or "").strip():
        return user_request
    enriched = await enrich_content_request_payload(uid, payload)
    return f"{json.dumps(enriched, ensure_ascii=False, indent=2)}\n"
