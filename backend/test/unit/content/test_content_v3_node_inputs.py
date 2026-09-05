from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import pytest

from yuxi.content.control.errors import ContentApplicationError
from yuxi.content.control.workflow.content_node_input import ContentNodeInputAssembler
from yuxi.content.model.contracts import INPUT_CONTRACT_REGISTRY
from yuxi.content.model.contracts.content_nodes import PlanVisualsInputV1
from yuxi.content.v3.workflow import WORKFLOW_V3


STRATEGY = {
    "content_direction": "CT05",
    "selected_group_id": "group-1",
    "creation_methods": ["M03"],
    "creation_method_definitions": [
        {
            "code": "M03",
            "name": "价值法",
            "method_type": "core",
            "principle": "表达可验证价值",
            "suitable_scenes": [],
            "sentence_patterns": [],
            "variable_schema": ["advantages"],
            "risk_rules": [],
        }
    ],
    "title_formula": {"code": "T03"},
    "body_formula": {"code": "C03"},
    "rule_version_id": "rules-v3",
    "match_snapshot_id": "match-1",
    "formula_snapshot_id": "formula-1",
}


STRATEGY["snapshot_hash"] = hashlib.sha256(
    json.dumps(STRATEGY, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


@pytest.mark.unit
def test_visual_plan_input_does_not_expose_material_names_as_image_evidence():
    payload = PlanVisualsInputV1.model_validate(
        {
            "selected_title": {"text": "杭州装修案例"},
            "content_draft": {"body": "杭州装修案例正文"},
            "strategy_snapshot": STRATEGY,
            "evidence_bundle": {"items": []},
            "media_evidence_items": [
                {
                    "id": "asset-1",
                    "display_name": "长沙旧房原图",
                    "file_name": "长沙案例.png",
                    "original_file_name": "长沙案例原图.png",
                    "selected_for_cover": True,
                }
            ],
            "artifact_version": {"id": "artifact-version-1"},
            "channel_profile": {},
        }
    )

    media = payload.media_evidence_items[0]
    assert media == {"id": "asset-1", "selected_for_cover": True}


EVIDENCE_BUNDLE = {
    "id": "bundle-1",
    "version": 2,
    "bundle_hash": "e" * 64,
    "items": [],
}
PRODUCT_EVIDENCE_PACK = {
    "strategy_snapshot_hash": STRATEGY["snapshot_hash"],
    "evidence_bundle_id": EVIDENCE_BUNDLE["id"],
    "evidence_bundle_version": EVIDENCE_BUNDLE["version"],
    "evidence_bundle_hash": EVIDENCE_BUNDLE["bundle_hash"],
    "slot_mappings": [],
    "unresolved_questions": [],
}
PRODUCT_EVIDENCE_PACK["pack_hash"] = hashlib.sha256(
    json.dumps(PRODUCT_EVIDENCE_PACK, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


def _state() -> dict:
    return {
        "content_brief": {"brand": {"name": "ContentFlow"}},
        "runtime_config_snapshot": {"creation_mode": "original"},
        "strategy_snapshot": deepcopy(STRATEGY),
        "formula_lexicon_bundle": {
            "required": True,
            "title_formula_code": "T03",
            "body_formula_code": "C03",
            "title": [
                {
                    "knowledge_base_id": "kb-title",
                    "file_id": "file-title",
                    "chunks": ["标题词库"],
                }
            ],
            "body": [
                {
                    "knowledge_base_id": "kb-body",
                    "file_id": "file-body",
                    "chunks": ["正文词库"],
                }
            ],
            "bundle_hash": "lexicon-bundle-hash",
        },
        "selected_title": {"id": "title-1", "text": "锁定标题"},
        "content_outline": {"body_formula_code": "C03", "sections": [{"section_id": "s1"}]},
        "content_draft": {"body": "正文", "topics": [], "body_formula_code": "C03"},
        "validation_report": {"status": "passed", "checks": []},
        "channel_result": {"title": "锁定标题", "body": "正文", "topics": []},
        "persona_diff": {"change_summary": []},
        "evidence_bundle": deepcopy(EVIDENCE_BUNDLE),
        "product_evidence_pack": deepcopy(PRODUCT_EVIDENCE_PACK),
        "title_evidence_requirements": [],
        "title_candidates": [
            {
                "id": "title-1",
                "text": "锁定标题",
                "formula_code": "T03",
                "evidence_ids": [],
                "reason": "公式匹配",
                "selectable": True,
            }
        ],
        "title_validation_report": {
            "status": "passed",
            "items": [{"id": "title-1", "status": "passed", "checks": []}],
        },
    }


@pytest.mark.unit
def test_generate_body_input_contains_locked_title_and_outline():
    node = {
        "id": "generate_body",
        "input_contract": "GenerateBodyInputV1",
        "state_inputs": [
            "content_brief",
            "strategy_snapshot",
            "selected_title",
            "content_outline",
            "product_evidence_pack",
            "evidence_bundle",
            "channel_profile",
            "persona_profile",
        ],
    }
    state = {**_state(), "channel_profile": {}, "persona_profile": {}}

    assembly = ContentNodeInputAssembler.build(node=node, state=state)

    assert assembly.payload["selected_title"]["text"] == "锁定标题"
    assert assembly.payload["content_outline"]["body_formula_code"] == "C03"
    assert len(assembly.snapshot_hash) == 64


@pytest.mark.unit
def test_unified_generation_input_exposes_locked_strategy_and_previous_validation_report():
    state = {**_state(), "channel_profile": {}, "persona_profile": {}}
    state["validation_report"] = {"status": "blocked", "checks": [{"code": "BODY_EVIDENCE_FAILED"}]}
    state["review_report"] = {
        "status": "blocked",
        "checks": [{"code": "TITLE_FACT_UNSUPPORTED", "location": "title"}],
    }
    node = next(item for item in WORKFLOW_V3["nodes"] if item["id"] == "generate_content")

    assembly = ContentNodeInputAssembler.build(node=node, state=state)

    assert assembly.payload["runtime_config_snapshot"]["creation_mode"] == "original"

    assert assembly.payload["strategy_snapshot"]["title_formula"]["code"] == "T03"
    assert assembly.payload["strategy_snapshot"]["body_formula"]["code"] == "C03"
    assert assembly.payload["validation_report"]["status"] == "blocked"
    assert assembly.payload["review_report"]["checks"][0]["code"] == "TITLE_FACT_UNSUPPORTED"
    assert assembly.payload["selected_title"]["text"] == "锁定标题"
    assert assembly.payload["content_draft"]["body"] == "正文"


@pytest.mark.unit
def test_semantic_review_input_contains_all_review_upstream_outputs():
    node = {
        "id": "semantic_review",
        "input_contract": "SemanticReviewInputV1",
        "state_inputs": [
            "content_brief",
            "strategy_snapshot",
            "selected_title",
            "content_outline",
            "content_draft",
            "validation_report",
            "channel_result",
            "evidence_bundle",
        ],
        "optional_state_inputs": ["persona_diff"],
    }

    assembly = ContentNodeInputAssembler.build(node=node, state=_state())

    assert assembly.payload["content_draft"]["body"] == "正文"
    assert assembly.payload["validation_report"]["status"] == "passed"
    assert assembly.payload["strategy_snapshot"]["snapshot_hash"] == STRATEGY["snapshot_hash"]
    assert assembly.payload["persona_diff"] == {"change_summary": []}


@pytest.mark.unit
def test_node_input_missing_fails_before_agent_delegation():
    node = {
        "id": "persona_style_polish",
        "input_contract": "PersonaStylePolishInputV1",
        "state_inputs": [
            "content_brief",
            "strategy_snapshot",
            "selected_title",
            "content_outline",
            "content_draft",
            "evidence_bundle",
            "channel_profile",
            "persona_profile",
        ],
    }
    state = _state()
    state.update({"channel_profile": {}, "persona_profile": {}})
    state.pop("content_draft")

    with pytest.raises(ContentApplicationError) as exc_info:
        ContentNodeInputAssembler.build(node=node, state=state)

    assert exc_info.value.code == "node_input_missing"
    assert "content_draft" in exc_info.value.message


@pytest.mark.unit
def test_decoration_generation_rejects_optional_formula_lexicon_bundle():
    node = next(item for item in WORKFLOW_V3["nodes"] if item["id"] == "generate_content")
    state = {**_state(), "channel_profile": {}, "persona_profile": {}}
    state["formula_lexicon_bundle"] = {
        "required": False,
        "title_formula_code": "T03",
        "body_formula_code": "C03",
        "title": [],
        "body": [],
    }

    with pytest.raises(ContentApplicationError) as exc_info:
        ContentNodeInputAssembler.build(node=node, state=state)

    assert exc_info.value.code == "node_input_invalid"
    assert "必须经过必选词库加载路径" in exc_info.value.message


@pytest.mark.unit
def test_input_contract_registry_contains_every_agent_payload_contract():
    assert set(INPUT_CONTRACT_REGISTRY) == {
        "AnalyzeContentValueInputV1",
        "AnalyzeAndSelectDirectionInputV1",
        "SelectCreationStrategyInputV1",
        "SelectContentDirectionInputV1",
        "ExplainStrategyInputV1",
        "CollectMissingEvidenceInputV1",
        "CollectMissingEvidenceInputV2",
        "CollectSelectedStrategyEvidenceInputV1",
        "CollectBusinessRuleEvidenceInputV1",
        "CollectPriceEvidenceInputV1",
        "CollectComplianceEvidenceInputV1",
        "CollectViralCandidatesInputV1",
        "SelectViralReferenceInputV1",
        "RankFormulaCandidatesInputV1",
        "RankFormulaCandidatesInputV2",
        "CollectStrategyProductEvidenceInputV1",
        "GenerateTitleCandidatesInputV1",
        "SelectTitleInputV1",
        "BuildOutlineInputV1",
        "GenerateBodyInputV1",
        "PersonaStylePolishInputV1",
        "GenerateContentInputV1",
        "SemanticReviewInputV1",
        "PlanVisualsInputV1",
        "SubmitCoverJobInputV1",
        "VisualReviewInputV1",
    }


@pytest.mark.unit
def test_strategy_snapshot_rejects_mutation_after_hash_is_locked():
    state = {**_state(), "channel_profile": {}, "persona_profile": {}}
    state["strategy_snapshot"]["body_formula"]["code"] = "C04"
    node = {
        "id": "generate_body",
        "input_contract": "GenerateBodyInputV1",
        "state_inputs": [
            "content_brief",
            "strategy_snapshot",
            "selected_title",
            "content_outline",
            "evidence_bundle",
            "channel_profile",
            "persona_profile",
        ],
    }

    with pytest.raises(ContentApplicationError) as exc_info:
        ContentNodeInputAssembler.build(node=node, state=state)

    assert exc_info.value.code == "node_input_invalid"


@pytest.mark.unit
def test_every_published_agent_node_can_assemble_its_declared_payload():
    state = {
        **_state(),
        "rule_version_id": "rules-v3",
        "content_type": {},
        "industry_pack": {},
        "channel_profile": {},
        "persona_profile": {},
        "media_evidence_items": [],
        "value_analysis": {
            "value_points": ["value"],
            "direction_candidates": [{"direction_code": "CT05", "reason": "证据充分", "evidence_ids": []}],
            "reasoning": "过程资料适合能力证明",
            "evidence_ids": [],
        },
        "content_angles": [{"direction_code": "CT05", "reason": "证据充分", "evidence_ids": []}],
        "selected_angle": {"direction_code": "CT05"},
        "strategy_selection": {
            "selected_direction_code": "CT05",
            "selected_group_id": "group-1",
            "creation_method_codes": ["M03"],
            "title_formula_code": "T03",
            "body_formula_code": "C03",
            "reason": "符合输入材料",
            "evidence_ids": [],
        },
        "match_decision_snapshot": {"id": "match-1", "selected_group_id": "group-1"},
        "formula_candidate_pool": {"title_formula_codes": ["T03"], "body_formula_codes": ["C03"]},
        "evidence_gap_analysis": {
            "has_missing": False,
            "missing_variable_codes": [],
            "missing_evidence_types": [],
        },
        "business_rule_evidence_collection": {
            "evidence_items": [],
            "citations": [],
            "unresolved_questions": [],
        },
        "price_evidence_collection": {"evidence_items": [], "citations": [], "unresolved_questions": []},
        "compliance_evidence_collection": {
            "evidence_items": [],
            "citations": [],
            "unresolved_questions": [],
        },
        "viral_candidate_collection": {"evidence_items": [], "citations": [], "unresolved_questions": []},
        "strategy_explanation": {"locked_group_id": "group-1"},
        "product_material_requirements": {
            "strategy_snapshot_hash": STRATEGY["snapshot_hash"],
            "required_variable_codes": ["advantages"],
            "requirements": [
                {
                    "requirement_id": "product_profile",
                    "material_type": "product_profile",
                    "variable_codes": ["advantages"],
                    "target_usages": ["title", "body"],
                    "required": True,
                    "query_hint": "检索产品资料",
                    "risk_level": "normal",
                }
            ],
        },
        "artifact_version": {"id": "artifact-version-1"},
        "visual_plan": {"plan_hash": "plan-hash", "size": {"width": 1080, "height": 1440}},
        "cover_job": {"cover_job_id": "cover-job-1"},
        "cover_assets": [{"id": "cover-asset-1"}],
    }

    for node in WORKFLOW_V3["nodes"]:
        if node["type"] == "agent":
            assembly = ContentNodeInputAssembler.build(node=node, state=state)
            assert assembly.contract_name == node["input_contract"]


@pytest.mark.unit
def test_title_selection_input_requires_a_selectable_candidate():
    node = {
        "id": "select_title",
        "input_contract": "SelectTitleInputV1",
        "state_inputs": [
            "content_brief",
            "strategy_snapshot",
            "product_evidence_pack",
            "evidence_bundle",
            "channel_profile",
            "persona_profile",
            "title_candidates",
            "title_validation_report",
        ],
    }
    state = {**_state(), "channel_profile": {}, "persona_profile": {}}
    state["title_candidates"][0]["selectable"] = False

    with pytest.raises(ContentApplicationError) as exc_info:
        ContentNodeInputAssembler.build(node=node, state=state)

    assert exc_info.value.code == "node_input_invalid"
    assert "至少需要一个通过确定性校验的候选" in exc_info.value.message
