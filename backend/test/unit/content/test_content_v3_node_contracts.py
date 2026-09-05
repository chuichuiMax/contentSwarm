from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest
from pydantic import ValidationError

from yuxi.content.model.contracts import (
    CONTRACT_REGISTRY,
    ContentAgentNodeInputV1,
    ContentNodeResultCollector,
    ContractDomainContext,
    ContractDomainValidationError,
    build_content_result_tool,
    get_contract_model,
    validate_content_node_result,
)


DOMAIN_CONTEXT = ContractDomainContext(
    locked_group_id="group-1",
    title_formula_pool=frozenset({"T1", "T2"}),
    body_formula_pool=frozenset({"B1", "B2"}),
    locked_title_formula_code="T1",
    locked_body_formula_code="B1",
    locked_body_formula_sections=frozenset({"事实与数据", "落地结果"}),
    allowed_evidence_by_usage={
        "any": frozenset({"e-any", "e-title", "e-body", "e-visual"}),
        "title": frozenset({"e-title"}),
        "body": frozenset({"e-body"}),
        "visual": frozenset({"e-visual"}),
    },
    allowed_asset_ids=frozenset({"asset-1", "asset-2"}),
    locked_title="locked title",
    artifact_version_id="artifact-v1",
    visual_plan_hash="plan-hash",
    allowed_numbers=frozenset({"88"}),
)


VALID_PAYLOADS = {
    "ContentDirectionDecisionResultV1": {
        "value_points": ["value"],
        "direction_candidates": [{"direction_code": "CT01", "reason": "reason", "evidence_ids": ["e-any"]}],
        "reasoning": "reasoning",
        "evidence_ids": ["e-any"],
        "selected_direction_code": "CT01",
        "selection_reason": "当前证据最充分",
        "selection_evidence_ids": ["e-any"],
    },
    "ContentValueResultV1": {
        "value_points": ["value"],
        "direction_candidates": [{"direction_code": "CT01", "reason": "reason", "evidence_ids": ["e-any"]}],
        "reasoning": "reasoning",
        "evidence_ids": ["e-any"],
    },
    "DirectionSelectionResultV1": {
        "direction_code": "CT01",
        "reason": "当前证据最充分",
        "evidence_ids": ["e-any"],
    },
    "StrategyExplanationResultV1": {
        "locked_group_id": "group-1",
        "explanation": "explanation",
        "risks": [],
        "evidence_ids": ["e-any"],
    },
    "EvidenceCollectionResultV1": {
        "evidence_items": [
            {
                "id": "new-evidence",
                "variable_codes": ["price"],
                "value": "value",
                "source_type": "knowledge_base",
                "source_id": "source-1",
                "source_version": "1",
                "verified_status": "retrieved",
                "allowed_usage": ["body"],
                "risk_level": "normal",
                "source_hash": "hash",
                "metadata": {
                    "writing_ready": True,
                    "body_formula_code": "B1",
                    "formula_section": "事实与数据",
                    "integration_instruction": "在事实段说明该项业务资料",
                    "relevance_reason": "直接补充当前主题所需事实",
                },
            }
        ],
        "citations": ["source-1"],
        "unresolved_questions": [],
    },
    "FormulaRankingResultV1": {
        "title_rankings": [{"formula_code": "T1", "reason": "best"}],
        "body_rankings": [{"formula_code": "B1", "reason": "best"}],
    },
    "TitleCandidatesResultV1": {
        "candidates": [
            {"id": "title-1", "text": "first title", "formula_code": "T1", "evidence_ids": ["e-title"], "reason": "a"},
            {"id": "title-2", "text": "second title", "formula_code": "T1", "evidence_ids": ["e-title"], "reason": "b"},
        ],
        "selected_title_formula_code": "T1",
        "evidence_ids": ["e-title"],
    },
    "TitleSelectionResultV1": {
        "selected_title_id": "title-1",
        "reason": "公式和证据最完整",
    },
    "OutlineResultV1": {
        "body_formula_code": "B1",
        "sections": [{"section_id": "s1", "goal": "goal", "evidence_ids": ["e-body"]}],
    },
    "ContentDraftResultV1": {
        "body": "body text",
        "topics": ["topic"],
        "paragraph_evidence": [{"paragraph_id": "p1", "evidence_ids": ["e-body"]}],
        "body_formula_code": "B1",
    },
    "PersonaPolishResultV1": {
        "polished_body": "polished body",
        "change_summary": ["tone"],
        "preserved_fact_checks": [{"evidence_id": "e-body", "preserved": True}],
    },
    "ContentReviewResultV1": {
        "status": "passed",
        "checks": [{"code": "facts", "status": "passed", "message": "ok", "evidence_ids": ["e-body"]}],
        "evidence_conflicts": [],
    },
    "VisualPlanResultV1": {
        "size": {"width": 1080, "height": 1440},
        "safe_area": {"top": 20, "right": 20, "bottom": 20, "left": 20},
        "text": ["cover text"],
        "source_asset_ids": ["asset-1"],
        "mode": "template",
        "risks": [],
        "artifact_version_id": "artifact-v1",
        "evidence_ids": ["e-visual"],
    },
    "CoverJobSubmissionResultV1": {
        "cover_job_id": "job-1",
        "plan_hash": "plan-hash",
        "source_asset_ids": ["asset-1"],
    },
    "VisualReviewResultV1": {
        "assets": [{"asset_id": "asset-1", "status": "passed", "issues": []}],
        "status": "passed",
        "recommended_asset_id": "asset-1",
    },
}


def _make_unknown(contract_name: str, payload: dict) -> dict:
    value = deepcopy(payload)
    if contract_name == "ContentDirectionDecisionResultV1":
        value["selection_evidence_ids"] = ["unknown"]
    elif contract_name == "ContentValueResultV1":
        value["evidence_ids"] = ["unknown"]
    elif contract_name == "DirectionSelectionResultV1":
        value["evidence_ids"] = ["unknown"]
    elif contract_name == "StrategyExplanationResultV1":
        value["locked_group_id"] = "unknown"
    elif contract_name == "EvidenceCollectionResultV1":
        value["evidence_items"].append(deepcopy(value["evidence_items"][0]))
    elif contract_name == "FormulaRankingResultV1":
        value["title_rankings"][0]["formula_code"] = "unknown"
    elif contract_name == "TitleCandidatesResultV1":
        value["candidates"][0]["formula_code"] = "unknown"
    elif contract_name in {"OutlineResultV1", "ContentDraftResultV1"}:
        value["body_formula_code"] = "unknown"
    elif contract_name == "PersonaPolishResultV1":
        value["preserved_fact_checks"][0]["evidence_id"] = "unknown"
    elif contract_name == "ContentReviewResultV1":
        value["checks"][0]["evidence_ids"] = ["unknown"]
    elif contract_name == "VisualPlanResultV1":
        value["source_asset_ids"] = ["unknown"]
    elif contract_name == "CoverJobSubmissionResultV1":
        value["plan_hash"] = "unknown"
    elif contract_name == "VisualReviewResultV1":
        value["assets"][0]["asset_id"] = "unknown"
    return value


@pytest.mark.parametrize("contract_name", sorted(VALID_PAYLOADS))
def test_each_contract_accepts_valid_payload(contract_name):
    result = validate_content_node_result(contract_name, VALID_PAYLOADS[contract_name], DOMAIN_CONTEXT)
    assert result.__class__.__name__ == contract_name


def test_creation_research_viral_example_is_style_reference_only():
    payload = deepcopy(VALID_PAYLOADS["EvidenceCollectionResultV1"])
    payload["evidence_items"][0].update(
        {
            "allowed_usage": ["body"],
            "metadata": {
                "material_type": "viral_example",
                "usage_mode": "structure_reference_only",
            },
        }
    )

    with pytest.raises(ContractDomainValidationError, match="样式参考"):
        validate_content_node_result("EvidenceCollectionResultV1", payload, DOMAIN_CONTEXT)


def _selected_viral_reference() -> dict:
    return {
        "id": "viral-reference",
        "variable_codes": [],
        "value": "抽象结构参考",
        "source_type": "knowledge_base",
        "source_id": "viral-source-1",
        "source_version": "1",
        "verified_status": "retrieved",
        "allowed_usage": ["style_reference"],
        "risk_level": "normal",
        "source_hash": "viral-hash",
        "metadata": {
            "material_type": "viral_example",
            "usage_mode": "structure_reference_only",
            "selected_reference": True,
            "selection_reason": "行业、渠道和正文结构一致",
            "selection_basis": {
                "input_variable_paths": ["business_variables.project_type", "business_variables.owner_pain"],
                "matched_dimensions": {"industry": "matched", "pain": "matched"},
                "structure_fillability": {
                    "required_variable_kinds": ["pain", "solution"],
                    "available_variable_paths": [
                        "business_variables.owner_pain",
                        "business_variables.advantages",
                    ],
                    "unfilled_required_slots": [],
                },
                "candidate_comparison": [
                    {"source_id": "viral-source-1", "decision": "selected", "reason": "结构可填充"}
                ],
            },
            "reference_blueprint": {
                "title_pattern": "痛点加结果悬念",
                "title_slot_sequence": ["audience", "pain", "solution"],
                "opening_hook": "具体用户场景切入",
                "content_block_sequence": [
                    {"order": 1, "function": "pain", "presentation": "short_paragraph"},
                    {"order": 2, "function": "solution", "presentation": "narrative"},
                ],
                "narrative_structure": ["痛点", "原因", "方案", "结果"],
                "paragraph_rhythm": "短段落",
                "list_pattern": {"type": "none", "position": [], "observed_item_count": 0},
                "emoji_pattern": {"enabled": False, "positions": [], "functions": []},
                "interaction_style": "结尾邀请讨论",
            },
        },
    }


def test_viral_rewrite_requires_exactly_one_selected_reference_with_blueprint():
    context = replace(DOMAIN_CONTEXT, creation_mode="viral_rewrite")
    payload = deepcopy(VALID_PAYLOADS["EvidenceCollectionResultV1"])

    with pytest.raises(ContractDomainValidationError, match="必须且只能选择一篇"):
        validate_content_node_result("EvidenceCollectionResultV1", payload, context)

    payload["evidence_items"].append(_selected_viral_reference())
    result = validate_content_node_result("EvidenceCollectionResultV1", payload, context)
    assert result.evidence_items[1].metadata["selected_reference"] is True

    payload["evidence_items"].append({**_selected_viral_reference(), "id": "viral-reference-2"})
    with pytest.raises(ContractDomainValidationError, match="必须且只能选择一篇"):
        validate_content_node_result("EvidenceCollectionResultV1", payload, context)


def test_original_mode_rejects_selected_viral_reference():
    payload = deepcopy(VALID_PAYLOADS["EvidenceCollectionResultV1"])
    payload["evidence_items"].append(_selected_viral_reference())

    with pytest.raises(ContractDomainValidationError, match="原创模式不得选用"):
        validate_content_node_result("EvidenceCollectionResultV1", payload, DOMAIN_CONTEXT)


def test_viral_rewrite_rejects_reference_not_selected_from_current_variables():
    context = replace(DOMAIN_CONTEXT, creation_mode="viral_rewrite")
    payload = deepcopy(VALID_PAYLOADS["EvidenceCollectionResultV1"])
    reference = _selected_viral_reference()
    reference["metadata"].pop("selection_basis")
    payload["evidence_items"].append(reference)

    with pytest.raises(ContractDomainValidationError) as exc_info:
        validate_content_node_result("EvidenceCollectionResultV1", payload, context)

    assert exc_info.value.code == "viral_reference_selection_invalid"


def test_viral_rewrite_rejects_unfillable_or_untyped_reference_structure():
    context = replace(DOMAIN_CONTEXT, creation_mode="viral_rewrite")
    payload = deepcopy(VALID_PAYLOADS["EvidenceCollectionResultV1"])
    reference = _selected_viral_reference()
    reference["metadata"]["selection_basis"]["structure_fillability"]["unfilled_required_slots"] = ["itemized_price"]
    payload["evidence_items"].append(reference)

    with pytest.raises(ContractDomainValidationError) as exc_info:
        validate_content_node_result("EvidenceCollectionResultV1", payload, context)

    assert exc_info.value.code == "viral_reference_unfillable"

    reference["metadata"]["selection_basis"]["structure_fillability"]["unfilled_required_slots"] = []
    reference["metadata"]["reference_blueprint"]["list_pattern"] = {"type": "fixed-four"}
    with pytest.raises(ContractDomainValidationError) as exc_info:
        validate_content_node_result("EvidenceCollectionResultV1", payload, context)

    assert exc_info.value.code == "viral_reference_blueprint_invalid"


def test_parallel_research_contracts_reject_cross_domain_evidence():
    payload = deepcopy(VALID_PAYLOADS["EvidenceCollectionResultV1"])
    payload["evidence_items"][0]["metadata"]["material_type"] = "price"
    payload["evidence_items"][0]["risk_level"] = "high_risk"
    validate_content_node_result("PriceEvidenceCollectionResultV1", payload, DOMAIN_CONTEXT)

    with pytest.raises(ContractDomainValidationError) as exc_info:
        validate_content_node_result("BusinessRuleEvidenceCollectionResultV1", payload, DOMAIN_CONTEXT)
    assert exc_info.value.code == "business_evidence_scope_invalid"

    payload["evidence_items"][0]["metadata"] = {
        "material_type": "platform_rule",
        "rule_kind": "forbidden_replacement_map",
    }
    payload["evidence_items"][0]["risk_level"] = "sensitive"
    validate_content_node_result("ComplianceEvidenceCollectionResultV1", payload, DOMAIN_CONTEXT)


def test_viral_candidate_and_selection_contracts_keep_retrieval_and_selection_separate():
    candidate = _selected_viral_reference()
    candidate["metadata"].pop("selected_reference")
    candidate["metadata"].pop("selection_reason")
    candidate["metadata"].pop("selection_basis")
    candidate["metadata"].pop("reference_blueprint")
    candidates = {"evidence_items": [candidate], "citations": [candidate["source_id"]], "unresolved_questions": []}
    validate_content_node_result("ViralCandidateCollectionResultV1", candidates, DOMAIN_CONTEXT)

    selection = {
        "selected_candidate_id": candidate["id"],
        "selection_reason": "项目、场景和痛点匹配，结构可由当前事实填充",
        "selection_basis": _selected_viral_reference()["metadata"]["selection_basis"],
        "reference_blueprint": _selected_viral_reference()["metadata"]["reference_blueprint"],
        "unresolved_questions": [],
    }
    context = replace(
        DOMAIN_CONTEXT,
        creation_mode="viral_rewrite",
        viral_candidate_ids=frozenset({candidate["id"]}),
    )
    validate_content_node_result("ViralReferenceSelectionResultV1", selection, context)

    selection["selected_candidate_id"] = "other-candidate"
    with pytest.raises(ContractDomainValidationError) as exc_info:
        validate_content_node_result("ViralReferenceSelectionResultV1", selection, context)
    assert exc_info.value.code == "unknown_id"


def test_creation_research_price_accepts_knowledge_price_without_effective_date():
    payload = deepcopy(VALID_PAYLOADS["EvidenceCollectionResultV1"])
    payload["evidence_items"][0]["metadata"] = {
        **payload["evidence_items"][0]["metadata"],
        "material_type": "price",
    }
    payload["evidence_items"][0]["risk_level"] = "high_risk"

    result = validate_content_node_result("EvidenceCollectionResultV1", payload, DOMAIN_CONTEXT)
    assert result.evidence_items[0].metadata["material_type"] == "price"

    payload["evidence_items"][0]["risk_level"] = "normal"
    with pytest.raises(ContractDomainValidationError, match="high_risk"):
        validate_content_node_result("EvidenceCollectionResultV1", payload, DOMAIN_CONTEXT)


def test_creation_research_rejects_business_fact_without_formula_section_mapping():
    payload = deepcopy(VALID_PAYLOADS["EvidenceCollectionResultV1"])
    payload["evidence_items"][0]["metadata"].pop("formula_section")

    with pytest.raises(ContractDomainValidationError, match="具体段落"):
        validate_content_node_result("EvidenceCollectionResultV1", payload, DOMAIN_CONTEXT)


def test_creation_research_rejects_business_fact_mapped_to_wrong_formula_section():
    payload = deepcopy(VALID_PAYLOADS["EvidenceCollectionResultV1"])
    payload["evidence_items"][0]["metadata"]["formula_section"] = "不存在的段落"

    with pytest.raises(ContractDomainValidationError, match="锁定候选范围"):
        validate_content_node_result("EvidenceCollectionResultV1", payload, DOMAIN_CONTEXT)


def test_content_review_can_cite_frozen_style_reference():
    context = replace(
        DOMAIN_CONTEXT,
        allowed_evidence_by_usage={
            **DOMAIN_CONTEXT.allowed_evidence_by_usage,
            "any": DOMAIN_CONTEXT.allowed_evidence_by_usage["any"] | {"e-style"},
            "style_reference": frozenset({"e-style"}),
        },
    )
    payload = deepcopy(VALID_PAYLOADS["ContentReviewResultV1"])
    payload["checks"] = [
        {
            "code": "VIRAL_STRUCTURE_REFERENCE",
            "status": "passed",
            "message": "只参考了爆款结构与表情符号模式",
            "evidence_ids": ["e-style"],
        }
    ]

    result = validate_content_node_result("ContentReviewResultV1", payload, context)

    assert result.checks[0].evidence_ids == ["e-style"]


def test_visual_plan_must_use_exactly_the_task_locked_gallery_image():
    context = replace(DOMAIN_CONTEXT, required_source_asset_ids=("asset-1",))
    payload = deepcopy(VALID_PAYLOADS["VisualPlanResultV1"])
    payload["source_asset_ids"] = []

    with pytest.raises(ContractDomainValidationError) as exc_info:
        validate_content_node_result("VisualPlanResultV1", payload, context)

    assert exc_info.value.code == "visual_source_locked"


def test_visual_plan_over_template_limit_is_returned_to_agent_for_revision():
    context = replace(DOMAIN_CONTEXT, visual_text_max_chars={"title": 7, "subtitle": 4})
    payload = deepcopy(VALID_PAYLOADS["VisualPlanResultV1"])
    payload["text"] = ["超过七个字符的封面标题", "四字以内"]

    with pytest.raises(ContractDomainValidationError) as exc_info:
        validate_content_node_result("VisualPlanResultV1", payload, context)

    assert exc_info.value.code == "visual_text_too_long"
    assert exc_info.value.field_path == "text.0"
    assert "最多 7 个字符" in str(exc_info.value)


def test_formula_ranking_pool_comes_from_match_snapshot_before_selection_exists():
    node_input = ContentAgentNodeInputV1.model_validate(
        {
            "task_id": "task-1",
            "parent_run_id": "run-1",
            "node_id": "rank_formula_candidates",
            "attempt": 1,
            "content_brief": {},
            "runtime_config_snapshot": {},
            "match_decision_snapshot": {
                "selected_group_id": "group-1",
                "eligible_title_formula_codes": ["T02", "T06"],
                "eligible_body_formula_codes": ["C02"],
            },
            "formula_selection_snapshot": {},
            "evidence_bundle": {"items": []},
            "evidence_bundle_hash": "hash",
            "locked_versions": {
                "industry_pack_version_id": "industry-v1",
                "channel_profile_version_id": "channel-v1",
                "persona_profile_version_id": None,
                "rule_version_id": "rules-v1",
                "title_formula_code": None,
                "body_formula_code": None,
                "artifact_version_id": None,
            },
            "locked_values": {},
            "node_responsibility": "rank",
            "prohibited_actions": [],
            "output_json_schema": {},
        }
    )

    context = ContractDomainContext.from_node_input(node_input)
    result = validate_content_node_result(
        "FormulaRankingResultV1",
        {
            "title_rankings": [{"formula_code": "T06", "reason": "best"}],
            "body_rankings": [{"formula_code": "C02", "reason": "best"}],
        },
        context,
    )

    assert result.title_rankings[0].formula_code == "T06"


def test_allowed_numbers_are_indexed_from_nested_evidence_values():
    payload = {
        "task_id": "task-1",
        "parent_run_id": "run-1",
        "node_id": "generate_title_candidates",
        "attempt": 1,
        "content_brief": {},
        "runtime_config_snapshot": {},
        "match_decision_snapshot": {},
        "formula_selection_snapshot": {"selected_title_formula_code": "T02"},
        "evidence_bundle": {
            "items": [
                {
                    "id": "ev-title",
                    "value": {"area": "89㎡", "results": ["收纳增加12㎡"]},
                    "verified_status": "user_confirmed",
                    "allowed_usage": ["title"],
                }
            ]
        },
        "evidence_bundle_hash": "hash",
        "locked_versions": {
            "industry_pack_version_id": "industry-v1",
            "channel_profile_version_id": "channel-v1",
            "persona_profile_version_id": None,
            "rule_version_id": "rules-v1",
            "title_formula_code": "T02",
            "body_formula_code": None,
            "artifact_version_id": None,
        },
        "locked_values": {},
        "node_responsibility": "title",
        "prohibited_actions": [],
        "output_json_schema": {},
    }
    context = ContractDomainContext.from_node_input(ContentAgentNodeInputV1.model_validate(payload))

    result = validate_content_node_result(
        "TitleCandidatesResultV1",
        {
            "candidates": [
                {
                    "id": "title-1",
                    "text": "89㎡多出12㎡收纳",
                    "formula_code": "T02",
                    "evidence_ids": ["ev-title"],
                    "reason": "facts",
                },
                {
                    "id": "title-2",
                    "text": "89㎡收纳改造",
                    "formula_code": "T02",
                    "evidence_ids": ["ev-title"],
                    "reason": "facts",
                },
            ],
            "selected_title_formula_code": "T02",
            "evidence_ids": ["ev-title"],
        },
        context,
    )

    assert context.allowed_numbers == frozenset({"89", "12"})
    assert result.candidates[0].text == "89㎡多出12㎡收纳"


@pytest.mark.parametrize("contract_name", sorted(VALID_PAYLOADS))
def test_each_contract_rejects_missing_required_field(contract_name):
    payload = deepcopy(VALID_PAYLOADS[contract_name])
    payload.pop(next(iter(payload)))
    with pytest.raises(ValidationError):
        validate_content_node_result(contract_name, payload, DOMAIN_CONTEXT)


@pytest.mark.parametrize("contract_name", sorted(VALID_PAYLOADS))
def test_each_contract_rejects_out_of_scope_field(contract_name):
    payload = {**deepcopy(VALID_PAYLOADS[contract_name]), "unauthorized_field": "value"}
    with pytest.raises(ValidationError) as exc_info:
        validate_content_node_result(contract_name, payload, DOMAIN_CONTEXT)
    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize(
    "contract_name",
    [name for name in sorted(VALID_PAYLOADS) if name != "TitleSelectionResultV1"],
)
def test_each_contract_rejects_unknown_or_unlocked_id(contract_name):
    with pytest.raises(ContractDomainValidationError):
        invalid = _make_unknown(contract_name, VALID_PAYLOADS[contract_name])
        validate_content_node_result(contract_name, invalid, DOMAIN_CONTEXT)


def test_content_value_contract_rejects_temporary_direction_codes():
    payload = deepcopy(VALID_PAYLOADS["ContentValueResultV1"])
    payload["direction_candidates"][0]["direction_code"] = "D01"

    with pytest.raises(ValidationError) as exc_info:
        validate_content_node_result("ContentValueResultV1", payload, DOMAIN_CONTEXT)

    assert exc_info.value.errors()[0]["loc"] == ("direction_candidates", 0, "direction_code")


def test_direction_selection_contract_rejects_temporary_direction_codes():
    payload = deepcopy(VALID_PAYLOADS["DirectionSelectionResultV1"])
    payload["direction_code"] = "D01"

    with pytest.raises(ValidationError) as exc_info:
        validate_content_node_result("DirectionSelectionResultV1", payload, DOMAIN_CONTEXT)

    assert exc_info.value.errors()[0]["loc"] == ("direction_code",)


@pytest.mark.parametrize(
    ("contract_name", "forbidden_field"),
    [
        ("TitleCandidatesResultV1", "body"),
        ("ContentDraftResultV1", "title"),
        ("ContentReviewResultV1", "revised_body"),
        ("ContentValueResultV1", "locked_group_id"),
        ("DirectionSelectionResultV1", "locked_group_id"),
        ("TitleSelectionResultV1", "title"),
        ("EvidenceCollectionResultV1", "article"),
    ],
)
def test_role_contracts_cannot_cross_node_responsibilities(contract_name, forbidden_field):
    payload = {**deepcopy(VALID_PAYLOADS[contract_name]), forbidden_field: "forbidden"}
    with pytest.raises(ValidationError):
        validate_content_node_result(contract_name, payload, DOMAIN_CONTEXT)


@pytest.mark.parametrize(
    ("contract_name", "field_path", "value"),
    [
        ("TitleCandidatesResultV1", ("candidates", 0, "text"), "unsupported 99"),
        ("ContentDraftResultV1", ("body",), "unsupported 99"),
        ("PersonaPolishResultV1", ("polished_body",), "unsupported 99"),
        ("VisualPlanResultV1", ("text", 0), "unsupported 99"),
    ],
)
def test_creative_contracts_block_unsupported_numbers(contract_name, field_path, value):
    payload = deepcopy(VALID_PAYLOADS[contract_name])
    target = payload
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = value
    with pytest.raises(ContractDomainValidationError) as exc_info:
        validate_content_node_result(contract_name, payload, DOMAIN_CONTEXT)
    assert exc_info.value.code == "unsupported_number"


def test_body_number_validation_ignores_line_leading_sequence_markers_only():
    payload = deepcopy(VALID_PAYLOADS["ContentDraftResultV1"])
    payload["body"] = "1. 第一项\n2、第二项\n3）第三项"

    validate_content_node_result("ContentDraftResultV1", payload, DOMAIN_CONTEXT)

    payload["body"] = "1. 第一项包含 99 元"
    with pytest.raises(ContractDomainValidationError) as exc_info:
        validate_content_node_result("ContentDraftResultV1", payload, DOMAIN_CONTEXT)

    assert exc_info.value.code == "unsupported_number"


def test_common_agent_input_requires_all_trace_and_lock_fields():
    payload = {
        "task_id": "task",
        "parent_run_id": "run",
        "node_id": "node",
        "attempt": 1,
        "content_brief": {},
        "runtime_config_snapshot": {},
        "match_decision_snapshot": {},
        "formula_selection_snapshot": {},
        "evidence_bundle": {"items": []},
        "evidence_bundle_hash": "hash",
        "locked_versions": {
            "industry_pack_version_id": "industry-v1",
            "channel_profile_version_id": "channel-v1",
            "persona_profile_version_id": None,
            "rule_version_id": "rules-v3",
            "title_formula_code": "T1",
            "body_formula_code": "B1",
            "artifact_version_id": None,
        },
        "locked_values": {},
        "node_responsibility": "do one thing",
        "prohibited_actions": ["do not write body"],
        "output_json_schema": get_contract_model("ContentValueResultV1").model_json_schema(),
    }
    assert ContentAgentNodeInputV1.model_validate(payload).task_id == "task"
    payload.pop("evidence_bundle_hash")
    with pytest.raises(ValidationError):
        ContentAgentNodeInputV1.model_validate(payload)


@pytest.mark.asyncio
async def test_result_collector_requires_activation_exactly_one_submission_and_no_defaulting():
    runtime = type("Runtime", (), {})()
    runtime._required_skill_closure = ["content-reviewer"]
    runtime._activated_required_skills = []
    collector = ContentNodeResultCollector("ContentReviewResultV1", DOMAIN_CONTEXT, runtime)

    with pytest.raises(ContractDomainValidationError, match="未激活"):
        await collector.submit(**VALID_PAYLOADS["ContentReviewResultV1"])
    assert collector.submission_count == 0

    runtime._activated_required_skills = ["content-reviewer"]
    invalid = _make_unknown("ContentReviewResultV1", VALID_PAYLOADS["ContentReviewResultV1"])
    with pytest.raises(ContractDomainValidationError, match="未授权"):
        await collector.submit(**invalid)
    assert collector.submission_count == 0

    await collector.submit(**VALID_PAYLOADS["ContentReviewResultV1"])
    assert collector.finalize()["status"] == "passed"
    with pytest.raises(ContractDomainValidationError, match="只能提交一次"):
        await collector.submit(**VALID_PAYLOADS["ContentReviewResultV1"])

    empty = ContentNodeResultCollector("ContentReviewResultV1", DOMAIN_CONTEXT, runtime)
    with pytest.raises(ContractDomainValidationError, match="未通过"):
        empty.finalize()


@pytest.mark.asyncio
async def test_cover_result_collector_requires_exact_created_job_submission():
    runtime = type("Runtime", (), {})()
    runtime._required_skill_closure = []
    runtime._activated_required_skills = []
    payload = VALID_PAYLOADS["CoverJobSubmissionResultV1"]
    collector = ContentNodeResultCollector("CoverJobSubmissionResultV1", DOMAIN_CONTEXT, runtime)

    with pytest.raises(ContractDomainValidationError) as exc_info:
        await collector.submit(**payload)
    assert exc_info.value.code == "cover_job_not_created"

    runtime._content_cover_job_submission = payload
    with pytest.raises(ContractDomainValidationError) as exc_info:
        await collector.submit(**{**payload, "cover_job_id": "invented-job"})
    assert exc_info.value.code == "cover_job_submission_mismatch"

    await collector.submit(**payload)
    assert collector.finalize() == payload


@pytest.mark.asyncio
async def test_creation_research_must_query_available_business_and_viral_libraries():
    runtime = type("Runtime", (), {})()
    runtime._required_skill_closure = ["content-evidence-researcher"]
    runtime._activated_required_skills = ["content-evidence-researcher"]
    runtime._visible_knowledge_bases = [
        {"kb_id": "kb-price", "name": "价格库"},
        {"kb_id": "kb-brand", "name": "品牌知识库"},
        {"kb_id": "kb-rules", "name": "平台规则"},
        {"kb_id": "kb-viral", "name": "爆款库"},
    ]
    runtime._content_queried_knowledge_bases = {"kb-viral"}
    runtime._content_retrieved_knowledge_results = {"source-1": [{"source_id": "source-1", "metadata": {}}]}
    collector = ContentNodeResultCollector("EvidenceCollectionResultV1", DOMAIN_CONTEXT, runtime)

    with pytest.raises(ContractDomainValidationError, match="必需知识库"):
        await collector.submit(**VALID_PAYLOADS["EvidenceCollectionResultV1"])

    runtime._content_queried_knowledge_bases = {"kb-price", "kb-brand", "kb-rules", "kb-viral"}
    await collector.submit(**VALID_PAYLOADS["EvidenceCollectionResultV1"])

    assert collector.submission_count == 1


@pytest.mark.asyncio
async def test_result_collector_requires_retrieved_knowledge_source_and_freezes_metadata():
    runtime = type("Runtime", (), {})()
    runtime._required_skill_closure = []
    runtime._activated_required_skills = []
    runtime._content_retrieved_knowledge_results = {
        "chunk-1": [
            {
                "source_id": "chunk-1",
                "content": "retrieved content",
                "metadata": {
                    "knowledge_base_id": "kb-1",
                    "knowledge_base_name": "工艺知识库",
                    "document_id": "file-1",
                    "document_name": "工艺手册.pdf",
                    "chunk_id": "chunk-1",
                },
            }
        ]
    }
    collector = ContentNodeResultCollector("EvidenceCollectionResultV1", DOMAIN_CONTEXT, runtime)
    payload = deepcopy(VALID_PAYLOADS["EvidenceCollectionResultV1"])

    with pytest.raises(ContractDomainValidationError, match="检索结果 ID"):
        await collector.submit(**payload)

    payload["evidence_items"][0]["source_id"] = "chunk-1"
    payload["evidence_items"][0]["metadata"]["agent_note"] = "keep"
    await collector.submit(**payload)

    metadata = collector.finalize()["evidence_items"][0]["metadata"]
    assert metadata["knowledge_base_id"] == "kb-1"
    assert metadata["document_name"] == "工艺手册.pdf"
    assert metadata["chunk_id"] == "chunk-1"
    assert metadata["agent_note"] == "keep"


@pytest.mark.asyncio
async def test_evidence_result_tool_accepts_schema_parsed_items_and_freezes_metadata():
    runtime = type("Runtime", (), {})()
    runtime._required_skill_closure = []
    runtime._activated_required_skills = []
    runtime._content_retrieved_knowledge_results = {
        "source-1": [
            {
                "source_id": "source-1",
                "content": "retrieved content",
                "metadata": {
                    "knowledge_base_id": "kb-1",
                    "knowledge_base_name": "工艺知识库",
                    "document_id": "file-1",
                    "document_name": "工艺手册.pdf",
                    "chunk_id": "source-1",
                },
            }
        ]
    }
    collector = ContentNodeResultCollector("EvidenceCollectionResultV1", DOMAIN_CONTEXT, runtime)
    tool = build_content_result_tool(collector)

    result = await tool.ainvoke(deepcopy(VALID_PAYLOADS["EvidenceCollectionResultV1"]))

    assert result == {"accepted": True, "contract": "EvidenceCollectionResultV1"}
    metadata = collector.finalize()["evidence_items"][0]["metadata"]
    assert metadata["knowledge_base_id"] == "kb-1"
    assert metadata["document_name"] == "工艺手册.pdf"
    assert metadata["chunk_id"] == "source-1"


@pytest.mark.asyncio
async def test_structured_result_tool_uses_registered_pydantic_schema():
    runtime = type("Runtime", (), {})()
    runtime._required_skill_closure = ["content-reviewer"]
    runtime._activated_required_skills = ["content-reviewer"]
    collector = ContentNodeResultCollector("ContentReviewResultV1", DOMAIN_CONTEXT, runtime)
    tool = build_content_result_tool(collector)

    rejected = await tool.ainvoke(_make_unknown("ContentReviewResultV1", VALID_PAYLOADS["ContentReviewResultV1"]))
    assert "请修正后重新提交" in rejected
    assert collector.submission_count == 0

    await tool.ainvoke(VALID_PAYLOADS["ContentReviewResultV1"])

    assert tool.name == "submit_content_node_result"
    assert collector.submission_count == 1
    assert len(CONTRACT_REGISTRY) == 23
