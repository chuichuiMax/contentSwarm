from pathlib import Path
import json

import pytest

from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
from yuxi.content.control.workflow.generation_input import project_generation_input
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


def test_hard_checks_cover_topic_cta_layout_mechanical_and_price_scope():
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
        "MECHANICAL_META_EXPRESSION",
        "LAYOUT_MARKDOWN_FORBIDDEN",
        "PRICE_EVIDENCE_SCOPE_MISMATCH",
        "PRICE_CITY_MISMATCH",
    } <= codes


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
