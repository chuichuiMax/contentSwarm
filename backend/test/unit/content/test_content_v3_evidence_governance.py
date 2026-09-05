from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import yuxi.content.control.evidence.service as evidence_service_module
from yuxi.content.control.evidence import EvidenceApplicationService
from yuxi.content.control.errors import ContentApplicationError
from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
from yuxi.content.model.evidence import (
    EvidenceBundleFrozenError,
    EvidenceGovernanceError,
    EvidenceItemV1,
    freeze_evidence_bundle,
    next_evidence_bundle_version,
    validate_evidence_references,
)
from yuxi.content.validators import validate_content
from yuxi.services.agent_delegation_service import AgentDelegationService
from yuxi.storage.postgres.models_content import (
    ContentEvidenceBundleVersion,
    ContentEvidenceItem,
    ContentTask,
    MediaEvidenceItem,
)


def _item(
    item_id: str,
    *,
    source_type: str = "manual_input",
    status: str = "user_confirmed",
    usage: tuple[str, ...] = ("title", "body"),
    risk: str = "normal",
    value="fact",
):
    return EvidenceItemV1(
        id=item_id,
        variable_codes=("price",),
        value=value,
        source_type=source_type,
        source_id=f"source-{item_id}",
        source_version="v1",
        verified_status=status,
        allowed_usage=usage,
        risk_level=risk,
        metadata={"field_path": "business.price"},
        source_hash=f"hash-{item_id}-1234",
    )


def test_freeze_bundle_is_hash_stable_and_keeps_source_counts_and_citations():
    items = [_item("ev-manual"), _item("ev-kb", source_type="knowledge_base", status="retrieved")]
    first = freeze_evidence_bundle(
        task_id="task-1",
        version=1,
        items=items,
        citations=[{"evidence_id": "ev-kb", "source_id": "doc-1"}],
    )
    second = freeze_evidence_bundle(
        task_id="task-1",
        version=1,
        items=list(reversed(items)),
        citations=[{"evidence_id": "ev-kb", "source_id": "doc-1"}],
        frozen_at=first.frozen_at,
    )

    assert first.bundle_hash == second.bundle_hash
    assert first.source_counts == {"manual_input": 1, "knowledge_base": 1}
    assert first.citations[0]["source_id"] == "doc-1"


def test_frozen_bundle_is_immutable_and_new_evidence_creates_new_version():
    current = freeze_evidence_bundle(task_id="task-1", version=1, items=[_item("ev-1")])
    with pytest.raises(EvidenceBundleFrozenError):
        current.add_item(_item("ev-2"))

    next_version = next_evidence_bundle_version(current, additions=[_item("ev-2")])

    assert next_version.version == 2
    assert next_version.supersedes_id == current.id
    assert [item.id for item in current.items] == ["ev-1"]
    assert [item.id for item in next_version.items] == ["ev-1", "ev-2"]


@pytest.mark.asyncio
async def test_freeze_node_treats_identical_existing_evidence_as_idempotent():
    current = freeze_evidence_bundle(task_id="task-1", version=1, items=[_item("ev-1")])
    echoed = current.items[0].model_dump(mode="json", exclude={"created_at", "metadata"})

    result = await V3DeterministicNodeHandler()._freeze_evidence_bundle(
        db=SimpleNamespace(),
        state={
            "task_id": "task-1",
            "run_id": "run-1",
            "evidence_bundle": current.model_dump(mode="json"),
            "evidence_collection": {"evidence_items": [echoed], "citations": []},
        },
        node_run_id="node-1",
    )

    assert result["evidence_bundle"]["version"] == 1
    assert result["evidence_bundle"]["bundle_hash"] == current.bundle_hash


@pytest.mark.asyncio
async def test_freeze_node_rejects_existing_evidence_id_with_changed_content():
    current = freeze_evidence_bundle(task_id="task-1", version=1, items=[_item("ev-1")])
    conflicting = current.items[0].model_dump(mode="json", exclude={"created_at", "metadata"})
    conflicting["value"] = "changed fact"

    with pytest.raises(EvidenceGovernanceError, match="内容不一致"):
        await V3DeterministicNodeHandler()._freeze_evidence_bundle(
            db=SimpleNamespace(),
            state={
                "task_id": "task-1",
                "run_id": "run-1",
                "evidence_bundle": current.model_dump(mode="json"),
                "evidence_collection": {"evidence_items": [conflicting], "citations": []},
            },
            node_run_id="node-1",
        )


def test_rejected_evidence_is_removed_from_next_bundle_and_cannot_be_referenced():
    current = freeze_evidence_bundle(task_id="task-1", version=1, items=[_item("ev-1"), _item("ev-2")])
    next_version = next_evidence_bundle_version(current, rejected_ids=frozenset({"ev-1"}))

    assert [item.id for item in next_version.items] == ["ev-2"]
    with pytest.raises(EvidenceGovernanceError) as exc_info:
        validate_evidence_references(next_version, ["ev-1"], usage="body")
    assert exc_info.value.code == "evidence_reference_forbidden"


def test_high_risk_evidence_requires_human_confirmation():
    with pytest.raises(EvidenceGovernanceError) as exc_info:
        freeze_evidence_bundle(
            task_id="task-1",
            version=1,
            items=[_item("ev-risk", status="confirmed", risk="high_risk")],
        )
    assert exc_info.value.code == "high_risk_evidence_unconfirmed"

    bundle = freeze_evidence_bundle(
        task_id="task-1",
        version=1,
        items=[_item("ev-risk", status="user_confirmed", risk="high_risk")],
    )
    assert bundle.items[0].verified_status == "user_confirmed"


def test_lexicon_value_cannot_become_evidence_or_pass_evidence_reference_validation():
    with pytest.raises(ValidationError, match="词库值"):
        _item("lex_sales_phrase")
    bundle = freeze_evidence_bundle(task_id="task-1", version=1, items=[_item("ev-1")])
    with pytest.raises(EvidenceGovernanceError):
        validate_evidence_references(bundle, ["lex_sales_phrase"], usage="body")


def test_unsupported_numbers_results_and_promises_are_blocked():
    evidence = freeze_evidence_bundle(
        task_id="task-1",
        version=1,
        items=[_item("ev-number", value="面积 88㎡")],
    )
    evidence_json = evidence.model_dump(mode="json")
    report = validate_content(
        title="保证效果",
        body="面积 99㎡，一定有效",
        topics=[],
        brief={"forbidden_terms": []},
        evidence_bundle=evidence_json,
        strategy={"methods": ["M1"], "title_formula_code": "T1", "body_formula_code": "B1"},
    )
    codes = {item["code"] for item in report["checks"]}
    assert "FACT_NUMBER_WITHOUT_SOURCE" in codes
    assert "CONTENT_HIGH_RISK_CLAIM" in codes
    assert report["status"] == "blocked"


def test_agent_knowledge_scope_is_preserved_and_only_research_nodes_can_retrieve(monkeypatch):
    context = SimpleNamespace(knowledges=["kb-a", "kb-b"], max_execution_steps=30)
    request = SimpleNamespace(
        knowledge_policy="agent_scope",
        node_run=SimpleNamespace(node_id="collect_missing_evidence"),
        max_execution_steps=12,
    )
    AgentDelegationService._apply_node_constraints(context, request)
    assert context.knowledges == ["kb-a", "kb-b"]

    request.node_run.node_id = "generate_body"
    with pytest.raises(ContentApplicationError) as exc_info:
        AgentDelegationService._apply_node_constraints(context, request)
    assert exc_info.value.code == "knowledge_node_forbidden"

    monkeypatch.setenv("LITE_MODE", "true")
    request.node_run.node_id = "semantic_review"
    with pytest.raises(ContentApplicationError) as exc_info:
        AgentDelegationService._apply_node_constraints(context, request)
    assert exc_info.value.code == "knowledge_capability_unavailable"


def test_no_selected_knowledge_base_removes_all_retrieval_tools():
    context = SimpleNamespace(
        knowledges=[],
        _required_skill_tools=[
            "get_business_facts",
            "query_kb",
            "open_kb_document",
            "find_kb_document",
        ],
    )

    AgentDelegationService._apply_knowledge_tool_scope(context)

    assert context._required_skill_tools == ["get_business_facts"]


@pytest.mark.asyncio
async def test_evidence_application_service_emits_added_rejected_and_frozen_events(monkeypatch):
    bundle = freeze_evidence_bundle(task_id="task-1", version=2, items=[_item("ev-2")])
    calls = []

    class FakeRepository:
        async def save_frozen_bundle(self, value):
            calls.append(("saved", value.id))

    class FakeDB:
        async def commit(self):
            calls.append(("committed", None))

    async def fake_append(run_id, event_type, payload, *, thread_id=None):
        calls.append((event_type, payload, thread_id, run_id))

    monkeypatch.setattr(evidence_service_module, "append_run_stream_event", fake_append)
    service = EvidenceApplicationService.__new__(EvidenceApplicationService)
    service.db = FakeDB()
    service.repository = FakeRepository()

    await service.persist_frozen_bundle(
        bundle,
        run_id="run-1",
        thread_id="task-1",
        added_evidence_ids=("ev-2",),
        rejected_evidence_ids=("ev-1",),
    )

    event_names = [item[0] for item in calls if item[0].startswith("content.")]
    assert event_names == ["content.evidence.added", "content.evidence.rejected", "content.evidence.frozen"]


def test_evidence_persistence_schema_separates_items_bundles_and_lexicon():
    assert ContentEvidenceItem.__tablename__ == "content_evidence_items"
    assert ContentEvidenceBundleVersion.__tablename__ == "content_evidence_bundle_versions"
    assert ContentTask.__table__.c.active_evidence_bundle_id.index is True
    assert MediaEvidenceItem.__table__.c.parser_version.default.arg == "unknown"
    source_constraint = next(
        constraint
        for constraint in ContentEvidenceItem.__table__.constraints
        if getattr(constraint, "name", "") == "ck_content_evidence_source_type"
    )
    assert "lexicon" not in str(source_constraint.sqltext)
