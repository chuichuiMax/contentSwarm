"""内容生产共享的确定性合规与证据校验。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?(?:%|元|万元|天|周|月|年|个|次|㎡|m²)?")


class ComplianceEngine:
    """执行版本化渠道、行业和企业合规规则，并返回替换差异。"""

    def validate_and_adapt(
        self,
        *,
        title: str,
        body: str,
        topics: list[str],
        channel_profile: dict[str, Any],
        policies: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        output = {"title": title, "body": body, "topics": list(topics)}
        checks: list[dict[str, Any]] = []
        diffs: list[dict[str, Any]] = []
        self._check_length("title", title, channel_profile.get("title_constraints") or {}, checks)
        self._check_length("body", body, channel_profile.get("body_constraints") or {}, checks)
        maximum_topics = (channel_profile.get("topic_constraints") or {}).get("max_count")
        if maximum_topics is not None and len(topics) > int(maximum_topics):
            checks.append(
                {
                    "code": "CHANNEL_TOPIC_COUNT",
                    "level": "error",
                    "location": "topics",
                    "message": f"话题数量超过 {maximum_topics}",
                }
            )

        for policy in policies:
            for rule in policy.get("rules") or []:
                if not rule.get("enabled", True):
                    continue
                for location in ("title", "body"):
                    source = output[location]
                    if not self._find_matches(source, rule):
                        continue
                    action = rule.get("action", "warn")
                    level = "error" if action in {"block", "confirm"} else "warning"
                    if action == "replace" and rule.get("replacement") is not None:
                        replaced = self._replace(source, rule)
                        if self._numeric_meaning_changed(source, replaced):
                            checks.append(
                                {
                                    "code": "UNSAFE_AUTO_REPLACEMENT",
                                    "level": "error",
                                    "location": location,
                                    "message": f"规则 {rule.get('rule_code')} 的替换会改变数字或事实含义",
                                    "rule_id": rule.get("id"),
                                }
                            )
                        else:
                            output[location] = replaced
                            diffs.append(
                                {
                                    "location": location,
                                    "before": source,
                                    "after": replaced,
                                    "rule_id": rule.get("id"),
                                }
                            )
                    else:
                        checks.append(
                            {
                                "code": rule.get("rule_code") or "COMPLIANCE_RULE_MATCH",
                                "level": level,
                                "location": location,
                                "message": rule.get("explanation") or f"命中合规规则：{rule.get('pattern')}",
                                "rule_id": rule.get("id"),
                                "human_confirmation_required": bool(rule.get("human_confirmation_required"))
                                or action == "confirm",
                            }
                        )
        status = (
            "blocked"
            if any(item["level"] == "error" for item in checks)
            else "warning"
            if checks or diffs
            else "passed"
        )
        return {"status": status, **output, "checks": checks, "replacement_diffs": diffs}

    @staticmethod
    def _check_length(
        location: str,
        text: str,
        config: dict[str, Any],
        checks: list[dict[str, Any]],
    ) -> None:
        minimum = config.get("min_length")
        maximum = config.get("max_length")
        if minimum is not None and len(text) < int(minimum):
            checks.append(
                {
                    "code": f"CHANNEL_{location.upper()}_SHORT",
                    "level": "warning",
                    "location": location,
                    "message": f"{location} 少于 {minimum} 字",
                }
            )
        if maximum is not None and len(text) > int(maximum):
            checks.append(
                {
                    "code": f"CHANNEL_{location.upper()}_LONG",
                    "level": "error",
                    "location": location,
                    "message": f"{location} 超过 {maximum} 字",
                }
            )

    @staticmethod
    def _find_matches(text: str, rule: dict[str, Any]) -> list[str]:
        pattern = str(rule.get("pattern") or "")
        if not pattern:
            return []
        if rule.get("match_type") == "regex":
            try:
                return re.findall(pattern, text)
            except re.error:
                return []
        return [pattern] if pattern in text else []

    @staticmethod
    def _replace(text: str, rule: dict[str, Any]) -> str:
        pattern = str(rule.get("pattern") or "")
        replacement = str(rule.get("replacement") or "")
        if rule.get("match_type") == "regex":
            try:
                return re.sub(pattern, replacement, text)
            except re.error:
                return text
        return text.replace(pattern, replacement)

    @staticmethod
    def _numeric_meaning_changed(before: str, after: str) -> bool:
        return _NUMBER_RE.findall(before) != _NUMBER_RE.findall(after)


def validate_numeric_evidence_coverage(text: str, evidence_bundle: dict[str, Any]) -> dict[str, Any]:
    """校验内容中的事实性数字是否有已确认证据支撑。"""

    claims = _NUMBER_RE.findall(text)
    supported: set[str] = set()
    evidence_ids_by_claim: dict[str, list[str]] = {}
    evidence_items = [item for item in evidence_bundle.get("items") or [] if isinstance(item, dict)]
    for item in evidence_items:
        if item.get("verified_status") in {"rejected", "unverified", "blocked"}:
            continue
        haystack = " ".join(
            str(value)
            for value in (item.get("content"), item.get("value"), item.get("values"), item.get("metadata"))
            if value is not None
        )
        for claim in claims:
            if claim in haystack:
                supported.add(claim)
                if item.get("id"):
                    evidence_ids_by_claim.setdefault(claim, []).append(str(item["id"]))
    unsupported = sorted(set(claims) - supported)
    return {
        "status": "blocked" if unsupported else "passed",
        "claims": claims,
        "unsupported_claims": unsupported,
        "evidence_ids_by_claim": evidence_ids_by_claim,
        "checks": [
            {
                "code": "NUMERIC_CLAIM_UNSUPPORTED",
                "level": "error",
                "location": "content",
                "message": f"数字 {claim} 没有已确认来源",
            }
            for claim in unsupported
        ],
    }


__all__ = ["ComplianceEngine", "validate_numeric_evidence_coverage"]
