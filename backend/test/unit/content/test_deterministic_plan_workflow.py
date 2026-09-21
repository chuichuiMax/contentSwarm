from yuxi.content.model.workflows.definition import WorkflowDefinitionPolicy
from yuxi.content.v3.joint_workflow import WORKFLOW_DETERMINISTIC_PLAN, WORKFLOW_STANDARDIZED_FACTORY


def test_deterministic_plan_workflow_has_no_strategy_selection_agent():
    WorkflowDefinitionPolicy.validate(WORKFLOW_DETERMINISTIC_PLAN)
    nodes = {node["id"]: node for node in WORKFLOW_DETERMINISTIC_PLAN["nodes"]}
    assert "select_creation_strategy" not in nodes
    assert "reselect_creation_strategy" not in nodes
    assert "lock_creation_strategy" not in nodes
    assert nodes["prepare_creation_plan_inputs"]["type"] == "deterministic"
    assert nodes["extract_creation_facts"]["type"] == "agent"
    assert nodes["extract_creation_facts"]["agent_slug"] == "content-fact-extraction-agent"
    assert nodes["merge_extracted_creation_facts"]["type"] == "deterministic"
    assert nodes["build_creation_plan"]["type"] == "deterministic"
    assert nodes["research_strategy_prices"]["input_contract"] == "ResearchCreationPlanPricesInputV1"
    assert sum(node["id"] == "generate_content" for node in nodes.values()) == 1


def test_standardized_factory_requires_material_gate_and_frozen_pack_before_generation():
    WorkflowDefinitionPolicy.validate(WORKFLOW_STANDARDIZED_FACTORY)
    nodes = {node["id"]: node for node in WORKFLOW_STANDARDIZED_FACTORY["nodes"]}
    edges = {tuple(edge) for edge in WORKFLOW_STANDARDIZED_FACTORY["edges"]}

    assert nodes["validate_material_gate"]["type"] == "deterministic"
    assert nodes["freeze_production_pack"]["type"] == "deterministic"
    assert nodes["compose_locked_quote_block"]["type"] == "deterministic"
    assert nodes["validate_composed_content"]["type"] == "deterministic"
    assert "production_pack" in nodes["generate_content"]["state_inputs"]
    assert nodes["semantic_review"]["output_contract"] == "StandardizedContentReviewResultV1"
    assert ("freeze_evidence_bundle", "generate_content") not in edges
    assert {
        ("freeze_evidence_bundle", "validate_material_gate"),
        ("validate_material_gate", "freeze_production_pack"),
        ("freeze_production_pack", "generate_content"),
        ("compose_locked_quote_block", "validate_composed_content"),
        ("validate_composed_content", "semantic_review"),
    } <= edges
    assert ("validate_composed_content", "human_content_approval") not in edges
