from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal


COMBINATION_MEMBER_COUNTS = {"single": 1, "double": 2, "triple": 3, "quadruple": 4}
METHOD_ROLES = {"primary", "supporting", "enhancer"}


@dataclass(frozen=True, slots=True)
class MethodMember:
    method_code: str
    role: Literal["primary", "supporting", "enhancer"]
    order: int


@dataclass(frozen=True, slots=True)
class CombinationGroup:
    code: str
    rule_version_id: str
    content_direction_code: str
    content_direction_name: str
    combination_type: Literal["single", "double", "triple", "quadruple"]
    method_members: tuple[MethodMember, ...]
    title_formula_candidate_codes: tuple[str, ...]
    body_formula_candidate_codes: tuple[str, ...]
    industry_scope: tuple[str, ...] = ()
    channel_scope: tuple[str, ...] = ()
    content_goal_codes: tuple[str, ...] = ()
    narrative_axis_codes: tuple[str, ...] = ()
    required_variable_codes: tuple[str, ...] = ()
    required_evidence_types: tuple[str, ...] = ()
    priority: int = 0
    enabled: bool = True
    scenario_description: str = ""
    source_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        expected = COMBINATION_MEMBER_COUNTS.get(self.combination_type)
        if expected is None or len(self.method_members) != expected:
            raise ValueError("组合类型与手法成员数量不一致")
        if tuple(member.order for member in self.method_members) != tuple(range(1, expected + 1)):
            raise ValueError("手法成员必须按从 1 开始的连续顺序排列")
        if len({member.method_code for member in self.method_members}) != expected:
            raise ValueError("同一组合组内的手法不能重复")
        if any(member.role not in METHOD_ROLES for member in self.method_members):
            raise ValueError("手法成员角色无效")
        if not self.title_formula_candidate_codes or not self.body_formula_candidate_codes:
            raise ValueError("V3 组合组必须同时配置标题和正文公式候选池")
        if len(set(self.title_formula_candidate_codes)) != len(self.title_formula_candidate_codes):
            raise ValueError("标题公式候选不能重复")
        if len(set(self.body_formula_candidate_codes)) != len(self.body_formula_candidate_codes):
            raise ValueError("正文公式候选不能重复")

    @classmethod
    def from_mapping(cls, value: dict[str, Any], *, rule_version_id: str) -> CombinationGroup:
        direction = value.get("content_direction") or {}
        members = tuple(
            MethodMember(
                method_code=str(item["method_code"]),
                role=item["role"],
                order=int(item["order"]),
            )
            for item in value.get("method_members") or []
        )
        return cls(
            code=str(value["code"]),
            rule_version_id=rule_version_id,
            content_direction_code=str(value.get("content_direction_code") or direction.get("code") or ""),
            content_direction_name=str(value.get("content_direction_name") or direction.get("name") or ""),
            combination_type=value["combination_type"],
            method_members=members,
            title_formula_candidate_codes=tuple(value.get("title_formula_candidate_codes") or []),
            body_formula_candidate_codes=tuple(value.get("body_formula_candidate_codes") or []),
            industry_scope=tuple(value.get("industry_scope") or []),
            channel_scope=tuple(value.get("channel_scope") or []),
            content_goal_codes=tuple(value.get("content_goal_codes") or []),
            narrative_axis_codes=tuple(value.get("narrative_axis_codes") or []),
            required_variable_codes=tuple(value.get("required_variable_codes") or []),
            required_evidence_types=tuple(value.get("required_evidence_types") or []),
            priority=int(value.get("priority") or 0),
            enabled=bool(value.get("enabled", True)),
            scenario_description=str(value.get("scenario_description") or ""),
            source_metadata=dict(value.get("source_metadata") or {}),
        )


@dataclass(frozen=True, slots=True)
class MatchRequest:
    content_direction_code: str
    industry_slug: str
    channel_code: str | None = None
    content_goal_code: str | None = None
    narrative_axis_code: str | None = None
    available_variable_codes: frozenset[str] = frozenset()
    available_evidence_types: frozenset[str] = frozenset()
    limit: int = 28


@dataclass(frozen=True, slots=True)
class EligibleGroup:
    group_code: str
    score: int
    score_details: dict[str, int]
    title_formula_candidate_codes: tuple[str, ...]
    body_formula_candidate_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RejectedGroup:
    group_code: str
    reasons: tuple[str, ...]
    missing_variable_codes: tuple[str, ...] = ()
    missing_evidence_types: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MatchDecision:
    status: Literal["matched", "blocked_by_rule"]
    content_direction_code: str
    eligible_groups: tuple[EligibleGroup, ...]
    rejected_groups: tuple[RejectedGroup, ...]
    selected_group_code: str | None
    selection_mode: Literal["deterministic"] = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "content_direction_code": self.content_direction_code,
            "eligible_groups": [item.to_dict() for item in self.eligible_groups],
            "rejected_groups": [item.to_dict() for item in self.rejected_groups],
            "selected_group_code": self.selected_group_code,
            "selection_mode": self.selection_mode,
        }

    def with_selected_group(self, group_code: str) -> MatchDecision:
        if self.status != "matched" or group_code not in {item.group_code for item in self.eligible_groups}:
            raise ValueError("选择的组合组不在固定规则通过集合中")
        return replace(self, selected_group_code=group_code)


class CombinationMatcher:
    """V3 四层规则的第一段：只做硬过滤和可重复的稳定排序。"""

    def match(self, groups: list[CombinationGroup], request: MatchRequest) -> MatchDecision:
        eligible: list[EligibleGroup] = []
        rejected: list[RejectedGroup] = []

        for group in groups:
            reasons: list[str] = []
            missing_variables = tuple(sorted(set(group.required_variable_codes) - request.available_variable_codes))
            missing_evidence = tuple(sorted(set(group.required_evidence_types) - request.available_evidence_types))

            if not group.enabled:
                reasons.append("disabled")
            if group.content_direction_code != request.content_direction_code:
                reasons.append("content_direction_mismatch")
            if group.industry_scope and request.industry_slug not in group.industry_scope:
                reasons.append("industry_mismatch")
            if group.channel_scope and request.channel_code not in group.channel_scope:
                reasons.append("channel_mismatch")
            if group.content_goal_codes and request.content_goal_code not in group.content_goal_codes:
                reasons.append("content_goal_mismatch")
            if group.narrative_axis_codes and request.narrative_axis_code not in group.narrative_axis_codes:
                reasons.append("narrative_axis_mismatch")
            if missing_variables:
                reasons.append("missing_variables")
            if missing_evidence:
                reasons.append("missing_evidence")

            if reasons:
                rejected.append(
                    RejectedGroup(
                        group_code=group.code,
                        reasons=tuple(reasons),
                        missing_variable_codes=missing_variables,
                        missing_evidence_types=missing_evidence,
                    )
                )
                continue

            score_details = {
                "priority": group.priority,
                "content_goal": 20 if request.content_goal_code in group.content_goal_codes else 0,
                "narrative_axis": 10 if request.narrative_axis_code in group.narrative_axis_codes else 0,
                "variable_coverage": len(group.required_variable_codes) * 2,
                "evidence_coverage": len(group.required_evidence_types) * 3,
            }
            eligible.append(
                EligibleGroup(
                    group_code=group.code,
                    score=sum(score_details.values()),
                    score_details=score_details,
                    title_formula_candidate_codes=group.title_formula_candidate_codes,
                    body_formula_candidate_codes=group.body_formula_candidate_codes,
                )
            )

        eligible.sort(key=lambda item: (-item.score, item.group_code))
        rejected.sort(key=lambda item: item.group_code)
        limited = tuple(eligible[: request.limit])
        return MatchDecision(
            status="matched" if limited else "blocked_by_rule",
            content_direction_code=request.content_direction_code,
            eligible_groups=limited,
            rejected_groups=tuple(rejected),
            selected_group_code=limited[0].group_code if limited else None,
        )
