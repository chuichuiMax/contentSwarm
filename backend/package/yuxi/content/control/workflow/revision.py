from __future__ import annotations

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
        "TITLE_TOO_LONG": "TITLE_VALIDATION_FAILED",
        "TITLE_TOO_SHORT": "TITLE_VALIDATION_FAILED",
        "TITLE_FORMULA_MISMATCH": "TITLE_VALIDATION_FAILED",
        "TITLE_FACT_UNSUPPORTED": "TITLE_VALIDATION_FAILED",
        "TITLE_PRODUCT_EVIDENCE_NOT_USED": "TITLE_VALIDATION_FAILED",
        "PERSONA_TONE_MISMATCH": "PERSONA_STYLE_FAILED",
        "PERSONA_STYLE_MISMATCH": "PERSONA_STYLE_FAILED",
        "MECHANICAL_META_EXPRESSION": "PERSONA_STYLE_FAILED",
        "EVIDENCE_REFERENCE_FORBIDDEN": "BODY_EVIDENCE_FAILED",
        "FACT_NUMBER_WITHOUT_SOURCE": "BODY_EVIDENCE_FAILED",
        "NUMERIC_CLAIM_UNSUPPORTED": "BODY_EVIDENCE_FAILED",
        "FACT_CHECK_FAILED": "BODY_EVIDENCE_FAILED",
        "FACT_INCONSISTENT": "BODY_EVIDENCE_FAILED",
        "KNOWLEDGE_EVIDENCE_UNUSED": "BODY_EVIDENCE_FAILED",
        "KNOWLEDGE_PRICE_DETAIL_UNUSED": "BODY_EVIDENCE_FAILED",
        "BODY_PRODUCT_EVIDENCE_NOT_USED": "BODY_EVIDENCE_FAILED",
        "BODY_LENGTH_OUT_OF_RANGE": "BODY_STRUCTURE_FAILED",
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
