"""普通模式必须实际审核首尾人设，并通过既有有限回修处理缺失。"""

import pytest

from yuxi.content.control.workflow.revision import RevisionRouteController, resolve_revision_reason
from yuxi.content.model.contracts import ContractDomainContext, validate_content_node_result
from yuxi.content.model.contracts.content_nodes import ContractDomainValidationError
from yuxi.content.v3.joint_workflow import WORKFLOW_PRICE_RECOVERY

CODES = ("PERSONA_OPENING", "PERSONA_CLOSING", "PERSONA_GROUNDING")


def report():
    return {
        "status": "passed",
        "checks": [
            {
                "code": code,
                "status": "passed",
                "location": "body",
                "message": "无人物资料，保持中性表达",
                "suggestion": "",
                "evidence_ids": [],
            }
            for code in CODES
        ],
        "evidence_conflicts": [],
    }


@pytest.mark.parametrize("missing", CODES)
def test_missing_persona_check_cannot_pass(missing):
    payload = report()
    payload["checks"] = [item for item in payload["checks"] if item["code"] != missing]
    with pytest.raises(ContractDomainValidationError, match="必须逐项审核"):
        validate_content_node_result(
            "ContentReviewResultV1", payload, ContractDomainContext(require_persona_review=True)
        )


@pytest.mark.parametrize("index", range(3))
def test_persona_block_routes_to_existing_bounded_generation(index):
    payload = report()
    payload["status"] = "blocked"
    payload["checks"][index].update(status="blocked", location="首段", suggestion="用已提供的工长身份关联报价问题")
    validate_content_node_result("ContentReviewResultV1", payload, ContractDomainContext(require_persona_review=True))
    reason = resolve_revision_reason(title_validation_report=None, validation_report=None, review_report=payload)
    assert reason == "PERSONA_STYLE_FAILED"
    router = RevisionRouteController()
    assert (
        router.decide(definition=WORKFLOW_PRICE_RECOVERY, reason_code=reason, retry_counts={}).target_node_id
        == "generate_content"
    )
    assert (
        router.decide(
            definition=WORKFLOW_PRICE_RECOVERY, reason_code=reason, retry_counts={"generate_content": 2}
        ).status
        == "limit_reached"
    )


@pytest.mark.parametrize("index", range(3))
def test_persona_warning_and_non_actionable_block_are_rejected(index):
    payload = report()
    context = ContractDomainContext(require_persona_review=True)
    payload["status"] = payload["checks"][index]["status"] = "warning"
    with pytest.raises(ContractDomainValidationError, match="不以 warning 放行"):
        validate_content_node_result("ContentReviewResultV1", payload, context)
    payload["status"] = payload["checks"][index]["status"] = "blocked"
    with pytest.raises(ContractDomainValidationError, match="定点修正建议"):
        validate_content_node_result("ContentReviewResultV1", payload, context)


def test_explicit_inapplicability_may_pass_but_does_not_remove_emoji_checks():
    payload = report()
    validate_content_node_result("ContentReviewResultV1", payload, ContractDomainContext(require_persona_review=True))
    with pytest.raises(ContractDomainValidationError, match="EMOJI_COVERAGE"):
        validate_content_node_result(
            "ContentReviewResultV1",
            payload,
            ContractDomainContext(require_persona_review=True, require_emoji_review=True),
        )


@pytest.mark.parametrize(
    "middle",
    [
        "🔨 拆除1954元。\n\n💡水电7886元。",
        "拆除1955元。\n\n水电7886元。",
        "水电7886元。\n\n拆除1954元。",
        "拆除1954元。水电7886元。",
    ],
)
def test_persona_repair_preserves_middle_text_order_and_paragraphs(middle):
    payload = {
        "title": {"text": "报价", "formula_code": "T1", "evidence_ids": []},
        "outline": {"body_formula_code": "B1", "sections": [{"section_id": "s1", "goal": "报价", "evidence_ids": []}]},
        "draft": {
            "body": f"我是工长。\n\n{middle}\n\n可以沟通改造需求。",
            "topics": [],
            "paragraph_evidence": [],
            "body_formula_code": "B1",
        },
    }
    context = ContractDomainContext(
        persona_repair_middle=("拆除1954元。", "水电7886元。"),
        locked_title="报价",
        locked_title_formula_code="T1",
        locked_body_formula_code="B1",
        allowed_numbers=frozenset({"1954", "7886"}),
    )
    if middle.startswith("🔨"):
        validate_content_node_result("GeneratedContentResultV1", payload, context)
    else:
        with pytest.raises(ContractDomainValidationError, match="中间各段必须逐字保留"):
            validate_content_node_result("GeneratedContentResultV1", payload, context)


def test_composition_audit_is_required_and_uses_existing_body_repair():
    payload = report()
    context = ContractDomainContext(require_composition_review=True)
    with pytest.raises(ContractDomainValidationError, match="COMPOSITION_ALIGNMENT"):
        validate_content_node_result("ContentReviewResultV1", payload, context)
    for code in ("CREATION_TYPE_ALIGNMENT", "COMPOSITION_ALIGNMENT"):
        payload["checks"].append(
            {
                "code": code,
                "status": "passed",
                "location": "正文",
                "message": "逐项核对本行组合",
                "suggestion": "",
                "evidence_ids": [],
            }
        )
    validate_content_node_result("ContentReviewResultV1", payload, context)
    payload["checks"][-1].update(status="blocked", suggestion="按工种总价补齐真实报价证据层")
    payload["status"] = "blocked"
    reason = resolve_revision_reason(title_validation_report=None, validation_report=None, review_report=payload)
    assert reason == "BODY_STRUCTURE_FAILED"
    assert (
        RevisionRouteController()
        .decide(definition=WORKFLOW_PRICE_RECOVERY, reason_code=reason, retry_counts={})
        .target_node_id
        == "generate_content"
    )
