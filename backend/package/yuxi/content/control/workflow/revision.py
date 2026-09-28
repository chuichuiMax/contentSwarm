from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal


_REVISION_REASON_LABELS = {
    "TITLE_VALIDATION_FAILED": "标题不符合公式或发布要求",
    "BODY_STRUCTURE_FAILED": "正文结构或表达不符合要求",
    "BODY_EVIDENCE_FAILED": "正文缺少有效的事实证据引用",
    "PERSONA_STYLE_FAILED": "正文语气或人设表达不符合要求",
    "SYSTEM_CONFIGURATION_FAILED": "系统配置校验失败",
    "REVIEW_CONTRACT_VIOLATION": "审核结果格式不符合要求",
}


def revision_reason_label(reason_code: str | None) -> str:
    return _REVISION_REASON_LABELS.get(str(reason_code or "").upper(), "内容校验发现阻断问题")


def build_generation_repair_constraints(payload: dict[str, Any]) -> dict[str, Any] | None:
    """为标准生产的模型输入和结果校验编译同一份回修边界。"""
    blocked = {
        item.get("code")
        for item in (payload.get("review_report") or {}).get("checks") or []
        if item.get("status") == "blocked"
    }
    if (
        not payload.get("production_pack")
        or not payload.get("content_draft")
        or not payload.get("selected_title")
        or not payload.get("content_outline")
        or (payload.get("validation_report") or {}).get("status") not in {"passed", "warning"}
        or not blocked
        or not blocked
        <= {
            "PERSONA_OPENING",
            "PERSONA_CLOSING",
            "EMOJI_COVERAGE",
            "EMOJI_APPROPRIATENESS",
            "EMOJI_RESTRICTIONS",
        }
    ):
        return None
    body = payload["content_draft"]["body"]
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
    editable = set()
    if "PERSONA_OPENING" in blocked:
        editable.update(range(1, min(2, len(paragraphs)) + 1))
    if "PERSONA_CLOSING" in blocked:
        editable.add(len(paragraphs))
    return {
        "mode": "persona_paragraphs_only" if editable else "emoji_only",
        "original_body": body,
        "editable_paragraph_numbers": sorted(editable),
        "immutable_title": deepcopy(payload["selected_title"]),
        "immutable_outline": deepcopy(payload["content_outline"]),
        "immutable_topics": list(payload["content_draft"].get("topics") or []),
        "instruction": (
            "段落从 1 开始计数，仅可改 editable_paragraph_numbers 中的段落；保持段落数量与顺序，"
            "其余段落文字逐字保留，仅允许调整 Emoji 和空格。可在可编辑段落间迁移身份并删除重复句；"
            "标题、大纲、话题保持原样，事实和段落证据映射必须一致。"
            if editable
            else "只调整 Emoji 及其相邻空格；标题、大纲、正文文字、数字、标点、顺序和话题保持原样。"
        ),
    }


@dataclass(frozen=True, slots=True)
class RevisionDecision:
    status: Literal["route", "continue", "limit_reached"]
    target_node_id: str | None
    retry_counts: dict[str, int]


class RevisionRouteController:
    """只解释发布定义中的 revision_routes，不允许 Agent 自由 goto。"""

    def decide(
        self,
        *,
        definition: dict[str, Any],
        reason_code: str | None,
        retry_counts: dict[str, int],
    ) -> RevisionDecision:
        if not reason_code:
            return RevisionDecision("continue", None, dict(retry_counts))
        route = next(
            (
                item
                for item in definition.get("revision_routes") or []
                if reason_code in (item.get("reason_codes") or [])
            ),
            None,
        )
        if route is None:
            return RevisionDecision("continue", None, dict(retry_counts))
        target = route["to"]
        counts = dict(retry_counts)
        current = int(counts.get(target, 0))
        if current >= int(route["max_attempts"]):
            return RevisionDecision("limit_reached", None, counts)
        counts[target] = current + 1
        return RevisionDecision("route", target, counts)


def resolve_revision_reason(
    *,
    title_validation_report: dict[str, Any] | None,
    validation_report: dict[str, Any] | None,
    review_report: dict[str, Any] | None,
) -> str | None:
    """把校验结果收敛成发布工作流允许识别的固定 reason code。"""

    title_report = title_validation_report or {}
    if title_report.get("status") == "blocked" or any(
        item.get("status") == "blocked" for item in title_report.get("items") or []
    ):
        return "TITLE_VALIDATION_FAILED"

    checks = [
        *(validation_report or {}).get("checks", []),
        *(review_report or {}).get("checks", []),
    ]
    blocked_codes = {
        str(item.get("code") or "").upper()
        for item in checks
        if item.get("level") == "error" or item.get("status") == "blocked"
    }
    if (
        not blocked_codes
        and (validation_report or {}).get("status") != "blocked"
        and (review_report or {}).get("status") != "blocked"
    ):
        return None
    system_codes = {
        "CONTENT_STRATEGY_SNAPSHOT_MISSING",
        "NODE_INPUT_MISSING",
        "NODE_INPUT_INVALID",
        "REVIEW_CONTRACT_INVALID",
    }
    if blocked_codes & system_codes:
        return "SYSTEM_CONFIGURATION_FAILED"

    reason_by_code = {
        "FACTUAL_ACCURACY": "BODY_EVIDENCE_FAILED",
        "WRITING_QUALITY": "BODY_STRUCTURE_FAILED",
        "CONTENT_REQUIRED_TERM_MISSING": "BODY_STRUCTURE_FAILED",
        "QUOTE_AMOUNT_REPEATED": "BODY_EVIDENCE_FAILED",
        "CREATION_TYPE_ALIGNMENT": "BODY_STRUCTURE_FAILED",
        "COMPOSITION_ALIGNMENT": "BODY_STRUCTURE_FAILED",
        "PERSONA_OPENING": "PERSONA_STYLE_FAILED",
        "PERSONA_CLOSING": "PERSONA_STYLE_FAILED",
        "PERSONA_GROUNDING": "PERSONA_STYLE_FAILED",
        "EMOJI_COVERAGE": "PERSONA_STYLE_FAILED",
        "EMOJI_APPROPRIATENESS": "PERSONA_STYLE_FAILED",
        "EMOJI_RESTRICTIONS": "PERSONA_STYLE_FAILED",
        "TITLE_TOO_LONG": "TITLE_VALIDATION_FAILED",
        "TITLE_TOO_SHORT": "TITLE_VALIDATION_FAILED",
        "TITLE_FORMULA_MISMATCH": "TITLE_VALIDATION_FAILED",
        "TITLE_REQUIRED_FACT_MISSING": "TITLE_VALIDATION_FAILED",
        "CHANNEL_TITLE_LONG": "TITLE_VALIDATION_FAILED",
        "CHANNEL_TITLE_SHORT": "TITLE_VALIDATION_FAILED",
        "TITLE_FACT_UNSUPPORTED": "TITLE_VALIDATION_FAILED",
        "TITLE_ALIGNMENT": "TITLE_VALIDATION_FAILED",
        "TITLE_PRODUCT_EVIDENCE_NOT_USED": "TITLE_VALIDATION_FAILED",
        "PERSONA_TONE_MISMATCH": "PERSONA_STYLE_FAILED",
        "PERSONA_STYLE_MISMATCH": "PERSONA_STYLE_FAILED",
        "MECHANICAL_META_EXPRESSION": "PERSONA_STYLE_FAILED",
        "NATURAL_EXPRESSION": "PERSONA_STYLE_FAILED",
        "LAYOUT_READABILITY": "BODY_STRUCTURE_FAILED",
        "PLATFORM_CTA": "BODY_STRUCTURE_FAILED",
        "PRICE_SCOPE_ALIGNMENT": "BODY_EVIDENCE_FAILED",
        "TOPIC_ALIGNMENT": "BODY_STRUCTURE_FAILED",
        "BODY_VALUE": "BODY_STRUCTURE_FAILED",
        "LAYOUT_MARKDOWN_FORBIDDEN": "BODY_STRUCTURE_FAILED",
        "LAYOUT_PARAGRAPH_TOO_LONG": "BODY_STRUCTURE_FAILED",
        "CTA_TOO_DIRECT": "BODY_STRUCTURE_FAILED",
        "TOPIC_COUNT_MISMATCH": "BODY_STRUCTURE_FAILED",
        "TOPIC_DUPLICATED": "BODY_STRUCTURE_FAILED",
        "TOPIC_OUTSIDE_CANDIDATE_POOL": "BODY_STRUCTURE_FAILED",
        "PRICE_EVIDENCE_SCOPE_MISMATCH": "BODY_EVIDENCE_FAILED",
        "PRICE_CITY_MISMATCH": "BODY_EVIDENCE_FAILED",
        "PRICE_CITY_UNVERIFIED": "BODY_EVIDENCE_FAILED",
        "EVIDENCE_REFERENCE_FORBIDDEN": "BODY_EVIDENCE_FAILED",
        "FACT_NUMBER_WITHOUT_SOURCE": "BODY_EVIDENCE_FAILED",
        "NUMERIC_CLAIM_UNSUPPORTED": "BODY_EVIDENCE_FAILED",
        "FACT_CHECK_FAILED": "BODY_EVIDENCE_FAILED",
        "FACT_INCONSISTENT": "BODY_EVIDENCE_FAILED",
        "KNOWLEDGE_EVIDENCE_UNUSED": "BODY_EVIDENCE_FAILED",
        "KNOWLEDGE_PRICE_DETAIL_UNUSED": "BODY_EVIDENCE_FAILED",
        "BODY_PRODUCT_EVIDENCE_NOT_USED": "BODY_EVIDENCE_FAILED",
        "DERIVED_CALCULATION_UNUSED": "BODY_EVIDENCE_FAILED",
        "TRADE_BREAKDOWN_UNUSED": "BODY_EVIDENCE_FAILED",
        "LABOR_AUX_BREAKDOWN_UNUSED": "BODY_EVIDENCE_FAILED",
        "BODY_LENGTH_OUT_OF_RANGE": "BODY_STRUCTURE_FAILED",
        "CHANNEL_BODY_LONG": "BODY_STRUCTURE_FAILED",
        "CHANNEL_BODY_SHORT": "BODY_STRUCTURE_FAILED",
        "BODY_FORMULA_MISMATCH": "BODY_STRUCTURE_FAILED",
        "CONTENT_STRUCTURE_MISMATCH": "BODY_STRUCTURE_FAILED",
        "CONTENT_FORBIDDEN_TERM": "BODY_STRUCTURE_FAILED",
        "CONTENT_HIGH_RISK_CLAIM": "BODY_STRUCTURE_FAILED",
        "COMPLIANCE_RULE_MATCH": "BODY_STRUCTURE_FAILED",
        "UNSAFE_AUTO_REPLACEMENT": "BODY_STRUCTURE_FAILED",
    }
    reasons = {reason_by_code[code] for code in blocked_codes if code in reason_by_code}
    if len(reasons) == 1 and len(blocked_codes) == sum(code in reason_by_code for code in blocked_codes):
        return reasons.pop()
    if reasons:
        for preferred in (
            "BODY_EVIDENCE_FAILED",
            "TITLE_VALIDATION_FAILED",
            "PERSONA_STYLE_FAILED",
            "BODY_STRUCTURE_FAILED",
        ):
            if preferred in reasons:
                return preferred
    return "REVIEW_CONTRACT_VIOLATION"


__all__ = ["RevisionDecision", "RevisionRouteController", "resolve_revision_reason", "revision_reason_label"]
