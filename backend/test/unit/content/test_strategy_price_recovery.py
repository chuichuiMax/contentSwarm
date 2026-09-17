from copy import deepcopy

import pytest

from test.unit.content.test_joint_strategy import joint_example
from yuxi.content.model.contracts.content_nodes import ContractDomainContext, validate_content_node_result
from yuxi.content.model.workflows.definition import WorkflowDefinitionPolicy
from yuxi.content.v3.joint_workflow import WORKFLOW_BLUEPRINT_FIRST


def test_price_recovery_is_bounded_and_preserves_historical_workflow():
    from yuxi.content.v3.joint_workflow import WORKFLOW_PRICE_RECOVERY

    WorkflowDefinitionPolicy.validate(WORKFLOW_PRICE_RECOVERY)
    assert len(WORKFLOW_BLUEPRINT_FIRST["nodes"]) == 25
    edges = WORKFLOW_PRICE_RECOVERY["edges"]
    chain = [
        "select_creation_strategy",
        "research_strategy_prices",
        "confirm_strategy_prices",
        "merge_strategy_prices",
        "reselect_creation_strategy",
        "lock_creation_strategy",
    ]
    assert all([a, b] in edges for a, b in zip(chain, chain[1:]))
    invalid = deepcopy(WORKFLOW_PRICE_RECOVERY)
    invalid["edges"].append(["select_creation_strategy", "lock_creation_strategy"])
    with pytest.raises(ValueError):
        WorkflowDefinitionPolicy.validate(invalid)


def test_price_gap_is_structured_without_changing_v1_snapshots():
    from yuxi.content.model.contracts.joint_strategy import JointStrategyDecisionV1, JointStrategyDecisionV2

    _, decision = joint_example()
    old = JointStrategyDecisionV1.model_validate(decision).model_dump()
    assert "price_research_questions" not in old
    decision["reference"] = {"status": "needs_input", "reason": "缺报价", "unresolved_questions": ["缺报价"]}
    decision["price_research_questions"] = ["杭州设计、水电标准单价及包含范围"]
    assert JointStrategyDecisionV2.model_validate(decision).price_research_questions


def price_result():
    return {
        "evidence_items": [
            {
                "id": "price_design",
                "variable_codes": [],
                "value": "杭州基础设计标准单价 42.8 元/㎡",
                "source_type": "knowledge_base",
                "source_id": "chunk-design",
                "source_version": "v1",
                "source_hash": "a" * 64,
                "verified_status": "retrieved",
                "allowed_usage": ["body"],
                "risk_level": "high_risk",
                "metadata": {
                    "material_type": "price",
                    "price_basis": "standard_unit_price",
                    "scope": "杭州基础设计",
                    "unit": "元/㎡",
                    "integration_instruction": "仅作标准单价参考，不是本项目成交价",
                },
            }
        ],
        "citations": ["chunk-design"],
        "unresolved_questions": ["尚无各分项工程量及实际成交金额"],
    }


@pytest.mark.parametrize("mutation", ["risk", "confirmation", "basis", "scope", "source"])
def test_prelock_price_rejects_ungoverned_evidence(mutation):
    result = price_result()
    item = result["evidence_items"][0]
    if mutation == "risk":
        item["risk_level"] = "normal"
    elif mutation == "confirmation":
        item["verified_status"] = "user_confirmed"
    elif mutation == "basis":
        item["metadata"].pop("price_basis")
    elif mutation == "scope":
        item["metadata"].pop("scope")
    else:
        item["source_type"] = "manual_input"
    with pytest.raises(ValueError):
        validate_content_node_result("StrategyPriceEvidenceResultV1", result, ContractDomainContext())


def test_prelock_price_keeps_standard_unit_price_and_source_without_locked_formula():
    result = validate_content_node_result("StrategyPriceEvidenceResultV1", price_result(), ContractDomainContext())
    assert result.evidence_items[0].value.endswith("42.8 元/㎡")
    assert result.evidence_items[0].verified_status == "retrieved"
    assert result.evidence_items[0].metadata["price_basis"] == "standard_unit_price"
    assert "body_formula_code" not in result.evidence_items[0].metadata


def test_standard_price_fills_quote_slot_without_quantity_or_budget_reconciliation():
    from yuxi.content.model.contracts.joint_strategy import validate_joint_strategy

    inputs, decision = joint_example()
    inputs["content_brief"]["form_values"]["budget"] = "18万元硬装总预算"
    item = price_result()["evidence_items"][0]
    item["verified_status"] = "user_confirmed"
    inputs["evidence_bundle"]["items"] = [item]
    for candidate in inputs["reference_candidates"]:
        candidate["reference_card"]["required_slots"].append({"name": "报价明细", "required": True})
    decision["reference"]["slot_mapping"]["报价明细"] = ["evidence_bundle.items.0.value"]
    decision["price_research_questions"] = []

    result = validate_joint_strategy(decision, inputs)

    assert result.reference.status == "selected"
    assert result.reference.slot_mapping["报价明细"] == ["evidence_bundle.items.0.value"]
    assert not result.price_research_questions
    assert inputs["evidence_bundle"]["items"][0] == item


@pytest.mark.asyncio
async def test_collector_computes_quote_hash_from_verified_retrieval():
    import hashlib
    from types import SimpleNamespace
    from yuxi.content.model.contracts.content_nodes import ContentNodeResultCollector, StrategyPriceEvidenceResultV1

    runtime = SimpleNamespace(_content_retrieved_knowledge_results={
        "chunk-design": [{"content": "杭州基础设计 42.8 元/㎡", "metadata": {"document_name": "杭州市-设计.xlsx"}}]
    })
    payload = price_result()
    payload["evidence_items"][0].pop("source_hash")
    payload["evidence_items"][0].pop("source_version")
    parsed = StrategyPriceEvidenceResultV1.model_validate(payload)
    collector = ContentNodeResultCollector("StrategyPriceEvidenceResultV1", ContractDomainContext(), runtime)
    await collector.submit(**parsed.model_dump())
    item = collector.finalize()["evidence_items"][0]
    assert item["source_hash"] == hashlib.sha256("杭州基础设计 42.8 元/㎡".encode()).hexdigest()
    assert item["source_version"] == item["source_hash"]
    assert item["metadata"]["document_name"] == "杭州市-设计.xlsx"


def test_reevaluation_sends_price_facts_once_and_preserves_research_questions():
    from yuxi.content.model.contracts.joint_strategy import ReevaluateJointStrategyInputV1

    inputs, _ = joint_example()
    inputs["strategy_price_evidence_collection"] = price_result()
    payload = ReevaluateJointStrategyInputV1.model_validate(inputs).model_dump()
    assert "evidence_items" not in payload["strategy_price_evidence_collection"]
    assert payload["strategy_price_evidence_collection"]["unresolved_questions"]
    assert inputs["strategy_price_evidence_collection"]["evidence_items"]


def test_missing_price_knowledge_is_configuration_error_not_missing_user_material():
    from types import SimpleNamespace
    from yuxi.content.control.errors import ContentApplicationError
    from yuxi.services.agent_delegation_service import AgentDelegationService

    with pytest.raises(ContentApplicationError, match="未配置可访问的价格库"):
        AgentDelegationService._restrict_research_knowledge_scope(
            SimpleNamespace(_visible_knowledge_bases=[]), "research_strategy_prices")


@pytest.mark.asyncio
async def test_prices_require_explicit_confirmation_before_freezing(monkeypatch):
    from yuxi.agents.buildin.content_workflow import graph
    from yuxi.content.model.evidence import EvidenceGovernanceError, EvidenceItemV1, freeze_evidence_bundle

    collection = price_result()
    with pytest.raises(EvidenceGovernanceError, match="人工确认"):
        freeze_evidence_bundle(
            task_id="task", version=1, items=[EvidenceItemV1.model_validate(collection["evidence_items"][0])]
        )

    def confirm(payload):
        assert payload["evidence_items"][0]["source_id"] == "chunk-design"
        return {**payload, "confirmed_evidence_ids": payload["evidence_ids"]}

    monkeypatch.setattr(graph, "interrupt", confirm)
    updated = await graph.ContentWorkflowAgent()._v3_human_review(
        {"id": "confirm_strategy_prices", "interrupt_type": "high_risk_facts"},
        {"task_id": "task", "run_id": "run", "strategy_price_evidence_collection": collection},
    )
    confirmed = updated["strategy_price_evidence_collection"]["evidence_items"][0]
    bundle = freeze_evidence_bundle(task_id="task", version=1, items=[EvidenceItemV1.model_validate(confirmed)])
    assert bundle.items[0].metadata["price_basis"] == "standard_unit_price"
    assert collection["evidence_items"][0]["verified_status"] == "retrieved"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node_id", ["research_strategy_prices", "reselect_creation_strategy", "collect_price_evidence"]
)
async def test_recovery_skips_unnecessary_delegations(node_id):
    from types import SimpleNamespace
    from yuxi.content.control.workflow.agent_node import AgentNodeHandler

    class DB:
        async def get(self, *_args):
            return SimpleNamespace(id="task")

        async def execute(self, *_args):
            return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(uid="user"))

    result = await AgentNodeHandler().execute(
        db=DB(),
        node={"id": node_id},
        node_run_id="node",
        state={
            "task_id": "task",
            "uid": "user",
            "joint_strategy_decision": {"price_research_questions": []},
            "strategy_selection": {},
            "strategy_price_evidence_collection": {"skipped": node_id != "collect_price_evidence"},
        },
    )
    assert result  # A delegation would require undeclared node fields and fail this test.


@pytest.mark.asyncio
async def test_empty_retrieval_still_runs_reassessment(monkeypatch):
    from types import SimpleNamespace
    from yuxi.content.control.workflow.agent_node import AgentNodeHandler
    from yuxi.content.control.workflow.content_node_input import ContentNodeInputAssembler

    class DB:
        async def get(self, *_args):
            return SimpleNamespace(
                id="task",
                industry_pack_version_id="pack",
                channel_profile_version_id="channel",
                persona_profile_version_id=None,
                rule_version_id="rule",
            )

        async def execute(self, *_args):
            return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(uid="user"))

    class ReassessmentReached(Exception):
        pass

    def build(**_kwargs):
        raise ReassessmentReached

    monkeypatch.setattr(ContentNodeInputAssembler, "build", build)
    with pytest.raises(ReassessmentReached):
        await AgentNodeHandler().execute(
            db=DB(),
            node={"id": "reselect_creation_strategy"},
            node_run_id="node",
            state={
                "task_id": "task",
                "uid": "user",
                "strategy_price_evidence_collection": {
                    "evidence_items": [],
                    "citations": [],
                    "unresolved_questions": ["已检索，没有杭州报价"],
                },
            },
        )
