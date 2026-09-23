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


@pytest.fixture
def standardized_repair():
    from yuxi.content.control.workflow.revision import build_generation_repair_constraints

    result = {
        "title": {"text": "北京装修报价", "formula_code": "T1", "evidence_ids": []},
        "outline": {"body_formula_code": "B1", "sections": [{"section_id": "s1", "goal": "报价", "evidence_ids": []}]},
        "draft": {
            "body": (
                "装修先看计价单位。\n\n我是北京工长，做事有耐心。👷这份报价仅供参考。💰"
                "\n\n逐项核对施工范围。📋\n\n先核对再做报价对比。"
            ),
            "body_formula_code": "B1",
            "topics": ["装修"],
            "paragraph_evidence": [],
        },
    }

    def prepare(codes):
        constraints = build_generation_repair_constraints(
            {
                "production_pack": {"schema_version": 1},
                "validation_report": {"status": "passed"},
                "review_report": {
                    "status": "blocked",
                    "checks": [{"code": code, "status": "blocked"} for code in codes],
                },
                "selected_title": result["title"],
                "content_outline": result["outline"],
                "content_draft": result["draft"],
            }
        )
        return ContractDomainContext(
            generation_repair_constraints=constraints,
            locked_title_formula_code="T1",
            locked_body_formula_code="B1",
        )

    return result, prepare


@pytest.mark.parametrize(
    ("codes", "numbers"),
    [
        (["PERSONA_OPENING"], [1, 2]),
        (["PERSONA_CLOSING"], [4]),
        (["PERSONA_OPENING", "PERSONA_CLOSING"], [1, 2, 4]),
        (["EMOJI_COVERAGE"], []),
    ],
)
def test_standardized_repair_has_explicit_minimal_scope(standardized_repair, codes, numbers):
    _, prepare = standardized_repair
    context = prepare(codes)
    assert context.generation_repair_constraints["editable_paragraph_numbers"] == numbers
    restored = ContractDomainContext.from_governance(
        match_decision_snapshot={},
        formula_selection_snapshot={},
        evidence_bundle={},
        locked_versions={
            "industry_pack_version_id": "industry",
            "channel_profile_version_id": "channel",
            "persona_profile_version_id": None,
            "rule_version_id": "rules",
            "title_formula_code": "T1",
            "body_formula_code": "B1",
            "artifact_version_id": None,
        },
        locked_values={
            "generation_repair_constraints": context.generation_repair_constraints,
        },
    )
    assert restored.generation_repair_constraints == context.generation_repair_constraints


def test_opening_repair_moves_identity_and_emoji_without_duplicate(standardized_repair):
    result, prepare = standardized_repair
    context = prepare(["PERSONA_OPENING"])
    result["draft"]["body"] = (
        "我是北京工长，做事有耐心。👷装修先看计价单位。\n\n这份报价仅供参考。💰"
        "\n\n逐项核对施工范围。📋\n\n先核对再做报价对比。"
    )
    assert (
        validate_content_node_result("GeneratedContentResultV1", result, context).draft.body.count("我是北京工长") == 1
    )


@pytest.mark.parametrize("changed", ["middle", "closing", "title", "outline", "topics", "merge", "amount", "unit"])
def test_opening_repair_rejects_changes_outside_scope(standardized_repair, changed):
    result, prepare = standardized_repair
    if changed in {"amount", "unit"}:
        result["draft"]["body"] = result["draft"]["body"].replace("逐项核对施工范围。", "施工按100元/㎡计费。")
    context = prepare(["PERSONA_OPENING"])
    if changed == "middle":
        result["draft"]["body"] = result["draft"]["body"].replace("逐项核对施工范围。", "免施工费。")
    elif changed == "closing":
        result["draft"]["body"] = result["draft"]["body"].replace("先核对再做报价对比。", "结尾已改变。")
    elif changed == "merge":
        result["draft"]["body"] = result["draft"]["body"].replace("\n\n", "", 1)
    elif changed == "amount":
        result["draft"]["body"] = result["draft"]["body"].replace("100元", "101元")
    elif changed == "unit":
        result["draft"]["body"] = result["draft"]["body"].replace("元/㎡", "元/米")
    elif changed == "title":
        result["title"]["text"] = "新标题"
    elif changed == "outline":
        result["outline"]["sections"][0]["goal"] = "新大纲"
    else:
        result["draft"]["topics"] = ["新话题"]
    with pytest.raises(ContractDomainValidationError, match="仅可编辑|保留冻结"):
        validate_content_node_result("GeneratedContentResultV1", result, context)


def test_closing_repair_does_not_unlock_opening(standardized_repair):
    result, prepare = standardized_repair
    context = prepare(["PERSONA_CLOSING"])
    result["draft"]["body"] = result["draft"]["body"].replace("装修先看计价单位。", "我是工长。")
    with pytest.raises(ContractDomainValidationError, match="仅可编辑"):
        validate_content_node_result("GeneratedContentResultV1", result, context)


def test_emoji_repair_preserves_all_text(standardized_repair):
    result, prepare = standardized_repair
    context = prepare(["EMOJI_COVERAGE"])
    result["draft"]["body"] = result["draft"]["body"].replace("👷", "").replace("我是北京工长", "👷我是北京工长")
    validate_content_node_result("GeneratedContentResultV1", result, context)
    result["draft"]["body"] = result["draft"]["body"].replace("计价单位", "成交总价")
    with pytest.raises(ContractDomainValidationError, match="仅表情回修不得改动"):
        validate_content_node_result("GeneratedContentResultV1", result, context)


def test_opening_repair_still_rejects_unknown_evidence(standardized_repair):
    result, prepare = standardized_repair
    context = prepare(["PERSONA_OPENING"])
    result["draft"]["paragraph_evidence"] = [{"paragraph_id": "p1", "evidence_ids": ["invented-evidence"]}]
    with pytest.raises(ContractDomainValidationError, match="Evidence|证据"):
        validate_content_node_result("GeneratedContentResultV1", result, context)


@pytest.mark.asyncio
async def test_limit_message_exposes_actual_emoji_failure_and_shared_counter():
    from yuxi.agents.buildin.content_workflow.graph import ContentWorkflowAgent
    from yuxi.content.control.errors import ContentApplicationError

    state = {
        "current_node": "semantic_review",
        "retry_counts": {"generate_content": 2},
        "validation_report": {"status": "passed", "checks": []},
        "review_report": {
            "status": "blocked",
            "checks": [
                {
                    "code": "EMOJI_COVERAGE",
                    "status": "blocked",
                    "message": "身份 Emoji 未紧邻身份句",
                    "location": "第三段",
                    "suggestion": "把工长 Emoji 移到首段身份句旁",
                },
                {"code": "EMOJI_APPROPRIATENESS", "status": "blocked", "message": "报价旁的工长符号错配"},
            ],
        },
    }
    with pytest.raises(ContentApplicationError) as exc:
        await ContentWorkflowAgent._execute_node(
            object(),
            {"id": "revise_if_needed", "type": "revision_router"},
            state,
            WORKFLOW_PRICE_RECOVERY,
        )
    assert exc.value.code == "content_revision_limit_reached"
    assert "身份 Emoji 未紧邻身份句" in str(exc.value)
    assert "报价旁的工长符号错配" in str(exc.value)
    assert "第三段" in str(exc.value) and "移到首段" in str(exc.value) and "2/2" in str(exc.value)
    assert state["retry_counts"] == {"generate_content": 2}
