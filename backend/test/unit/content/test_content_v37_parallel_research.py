from __future__ import annotations

import pytest

from yuxi.agents.buildin.content_workflow.state import merge_delegated_agent_runs
from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
from yuxi.content.model.evidence import freeze_evidence_bundle


@pytest.mark.unit
def test_parallel_agent_run_updates_are_merged_without_overwrite():
    assert merge_delegated_agent_runs(
        {"collect_business_rule_evidence": "run-business"},
        {"collect_price_evidence": "run-price"},
    ) == {
        "collect_business_rule_evidence": "run-business",
        "collect_price_evidence": "run-price",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_original_mode_merges_parallel_research_without_viral_reference():
    bundle = freeze_evidence_bundle(task_id="task-1", version=1, items=[]).model_dump(mode="json")
    empty = {"evidence_items": [], "citations": [], "unresolved_questions": []}
    result = await V3DeterministicNodeHandler._merge_research_evidence(
        db=None,
        node_run_id="node-run-1",
        state={
            "rule_version_id": "rules-v3",
            "runtime_config_snapshot": {
                "creation_mode": "original",
                "industry_pack_version_id": "industry-pack",
                "channel_profile_version_id": "channel-profile",
                "rule_version_id": "rules-v3",
            },
            "match_decision_snapshot": {"selected_group_id": "group-1"},
            "formula_selection_snapshot": {
                "selected_title_formula_code": "T03",
                "selected_body_formula_code": "C03",
            },
            "strategy_snapshot": {"title_formula": {"code": "T03"}, "body_formula": {"code": "C03"}},
            "evidence_bundle": bundle,
            "business_rule_evidence_collection": empty,
            "price_evidence_collection": empty,
            "compliance_evidence_collection": empty,
            "viral_candidate_collection": empty,
            "viral_reference_selection": {
                "selected_candidate_id": None,
                "selection_reason": "原创模式跳过爆款参考选择",
                "selection_basis": {},
                "reference_blueprint": None,
                "unresolved_questions": [],
            },
        },
    )

    assert result["evidence_collection"] == empty
