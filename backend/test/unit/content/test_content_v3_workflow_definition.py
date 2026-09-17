from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from yuxi.content.control.workflow.revision import RevisionRouteController
from yuxi.content.model.workflows.definition import DEFAULT_CONTRACTS, WorkflowCatalog, WorkflowDefinitionPolicy
from yuxi.content.v3.seed import _upgrade_system_workflow_v3
from yuxi.content.v3.agents import CONTENT_AGENT_SPECS
from yuxi.content.v3.workflow import WORKFLOW_V3


AGENTS = {
    "content-strategy-agent",
    "content-research-agent",
    "content-business-rule-research-agent",
    "content-price-research-agent",
    "content-compliance-research-agent",
    "content-viral-candidate-agent",
    "content-viral-selection-agent",
    "content-generation-agent",
    "content-review-agent",
    "content-visual-agent",
}
SKILLS = {
    "content-value-analyzer",
    "content-strategy-planner",
    "content-evidence-researcher",
    "content-business-rule-researcher",
    "content-price-researcher",
    "content-compliance-researcher",
    "viral-candidate-researcher",
    "viral-reference-selector",
    "content-title-generator",
    "content-outline-builder",
    "content-body-generator",
    "viral-structure-rewriter",
    "viral-layout-formatter",
    "humanizer-zh",
    "content-human-expression",
    "content-reviewer",
    "content-visual-planner",
    "content-cover-generator",
    "content-visual-reviewer",
}
CATALOG = WorkflowCatalog(
    agents=frozenset(AGENTS),
    skills=frozenset(SKILLS),
    contracts=frozenset(DEFAULT_CONTRACTS),
    backends=frozenset({"managed"}),
)


def _node(definition: dict, node_id: str) -> dict:
    return next(item for item in definition["nodes"] if item["id"] == node_id)


@pytest.mark.unit
def test_v37_has_26_nodes_and_passes_full_catalog_validation():
    WorkflowDefinitionPolicy.validate(WORKFLOW_V3, catalog=CATALOG)
    assert len(WORKFLOW_V3["nodes"]) == 26
    assert len(WORKFLOW_V3["edges"]) == 27


@pytest.mark.unit
def test_simplified_agent_nodes_receive_required_upstream_state():
    expected = {
        "collect_business_rule_evidence": (
            "CollectBusinessRuleEvidenceInputV1",
            {"strategy_selection", "strategy_snapshot", "evidence_gap_analysis", "evidence_bundle"},
        ),
        "collect_price_evidence": (
            "CollectPriceEvidenceInputV1",
            {"content_brief", "strategy_snapshot", "evidence_gap_analysis", "runtime_config_snapshot"},
        ),
        "collect_compliance_evidence": (
            "CollectComplianceEvidenceInputV1",
            {"strategy_selection", "strategy_snapshot", "evidence_gap_analysis", "evidence_bundle"},
        ),
        "collect_viral_candidates": (
            "CollectViralCandidatesInputV1",
            {"strategy_selection", "strategy_snapshot", "evidence_gap_analysis", "evidence_bundle"},
        ),
        "select_viral_reference": (
            "SelectViralReferenceInputV1",
            {"content_brief", "strategy_snapshot", "runtime_config_snapshot", "viral_candidate_collection"},
        ),
        "generate_content": (
            "GenerateContentInputV1",
            {"strategy_snapshot", "formula_lexicon_bundle", "content_brief", "evidence_bundle"},
        ),
        "semantic_review": ("SemanticReviewInputV1", {"content_draft", "validation_report", "strategy_snapshot"}),
        "plan_visuals": ("PlanVisualsInputV1", {"content_draft", "artifact_version", "channel_profile"}),
        "submit_cover_job": ("SubmitCoverJobInputV1", {"visual_plan", "artifact_version"}),
        "visual_review": ("VisualReviewInputV1", {"visual_plan", "cover_job", "cover_assets"}),
    }
    nodes = {item["id"]: item for item in WORKFLOW_V3["nodes"] if item["type"] == "agent"}
    assert set(nodes) == set(expected)
    for node_id, (contract, inputs) in expected.items():
        assert nodes[node_id]["input_contract"] == contract
        assert inputs <= set(nodes[node_id]["state_inputs"])


@pytest.mark.unit
def test_only_parallel_research_nodes_can_query_knowledge_base():
    assert {
        item["id"]
        for item in WORKFLOW_V3["nodes"]
        if item["type"] == "agent" and item["knowledge_policy"] == "agent_scope"
    } == {
        "collect_business_rule_evidence",
        "collect_price_evidence",
        "collect_compliance_evidence",
        "collect_viral_candidates",
    }


@pytest.mark.unit
def test_creation_research_is_split_into_bounded_parallel_nodes_before_selection():
    research_ids = {
        "collect_business_rule_evidence",
        "collect_price_evidence",
        "collect_compliance_evidence",
        "collect_viral_candidates",
    }
    for node_id in research_ids:
        node = _node(WORKFLOW_V3, node_id)
        assert node["parallel_group"] == "research"
        assert node["timeout_seconds"] <= 140
        assert node["max_knowledge_bases"] <= 2
        assert node["max_chars_per_knowledge_chunk"] <= 2400
        assert "runtime_config_snapshot" in node["state_inputs"]
        assert ["load_formula_lexicons", node_id] in WORKFLOW_V3["edges"]
        assert [node_id, "select_viral_reference"] in WORKFLOW_V3["edges"]
    selector = _node(WORKFLOW_V3, "select_viral_reference")
    assert _node(WORKFLOW_V3, "collect_business_rule_evidence")["timeout_seconds"] == 125
    assert _node(WORKFLOW_V3, "collect_viral_candidates")["max_chunks_per_knowledge_base"] == 2
    assert _node(WORKFLOW_V3, "collect_viral_candidates")["max_chars_per_knowledge_chunk"] == 800
    assert _node(WORKFLOW_V3, "collect_viral_candidates")["timeout_seconds"] == 140
    assert selector["timeout_seconds"] == 75
    assert selector["knowledge_policy"] == "frozen_evidence_only"
    assert selector["required_skills"] == ["viral-reference-selector"]
    assert ["select_viral_reference", "merge_research_evidence"] in WORKFLOW_V3["edges"]


@pytest.mark.unit
def test_parallel_research_two_model_calls_fit_inside_each_node_timeout():
    agent_by_node = {
        "collect_business_rule_evidence": "content-business-rule-research-agent",
        "collect_price_evidence": "content-price-research-agent",
        "collect_compliance_evidence": "content-compliance-research-agent",
        "collect_viral_candidates": "content-viral-candidate-agent",
    }
    specs = {item.slug: item for item in CONTENT_AGENT_SPECS}

    for node_id, agent_slug in agent_by_node.items():
        spec = specs[agent_slug]
        assert spec.model_retry_times == 0
        assert spec.model_call_timeout_seconds * 2 + 10 <= _node(WORKFLOW_V3, node_id)["timeout_seconds"]


@pytest.mark.unit
def test_strategy_is_deterministic_and_generation_remains_one_agent_call():
    strategy = _node(WORKFLOW_V3, "select_creation_strategy")
    generation = _node(WORKFLOW_V3, "generate_content")
    node_ids = [item["id"] for item in WORKFLOW_V3["nodes"]]
    assert strategy["type"] == "deterministic"
    assert generation["required_skills"] == [
        "content-title-generator",
        "content-outline-builder",
        "content-body-generator",
        "viral-structure-rewriter",
        "viral-layout-formatter",
        "humanizer-zh",
        "content-human-expression",
    ]
    assert node_ids.index("select_creation_strategy") < node_ids.index("lock_creation_strategy")
    assert node_ids.index("freeze_evidence_bundle") < node_ids.index("generate_content")
    assert not {
        "match_combination_group",
        "rank_formula_candidates",
        "lock_formula_selection",
        "collect_strategy_product_evidence",
        "generate_title_candidates",
        "select_title",
        "build_outline",
        "generate_body",
        "persona_style_polish",
    } & set(node_ids)


@pytest.mark.unit
def test_system_seed_upgrades_to_content_and_cover_version_14():
    stale = SimpleNamespace(
        created_by="system",
        schema_version=3,
        definition_json={},
        definition_hash="stale",
        input_schema={},
        output_schema={},
    )
    assert _upgrade_system_workflow_v3(stale) is True
    assert stale.version == 14
    assert stale.definition_json == WORKFLOW_V3


@pytest.mark.unit
@pytest.mark.parametrize(
    "field",
    [
        "agent_slug",
        "required_skills",
        "input_contract",
        "output_contract",
        "backend",
        "knowledge_policy",
        "timeout_seconds",
        "max_execution_steps",
        "max_tool_calls",
        "token_budget",
    ],
)
def test_agent_nodes_require_complete_runtime_configuration(field: str):
    definition = deepcopy(WORKFLOW_V3)
    _node(definition, "generate_content").pop(field)
    with pytest.raises(ValueError, match="缺少配置"):
        WorkflowDefinitionPolicy.validate(definition, catalog=CATALOG)


@pytest.mark.unit
def test_fixed_strategy_lock_and_human_gates_are_required():
    invalid_lock = deepcopy(WORKFLOW_V3)
    _node(invalid_lock, "lock_creation_strategy")["type"] = "agent"
    with pytest.raises(ValueError, match="固定规则"):
        WorkflowDefinitionPolicy.validate(invalid_lock)
    missing_gate = deepcopy(WORKFLOW_V3)
    missing_gate["nodes"] = [item for item in missing_gate["nodes"] if item["id"] != "human_content_approval"]
    missing_gate["edges"] = [edge for edge in missing_gate["edges"] if "human_content_approval" not in edge]
    with pytest.raises(ValueError, match="26 个节点|人工关口"):
        WorkflowDefinitionPolicy.validate(missing_gate)


@pytest.mark.unit
def test_cover_generation_runs_after_content_approval_and_before_snapshot():
    node_ids = [item["id"] for item in WORKFLOW_V3["nodes"]]
    assert node_ids[node_ids.index("human_content_approval") :] == [
        "human_content_approval",
        "plan_visuals",
        "submit_cover_job",
        "wait_cover_job",
        "visual_review",
        "select_cover",
        "save_artifact_snapshot",
    ]
    assert "select_cover" in WORKFLOW_V3["required_human_gates"]


@pytest.mark.unit
def test_validation_cannot_bypass_revision_router():
    bypass = deepcopy(WORKFLOW_V3)
    bypass["edges"].remove(["deterministic_validate", "revise_if_needed"])
    bypass["edges"].append(["deterministic_validate", "semantic_review"])
    with pytest.raises(ValueError, match="固定回修"):
        WorkflowDefinitionPolicy.validate(bypass)


@pytest.mark.unit
def test_revision_controller_routes_generation_failures_to_unified_agent():
    controller = RevisionRouteController()
    first = controller.decide(definition=WORKFLOW_V3, reason_code="BODY_EVIDENCE_FAILED", retry_counts={})
    second = controller.decide(
        definition=WORKFLOW_V3, reason_code="BODY_EVIDENCE_FAILED", retry_counts=first.retry_counts
    )
    stopped = controller.decide(
        definition=WORKFLOW_V3, reason_code="BODY_EVIDENCE_FAILED", retry_counts=second.retry_counts
    )
    assert first.target_node_id == "generate_content"
    assert second.retry_counts == {"generate_content": 2}
    assert stopped.status == "limit_reached"

    persona = controller.decide(definition=WORKFLOW_V3, reason_code="PERSONA_STYLE_FAILED", retry_counts={})
    assert persona.target_node_id == "generate_content"


@pytest.mark.unit
def test_schema_v2_and_unknown_agent_are_rejected():
    old = deepcopy(WORKFLOW_V3)
    old["schema_version"] = 2
    with pytest.raises(ValueError, match="只支持 V3"):
        WorkflowDefinitionPolicy.validate(old)
    unknown = deepcopy(WORKFLOW_V3)
    _node(unknown, "generate_content")["agent_slug"] = "missing-agent"
    with pytest.raises(ValueError, match="未知 Agent"):
        WorkflowDefinitionPolicy.validate(unknown, catalog=CATALOG)
