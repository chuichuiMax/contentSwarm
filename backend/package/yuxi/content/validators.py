from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from yuxi.content.rules import brief_variable_map

NUMBER_UNITS = r"万元|平方米|平米|m²|元|天|周|月|年|岁|个|位|次|㎡|平|人|%"
NUMBER_PATTERN = re.compile(rf"\d+(?:\.\d+)?(?:{NUMBER_UNITS})?")
NUMBER_TOKEN_PATTERN = re.compile(rf"(?P<value>\d+(?:\.\d+)?)(?P<unit>{NUMBER_UNITS})?")
HIGH_RISK_CLAIMS = ("保证", "百分百", "100%", "一定有效", "绝对", "零风险", "最便宜", "第一")
NUMBER_UNIT_SPACING = re.compile(rf"(?<=\d)\s+(?={NUMBER_UNITS})")
PERSONA_NUMBER_UNITS = {
    "age": "岁",
    "workYears": "年",
    "work_years": "年",
    "servedSiteCount": "个",
    "ownerRecommendCount": "位",
}


def _canonical_number_token(token: str) -> str:
    matched = NUMBER_TOKEN_PATTERN.fullmatch(token)
    if matched is None:
        return token
    value = matched.group("value")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    unit = matched.group("unit") or ""
    if unit in {"平方米", "平米", "m²", "平"}:
        unit = "㎡"
    return f"{value}{unit}"


def _evidence_id(task_id: str, key: str, value: Any) -> str:
    digest = hashlib.sha256(
        f"{task_id}:{key}:{json.dumps(value, ensure_ascii=False, sort_keys=True)}".encode()
    ).hexdigest()[:16]
    return f"ev_{digest}"


def normalize_manual_evidence(task_id: str, brief: dict[str, Any]) -> dict[str, Any]:
    variables = brief_variable_map(brief)
    items = []
    for key, value in variables.items():
        if value in (None, "", []):
            continue
        items.append(
            {
                "id": _evidence_id(task_id, key, value),
                "type": "business_fact",
                "key": key,
                "value": value,
                "source_type": "manual_input",
                "source_id": f"field_{key}",
                "source_version": "brief-v1",
                "verified_status": "user_confirmed",
                "allowed_usage": ["title", "body"],
            }
        )
    return {"items": items, "summary": {"manual": len(items), "knowledge": 0, "business_api": 0}}


def merge_evidence(base: dict[str, Any], additions: list[dict[str, Any]]) -> dict[str, Any]:
    items = list(base.get("items") or [])
    known = {item.get("id") for item in items}
    for item in additions:
        if item.get("id") not in known:
            items.append(item)
            known.add(item.get("id"))
    summary = dict(base.get("summary") or {})
    summary["knowledge"] = sum(1 for item in items if item.get("source_type") == "knowledge_base")
    summary["manual"] = sum(1 for item in items if item.get("source_type") == "manual_input")
    summary["business_api"] = sum(1 for item in items if item.get("source_type") in {"business_api", "mcp"})
    return {"items": items, "summary": summary}


def _structured_number_aliases(value: Any, inherited_unit: str = "") -> set[str]:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped[0] not in "{[":
            return set()
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            return set()

    aliases: set[str] = set()
    if isinstance(value, dict):
        amount = value.get("amount")
        amount_unit = str(value.get("unit") or inherited_unit).strip()
        if (
            isinstance(amount, (int, float))
            and not isinstance(amount, bool)
            and amount_unit
            and re.fullmatch(NUMBER_UNITS, amount_unit)
        ):
            scalar = str(int(amount)) if isinstance(amount, float) and amount.is_integer() else str(amount)
            aliases.add(_canonical_number_token(f"{scalar}{amount_unit}"))
        if amount_unit:
            for key, nested in value.items():
                if not (key.endswith("_amount") or key.endswith("_total")):
                    continue
                if not isinstance(nested, (int, float)) or isinstance(nested, bool):
                    continue
                scalar = str(int(nested)) if isinstance(nested, float) and nested.is_integer() else str(nested)
                aliases.add(_canonical_number_token(f"{scalar}{amount_unit}"))
        for key, nested in value.items():
            unit = PERSONA_NUMBER_UNITS.get(key)
            scalar = str(nested).strip()
            if unit and re.fullmatch(r"\d+(?:\.\d+)?", scalar):
                aliases.add(_canonical_number_token(f"{scalar}{unit}"))
            aliases.update(_structured_number_aliases(nested, amount_unit))
    elif isinstance(value, list):
        for nested in value:
            aliases.update(_structured_number_aliases(nested, inherited_unit))
    return aliases


def evidence_number_tokens(evidence_bundle: dict[str, Any]) -> list[str]:
    evidence_values = [
        item.get("value") for item in evidence_bundle.get("items") or [] if item.get("value") is not None
    ]
    evidence_text = " ".join(json.dumps(value, ensure_ascii=False) for value in evidence_values)
    tokens = {
        _canonical_number_token(token) for token in NUMBER_PATTERN.findall(NUMBER_UNIT_SPACING.sub("", evidence_text))
    }
    for item in evidence_bundle.get("items") or []:
        value = item.get("value")
        unit = str((item.get("metadata") or {}).get("unit") or "").strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool) and unit:
            scalar = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
            tokens.add(_canonical_number_token(f"{scalar}{unit}"))
        if "quote_block" in (item.get("variable_codes") or []) and isinstance(value, dict):
            # 中文报价常省略人民币单位（1000/项），补写“元”不构成新数字。
            for amount in re.findall(
                r"(?<![\d.])(\d+(?:\.\d+)?)\s*/\s*(?:㎡|平米|平方米|平|项|个|间|套|米)",
                value.get("original_content", ""),
            ):
                tokens.add(_canonical_number_token(f"{amount}元"))
        if item.get("source_type") not in {None, "manual_input"}:
            continue
        if value is None:
            continue
        tokens.update(_structured_number_aliases(value))
        raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        for field, unit in PERSONA_NUMBER_UNITS.items():
            for number in re.findall(rf'"{field}"\s*:\s*"?(\d+(?:\.\d+)?)"?', raw):
                tokens.add(_canonical_number_token(f"{number}{unit}"))
    return sorted(tokens)


def unsupported_number_tokens(content: str, evidence_bundle: dict[str, Any]) -> list[str]:
    values = [item.get("value") for item in evidence_bundle.get("items") or [] if item.get("value") is not None]
    evidence_text = " ".join(json.dumps(value, ensure_ascii=False) for value in values)
    evidence_text = NUMBER_UNIT_SPACING.sub("", evidence_text)
    content = re.sub(r"[0-9]\ufe0f?\u20e3", "", content)
    allowed = set(evidence_number_tokens(evidence_bundle))
    unsupported = set()
    for number in NUMBER_PATTERN.findall(content):
        if number in evidence_text or _canonical_number_token(number) in allowed:
            continue
        unsupported.add(number)
    return sorted(unsupported)


def validate_content(
    *,
    title: str,
    body: str,
    topics: list[str],
    brief: dict[str, Any],
    evidence_bundle: dict[str, Any],
    strategy: dict[str, Any],
    title_publication_year: str | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    combined = f"{title}\n{body}\n{' '.join(topics)}"
    allowed_year_tokens = {title_publication_year, f"{title_publication_year}年"} if title_publication_year else set()
    unsupported = set(unsupported_number_tokens(title, evidence_bundle)) - allowed_year_tokens
    unsupported.update(unsupported_number_tokens(f"{body}\n{' '.join(topics)}", evidence_bundle))
    for number in sorted(unsupported):
        checks.append(
            {
                "code": "FACT_NUMBER_WITHOUT_SOURCE",
                "level": "error",
                "location": "content",
                "message": f"数字“{number}”没有出现在证据包中",
                "evidence_ids": [],
                "suggestion": "删除该数字，或补充可追溯的业务事实/知识来源",
            }
        )

    forbidden_terms = brief.get("forbidden_terms") or []
    for term in forbidden_terms:
        if term and term in combined:
            checks.append(
                {
                    "code": "CONTENT_FORBIDDEN_TERM",
                    "level": "error",
                    "location": "content",
                    "message": f"包含明确禁止的表达“{term}”",
                    "evidence_ids": [],
                    "suggestion": "删除或改写该表达",
                }
            )

    for term in brief.get("required_terms") or []:
        if term and term not in combined:
            checks.append(
                {
                    "code": "CONTENT_REQUIRED_TERM_MISSING",
                    "level": "warning",
                    "location": "content",
                    "message": f"缺少要求包含的表达“{term}”",
                    "evidence_ids": [],
                    "suggestion": "在不影响自然表达的前提下补充",
                }
            )

    for claim in HIGH_RISK_CLAIMS:
        # “第一次刷到”等明确时间/步骤序数不属于排名宣传，避免无效回修。
        matched = (
            re.search(
                r"第一(?!次|天|周|月|年|步|阶段|版|期|轮|页|张|个|条|段|件|套|层|集|批|遍|回|季度|部分)",
                combined,
            )
            if claim == "第一"
            else claim in combined
        )
        if matched:
            checks.append(
                {
                    "code": "CONTENT_HIGH_RISK_CLAIM",
                    "level": "error",
                    "location": "content",
                    "message": f"检测到高风险绝对化表达“{claim}”",
                    "evidence_ids": [],
                    "suggestion": "改为有边界、可验证的客观表达",
                }
            )

    if (
        not strategy.get("methods")
        or not strategy.get("title_formula_code")
        or not (strategy.get("body_formula_code") or strategy.get("content_formula_code"))
    ):
        checks.append(
            {
                "code": "CONTENT_STRATEGY_SNAPSHOT_MISSING",
                "level": "error",
                "location": "strategy",
                "message": "内容缺少完整策略快照",
                "evidence_ids": [],
                "suggestion": "重新完成策略阶段后生成",
            }
        )

    status = "blocked" if any(item["level"] == "error" for item in checks) else "warning" if checks else "passed"
    return {"status": status, "checks": checks}


def lock_modular_topics(topics: list[str], rule_bundle: dict[str, Any]) -> list[str]:
    """按冻结候选池去重并补齐话题，避免把互斥数量规则交给模型回修。"""

    topic_rules = (rule_bundle.get("runtime_rules") or {}).get("viral-topic-author") or {}
    if not topic_rules:
        return [text for item in topics if (text := str(item).strip().lstrip("#").strip())]
    required = int(topic_rules.get("topic_count") or 10)
    must_use_pool = bool(topic_rules.get("must_use_candidate_pool"))
    must_be_unique = topic_rules.get("must_be_unique", True) is not False

    def normalize(item: Any) -> str:
        return str(item or "").strip().lstrip("#").strip()

    pool = list(dict.fromkeys(text for item in rule_bundle.get("topic_candidates") or [] if (text := normalize(item))))
    allowed = set(pool)
    locked: list[str] = []
    seen: set[str] = set()
    for item in topics:
        text = normalize(item)
        if not text or (must_use_pool and text not in allowed) or (must_be_unique and text in seen):
            continue
        locked.append(text)
        seen.add(text)
        if len(locked) == required:
            return locked
    for item in pool:
        if item in seen:
            continue
        locked.append(item)
        seen.add(item)
        if len(locked) == required:
            return locked
    return locked


def validate_modular_content(
    *,
    title: str,
    body: str,
    topics: list[str],
    draft: dict[str, Any],
    brief: dict[str, Any],
    evidence_bundle: dict[str, Any],
    rule_bundle: dict[str, Any],
) -> list[dict[str, Any]]:
    """执行 v4 可确定判断的运营规则；自然程度继续交给审核 Agent。"""

    checks: list[dict[str, Any]] = []
    rules = rule_bundle.get("runtime_rules") or {}
    topic_rules = rules.get("viral-topic-author") or {}
    required_topic_count = int(topic_rules.get("topic_count") or 10)
    normalized_topics = [str(topic).strip().lstrip("#").strip() for topic in topics]
    direct_reference = (rule_bundle.get("single_blueprint") or {}).get("writing_mode") in {
        "direct_reference",
        "raw_reference_text",
    }
    if not direct_reference and len(topics) != required_topic_count:
        checks.append(
            {
                "code": "TOPIC_COUNT_MISMATCH",
                "level": "error",
                "location": "topics",
                "message": f"话题必须恰好 {required_topic_count} 个，当前为 {len(topics)} 个",
                "evidence_ids": [],
            }
        )
    if len(normalized_topics) != len(set(normalized_topics)):
        checks.append(
            {
                "code": "TOPIC_DUPLICATED",
                "level": "error",
                "location": "topics",
                "message": "话题存在重复项",
                "evidence_ids": [],
            }
        )
    allowed_topics = {str(item).strip().lstrip("#").strip() for item in rule_bundle.get("topic_candidates") or []}
    outside_pool = sorted({item for item in normalized_topics if item and item not in allowed_topics})
    if topic_rules.get("must_use_candidate_pool") and outside_pool:
        checks.append(
            {
                "code": "TOPIC_OUTSIDE_CANDIDATE_POOL",
                "level": "error",
                "location": "topics",
                "message": "话题不在本次冻结候选池中: " + "、".join(outside_pool),
                "evidence_ids": [],
            }
        )

    platform_rules = rules.get("viral-platform-expression") or {}
    combined = "\n".join([title, body, *topics])
    forbidden = (platform_rules.get("forbidden_lexicon") or {}).get("alternatives")
    if forbidden is None:
        forbidden = platform_rules.get("forbidden_replacements") or {}
    residual_terms = [term for term in forbidden if term in combined]
    if residual_terms:
        checks.append(
            {
                "code": "CONTENT_FORBIDDEN_TERM",
                "level": "error",
                "location": "content",
                "message": "问题词替换后仍有残留: " + "、".join(residual_terms),
                "evidence_ids": [],
            }
        )
    direct_cta_patterns = list(platform_rules.get("direct_cta_patterns") or [])
    direct_cta_patterns.extend((r"把.{0,8}(?:户型|项目).{0,4}发", r"(?:评论|私信|留言).{0,10}(?:户型|项目|告诉|联系)"))
    matched_cta = [] if direct_reference else [pattern for pattern in direct_cta_patterns if re.search(pattern, body)]
    if matched_cta:
        checks.append(
            {
                "code": "CTA_TOO_DIRECT",
                "level": "error",
                "location": "body",
                "message": "结尾或正文包含强引导评论、私信或发送资料的表达",
                "evidence_ids": [],
                "matched_terms": matched_cta,
            }
        )
    matched_risk = [term for term in platform_rules.get("high_risk_claims") or [] if term in combined]
    if matched_risk:
        checks.append(
            {
                "code": "CONTENT_HIGH_RISK_CLAIM",
                "level": "error",
                "location": "content",
                "message": "包含高风险或绝对化宣传: " + "、".join(matched_risk),
                "evidence_ids": [],
            }
        )

    natural_rules = rules.get("viral-natural-expression") or {}
    matched_mechanical = [term for term in natural_rules.get("mechanical_markers") or [] if term in body]
    if matched_mechanical:
        checks.append(
            {
                "code": "MECHANICAL_META_EXPRESSION",
                "level": natural_rules.get("mechanical_marker_level", "error"),
                "location": "body",
                "message": "正文包含可优化的报幕式措辞；是否严重影响理解或身份表达需结合上下文审核",
                "suggestion": "可直接描述场景、事实或动作，保留原有信息",
                "evidence_ids": [],
                "matched_terms": matched_mechanical,
            }
        )

    layout_rules = rules.get("viral-layout-expression") or {}
    markdown_literals = list(layout_rules.get("forbidden_markdown_patterns") or [])
    matched_markdown = [pattern for pattern in markdown_literals if pattern in body]
    markdown_regexes = (r"(?m)^#{1,6}\s", r"(?m)^\s*\|.*\|\s*$")
    matched_markdown.extend(pattern for pattern in markdown_regexes if re.search(pattern, body))
    if matched_markdown:
        checks.append(
            {
                "code": "LAYOUT_MARKDOWN_FORBIDDEN",
                "level": "error",
                "location": "body",
                "message": "正文包含不可直接发布的 Markdown 标题、表格或代码块",
                "evidence_ids": [],
            }
        )
    max_paragraph_chars = layout_rules.get("max_paragraph_chars", 160)
    long_paragraphs = [
        part
        for part in re.split(r"\n\s*\n", body)
        if max_paragraph_chars is not None and len(part.strip()) > int(max_paragraph_chars)
    ]
    if long_paragraphs:
        checks.append(
            {
                "code": "LAYOUT_PARAGRAPH_TOO_LONG",
                "level": "error",
                "location": "body",
                "message": f"存在超过 {max_paragraph_chars} 字的连续长段，需按语义拆分",
                "evidence_ids": [],
            }
        )

    price_rules = rules.get("viral-price-author") or {}
    used_body_evidence = {
        evidence_id
        for paragraph in draft.get("paragraph_evidence") or []
        for evidence_id in paragraph.get("evidence_ids") or []
    }
    variables = brief_variable_map(brief)
    expected_city = next(
        (str(variables[key]).strip() for key in ("city", "location", "region", "serviceCity") if variables.get(key)),
        "",
    )
    if not expected_city:
        brief_text = " ".join(str(value) for value in variables.values() if isinstance(value, str))
        city_match = re.search(r'["\']serviceCity["\']\s*:\s*["\']([^"\']{2,12})["\']', brief_text)
        expected_city = city_match.group(1).strip() if city_match else ""
    expected_city = expected_city.removesuffix("市")
    for item in evidence_bundle.get("items") or []:
        metadata = item.get("metadata") or {}
        if str(item.get("id") or "") not in used_body_evidence or metadata.get("material_type") != "price":
            continue
        missing = [key for key in price_rules.get("required_price_metadata") or [] if not metadata.get(key)]
        if missing:
            checks.append(
                {
                    "code": "PRICE_EVIDENCE_SCOPE_MISMATCH",
                    "level": "error",
                    "location": "body",
                    "message": "已使用的报价证据缺少口径字段: " + "、".join(missing),
                    "evidence_ids": [str(item["id"])],
                }
            )
        evidence_city = str(metadata.get("city") or metadata.get("region") or "").strip().removesuffix("市")
        evidence_scope = " ".join((str(item.get("value") or ""), str(metadata))).removesuffix("市")
        if expected_city and evidence_city and expected_city != evidence_city:
            checks.append(
                {
                    "code": "PRICE_CITY_MISMATCH",
                    "level": "error",
                    "location": "body",
                    "message": f"报价证据城市为 {evidence_city}，与任务城市 {expected_city} 不一致",
                    "evidence_ids": [str(item["id"])],
                }
            )
        elif expected_city and expected_city not in evidence_scope:
            checks.append(
                {
                    "code": "PRICE_CITY_UNVERIFIED",
                    "level": "error",
                    "location": "body",
                    "message": f"报价证据未注明任务城市 {expected_city}，不能用于本地单价",
                    "evidence_ids": [str(item["id"])],
                }
            )
    return checks
