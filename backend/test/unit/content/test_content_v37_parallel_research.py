from __future__ import annotations

import pytest

from yuxi.agents.buildin.content_workflow.state import merge_delegated_agent_runs
from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler


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
async def test_original_mode_cannot_merge_research_for_generation():
    with pytest.raises(ValueError, match="只支持爆款仿写"):
        await V3DeterministicNodeHandler._merge_research_evidence(
            db=None,
            node_run_id="node-run-1",
            state={"runtime_config_snapshot": {"creation_mode": "original"}},
        )
