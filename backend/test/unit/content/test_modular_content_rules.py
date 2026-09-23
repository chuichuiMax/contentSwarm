from pathlib import Path
import json

import pytest

from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
from yuxi.content.control.workflow.generation_input import project_generation_input
from yuxi.content.control.workflow.revision import resolve_revision_reason
from yuxi.content.model.contracts import ContractDomainContext, validate_content_node_result
from yuxi.content.model.contracts.content_nodes import ContractDomainValidationError
from yuxi.content.v3.modular_rules import (
    GENERATION_SKILLS,
    MODULAR_RULE_BUNDLE_VERSION,
    build_modular_rule_bundle,
    required_review_codes,
    requires_persona_review,
    select_modular_generation_skills,
)
from yuxi.content.validators import validate_modular_content


def _payload(*, price=False, report=None):
    evidence = []
    if price:
        evidence.append(
            {
                "id": "price-1",
                "variable_codes": ["price"],
                "metadata": {"material_type": "price", "price_basis": "project_quote"},
            }
        )
    return {
        "content_brief": {"form_values": {"city": "长沙"}},
        "strategy_snapshot": {"body_formula": {"code": "FRB01"}},
        "evidence_bundle": {"items": evidence},
        **({"validation_report": report} if report else {}),
    }


def test_rule_bundle_freezes_each_skill_hash_and_topic_pool():
    bundle = build_modular_rule_bundle({"form_values": {"city": "长沙"}})

    assert bundle["bundle_version"] == MODULAR_RULE_BUNDLE_VERSION
    assert len(bundle["bundle_hash"]) == 64
    assert {item["slug"] for item in bundle["modules"]} == {
        *GENERATION_SKILLS,
        "viral-modular-reviewer",
        "viral-cover-matcher",
    }
    assert all(len(item["content_hash"]) == 64 and len(item["rules_hash"]) == 64 for item in bundle["modules"])
    assert "长沙装修" in bundle["topic_candidates"]
    assert len(bundle["topic_candidates"]) >= 10
    assert bundle["runtime_rules"]["viral-author-core"]["reference_policy"] == {
        "required_slot_mode": "mapped_facts_only",
        "minimum_mapped_slots": 1,
        "unmapped_block_mode": "generalize_or_omit",
        "reference_facts_must_be_grounded": True,
    }
    assert bundle["runtime_rules"]["viral-price-author"]["price_policy"] == {
        "explicit_total_mode": "authoritative",
        "itemized_list_mode": "may_be_partial",
        "sum_claim_mode": "verified_equal_only",
    }


def test_first_generation_skips_price_skill_without_price_and_adds_it_with_price():
    without_price = select_modular_generation_skills(GENERATION_SKILLS, _payload())
    with_price = select_modular_generation_skills(GENERATION_SKILLS, _payload(price=True))

    assert "viral-price-author" not in without_price
    assert "viral-price-author" in with_price
    assert without_price[0] == with_price[0] == "viral-author-core"


def test_repair_routes_only_to_the_module_for_the_blocking_code():
    report = {
        "status": "blocked",
        "checks": [{"code": "TITLE_FORMULA_MISMATCH", "level": "error"}],
    }

    assert select_modular_generation_skills(GENERATION_SKILLS, _payload(report=report)) == (
        "viral-author-core",
        "viral-title-author",
    )


def test_standardized_review_only_requires_persona_when_manifest_declares_persona_materials():
    payload = {
        "production_pack": {
            "material_manifest": {"requirements": [{"variable_code": "product"}, {"variable_code": "price"}]}
        },
        "strategy_snapshot": {},
        "evidence_bundle": {"items": []},
        "content_brief": {"form_values": {}},
    }

    assert requires_persona_review(payload) is False
    assert not {"PERSONA_OPENING", "PERSONA_CLOSING", "PERSONA_GROUNDING"} & set(required_review_codes(payload))

    payload["production_pack"]["material_manifest"]["requirements"].append({"variable_code": "persona_fact"})

    assert requires_persona_review(payload) is True
    assert {"PERSONA_OPENING", "PERSONA_CLOSING", "PERSONA_GROUNDING"} <= set(required_review_codes(payload))


def test_standardized_review_follows_frozen_emoji_policy():
    payload = {
        "production_pack": {
            "material_manifest": {"requirements": []},
            "expression_policy": {"emoji_allowed": False, "required_categories": []},
        },
        "strategy_snapshot": {},
        "evidence_bundle": {"items": []},
        "content_brief": {"form_values": {}},
    }

    assert not {"EMOJI_COVERAGE", "EMOJI_APPROPRIATENESS", "EMOJI_RESTRICTIONS"} & set(required_review_codes(payload))


def test_hard_checks_cover_topic_cta_layout_and_price_scope():
    bundle = build_modular_rule_bundle({"form_values": {"city": "长沙"}})
    topics = bundle["topic_candidates"][:9] + [bundle["topic_candidates"][0]]
    checks = validate_modular_content(
        title="长沙蕞便宜装修",
        body="## 首先\n\n把户型发来，我帮你看。",
        topics=topics,
        draft={"paragraph_evidence": [{"paragraph_id": "p1", "evidence_ids": ["price-1"]}]},
        brief={"form_values": {"city": "长沙"}},
        evidence_bundle={
            "items": [
                {
                    "id": "price-1",
                    "metadata": {"material_type": "price", "city": "杭州"},
                }
            ]
        },
        rule_bundle=bundle,
    )

    codes = {item["code"] for item in checks}
    assert {
        "TOPIC_DUPLICATED",
        "CTA_TOO_DIRECT",
        "CONTENT_HIGH_RISK_CLAIM",
        "LAYOUT_MARKDOWN_FORBIDDEN",
        "PRICE_EVIDENCE_SCOPE_MISMATCH",
        "PRICE_CITY_MISMATCH",
    } <= codes


@pytest.mark.parametrize(
    "connector", ["首先", "其次", "综上", "值得注意的是", "通过以上内容", "下面来说", "接下来看看"]
)
def test_normal_connectors_do_not_trigger_mechanical_expression_check(connector):
    bundle = build_modular_rule_bundle({})
    checks = validate_modular_content(
        title="工长的施工记录",
        body=f"{connector}，水电定位要结合家具摆放确认。",
        topics=bundle["topic_candidates"][:10],
        draft={},
        brief={},
        evidence_bundle={"items": []},
        rule_bundle=bundle,
    )

    assert not any(item["code"] == "MECHANICAL_META_EXPRESSION" for item in checks)


@pytest.mark.parametrize("historical", [False, True])
def test_mechanical_phrase_match_is_advisory_under_new_rules(historical):
    bundle = build_modular_rule_bundle({})
    if historical:
        bundle["runtime_rules"]["viral-natural-expression"].pop("mechanical_marker_level", None)
    checks = validate_modular_content(
        title="工长的施工记录",
        body="先说背景，这次是旧房的水电改造。",
        topics=bundle["topic_candidates"][:10],
        draft={},
        brief={},
        evidence_bundle={"items": []},
        rule_bundle=bundle,
    )

    check = next(item for item in checks if item["code"] == "MECHANICAL_META_EXPRESSION")
    assert check["level"] == ("error" if historical else "warning")
    assert check["matched_terms"] == ["先说背景"]
    reason = resolve_revision_reason(
        title_validation_report=None, validation_report={"checks": checks}, review_report=None
    )
    assert reason == ("PERSONA_STYLE_FAILED" if historical else None)


@pytest.mark.parametrize("contract", ["ContentReviewResultV1", "StandardizedContentReviewResultV1"])
@pytest.mark.parametrize("serious", [False, True])
def test_natural_expression_advice_passes_contract_without_repair_but_serious_issues_block(contract, serious):
    status = "blocked" if serious else "warning"
    payload = {
        "status": status,
        "checks": [
            {
                "code": "NATURAL_EXPRESSION",
                "status": status,
                "location": "正文首句：目前资料里记录的技能是工长、水电、泥瓦",
                "message": "整篇以资料审核员身份分析作者" if serious else "个别措辞略显书面化，但身份一致、语义清楚",
                "suggestion": "用工长本人身份叙述" if serious else "可改为：我做过工长、水电、泥瓦",
                "evidence_ids": [],
            }
        ],
        "evidence_conflicts": [],
    }
    context = ContractDomainContext(required_modular_review_codes=frozenset({"NATURAL_EXPRESSION"}))

    validate_content_node_result(contract, payload, context)
    reason = resolve_revision_reason(title_validation_report=None, validation_report=None, review_report=payload)

    assert reason == ("PERSONA_STYLE_FAILED" if serious else None)


@pytest.mark.parametrize(
    "code", ["TITLE_ALIGNMENT", "BODY_VALUE", "LAYOUT_READABILITY", "PLATFORM_CTA", "TOPIC_ALIGNMENT"]
)
def test_other_required_dimensions_still_reject_warning(code):
    payload = {
        "status": "warning",
        "checks": [{"code": code, "status": "warning", "message": "未满足要求", "evidence_ids": []}],
        "evidence_conflicts": [],
    }
    context = ContractDomainContext(required_modular_review_codes=frozenset({code}))

    with pytest.raises(ContractDomainValidationError, match="不以 warning 放行"):
        validate_content_node_result("ContentReviewResultV1", payload, context)


@pytest.mark.asyncio
@pytest.mark.parametrize("hard_error", [False, True])
async def test_deterministic_style_advice_preserves_warning_and_does_not_mask_errors(monkeypatch, hard_error):
    monkeypatch.setattr(
        "yuxi.content.control.workflow.deterministic_node.validate_content",
        lambda **kwargs: {"status": "passed", "checks": []},
    )
    bundle = build_modular_rule_bundle({})
    result = await V3DeterministicNodeHandler._deterministic_validate(
        db=object(),
        node_run_id="node-style-advice",
        state={
            "selected_title": {"text": "工长的施工记录"},
            "content_brief": {},
            "evidence_bundle": {"items": []},
            "content_draft": {
                "body": "先说背景，这次是旧房改造。\n\n" + "水电定位结合家具摆放确认，施工按现场情况推进。\n\n" * 8,
                "topics": bundle["topic_candidates"][:10],
            },
            "runtime_config_snapshot": {"content_rule_bundle": bundle},
            "channel_result": {"checks": [{"code": "CHANNEL_TITLE_LONG", "level": "error"}] if hard_error else []},
        },
    )

    report = result["validation_report"]
    assert report["status"] == ("blocked" if hard_error else "warning")
    assert any(item["code"] == "MECHANICAL_META_EXPRESSION" and item["level"] == "warning" for item in report["checks"])
    reason = resolve_revision_reason(title_validation_report=None, validation_report=report, review_report=None)
    assert reason == ("TITLE_VALIDATION_FAILED" if hard_error else None)


@pytest.mark.asyncio
async def test_channel_adaptation_applies_versioned_problem_word_replacement():
    bundle = build_modular_rule_bundle({})
    result = await V3DeterministicNodeHandler._adapt_to_channel(
        db=object(),
        node_run_id="node-1",
        state={
            "selected_title": {"text": "最省心的装修"},
            "content_draft": {"body": "最后把范围核对清楚。", "topics": []},
            "channel_profile": {},
            "compliance_policies": [],
            "runtime_config_snapshot": {"content_rule_bundle": bundle},
        },
    )

    assert result["selected_title"]["text"] == "蕞省心的装修"
    assert result["content_draft"]["body"] == "蕞后把范围核对清楚。"
    assert result["channel_result"]["replacement_diffs"]


def test_generation_skill_prompt_budget_stays_below_approved_limit():
    root = Path(__file__).resolve().parents[3] / "package" / "yuxi" / "agents" / "skills" / "buildin"
    chars = {slug: len((root / slug / "SKILL.md").read_text(encoding="utf-8")) for slug in GENERATION_SKILLS}

    assert sum(value for slug, value in chars.items() if slug != "viral-price-author") <= 7000
    assert sum(chars.values()) <= 8000


def test_model_projection_omits_audit_hashes_and_inactive_price_rules():
    bundle = build_modular_rule_bundle({})
    payload = {
        "content_brief": {"form_values": {}, "business_variables": {}},
        "strategy_snapshot": {"title_formula": {}, "body_formula": {}},
        "formula_lexicon_bundle": {},
        "evidence_bundle": {"items": []},
        "channel_profile": {},
        "persona_profile": {},
        "runtime_config_snapshot": {
            "creation_mode": "viral_rewrite",
            "content_rule_bundle": bundle,
        },
    }

    projected = project_generation_input(payload)
    model_bundle = projected["runtime_config_snapshot"]["content_rule_bundle"]

    assert "modules" not in model_bundle
    assert "viral-price-author" not in model_bundle["active_modules"]
    assert len(json.dumps(model_bundle, ensure_ascii=False)) < len(json.dumps(bundle, ensure_ascii=False))


def test_modular_review_cannot_omit_a_required_quality_dimension():
    context = ContractDomainContext(required_modular_review_codes=frozenset({"NATURAL_EXPRESSION"}))
    payload = {"status": "passed", "checks": [], "evidence_conflicts": []}

    with pytest.raises(ContractDomainValidationError, match="NATURAL_EXPRESSION"):
        validate_content_node_result("ContentReviewResultV1", payload, context)


def test_modular_visual_plan_must_match_the_locked_content_intent():
    context = ContractDomainContext(
        artifact_version_id="artifact-v1",
        required_visual_intent="whole_house_quote",
    )
    payload = {
        "size": {"width": 1080, "height": 1440},
        "safe_area": {"top": 80, "right": 80, "bottom": 80, "left": 80},
        "text": [],
        "source_asset_ids": [],
        "mode": "template",
        "risks": [],
        "artifact_version_id": "artifact-v1",
        "evidence_ids": [],
        "visual_intent": "craft_detail",
        "selection_reason": "使用工艺近景",
    }

    with pytest.raises(ContractDomainValidationError, match="visual_intent"):
        validate_content_node_result("VisualPlanResultV1", payload, context)


@pytest.mark.parametrize("code", ["PERSONA_OPENING", "PERSONA_CLOSING", "NATURAL_EXPRESSION"])
@pytest.mark.parametrize("emoji_allowed", [True, False])
def test_standardized_repair_loads_expression_dependencies(code, emoji_allowed):
    payload = _payload()
    payload["review_report"] = {"status": "blocked", "checks": [{"code": code, "status": "blocked"}]}
    payload["production_pack"] = {
        "expression_policy": {
            "emoji_allowed": emoji_allowed,
            "required_categories": [{"code": "identity_trust"}] if emoji_allowed else [],
        }
    }
    skills = select_modular_generation_skills(GENERATION_SKILLS, payload)
    assert "viral-natural-expression" in skills
    assert ("viral-layout-expression" in skills) == (code.startswith("PERSONA_") or emoji_allowed)
