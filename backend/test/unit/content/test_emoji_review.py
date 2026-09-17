import pytest
from types import SimpleNamespace
from pathlib import Path

from yuxi.content.control.workflow.revision import RevisionRouteController, resolve_revision_reason
from yuxi.content.model.contracts import ContractDomainContext, validate_content_node_result
from yuxi.content.model.contracts.content_nodes import ContractDomainValidationError
from yuxi.content.v3.joint_workflow import WORKFLOW_PRICE_RECOVERY


def review_payload():
    return {
        "status": "passed",
        "evidence_conflicts": [],
        "checks": [
            {
                "code": code,
                "status": "passed",
                "location": "body",
                "message": "已核对现有内容",
                "suggestion": "",
                "evidence_ids": [],
            }
            for code in ("EMOJI_COVERAGE", "EMOJI_APPROPRIATENESS", "EMOJI_RESTRICTIONS")
        ],
    }


@pytest.mark.parametrize("missing", ["EMOJI_COVERAGE", "EMOJI_APPROPRIATENESS", "EMOJI_RESTRICTIONS", "all"])
def test_emoji_review_cannot_pass_without_required_checks(missing):
    payload = review_payload()
    payload["checks"] = [item for item in payload["checks"] if item["code"] != missing and missing != "all"]
    with pytest.raises(ContractDomainValidationError, match="必须逐项审核表情"):
        validate_content_node_result("ContentReviewResultV1", payload, ContractDomainContext(require_emoji_review=True))


@pytest.mark.parametrize("index", [0, 1, 2])
def test_emoji_failure_routes_to_bounded_generation_repair(index):
    payload = review_payload()
    payload["status"] = "blocked"
    payload["checks"][index].update(status="blocked", location="一、拆除", suggestion="在拆除条目前加 🔨，保留原文")
    validate_content_node_result("ContentReviewResultV1", payload, ContractDomainContext(require_emoji_review=True))
    reason = resolve_revision_reason(title_validation_report=None, validation_report=None, review_report=payload)
    assert reason == "PERSONA_STYLE_FAILED"
    router = RevisionRouteController()
    first = router.decide(definition=WORKFLOW_PRICE_RECOVERY, reason_code=reason, retry_counts={})
    assert first.target_node_id == "generate_content"
    limited = router.decide(
        definition=WORKFLOW_PRICE_RECOVERY, reason_code=reason, retry_counts={"generate_content": 2}
    )
    assert limited.status == "limit_reached"


def test_emoji_block_requires_actionable_repair_suggestion():
    payload = review_payload()
    payload["status"] = payload["checks"][0]["status"] = "blocked"
    with pytest.raises(ContractDomainValidationError, match="定点修正建议"):
        validate_content_node_result("ContentReviewResultV1", payload, ContractDomainContext(require_emoji_review=True))


def test_emoji_warning_cannot_bypass_repair():
    payload = review_payload()
    payload["status"] = payload["checks"][0]["status"] = "warning"
    with pytest.raises(ContractDomainValidationError, match="不以 warning 放行"):
        validate_content_node_result("ContentReviewResultV1", payload, ContractDomainContext(require_emoji_review=True))


@pytest.mark.parametrize("scope", ["expression", "full"])
def test_review_injects_only_requested_scope_and_records_effective_instructions(scope):
    from yuxi.agents.middlewares.skills import SkillsMiddleware
    from yuxi.agents.skills.buildin import BUILTIN_SKILLS

    spec = next(item for item in BUILTIN_SKILLS if item.slug == "content-reviewer")
    context = SimpleNamespace(
        _content_max_model_calls=2,
        _content_node_input=SimpleNamespace(payload={"review_scope": scope}),
        _runtime_skill_metadata={
            spec.slug: {
                "instructions": (Path(spec.source_dir) / "SKILL.md").read_text(),
                "version": spec.version,
                "content_hash": "test",
            }
        },
    )
    instructions = SkillsMiddleware()._build_required_skills_section([spec.slug], context)
    assert "EMOJI_COVERAGE" in instructions
    assert "PERSONA_OPENING" in instructions
    assert "PERSONA_CLOSING" in instructions
    assert "PERSONA_GROUNDING" in instructions
    assert "submit_content_node_result" in instructions
    assert ("## 完整审核" in instructions) == (scope == "full")
    assert context._content_applied_skill_instructions[spec.slug]["mode"] == scope


def test_review_uses_bounded_streaming_calls_and_preserves_model_reasoning_override():
    from yuxi.services.agent_delegation_service import AgentDelegationService

    request = SimpleNamespace(
        node_run=SimpleNamespace(node_id="semantic_review"), knowledge_policy="frozen_evidence_only"
    )
    context = SimpleNamespace(reasoning_effort=None)
    AgentDelegationService._apply_node_constraints(context, request)
    assert context._content_max_model_calls == 2
    assert context.model_call_timeout_seconds == 120
    assert context.reasoning_effort == "medium"
    context.reasoning_effort = "low"
    AgentDelegationService._apply_node_constraints(context, request)
    assert context.reasoning_effort == "low"


@pytest.mark.parametrize("body", ["🔨长沙115㎡拆除1954元。", "长沙115㎡拆除💰1954元。"])
def test_emoji_only_repair_preserves_original_text(body):
    payload = {
        "title": {"text": "长沙拆除明细", "formula_code": "T1", "evidence_ids": []},
        "outline": {"body_formula_code": "B1", "sections": [{"section_id": "s1", "goal": "报价", "evidence_ids": []}]},
        "draft": {"body": body, "topics": [], "paragraph_evidence": [], "body_formula_code": "B1"},
    }
    context = ContractDomainContext(
        emoji_repair_body="长沙115㎡拆除1954元。",
        locked_title="长沙拆除明细",
        locked_title_formula_code="T1",
        locked_body_formula_code="B1",
        allowed_numbers=frozenset({"115", "1954"}),
    )
    validate_content_node_result("GeneratedContentResultV1", payload, context)
    for changed in ("长沙115㎡拆除1955元。", "长沙115元拆除1954元。", "长沙115㎡拆除1954元！", "长沙拆除115㎡1954元。"):
        payload["draft"]["body"] = changed
        with pytest.raises(ContractDomainValidationError, match="不得改动原文字"):
            validate_content_node_result("GeneratedContentResultV1", payload, context)
