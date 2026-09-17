from __future__ import annotations

from typing import Any

LEGACY_PLATFORM_WORKFLOW_V3_IDS = frozenset(
    {
        "content-workflow-enterprise-v3",
        "content-workflow-enterprise-v3.1",
        "content-workflow-enterprise-v3.2",
        "content-workflow-enterprise-v3.3",
        "content-workflow-enterprise-v3.4",
        "content-workflow-enterprise-v3.5",
        "content-workflow-enterprise-v3.6",
    }
)
LEGACY_PLATFORM_WORKFLOW_V3_ID = "content-workflow-enterprise-v3.5"
PLATFORM_WORKFLOW_V3_ID = "content-workflow-enterprise-v3.7"


def _fixed(node_id: str) -> dict[str, Any]:
    return {"id": node_id, "type": "deterministic", "handler": node_id}


def _human(node_id: str, interrupt_type: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "human_review",
        "interrupt_type": interrupt_type,
        "expected_state_version_required": True,
    }


def _agent(
    node_id: str,
    agent_slug: str,
    skill_slugs: str | tuple[str, ...],
    input_contract: str,
    output_contract: str,
    *,
    state_inputs: tuple[str, ...],
    optional_state_inputs: tuple[str, ...] = (),
    knowledge_policy: str = "frozen_evidence_only",
    max_tool_calls: int = 4,
    max_retrieval_rounds: int = 0,
    max_knowledge_bases: int = 0,
    max_chunks_per_knowledge_base: int = 0,
    max_chars_per_knowledge_chunk: int = 0,
    token_budget: int = 8000,
    timeout_seconds: int = 120,
    parallel_group: str | None = None,
) -> dict[str, Any]:
    node = {
        "id": node_id,
        "type": "agent",
        "agent_slug": agent_slug,
        "required_skills": [skill_slugs] if isinstance(skill_slugs, str) else list(skill_slugs),
        "input_contract": input_contract,
        "state_inputs": list(state_inputs),
        "optional_state_inputs": list(optional_state_inputs),
        "output_contract": output_contract,
        "backend": "managed",
        "knowledge_policy": knowledge_policy,
        "timeout_seconds": timeout_seconds,
        "max_execution_steps": 12,
        "max_tool_calls": max_tool_calls,
        "token_budget": token_budget,
        "result_tool_name": "submit_content_node_result",
    }
    if knowledge_policy == "agent_scope":
        node.update(
            {
                "max_retrieval_rounds": max_retrieval_rounds,
                "max_knowledge_bases": max_knowledge_bases,
                "max_chunks_per_knowledge_base": max_chunks_per_knowledge_base,
                "max_chars_per_knowledge_chunk": max_chars_per_knowledge_chunk,
            }
        )
    if parallel_group:
        node["parallel_group"] = parallel_group
    return node


WORKFLOW_V3_NODES = [
    _fixed("compile_runtime_snapshot"),
    _fixed("ingest_real_materials"),
    _fixed("normalize_evidence"),
    _fixed("select_creation_strategy"),
    _fixed("lock_creation_strategy"),
    _fixed("load_formula_lexicons"),
    _agent(
        "collect_business_rule_evidence",
        "content-business-rule-research-agent",
        "content-business-rule-researcher",
        "CollectBusinessRuleEvidenceInputV1",
        "BusinessRuleEvidenceCollectionResultV1",
        state_inputs=(
            "rule_version_id",
            "content_brief",
            "strategy_selection",
            "strategy_snapshot",
            "evidence_gap_analysis",
            "evidence_bundle",
            "runtime_config_snapshot",
        ),
        knowledge_policy="agent_scope",
        max_tool_calls=4,
        max_retrieval_rounds=2,
        max_knowledge_bases=2,
        max_chunks_per_knowledge_base=4,
        max_chars_per_knowledge_chunk=2400,
        token_budget=6000,
        timeout_seconds=125,
        parallel_group="research",
    ),
    _agent(
        "collect_price_evidence",
        "content-price-research-agent",
        "content-price-researcher",
        "CollectPriceEvidenceInputV1",
        "PriceEvidenceCollectionResultV1",
        state_inputs=(
            "content_brief",
            "strategy_snapshot",
            "evidence_gap_analysis",
            "runtime_config_snapshot",
        ),
        knowledge_policy="agent_scope",
        max_tool_calls=3,
        max_retrieval_rounds=1,
        max_knowledge_bases=1,
        max_chunks_per_knowledge_base=4,
        max_chars_per_knowledge_chunk=2400,
        token_budget=5000,
        timeout_seconds=125,
        parallel_group="research",
    ),
    _agent(
        "collect_compliance_evidence",
        "content-compliance-research-agent",
        "content-compliance-researcher",
        "CollectComplianceEvidenceInputV1",
        "ComplianceEvidenceCollectionResultV1",
        state_inputs=(
            "rule_version_id",
            "content_brief",
            "strategy_selection",
            "strategy_snapshot",
            "evidence_gap_analysis",
            "evidence_bundle",
            "runtime_config_snapshot",
        ),
        knowledge_policy="agent_scope",
        max_tool_calls=4,
        max_retrieval_rounds=1,
        max_knowledge_bases=1,
        max_chunks_per_knowledge_base=4,
        max_chars_per_knowledge_chunk=2400,
        token_budget=5000,
        timeout_seconds=100,
        parallel_group="research",
    ),
    _agent(
        "collect_viral_candidates",
        "content-viral-candidate-agent",
        "viral-candidate-researcher",
        "CollectViralCandidatesInputV1",
        "ViralCandidateCollectionResultV1",
        state_inputs=(
            "rule_version_id",
            "content_brief",
            "strategy_selection",
            "strategy_snapshot",
            "evidence_gap_analysis",
            "evidence_bundle",
            "runtime_config_snapshot",
        ),
        knowledge_policy="agent_scope",
        max_tool_calls=3,
        max_retrieval_rounds=1,
        max_knowledge_bases=1,
        max_chunks_per_knowledge_base=2,
        max_chars_per_knowledge_chunk=800,
        token_budget=4000,
        timeout_seconds=140,
        parallel_group="research",
    ),
    _agent(
        "select_viral_reference",
        "content-viral-selection-agent",
        "viral-reference-selector",
        "SelectViralReferenceInputV1",
        "ViralReferenceSelectionResultV1",
        state_inputs=(
            "content_brief",
            "strategy_snapshot",
            "runtime_config_snapshot",
            "viral_candidate_collection",
        ),
        max_tool_calls=1,
        token_budget=8000,
        timeout_seconds=75,
    ),
    _fixed("merge_research_evidence"),
    _human("confirm_high_risk_facts", "high_risk_facts"),
    _fixed("freeze_evidence_bundle"),
    _agent(
        "generate_content",
        "content-generation-agent",
        (
            "content-title-generator",
            "content-outline-builder",
            "content-body-generator",
            "viral-structure-rewriter",
            "viral-layout-formatter",
            "humanizer-zh",
            "content-human-expression",
        ),
        "GenerateContentInputV1",
        "GeneratedContentResultV1",
        state_inputs=(
            "content_brief",
            "strategy_snapshot",
            "formula_lexicon_bundle",
            "evidence_bundle",
            "channel_profile",
            "persona_profile",
            "runtime_config_snapshot",
        ),
        optional_state_inputs=(
            "validation_report",
            "review_report",
            "selected_title",
            "content_outline",
            "content_draft",
        ),
        token_budget=12000,
    ),
    _fixed("adapt_to_channel"),
    _fixed("deterministic_validate"),
    _agent(
        "semantic_review",
        "content-review-agent",
        "content-reviewer",
        "SemanticReviewInputV1",
        "ContentReviewResultV1",
        state_inputs=(
            "content_brief",
            "strategy_snapshot",
            "selected_title",
            "content_outline",
            "content_draft",
            "validation_report",
            "channel_result",
            "evidence_bundle",
        ),
        optional_state_inputs=("persona_diff",),
        knowledge_policy="frozen_evidence_only",
    ),
    {"id": "revise_if_needed", "type": "revision_router", "handler": "revise_if_needed"},
    _human("human_content_approval", "content_approval"),
    _agent(
        "plan_visuals",
        "content-visual-agent",
        "content-visual-planner",
        "PlanVisualsInputV1",
        "VisualPlanResultV1",
        state_inputs=(
            "selected_title",
            "content_draft",
            "strategy_snapshot",
            "evidence_bundle",
            "media_evidence_items",
            "artifact_version",
            "channel_profile",
            "runtime_config_snapshot",
        ),
    ),
    _agent(
        "submit_cover_job",
        "content-visual-agent",
        "content-cover-generator",
        "SubmitCoverJobInputV1",
        "CoverJobSubmissionResultV1",
        state_inputs=("visual_plan", "artifact_version", "media_evidence_items"),
        max_tool_calls=2,
    ),
    {
        "id": "wait_cover_job",
        "type": "external_wait",
        "external_job_type": "content_cover",
        "timeout_seconds": 900,
        "state_version_required": True,
    },
    _agent(
        "visual_review",
        "content-visual-agent",
        "content-visual-reviewer",
        "VisualReviewInputV1",
        "VisualReviewResultV1",
        state_inputs=(
            "selected_title",
            "content_draft",
            "visual_plan",
            "cover_job",
            "cover_assets",
            "evidence_bundle",
        ),
    ),
    _human("select_cover", "cover_selection"),
    _fixed("save_artifact_snapshot"),
]

WORKFLOW_V3 = {
    "schema_version": 3,
    "runtime_limits": {"max_steps": 60, "max_revision_attempts": 3},
    "nodes": WORKFLOW_V3_NODES,
    "edges": [
        ["compile_runtime_snapshot", "ingest_real_materials"],
        ["ingest_real_materials", "normalize_evidence"],
        ["normalize_evidence", "select_creation_strategy"],
        ["select_creation_strategy", "lock_creation_strategy"],
        ["lock_creation_strategy", "load_formula_lexicons"],
        *[
            ["load_formula_lexicons", node_id]
            for node_id in (
                "collect_business_rule_evidence",
                "collect_price_evidence",
                "collect_compliance_evidence",
                "collect_viral_candidates",
            )
        ],
        *[
            [node_id, "select_viral_reference"]
            for node_id in (
                "collect_business_rule_evidence",
                "collect_price_evidence",
                "collect_compliance_evidence",
                "collect_viral_candidates",
            )
        ],
        ["select_viral_reference", "merge_research_evidence"],
        ["merge_research_evidence", "confirm_high_risk_facts"],
        ["confirm_high_risk_facts", "freeze_evidence_bundle"],
        ["freeze_evidence_bundle", "generate_content"],
        ["generate_content", "adapt_to_channel"],
        ["adapt_to_channel", "deterministic_validate"],
        ["deterministic_validate", "revise_if_needed"],
        ["semantic_review", "revise_if_needed"],
        ["human_content_approval", "plan_visuals"],
        ["plan_visuals", "submit_cover_job"],
        ["submit_cover_job", "wait_cover_job"],
        ["wait_cover_job", "visual_review"],
        ["visual_review", "select_cover"],
        ["select_cover", "save_artifact_snapshot"],
    ],
    "revision_routes": [
        {
            "from": "revise_if_needed",
            "to": "generate_content",
            "reason_codes": [
                "TITLE_VALIDATION_FAILED",
                "BODY_STRUCTURE_FAILED",
                "BODY_EVIDENCE_FAILED",
                "PERSONA_STYLE_FAILED",
            ],
            "max_attempts": 2,
        },
    ],
    "required_human_gates": sorted(
        {
            "confirm_high_risk_facts",
            "human_content_approval",
            "select_cover",
        }
    ),
}


__all__ = [
    "LEGACY_PLATFORM_WORKFLOW_V3_ID",
    "LEGACY_PLATFORM_WORKFLOW_V3_IDS",
    "PLATFORM_WORKFLOW_V3_ID",
    "WORKFLOW_V3",
    "WORKFLOW_V3_NODES",
]
