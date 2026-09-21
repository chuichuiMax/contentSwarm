import hashlib
import json
from copy import deepcopy

from yuxi.content.control.workflow.generation_input import (
    project_generation_input,
    project_locked_quote_safe_input,
    project_review_input,
    project_visual_input,
    project_visual_review_input,
)
from yuxi.content.model.locked_blocks import render_semicolon_lines


def test_generation_projection_keeps_writing_facts_and_removes_audit_duplicates():
    blueprint = {"layer_sequence": [{"code": "business"}], "phrase_composition": [{"min_groups": 1}]}
    payload = {
        "content_brief": {
            "topic": "收纳改造",
            "form_values": {"pain": "空间不足", "count": True, "detail": "保留原始说明"},
            "business_variables": {},
        },
        "strategy_snapshot": {
            "snapshot_hash": "a" * 64,
            "decision": {"reason": "只供审计"},
            "reference_snapshot": {"reference_blueprint": {"hook": "重复参考"}},
            "direction_blueprint": blueprint,
            "title_formula": {"code": "T01"},
            "body_formula": {
                "code": "C01",
                "composition_blueprint": blueprint,
                "body_calling": {
                    "sections": [{"id": "opening", "instruction": "说明问题"}],
                    "composition_blueprint": blueprint,
                },
                "body_calling_source": {"document": "审计来源"},
            },
        },
        "formula_lexicon_bundle": {"required": False},
        "evidence_bundle": {
            "items": [
                {
                    "id": "fact-1",
                    "source_type": "manual_input",
                    "source_id": "field_pain",
                    "verified_status": "user_confirmed",
                    "variable_codes": ["pain"],
                    "value": "空间不足",
                    "allowed_usage": ["title", "body"],
                    "source_hash": "audit-hash",
                },
                {
                    "id": "fact-2",
                    "source_type": "manual_input",
                    "source_id": "field_count",
                    "verified_status": "user_confirmed",
                    "variable_codes": ["count"],
                    "value": 1,
                    "allowed_usage": ["body"],
                },
                {
                    "id": "reference",
                    "allowed_usage": ["style_reference"],
                    "metadata": {
                        "material_type": "viral_example",
                        "selected_reference": True,
                        "reference_blueprint": {"opening_hook": "问题开头"},
                        "selection_basis": {
                            "input_variable_paths": ["evidence_bundle.items.0.value"],
                            "matched_dimensions": {"topic": 4},
                            "structure_fillability": {"unfilled_required_slots": []},
                            "candidate_comparison": [{"reason": "只供选择审计"}],
                            "prepared_reference_decision": {"reason": "只供选择审计"},
                        },
                    },
                },
            ]
        },
        "channel_profile": {},
        "persona_profile": {},
        "runtime_config_snapshot": {
            "creation_mode": "viral_rewrite",
            "visual_material": {"private": True},
            "content_rule_bundle": {"bundle_version": "viral-modular-v1", "bundle_hash": "rules"},
        },
        "expression_guidance": {
            "snapshot_hash": "expression-rules",
            "sources": [{"role": "tone_reference", "chunks": [{"content": "自然短句"}]}],
        },
    }
    original = deepcopy(payload)

    view = project_generation_input(payload)

    assert payload == original
    assert view["content_brief"]["form_values"] == {"count": True, "detail": "保留原始说明"}
    assert view["evidence_bundle"]["items"][0]["value"] == "空间不足"
    assert "source_hash" not in view["evidence_bundle"]["items"][0]
    strategy = view["strategy_snapshot"]
    assert strategy["direction_blueprint"] == blueprint
    assert strategy["body_formula"]["body_calling"]["sections"][0]["id"] == "opening"
    for key in ("snapshot_hash", "source_snapshot_hash", "decision", "reference_snapshot"):
        assert key not in strategy
    for key in ("composition_blueprint", "body_calling_source"):
        assert key not in strategy["body_formula"]
    assert "composition_blueprint" not in strategy["body_formula"]["body_calling"]
    reference = view["evidence_bundle"]["items"][2]["metadata"]
    assert reference["reference_blueprint"] == {"opening_hook": "问题开头"}
    assert reference["selection_basis"]["structure_fillability"] == {"unfilled_required_slots": []}
    assert "candidate_comparison" not in reference["selection_basis"]
    assert "prepared_reference_decision" not in reference["selection_basis"]
    runtime = view["runtime_config_snapshot"]
    assert runtime["creation_mode"] == "viral_rewrite"
    assert runtime["content_rule_bundle"]["bundle_hash"] == "rules"
    assert "viral-price-author" not in runtime["content_rule_bundle"]["active_modules"]
    assert "modules" not in runtime["content_rule_bundle"]
    assert view["expression_guidance"] == payload["expression_guidance"]


def test_generation_projection_keeps_unmatched_brief_value():
    payload = {
        "content_brief": {"form_values": {"price": "项目预算"}, "business_variables": {}},
        "strategy_snapshot": {"snapshot_hash": "b" * 64, "title_formula": {}, "body_formula": {}},
        "formula_lexicon_bundle": {},
        "evidence_bundle": {
            "items": [
                {
                    "source_type": "manual_input",
                    "source_id": "field_price",
                    "verified_status": "user_confirmed",
                    "variable_codes": ["price"],
                    "value": "标准单价",
                }
            ]
        },
        "channel_profile": {},
        "persona_profile": {},
        "runtime_config_snapshot": {"creation_mode": "viral_rewrite"},
    }

    view = project_generation_input(payload)

    assert view["content_brief"]["form_values"]["price"] == "项目预算"


def test_generation_projection_keeps_user_request_even_when_evidence_is_identical():
    payload = {
        "content_brief": {"form_values": {"user_request": "不要承诺随时看工地"}, "business_variables": {}},
        "strategy_snapshot": {"body_formula": {}},
        "formula_lexicon_bundle": {},
        "evidence_bundle": {
            "items": [
                {
                    "source_type": "manual_input",
                    "source_id": "field_user_request",
                    "verified_status": "user_confirmed",
                    "variable_codes": ["user_request"],
                    "value": "不要承诺随时看工地",
                }
            ]
        },
        "channel_profile": {},
        "persona_profile": {},
        "runtime_config_snapshot": {"creation_mode": "viral_rewrite"},
    }

    view = project_generation_input(payload)

    assert view["content_brief"]["form_values"]["user_request"] == "不要承诺随时看工地"


def test_generation_projection_keeps_distinct_formula_blueprint():
    payload = {
        "content_brief": {"form_values": {}, "business_variables": {}},
        "strategy_snapshot": {
            "direction_blueprint": {"layer_sequence": ["direction"]},
            "body_formula": {
                "composition_blueprint": {"layer_sequence": ["formula"]},
                "body_calling": {"composition_blueprint": {"layer_sequence": ["calling"]}},
            },
        },
        "formula_lexicon_bundle": {},
        "evidence_bundle": {"items": []},
        "channel_profile": {},
        "persona_profile": {},
        "runtime_config_snapshot": {"creation_mode": "viral_rewrite"},
    }

    view = project_generation_input(payload)

    assert view["strategy_snapshot"]["body_formula"]["composition_blueprint"] == {"layer_sequence": ["formula"]}
    assert view["strategy_snapshot"]["body_formula"]["body_calling"]["composition_blueprint"] == {
        "layer_sequence": ["calling"]
    }


def test_review_projection_preserves_final_draft_and_reference_facts():
    blueprint = {"opening_hook": "疑问开头", "content_block_sequence": ["价格", "范围"]}
    payload = {
        "review_scope": "full",
        "channel_profile": {"body_constraints": {"emoji_allowed": True}},
        "persona_profile": {"identity": "工长"},
        "content_brief": {
            "form_values": {"budget": "预算18万元", "quote_type": "项目硬装预算"},
            "business_variables": {},
        },
        "strategy_snapshot": {
            "snapshot_hash": "a" * 64,
            "decision": {"reason": "审计留存"},
            "reference_snapshot": {"reference_blueprint": blueprint},
            "direction_blueprint": {"layer_sequence": ["证据"]},
            "body_formula": {"code": "FRB08", "body_calling": {"sections": [{"id": "price"}]}},
        },
        "selected_title": {"text": "预算怎么核对"},
        "content_outline": {"sections": [{"section_id": "price"}]},
        "content_draft": {"body": "预算18万元，先核对范围。"},
        "validation_report": {"status": "passed"},
        "channel_result": {"body": "预算18万元，先核对范围。"},
        "persona_diff": None,
        "evidence_bundle": {
            "items": [
                {
                    "id": "budget",
                    "source_type": "manual_input",
                    "source_id": "field_budget",
                    "verified_status": "user_confirmed",
                    "variable_codes": ["budget"],
                    "value": "预算18万元",
                    "allowed_usage": ["body"],
                    "source_hash": "audit",
                },
                {
                    "id": "reference",
                    "allowed_usage": ["style_reference"],
                    "metadata": {
                        "material_type": "viral_example",
                        "selected_reference": True,
                        "reference_blueprint": blueprint,
                        "selection_basis": {
                            "structure_fillability": {"unfilled_required_slots": []},
                            "candidate_comparison": [{"reason": "审计留存"}],
                        },
                    },
                },
            ]
        },
        "runtime_config_snapshot": {
            "content_rule_bundle": {"bundle_version": "viral-modular-v1", "bundle_hash": "rules"}
        },
        "expression_guidance": {
            "snapshot_hash": "expression-rules",
            "sources": [{"role": "concrete_expression", "chunks": [{"content": "动作化表达"}]}],
        },
        "expression_policy": {
            "emoji_allowed": True,
            "minimum_semantic_categories": 3,
            "required_categories": [
                {"code": "verified_data", "target": "数据"},
                {"code": "identity_trust", "target": "身份"},
                {"code": "process_action", "target": "动作"},
            ],
        },
    }
    original = deepcopy(payload)

    view = project_review_input(payload)

    assert payload == original
    assert view["content_draft"] == payload["content_draft"]
    assert view["selected_title"] == payload["selected_title"]
    assert view["content_brief"]["form_values"] == {"quote_type": "项目硬装预算"}
    assert view["evidence_bundle"]["items"][0]["value"] == "预算18万元"
    assert view["evidence_bundle"]["items"][1]["metadata"]["reference_blueprint"] == blueprint
    assert "snapshot_hash" not in view["strategy_snapshot"]
    assert "candidate_comparison" not in view["evidence_bundle"]["items"][1]["metadata"]["selection_basis"]
    assert view["runtime_config_snapshot"]["content_rule_bundle"]["bundle_hash"] == "rules"
    assert "viral-modular-reviewer" in view["runtime_config_snapshot"]["content_rule_bundle"]["active_modules"]
    assert view["expression_guidance"] == payload["expression_guidance"]
    assert view["expression_policy"] == payload["expression_policy"]
    assert view["runtime_config_snapshot"]["required_review_codes"] == [
        "EMOJI_COVERAGE",
        "EMOJI_APPROPRIATENESS",
        "EMOJI_RESTRICTIONS",
        "PERSONA_OPENING",
        "PERSONA_CLOSING",
        "PERSONA_GROUNDING",
        "CREATION_TYPE_ALIGNMENT",
        "COMPOSITION_ALIGNMENT",
        "TITLE_ALIGNMENT",
        "BODY_VALUE",
        "NATURAL_EXPRESSION",
        "LAYOUT_READABILITY",
        "PLATFORM_CTA",
        "TOPIC_ALIGNMENT",
        "PRICE_SCOPE_ALIGNMENT",
    ]


def test_visual_projection_exposes_exact_locked_values_and_visual_evidence_allowlist():
    strategy_snapshot = {
        "content_direction": "CT02",
        "selected_group_id": "group-1",
        "creation_methods": ["M1"],
        "creation_method_definitions": [{"code": "M1", "name": "项目单价型", "variable_schema": []}],
        "title_formula": {"code": "T1"},
        "body_formula": {"code": "B1"},
        "rule_version_id": "rules-v3",
        "match_snapshot_id": "match-1",
        "formula_snapshot_id": "formula-1",
    }
    strategy_snapshot["snapshot_hash"] = hashlib.sha256(
        json.dumps(strategy_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    payload = {
        "selected_title": {"text": "长沙拆除单价"},
        "content_draft": {"body": "长沙旧房局改拆除单价参考。"},
        "strategy_snapshot": strategy_snapshot,
        "evidence_bundle": {
            "items": [
                {"id": "ev-body", "allowed_usage": ["body"]},
                {"id": "ev-visual", "allowed_usage": ["visual"]},
            ]
        },
        "media_evidence_items": [{"id": "asset-1", "selected_for_cover": True}],
        "artifact_version": {"id": "artifact-1"},
        "channel_profile": {},
        "runtime_config_snapshot": {
            "content_rule_bundle": {"bundle_version": "viral-modular-v1", "bundle_hash": "rules"}
        },
    }

    view = project_visual_input(
        payload,
        required_visual_intent="partial_renovation",
        required_source_asset_ids=("asset-1",),
        allowed_visual_evidence_ids=frozenset({"ev-visual"}),
    )

    assert view["required_visual_intent"] == "partial_renovation"
    assert view["required_source_asset_ids"] == ["asset-1"]
    assert view["allowed_visual_evidence_ids"] == ["ev-visual"]
    assert view["evidence_bundle"] == payload["evidence_bundle"]


def test_visual_projections_hide_composed_locked_quote_from_models():
    original = "拆除：1000元；水电：2400元"
    rendered = render_semicolon_lines(original)
    evidence_bundle = {
        "items": [
            {
                "id": "ev-quote",
                "variable_codes": ["quote_block"],
                "value": {"original_content": original},
                "allowed_usage": ["body"],
            }
        ]
    }
    strategy_snapshot = {
        "content_direction": "CT03",
        "selected_group_id": "group-1",
        "creation_methods": ["M1"],
        "creation_method_definitions": [{"code": "M1", "name": "单价面积型", "variable_schema": []}],
        "title_formula": {"code": "T1"},
        "body_formula": {"code": "B1"},
        "rule_version_id": "rules-v3",
        "match_snapshot_id": "match-1",
        "formula_snapshot_id": "formula-1",
    }
    strategy_snapshot["snapshot_hash"] = hashlib.sha256(
        json.dumps(strategy_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    visual_payload = {
        "selected_title": {"text": "长沙装修1.16w"},
        "content_draft": {"body": f"开场。\n\n{rendered}\n\n结尾。"},
        "strategy_snapshot": strategy_snapshot,
        "evidence_bundle": evidence_bundle,
        "media_evidence_items": [],
        "artifact_version": {"id": "artifact-1"},
        "channel_profile": {},
        "runtime_config_snapshot": {},
    }

    plan_view = project_visual_input(visual_payload)
    review_view = project_visual_review_input(
        {
            "selected_title": visual_payload["selected_title"],
            "content_draft": visual_payload["content_draft"],
            "visual_plan": {"cover": "quote"},
            "cover_job": {"id": "job-1"},
            "cover_assets": [{"id": "asset-1"}],
            "evidence_bundle": evidence_bundle,
        }
    )

    for view in (plan_view, review_view):
        serialized = json.dumps(view, ensure_ascii=False)
        assert original not in serialized
        assert rendered not in serialized
        assert "锁定报价块已由程序插入" in view["content_draft"]["body"]


def test_default_agent_projection_hides_server_snapshot_and_quote_evidence():
    original = "拆除：1000元；水电：2400元"
    payload = {
        "runtime_config_snapshot": {
            "creation_mode": "viral_rewrite",
            "trusted_external_material_snapshot": {"quote_block": {"original_content": original}},
        },
        "evidence_bundle": {
            "items": [
                {
                    "id": "ev-quote",
                    "variable_codes": ["quote_block"],
                    "value": {"original_content": original, "content_hash": "secret"},
                    "source_hash": "secret",
                }
            ]
        },
    }

    view = project_locked_quote_safe_input(payload)

    assert view is not None
    serialized = json.dumps(view, ensure_ascii=False)
    assert original not in serialized
    assert "trusted_external_material_snapshot" not in serialized
    assert "source_hash" not in serialized
