from __future__ import annotations

from dataclasses import replace

import pytest

from yuxi.content.control.strategy.recommend_v3 import (
    PreviewV3StrategyCommand,
    PreviewV3StrategyHandler,
    StrategyPreviewActor,
    StrategyPreviewContext,
)
from yuxi.content.model.rules.engine import CombinationGroup, CombinationMatcher, MatchRequest
from yuxi.content.v3.fixtures import load_decoration_matrix
from yuxi.content.v3.seed import DECORATION_INDUSTRY_PACK_V3_ID, PLATFORM_RULE_V3_ID


def _groups() -> list[CombinationGroup]:
    fixture = load_decoration_matrix()
    return [
        CombinationGroup.from_mapping(
            {
                **item,
                "industry_scope": ["decoration"],
                "channel_scope": [],
                "priority": 1000 - index,
            },
            rule_version_id=PLATFORM_RULE_V3_ID,
        )
        for index, item in enumerate(fixture["groups"])
    ]


@pytest.mark.unit
@pytest.mark.parametrize("content_direction_code", [f"CT0{index}" for index in range(1, 8)])
def test_each_decoration_direction_has_four_eligible_groups(content_direction_code: str):
    decision = CombinationMatcher().match(
        _groups(),
        MatchRequest(content_direction_code=content_direction_code, industry_slug="decoration"),
    )

    assert decision.status == "matched"
    assert len(decision.eligible_groups) == 4
    assert decision.selected_group_code == decision.eligible_groups[0].group_code


@pytest.mark.unit
def test_matcher_applies_hard_filters_before_stable_sorting():
    source = next(group for group in _groups() if group.content_direction_code == "CT05")
    constrained = replace(
        source,
        code="constrained",
        priority=9999,
        channel_scope=("xiaohongshu",),
        content_goal_codes=("brand",),
        narrative_axis_codes=("process_to_result",),
        required_variable_codes=("process",),
        required_evidence_types=("process",),
    )
    request = MatchRequest(
        content_direction_code="CT05",
        industry_slug="decoration",
        channel_code="douyin",
        content_goal_code="acquire",
        narrative_axis_code="other",
    )

    decision = CombinationMatcher().match([constrained], request)

    assert decision.status == "blocked_by_rule"
    assert decision.selected_group_code is None
    assert set(decision.rejected_groups[0].reasons) == {
        "channel_mismatch",
        "content_goal_mismatch",
        "narrative_axis_mismatch",
        "missing_variables",
        "missing_evidence",
    }


@pytest.mark.unit
def test_matcher_order_is_repeatable_and_never_falls_back_to_v2():
    groups = [group for group in _groups() if group.content_direction_code == "CT03"]
    request = MatchRequest(content_direction_code="CT03", industry_slug="decoration")

    forward = CombinationMatcher().match(groups, request)
    reversed_result = CombinationMatcher().match(list(reversed(groups)), request)
    blocked = CombinationMatcher().match(
        groups,
        MatchRequest(content_direction_code="CT03", industry_slug="medical"),
    )

    assert [item.group_code for item in forward.eligible_groups] == [
        item.group_code for item in reversed_result.eligible_groups
    ]
    assert blocked.status == "blocked_by_rule"
    assert blocked.eligible_groups == ()


@pytest.mark.unit
def test_agent_can_select_any_group_that_passes_hard_rules():
    groups = [group for group in _groups() if group.content_direction_code == "CT03"]
    decision = CombinationMatcher().match(
        groups,
        MatchRequest(content_direction_code="CT03", industry_slug="decoration"),
    )
    agent_selected_group = decision.eligible_groups[-1].group_code

    locked = decision.with_selected_group(agent_selected_group)

    assert agent_selected_group != decision.eligible_groups[0].group_code
    assert locked.selected_group_code == agent_selected_group


@pytest.mark.unit
def test_agent_cannot_select_group_rejected_by_hard_rules():
    decision = CombinationMatcher().match(
        [group for group in _groups() if group.content_direction_code == "CT03"],
        MatchRequest(content_direction_code="CT03", industry_slug="decoration"),
    )

    with pytest.raises(ValueError, match="不在固定规则通过集合中"):
        decision.with_selected_group("group-outside-eligible-set")


@pytest.mark.unit
def test_scene_method_supports_primary_and_supporting_roles_and_all_group_sizes():
    groups = _groups()
    scene_members = [member for group in groups for member in group.method_members if member.method_code == "S01"]

    assert {member.role for member in scene_members} >= {"primary", "supporting"}
    assert {group.combination_type for group in groups} == {"single", "double", "triple", "quadruple"}


@pytest.mark.unit
def test_combination_group_rejects_member_count_mismatch():
    source = _groups()[0]

    with pytest.raises(ValueError, match="组合类型与手法成员数量不一致"):
        replace(source, combination_type="quadruple")


class _FakePreviewPort:
    def __init__(self, context: StrategyPreviewContext):
        self.context = context

    async def load_context(self, **_kwargs) -> StrategyPreviewContext:
        return self.context


@pytest.mark.unit
@pytest.mark.asyncio
async def test_preview_handler_returns_decision_without_creating_run():
    groups = tuple(group for group in _groups() if group.content_direction_code == "CT05")
    context = StrategyPreviewContext(
        task_id="task-1",
        rule_version_id=PLATFORM_RULE_V3_ID,
        industry_pack_version_id=DECORATION_INDUSTRY_PACK_V3_ID,
        channel_profile_version_id=None,
        content_direction_code="CT05",
        industry_slug="decoration",
        channel_code=None,
        content_goal_code="brand",
        narrative_axis_code=None,
        available_variable_codes=frozenset(),
        available_evidence_types=frozenset(),
        groups=groups,
    )
    handler = PreviewV3StrategyHandler(_FakePreviewPort(context))

    result = await handler.execute(
        PreviewV3StrategyCommand(
            task_id="task-1",
            actor=StrategyPreviewActor(uid="user-1", role="user", tenant_id=None),
        )
    )

    assert result["preview"] is True
    assert result["creates_run"] is False
    assert len(result["decision"]["eligible_groups"]) == 4
    assert result["rule_version_id"] == PLATFORM_RULE_V3_ID
