from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

import yuxi.agents.buildin.content_workflow.graph as content_workflow_graph_module
import yuxi.content.control.workflow.deterministic_node as deterministic_node_module
from yuxi.agents.buildin.content_workflow.context import ContentWorkflowContext
from yuxi.agents.buildin.content_workflow.graph import (
    ContentWorkflowAgent,
    _find_cached_parallel_result,
    _parallel_cache_key,
)
from yuxi.agents.buildin.content_workflow.state import ContentWorkflowState
from yuxi.content.control.workflow.agent_node import AgentNodeHandler, AgentNodeResultMapper
from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler, _derive_scene_evidence
from yuxi.content.control.errors import ContentApplicationError
from yuxi.content.control.workflow.revision import resolve_revision_reason, revision_reason_label
from yuxi.content.control.strategy.recommend_v3 import StrategyPreviewContext
from yuxi.content.model.rules.engine import CombinationGroup, MethodMember
from yuxi.content.v3.workflow import WORKFLOW_V3
from yuxi.storage.postgres.models_content import ContentNodeRun, ContentTask


@pytest.mark.unit
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("TITLE_TOO_LONG", "TITLE_VALIDATION_FAILED"),
        ("TITLE_FACT_UNSUPPORTED", "TITLE_VALIDATION_FAILED"),
        ("PERSONA_TONE_MISMATCH", "PERSONA_STYLE_FAILED"),
        ("MECHANICAL_META_EXPRESSION", "PERSONA_STYLE_FAILED"),
        ("EVIDENCE_REFERENCE_FORBIDDEN", "BODY_EVIDENCE_FAILED"),
        ("KNOWLEDGE_EVIDENCE_UNUSED", "BODY_EVIDENCE_FAILED"),
        ("KNOWLEDGE_PRICE_DETAIL_UNUSED", "BODY_EVIDENCE_FAILED"),
        ("BODY_LENGTH_OUT_OF_RANGE", "BODY_STRUCTURE_FAILED"),
    ],
)
def test_revision_reason_is_deterministic(code: str, expected: str):
    result = resolve_revision_reason(
        title_validation_report=None,
        validation_report={"status": "blocked", "checks": [{"code": code, "level": "error"}]},
        review_report=None,
    )

    assert result == expected


@pytest.mark.unit
def test_revision_reason_is_empty_when_all_checks_pass():
    assert (
        resolve_revision_reason(
            title_validation_report={"status": "passed", "items": []},
            validation_report={"status": "passed", "checks": []},
            review_report={"status": "passed", "checks": []},
        )
        is None
    )


@pytest.mark.unit
def test_revision_reason_label_never_exposes_internal_code():
    assert revision_reason_label("BODY_EVIDENCE_FAILED") == "正文缺少有效的事实证据引用"
    assert revision_reason_label("PERSONA_STYLE_FAILED") == "正文语气或人设表达不符合要求"
    assert revision_reason_label("UNKNOWN_REASON") == "内容校验发现阻断问题"


@pytest.mark.unit
def test_scene_evidence_is_derived_only_from_traceable_business_fields():
    derived = _derive_scene_evidence(
        "task-1",
        {
            "audience": ["杭州改善型装修三口之家"],
            "business_variables": {
                "project_type": "三室两厅一卫",
                "area": "89㎡",
                "owner_pain": "厨房操作台不足，儿童房需要学习与储物",
            },
        },
    )

    assert derived is not None
    assert derived.variable_codes == ("scene",)
    assert derived.verified_status == "user_confirmed"
    assert "杭州改善型装修三口之家" in derived.value
    assert "三室两厅一卫" in derived.value
    assert "89㎡" in derived.value
    assert "厨房操作台不足" in derived.value
    assert derived.metadata["derived_from_fields"] == [
        "audience",
        "business_variables.project_type",
        "business_variables.area",
        "business_variables.owner_pain",
    ]


@pytest.mark.unit
def test_scene_evidence_is_not_invented_without_a_business_pain():
    assert (
        _derive_scene_evidence(
            "task-1",
            {
                "audience": ["杭州业主"],
                "business_variables": {"project_type": "三室两厅一卫", "area": "89㎡"},
            },
        )
        is None
    )


@pytest.mark.asyncio
async def test_normalize_evidence_adds_derived_scene_to_frozen_bundle(monkeypatch):
    persisted = {}

    class FakeEvidenceService:
        def __init__(self, _db):
            pass

        async def persist_frozen_bundle(self, bundle, **kwargs):
            persisted["bundle"] = bundle
            persisted["kwargs"] = kwargs

    monkeypatch.setattr(deterministic_node_module, "EvidenceApplicationService", FakeEvidenceService)

    result = await V3DeterministicNodeHandler()._normalize_evidence(
        db=SimpleNamespace(),
        state={
            "task_id": "task-1",
            "run_id": "run-1",
            "content_brief": {
                "audience": ["杭州改善型装修三口之家"],
                "business_variables": {
                    "project_type": "三室两厅一卫",
                    "area": "89㎡",
                    "owner_pain": "厨房操作台不足",
                },
            },
            "media_evidence_items": [],
        },
        node_run_id="node-run-1",
    )

    scene_items = [item for item in result["evidence_bundle"]["items"] if "scene" in item["variable_codes"]]
    assert len(scene_items) == 1
    assert scene_items[0]["source_id"] == "derived_scene_from_business_brief"
    assert persisted["kwargs"]["added_evidence_ids"]


@pytest.mark.asyncio
async def test_strategy_selection_uses_highest_ranked_eligible_group_without_agent(monkeypatch):
    groups = (
        CombinationGroup(
            code="group-low",
            rule_version_id="rules-v3",
            content_direction_code="CT01",
            content_direction_name="案例",
            combination_type="single",
            method_members=(MethodMember("M01", "primary", 1),),
            title_formula_candidate_codes=("T02",),
            body_formula_candidate_codes=("C02",),
            priority=10,
        ),
        CombinationGroup(
            code="group-high",
            rule_version_id="rules-v3",
            content_direction_code="CT01",
            content_direction_name="案例",
            combination_type="single",
            method_members=(MethodMember("M03", "primary", 1),),
            title_formula_candidate_codes=("T01",),
            body_formula_candidate_codes=("C01",),
            priority=20,
        ),
    )

    class FakePreviewRepository:
        def __init__(self, _db):
            pass

        async def load_context(self, **_kwargs):
            return StrategyPreviewContext(
                task_id="task-1",
                rule_version_id="rules-v3",
                industry_pack_version_id="industry-v3",
                channel_profile_version_id="channel-v1",
                content_direction_code="CT01",
                industry_slug="decoration",
                channel_code="xiaohongshu",
                content_goal_code="lead",
                narrative_axis_code=None,
                available_variable_codes=frozenset(),
                available_evidence_types=frozenset(),
                groups=groups,
            )

    class FakeDB:
        async def get(self, model, identity):
            assert model is ContentTask
            assert identity == "task-1"
            return SimpleNamespace(id="task-1", uid="user-1", created_by="user-1", tenant_id=None)

    monkeypatch.setattr(deterministic_node_module, "PostgresStrategyPreviewRepository", FakePreviewRepository)

    result = await V3DeterministicNodeHandler()._select_creation_strategy(
        db=FakeDB(),
        state={"task_id": "task-1", "uid": "user-1", "evidence_bundle": {"items": []}},
        node_run_id="node-run-1",
    )

    assert result["strategy_selection"]["selected_group_id"] == "group-high"
    assert result["strategy_selection"]["title_formula_code"] == "T01"
    assert result["selected_angle"]["selected_by"] == "deterministic"


@pytest.mark.asyncio
async def test_strategy_snapshot_locks_one_title_formula_one_body_formula_and_all_group_methods():
    group = SimpleNamespace(
        id="group-1",
        method_members=[
            {"method_code": "M01", "role": "primary", "order": 1},
            {"method_code": "M03", "role": "supporting", "order": 2},
        ],
        methods=[],
    )
    title = SimpleNamespace(
        code="T03",
        name="标题公式",
        core_goal="命中受众与痛点",
        reference_examples=[],
        variable_schema=[],
        compatible_methods=["M01", "M03"],
        risk_rules=[],
    )
    body = SimpleNamespace(
        code="C03",
        name="正文公式",
        structure_schema=[{"slot": "opening"}],
        reference_examples=[],
        required_variables=[],
        output_schema={},
        compatible_methods=["M01", "M03"],
        risk_rules=[],
    )

    class Result:
        def __init__(self, value, *, many=False):
            self.value = value
            self.many = many

        def scalar_one_or_none(self):
            return self.value

        def scalars(self):
            return self.value

    class FakeDb:
        def __init__(self):
            self.results = [
                Result(title),
                Result(body),
                Result(
                    [
                        SimpleNamespace(
                            code=code,
                            name=f"手法 {code}",
                            method_type="core",
                            principle="可验证原则",
                            suitable_scenes=[],
                            sentence_patterns=[],
                            variable_schema=["result"],
                            risk_rules=[],
                        )
                        for code in ("M01", "M03")
                    ],
                    many=True,
                ),
            ]

        async def get(self, model, identity):
            del model
            assert identity == "group-1"
            return group

        async def execute(self, query):
            del query
            return self.results.pop(0)

    snapshot = await ContentWorkflowAgent._build_strategy_snapshot(
        db=FakeDb(),
        state={"rule_version_id": "rules-v3", "selected_angle": {"direction_code": "CT05"}},
        match={"id": "match-1", "selected_group_id": "group-1"},
        formula_snapshot_id="formula-1",
        title_formula_code="T03",
        body_formula_code="C03",
    )

    assert snapshot["creation_methods"] == ["M01", "M03"]
    assert [item["code"] for item in snapshot["creation_method_definitions"]] == ["M01", "M03"]
    assert snapshot["title_formula"]["code"] == "T03"
    assert snapshot["body_formula"]["code"] == "C03"
    assert len(snapshot["snapshot_hash"]) == 64


@pytest.mark.unit
def test_persona_mapper_changes_language_only_and_preserves_locked_draft_fields():
    state = {
        "content_draft": {
            "body": "原正文",
            "topics": ["装修"],
            "paragraph_evidence": [{"paragraph_id": "p1", "evidence_ids": ["ev1"]}],
            "body_formula_code": "C01",
        }
    }

    mapped = AgentNodeResultMapper.to_state(
        "persona_style_polish",
        {
            "polished_body": "润色后的正文",
            "change_summary": ["调整语气"],
            "preserved_fact_checks": [{"evidence_id": "ev1", "preserved": True}],
        },
        state,
    )

    assert mapped["content_draft"] == {
        **state["content_draft"],
        "body": "润色后的正文",
    }
    assert mapped["persona_diff"]["preserved_fact_checks"][0]["preserved"] is True


@pytest.mark.unit
def test_direction_selection_mapper_only_accepts_value_analysis_candidates():
    state = {
        "content_angles": [
            {"direction_code": "CT03", "reason": "原候选原因", "evidence_ids": ["ev-1"]},
            {"direction_code": "CT05", "reason": "原候选原因", "evidence_ids": ["ev-2"]},
        ]
    }

    mapped = AgentNodeResultMapper.to_state(
        "select_content_direction",
        {"direction_code": "CT05", "reason": "过程证据更完整", "evidence_ids": ["ev-2"]},
        state,
    )

    assert mapped["selected_angle"] == {
        "direction_code": "CT05",
        "reason": "过程证据更完整",
        "evidence_ids": ["ev-2"],
        "selected_by": "agent",
    }

    with pytest.raises(ValueError, match="不在价值分析候选集中"):
        AgentNodeResultMapper.to_state(
            "select_content_direction",
            {"direction_code": "CT07", "reason": "越界选择", "evidence_ids": []},
            state,
        )


@pytest.mark.unit
def test_combined_value_direction_mapper_selects_only_its_own_candidate():
    result = {
        "value_points": ["空间改造前后差异明确"],
        "direction_candidates": [
            {"direction_code": "CT03", "reason": "避坑", "evidence_ids": ["ev-1"]},
            {"direction_code": "CT05", "reason": "过程", "evidence_ids": ["ev-2"]},
        ],
        "reasoning": "过程证据更完整",
        "evidence_ids": ["ev-1", "ev-2"],
        "selected_direction_code": "CT05",
        "selection_reason": "过程证据更完整",
        "selection_evidence_ids": ["ev-2"],
    }

    mapped = AgentNodeResultMapper.to_state("analyze_and_select_direction", result, {})

    assert mapped["content_angles"] == result["direction_candidates"]
    assert mapped["selected_angle"]["direction_code"] == "CT05"
    assert mapped["selected_angle"]["selected_by"] == "agent"

    with pytest.raises(ValueError, match="不在价值分析候选集中"):
        AgentNodeResultMapper.to_state(
            "analyze_and_select_direction",
            {**result, "selected_direction_code": "CT07"},
            {},
        )


@pytest.mark.asyncio
async def test_formula_ranking_skips_delegation_for_single_valid_pair():
    node_id = "rank_formula_candidates"
    state = {
        "formula_candidate_pool": {"valid_formula_pairs": [{"title_formula_code": "T03", "body_formula_code": "C03"}]}
    }
    node_run = SimpleNamespace(id="node-run-1")
    task = SimpleNamespace(id="task-1")
    user = SimpleNamespace(uid="user-1")

    class Result:
        def scalar_one_or_none(self):
            return user

    class FakeDB:
        async def get(self, model, _key):
            if model is ContentNodeRun:
                return node_run
            if model is ContentTask:
                return task
            raise AssertionError(f"unexpected model: {model}")

        async def execute(self, statement):
            assert statement is not None
            return Result()

    result = await AgentNodeHandler().execute(
        db=FakeDB(),
        node={"id": node_id},
        state={"task_id": "task-1", "uid": "user-1", **state},
        node_run_id="node-run-1",
    )

    assert result["formula_rankings"]["skipped"] is True


@pytest.mark.asyncio
async def test_prepare_formula_selection_keeps_only_pairs_covered_by_evidence():
    results = iter(
        [
            [
                SimpleNamespace(code="T01", variable_schema=["audience", "number", "result"]),
                SimpleNamespace(code="T03", variable_schema=["audience", "pain_points"]),
            ],
            [SimpleNamespace(code="C03", required_variables=["pain_points", "advantages"])],
        ]
    )

    class Result:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return self.values

    class FakeDB:
        async def execute(self, statement):
            assert statement is not None
            return Result(next(results))

    result = await V3DeterministicNodeHandler()._prepare_formula_selection(
        db=FakeDB(),
        state={
            "rule_version_id": "rules-v3",
            "content_brief": {
                "audience": "小户型业主",
                "form_values": {"pain_points": "收纳不足"},
            },
            "evidence_bundle": {"items": [{"variable_codes": ["advantages", "result"], "value": "增加收纳空间"}]},
            "formula_candidate_pool": {
                "combination_group_id": "group-1",
                "title_formula_codes": ["T01", "T03"],
                "body_formula_codes": ["C03"],
            },
        },
        node_run_id="node-run-1",
    )

    pool = result["formula_candidate_pool"]
    assert pool["title_formula_codes"] == ["T03"]
    assert pool["body_formula_codes"] == ["C03"]
    assert pool["valid_formula_pair_count"] == 1


@pytest.mark.asyncio
async def test_high_risk_gate_does_not_interrupt_without_new_high_risk_facts(monkeypatch):
    monkeypatch.setattr(
        content_workflow_graph_module,
        "interrupt",
        lambda _payload: pytest.fail("没有高风险新事实时不应触发人工中断"),
    )

    result = await ContentWorkflowAgent()._v3_human_review(
        {"id": "confirm_high_risk_facts", "interrupt_type": "high_risk_facts"},
        {
            "task_id": "task-1",
            "run_id": "run-1",
            "state_version": 1,
            "evidence_collection": {
                "evidence_items": [{"id": "ev-1", "risk_level": "normal", "verified_status": "retrieved"}]
            },
        },
    )

    assert result["state_version"] == 2


@pytest.mark.asyncio
async def test_high_risk_gate_excludes_unconfirmed_external_fact_without_interrupt(monkeypatch):
    monkeypatch.setattr(
        content_workflow_graph_module,
        "interrupt",
        lambda _payload: pytest.fail("未确认的外部高风险资料应自动排除，不应阻塞首稿"),
    )

    result = await ContentWorkflowAgent()._v3_human_review(
        {"id": "confirm_high_risk_facts", "interrupt_type": "high_risk_facts"},
        {
            "task_id": "task-1",
            "run_id": "run-1",
            "state_version": 1,
            "evidence_collection": {
                "evidence_items": [
                    {
                        "id": "ev-price",
                        "source_id": "price-row-1",
                        "value": "外部价格",
                        "risk_level": "high_risk",
                        "verified_status": "retrieved",
                    },
                    {
                        "id": "ev-user-budget",
                        "source_id": "brief-budget",
                        "value": "用户填写预算",
                        "risk_level": "high_risk",
                        "verified_status": "user_confirmed",
                    },
                ],
                "citations": ["price-row-1", "brief-budget"],
            },
        },
    )

    assert [item["id"] for item in result["evidence_collection"]["evidence_items"]] == ["ev-user-budget"]
    assert result["evidence_collection"]["citations"] == ["brief-budget"]
    assert "未经用户确认" in result["evidence_collection"]["unresolved_questions"][0]


@pytest.mark.asyncio
async def test_merge_research_excludes_unconfirmed_external_high_risk_evidence(monkeypatch):
    captured = {}

    class ValidatedResult:
        def __init__(self, payload):
            self.payload = payload

        def model_dump(self, **_kwargs):
            return self.payload

    def fake_validate(_contract, payload, _context):
        captured.update(payload)
        return ValidatedResult(payload)

    monkeypatch.setattr(deterministic_node_module, "validate_content_node_result", fake_validate)

    result = await V3DeterministicNodeHandler()._merge_research_evidence(
        db=SimpleNamespace(),
        state={
            "runtime_config_snapshot": {"creation_mode": "original", "rule_version_id": "rules-v3"},
            "formula_selection_snapshot": {},
            "evidence_bundle": {"items": []},
            "business_rule_evidence_collection": {"evidence_items": []},
            "compliance_evidence_collection": {"evidence_items": []},
            "price_evidence_collection": {
                "evidence_items": [
                    {
                        "id": "ev-price",
                        "value": "设计费 55.8 元/平方米",
                        "source_id": "price-row-1",
                        "risk_level": "high_risk",
                        "verified_status": "retrieved",
                    }
                ],
                "citations": ["price-row-1"],
                "unresolved_questions": [],
            },
            "viral_reference_selection": {},
            "viral_candidate_collection": {"evidence_items": []},
        },
        node_run_id="node-run-1",
    )

    assert result["evidence_collection"]["evidence_items"] == []
    assert captured["citations"] == []
    assert "未经用户确认" in captured["unresolved_questions"][0]


@pytest.mark.asyncio
async def test_semantic_review_skips_model_for_normal_first_draft():
    node_run = SimpleNamespace(id="node-run-1")
    task = SimpleNamespace(id="task-1")
    user = SimpleNamespace(uid="user-1")

    class Result:
        def scalar_one_or_none(self):
            return user

    class FakeDB:
        async def get(self, model, _key):
            if model is ContentNodeRun:
                return node_run
            if model is ContentTask:
                return task
            raise AssertionError(f"unexpected model: {model}")

        async def execute(self, _statement):
            return Result()

    result = await AgentNodeHandler().execute(
        db=FakeDB(),
        node={"id": "semantic_review"},
        state={"task_id": "task-1", "uid": "user-1", "runtime_config_snapshot": {}},
        node_run_id="node-run-1",
    )

    assert result["review_report"]["status"] == "passed"
    assert result["review_report"]["skipped"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("node_id", "result_field"),
    [
        ("collect_business_rule_evidence", "business_rule_evidence_collection"),
        ("collect_price_evidence", "price_evidence_collection"),
        ("collect_compliance_evidence", "compliance_evidence_collection"),
    ],
)
async def test_parallel_research_skips_agent_when_evidence_has_no_gap(node_id, result_field):
    node_run = SimpleNamespace(id="node-run-1")
    task = SimpleNamespace(id="task-1")
    user = SimpleNamespace(uid="user-1")

    class Result:
        def scalar_one_or_none(self):
            return user

    class FakeDB:
        async def get(self, model, _key):
            if model is ContentNodeRun:
                return node_run
            if model is ContentTask:
                return task
            raise AssertionError(f"unexpected model: {model}")

        async def execute(self, _statement):
            return Result()

    result = await AgentNodeHandler().execute(
        db=FakeDB(),
        node={"id": node_id},
        state={
            "task_id": "task-1",
            "uid": "user-1",
            "evidence_gap_analysis": {"has_missing": False},
            "runtime_config_snapshot": {},
        },
        node_run_id="node-run-1",
    )

    assert result[result_field]["skipped"] is True
    assert result[result_field]["evidence_items"] == []


def test_parallel_cache_key_changes_only_when_node_inputs_change():
    node = {
        "id": "collect_price_evidence",
        "parallel_group": "research",
        "state_inputs": ["content_brief", "strategy_snapshot"],
    }
    state = {"content_brief": {"city": "杭州"}, "strategy_snapshot": {"id": "s1"}, "run_id": "run-1"}

    first = _parallel_cache_key(node, state)
    assert first == _parallel_cache_key(node, {**state, "run_id": "run-2"})
    assert first != _parallel_cache_key(node, {**state, "content_brief": {"city": "长沙"}})


def test_parallel_retry_reuses_only_completed_result_with_same_input_snapshot():
    completed = [
        SimpleNamespace(
            id="stale-run",
            input_snapshot={"cache_key": "old"},
            output_snapshot={"result": {"price_evidence_collection": {"evidence_items": []}}},
        ),
        SimpleNamespace(
            id="matching-run",
            input_snapshot={"cache_key": "current"},
            output_snapshot={"result": {"price_evidence_collection": {"evidence_items": [{"id": "ev-1"}]}}},
        ),
    ]

    cached = _find_cached_parallel_result(completed, "current")

    assert cached is not None
    assert cached[0].id == "matching-run"
    assert cached[1]["price_evidence_collection"]["evidence_items"][0]["id"] == "ev-1"


@pytest.mark.asyncio
async def test_unique_formula_pair_is_locked_deterministically_even_in_pro_mode(monkeypatch):
    captured = {}

    class FakeSelectionRepository:
        def __init__(self, _db):
            pass

        async def save_formula_selection(self, **kwargs):
            captured["selected_by"] = kwargs["selected_by"]
            captured["decision"] = kwargs["decision"]
            return SimpleNamespace(id="formula-snapshot-1")

    @asynccontextmanager
    async def fake_session_context():
        yield object()

    async def fake_strategy_snapshot(**_kwargs):
        return {"snapshot_hash": "a" * 64}

    async def fake_append(*_args, **_kwargs):
        return None

    monkeypatch.setattr(content_workflow_graph_module, "PostgresDecisionSnapshotRepository", FakeSelectionRepository)
    monkeypatch.setattr(content_workflow_graph_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(ContentWorkflowAgent, "_build_strategy_snapshot", staticmethod(fake_strategy_snapshot))
    monkeypatch.setattr(content_workflow_graph_module, "append_run_stream_event", fake_append)
    monkeypatch.setattr(
        content_workflow_graph_module,
        "interrupt",
        lambda _payload: pytest.fail("唯一有效公式对不应触发专业模式人工覆盖"),
    )

    result = await ContentWorkflowAgent()._v3_human_review(
        {"id": "lock_formula_selection", "interrupt_type": "formula_selection"},
        {
            "task_id": "task-1",
            "run_id": "run-1",
            "uid": "user-1",
            "rule_version_id": "rules-v3",
            "state_version": 1,
            "task_mode": "pro",
            "match_decision_snapshot": {"id": "match-1", "selected_group_id": "group-1"},
            "formula_candidate_pool": {
                "title_formula_codes": ["T03"],
                "body_formula_codes": ["C03"],
                "valid_formula_pairs": [{"title_formula_code": "T03", "body_formula_code": "C03"}],
            },
            "formula_rankings": {
                "title_rankings": [{"formula_code": "T03", "reason": "唯一有效公式对"}],
                "body_rankings": [{"formula_code": "C03", "reason": "唯一有效公式对"}],
            },
            "evidence_bundle": {"bundle_hash": "evidence-hash"},
        },
    )

    assert captured["selected_by"] == "deterministic"
    assert captured["decision"].selection_mode == "deterministic"
    assert result["formula_selection_snapshot"]["selected_title_formula_code"] == "T03"
    assert result["formula_selection_snapshot"]["selected_body_formula_code"] == "C03"


@pytest.mark.asyncio
async def test_v3_graph_compiles_revision_routes_as_controlled_conditional_edges():
    agent = ContentWorkflowAgent()
    agent.checkpointer = InMemorySaver()
    context = ContentWorkflowContext(
        uid="user-1",
        thread_id="content-v3-compile",
        workflow_definition=deepcopy(WORKFLOW_V3),
        rule_bundle={},
    )

    workflow = await agent.get_graph(context=context)
    revision_edges = {
        edge.target for edge in workflow.get_graph().edges if edge.source == "revise_if_needed" and edge.conditional
    }

    assert revision_edges == {"semantic_review", "generate_content", "human_content_approval"}


@pytest.mark.asyncio
async def test_blocked_title_validation_routes_back_to_title_agent_without_human_interrupt():
    agent = ContentWorkflowAgent()
    state = {
        "task_id": "task-1",
        "run_id": "run-1",
        "current_node": "validate_title_candidates",
        "retry_counts": {},
        "title_validation_report": {
            "status": "blocked",
            "items": [
                {
                    "id": "t1",
                    "status": "blocked",
                    "missing_required_slots": ["product_profile"],
                    "checks": [{"code": "TITLE_PRODUCT_EVIDENCE_NOT_USED", "level": "error"}],
                }
            ],
        },
    }

    result = await agent._execute_node(
        {"id": "revise_if_needed", "type": "revision_router"},
        state,
        WORKFLOW_V3,
    )

    assert result["revision_target"] == "generate_content"
    assert result["revision_status"] == "route"
    assert result["retry_counts"] == {"generate_content": 1}


@pytest.mark.asyncio
async def test_passed_title_validation_continues_to_title_agent_selection():
    result = await ContentWorkflowAgent()._execute_node(
        {"id": "revise_if_needed", "type": "revision_router"},
        {
            "task_id": "task-1",
            "run_id": "run-1",
            "current_node": "validate_title_candidates",
            "retry_counts": {},
            "title_validation_report": {"status": "passed", "items": []},
        },
        WORKFLOW_V3,
    )

    assert result["revision_target"] == "select_title"
    assert result["revision_status"] == "continue"


@pytest.mark.unit
def test_title_selection_mapper_only_accepts_selectable_candidates():
    state = {
        "title_candidates": [
            {
                "id": "title-1",
                "text": "候选一",
                "formula_code": "T03",
                "evidence_ids": ["ev-1"],
                "selectable": False,
            },
            {
                "id": "title-2",
                "text": "候选二",
                "formula_code": "T03",
                "evidence_ids": ["ev-2"],
                "selectable": True,
            },
        ]
    }

    mapped = AgentNodeResultMapper.to_state(
        "select_title",
        {"selected_title_id": "title-2", "reason": "证据与公式更完整"},
        state,
    )

    assert mapped["selected_title"] == {
        **state["title_candidates"][1],
        "selection_reason": "证据与公式更完整",
        "selected_by": "agent",
    }

    with pytest.raises(ValueError, match="只能选择通过确定性校验"):
        AgentNodeResultMapper.to_state(
            "select_title",
            {"selected_title_id": "title-1", "reason": "错误选择"},
            state,
        )


@pytest.mark.unit
def test_unified_generation_mapper_publishes_title_outline_and_body_together():
    result = {
        "title": {"text": "统一生成标题", "formula_code": "T03", "evidence_ids": ["ev-1"]},
        "outline": {
            "body_formula_code": "C03",
            "sections": [{"section_id": "s1", "goal": "说明价值", "evidence_ids": ["ev-1"]}],
        },
        "draft": {
            "body": "统一生成正文",
            "topics": ["装修"],
            "paragraph_evidence": [{"paragraph_id": "p1", "evidence_ids": ["ev-1"]}],
            "body_formula_code": "C03",
        },
    }

    mapped = AgentNodeResultMapper.to_state("generate_content", result, {})

    assert mapped["selected_title"]["text"] == "统一生成标题"
    assert mapped["selected_title"]["selected_by"] == "agent"
    assert mapped["content_outline"] == result["outline"]
    assert mapped["content_draft"] == result["draft"]


@pytest.mark.asyncio
async def test_save_artifact_allows_content_version_without_cover(monkeypatch):
    task = SimpleNamespace(id="task-1", tenant_id=None, rule_version_id="rules-v3")
    saved = {}

    class FakeDB:
        def add(self, artifact):
            saved["artifact"] = artifact

        async def flush(self):
            return None

    class FakeRepository:
        def __init__(self, db):
            del db

        async def get_task(self, task_id, for_update=False):
            del for_update
            return task if task_id == task.id else None

        async def get_artifact_for_task(self, task_id):
            assert task_id == task.id
            return None

        async def save_artifact_version(self, *, artifact, **kwargs):
            saved["version_kwargs"] = kwargs
            return SimpleNamespace(
                id="artifact-version-1",
                version=1,
                cover_asset_id=artifact.cover_asset_id,
                cover_job_id=artifact.cover_job_id,
            )

        async def add_review_record(self, **kwargs):
            del kwargs

        async def track(self, *args, **kwargs):
            del args, kwargs

    @asynccontextmanager
    async def fake_session_context():
        yield FakeDB()

    def reject_cover_repository(db):
        del db
        raise AssertionError("无视觉工作流时不应读取封面仓储")

    monkeypatch.setattr(content_workflow_graph_module, "ContentRepository", FakeRepository)
    monkeypatch.setattr(content_workflow_graph_module, "ContentCoverRepository", reject_cover_repository)
    monkeypatch.setattr(
        content_workflow_graph_module.pg_manager,
        "get_async_session_context",
        fake_session_context,
    )

    result = await ContentWorkflowAgent()._save_artifact(
        {
            "task_id": task.id,
            "uid": "user-1",
            "run_id": "run-1",
            "content_draft": {
                "body": "已审核正文",
                "topics": ["装修"],
                "paragraph_evidence": [{"paragraph_id": "p1", "evidence_ids": ["ev-body"]}],
            },
            "selected_title": {
                "id": "title-1",
                "text": "Agent 选择的标题",
                "evidence_ids": ["ev-title"],
            },
            "strategy_snapshot": {"snapshot_hash": "s" * 64, "title_formula": {}, "body_formula": {}},
            "evidence_bundle": {"bundle_hash": "e" * 64, "items": []},
            "review_report": {"status": "passed", "checks": []},
            "approval_result": {"status": "approved", "reviewer_uid": "user-1"},
            "runtime_config_snapshot": {},
        }
    )

    assert result["artifact_version"]["cover_asset_id"] is None
    assert saved["artifact"].cover_asset_id is None
    assert saved["artifact"].evidence_usage_snapshot == {
        "version": 1,
        "items": [
            {"evidence_id": "ev-title", "usages": [{"target": "title", "location": "标题"}]},
            {
                "evidence_id": "ev-body",
                "usages": [{"target": "body", "location": "正文第1段", "paragraph_id": "p1"}],
            },
        ],
    }
    assert task.selected_title_json["text"] == "Agent 选择的标题"
    assert task.status == "reviewed"


@pytest.mark.asyncio
async def test_save_artifact_binds_selected_reviewed_cover(monkeypatch):
    task = SimpleNamespace(id="task-1", tenant_id=None, rule_version_id="rules-v3")
    saved = {}

    class FakeDB:
        def add(self, artifact):
            saved["artifact"] = artifact

        async def flush(self):
            return None

    class FakeRepository:
        def __init__(self, db):
            del db

        async def get_task(self, task_id, for_update=False):
            del for_update
            return task if task_id == task.id else None

        async def get_artifact_for_task(self, task_id):
            assert task_id == task.id
            return None

        async def save_artifact_version(self, *, artifact, **kwargs):
            del kwargs
            return SimpleNamespace(
                id="artifact-version-1",
                version=1,
                cover_asset_id=artifact.cover_asset_id,
                cover_job_id=artifact.cover_job_id,
            )

        async def add_review_record(self, **kwargs):
            del kwargs

        async def track(self, *args, **kwargs):
            del args, kwargs

    class FakeCoverRepository:
        def __init__(self, db):
            del db

        async def get_job(self, job_id):
            assert job_id == "cover-job-1"
            return SimpleNamespace(
                status="succeeded",
                content_task_id=task.id,
                result_json={"asset_ids": ["cover-asset-1"]},
            )

        async def get_asset(self, asset_id):
            assert asset_id == "cover-asset-1"
            return SimpleNamespace(id=asset_id, owner_uid="user-1", role="output")

    @asynccontextmanager
    async def fake_session_context():
        yield FakeDB()

    monkeypatch.setattr(content_workflow_graph_module, "ContentRepository", FakeRepository)
    monkeypatch.setattr(content_workflow_graph_module, "ContentCoverRepository", FakeCoverRepository)
    monkeypatch.setattr(
        content_workflow_graph_module.pg_manager,
        "get_async_session_context",
        fake_session_context,
    )

    result = await ContentWorkflowAgent()._save_artifact(
        {
            "task_id": task.id,
            "uid": "user-1",
            "run_id": "run-1",
            "content_draft": {"body": "已审核正文", "topics": ["装修"]},
            "selected_title": {"id": "title-1", "text": "最终标题"},
            "strategy_snapshot": {"snapshot_hash": "s" * 64, "title_formula": {}, "body_formula": {}},
            "evidence_bundle": {"bundle_hash": "e" * 64, "items": []},
            "review_report": {"status": "passed", "checks": []},
            "approval_result": {"status": "approved", "reviewer_uid": "user-1"},
            "artifact_version": {"id": "artifact-version-1", "content_hash": "c" * 64},
            "selected_cover": {"asset_id": "cover-asset-1", "cover_job_id": "cover-job-1"},
            "visual_review": {"assets": [{"asset_id": "cover-asset-1", "status": "passed"}]},
            "runtime_config_snapshot": {},
        }
    )

    assert result["artifact_version"]["cover_asset_id"] == "cover-asset-1"
    assert result["artifact_version"]["cover_job_id"] == "cover-job-1"
    assert saved["artifact"].cover_asset_id == "cover-asset-1"
    assert saved["artifact"].cover_job_id == "cover-job-1"


@pytest.mark.asyncio
async def test_deterministic_block_routes_directly_to_targeted_agent_revision():
    agent = ContentWorkflowAgent()
    graph = StateGraph(ContentWorkflowState)

    async def revise(state: ContentWorkflowState):
        return await agent._execute_node(
            {"id": "revise_if_needed", "type": "revision_router"},
            state,
            WORKFLOW_V3,
        )

    graph.add_node("revise_if_needed", revise)
    graph.add_edge(START, "revise_if_needed")
    graph.add_edge("revise_if_needed", END)
    workflow = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "correction-gate"}}
    state = {
        "task_id": "task-1",
        "run_id": "run-1",
        "current_node": "deterministic_validate",
        "state_version": 7,
        "retry_counts": {},
        "validation_report": {
            "status": "blocked",
            "checks": [
                {
                    "code": "BODY_LENGTH_OUT_OF_RANGE",
                    "level": "error",
                    "message": "正文长度不符合要求",
                }
            ],
        },
    }

    await workflow.ainvoke(state, config=config)
    completed = await workflow.aget_state(config)
    assert completed.interrupts == ()
    assert completed.values["revision_target"] == "generate_content"
    assert completed.values["retry_counts"] == {"generate_content": 1}


@pytest.mark.asyncio
async def test_system_validation_failure_never_reaches_semantic_agent_or_human_approval():
    agent = ContentWorkflowAgent()
    state = {
        "task_id": "task-1",
        "run_id": "run-1",
        "current_node": "deterministic_validate",
        "validation_report": {
            "status": "blocked",
            "checks": [{"code": "CONTENT_STRATEGY_SNAPSHOT_MISSING", "level": "error"}],
        },
    }

    with pytest.raises(ContentApplicationError) as exc_info:
        await agent._execute_node(
            {"id": "revise_if_needed", "type": "revision_router"},
            state,
            WORKFLOW_V3,
        )

    assert exc_info.value.code == "system_configuration_failed"


@pytest.mark.asyncio
async def test_final_approval_is_a_backend_hard_gate_for_both_reports():
    agent = ContentWorkflowAgent()
    state = {
        "task_id": "task-1",
        "run_id": "run-1",
        "state_version": 1,
        "validation_report": {"status": "passed", "checks": []},
        "review_report": {
            "status": "blocked",
            "checks": [{"code": "FACT_INCONSISTENT", "status": "blocked"}],
        },
    }

    with pytest.raises(ContentApplicationError) as exc_info:
        await agent._v3_human_review(
            {"id": "human_content_approval", "interrupt_type": "content_approval"},
            state,
        )

    assert exc_info.value.code == "content_approval_blocked"


@pytest.mark.asyncio
async def test_final_approval_is_automatic_after_reports_pass():
    agent = ContentWorkflowAgent()
    state = {
        "task_id": "task-1",
        "run_id": "run-1",
        "uid": "user-1",
        "state_version": 1,
        "selected_title": {"text": "标题"},
        "content_draft": {"body": "正文"},
        "evidence_bundle": {"bundle_hash": "bundle-1"},
        "validation_report": {"status": "passed", "checks": []},
        "review_report": {"status": "passed", "checks": []},
    }

    result = await agent._v3_human_review(
        {"id": "human_content_approval", "interrupt_type": "content_approval"},
        state,
    )

    assert result["approval_result"] == {
        "status": "approved",
        "note": "固定校验通过后自动批准",
        "reviewer_uid": "system",
    }


@pytest.mark.asyncio
async def test_blocked_content_validation_routes_to_revision_without_human_interrupt():
    agent = ContentWorkflowAgent()
    result = await agent._execute_node(
        {"id": "revise_if_needed", "type": "revision_router"},
        {
            "task_id": "task-1",
            "run_id": "run-1",
            "current_node": "deterministic_validate",
            "retry_counts": {},
            "validation_report": {
                "status": "blocked",
                "checks": [{"code": "BODY_LENGTH_OUT_OF_RANGE", "level": "error"}],
            },
        },
        WORKFLOW_V3,
    )

    assert result["revision_status"] == "route"
    assert result["revision_target"] == "generate_content"
    assert result["revision_reason_code"] == "BODY_STRUCTURE_FAILED"


def _title_gate_graph(agent: ContentWorkflowAgent):
    graph = StateGraph(ContentWorkflowState)

    async def select_title(state: ContentWorkflowState):
        return await agent._v3_human_review(
            {"id": "select_title", "interrupt_type": "title_selection"},
            state,
        )

    graph.add_node("select_title", select_title)
    graph.add_edge(START, "select_title")
    graph.add_edge("select_title", END)
    return graph.compile(checkpointer=InMemorySaver())


@pytest.mark.asyncio
async def test_v3_human_gate_rejects_stale_resume_and_only_exposes_selectable_titles():
    workflow = _title_gate_graph(ContentWorkflowAgent())
    config = {"configurable": {"thread_id": "title-gate-stale"}}
    state = {
        "task_id": "task-1",
        "run_id": "run-1",
        "state_version": 3,
        "title_candidates": [
            {"id": "good", "text": "可选标题", "selectable": True},
            {"id": "bad", "text": "阻断标题", "selectable": False},
        ],
    }

    await workflow.ainvoke(state, config=config)
    interrupted = await workflow.aget_state(config)
    assert [item["id"] for item in interrupted.interrupts[0].value["options"]] == ["good"]

    with pytest.raises(ValueError, match="已过期或目标不匹配"):
        await workflow.ainvoke(
            Command(
                resume={
                    "run_id": "run-1",
                    "node_id": "select_title",
                    "expected_state_version": 2,
                    "title_id": "good",
                }
            ),
            config=config,
        )


@pytest.mark.asyncio
async def test_v3_human_gate_accepts_exact_run_node_and_state_version():
    workflow = _title_gate_graph(ContentWorkflowAgent())
    config = {"configurable": {"thread_id": "title-gate-current"}}
    state = {
        "task_id": "task-1",
        "run_id": "run-1",
        "state_version": 3,
        "title_candidates": [{"id": "good", "text": "可选标题", "selectable": True}],
    }

    await workflow.ainvoke(state, config=config)
    result = await workflow.ainvoke(
        Command(
            resume={
                "run_id": "run-1",
                "node_id": "select_title",
                "expected_state_version": 3,
                "title_id": "good",
            }
        ),
        config=config,
    )

    assert result["selected_title"]["id"] == "good"
    assert result["state_version"] == 4
