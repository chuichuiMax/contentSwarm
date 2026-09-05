from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from yuxi.content.rules import brief_variable_map

NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?(?:%|元|万元|天|周|月|年|个|次|㎡|人)?")
HIGH_RISK_CLAIMS = ("保证", "百分百", "100%", "一定有效", "绝对", "零风险", "最便宜", "第一")


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


def evidence_number_tokens(evidence_bundle: dict[str, Any]) -> list[str]:
    evidence_text = " ".join(
        json.dumps(item.get("value"), ensure_ascii=False)
        for item in evidence_bundle.get("items") or []
        if item.get("value") is not None
    )
    return sorted(set(NUMBER_PATTERN.findall(evidence_text)))


def unsupported_number_tokens(content: str, evidence_bundle: dict[str, Any]) -> list[str]:
    evidence_text = " ".join(
        json.dumps(item.get("value"), ensure_ascii=False)
        for item in evidence_bundle.get("items") or []
        if item.get("value") is not None
    )
    return sorted({number for number in NUMBER_PATTERN.findall(content) if number not in evidence_text})


def validate_content(
    *,
    title: str,
    body: str,
    topics: list[str],
    brief: dict[str, Any],
    evidence_bundle: dict[str, Any],
    strategy: dict[str, Any],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    combined = f"{title}\n{body}\n{' '.join(topics)}"
    for number in unsupported_number_tokens(combined, evidence_bundle):
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
        if claim in combined:
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
