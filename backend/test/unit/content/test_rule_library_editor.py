from copy import deepcopy

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from yuxi.content.catalog import CONTENT_TYPES
from yuxi.content.rules import BODY_FORMULAS, METHODS, TITLE_FORMULAS
from yuxi.content.rule_library import active_combination_rules
from yuxi.content.schemas import CreationMethodInput, RuleBundleUpdate
from yuxi.content.v3.fixtures import load_decoration_matrix
from yuxi.services.content_service import normalize_rule_bundle, validate_rule_bundle_for_publish


def _bundle() -> dict:
    fixture = load_decoration_matrix()
    return {
        "methods": deepcopy(METHODS),
        "title_formulas": deepcopy(TITLE_FORMULAS),
        "content_formulas": deepcopy(BODY_FORMULAS),
        "content_types": deepcopy(CONTENT_TYPES),
        "combination_rules": [
            {
                **deepcopy(group),
                "schema_version": 3,
                "content_type_codes": [group["content_direction"]["code"]],
            }
            for group in fixture["groups"]
        ],
        "formula_patterns": [],
        "variables": [],
    }


def test_v3_seed_rule_bundle_is_publishable():
    validation = validate_rule_bundle_for_publish(_bundle())
    assert validation == {"errors": [], "warnings": []}


def test_publish_validation_reports_unknown_v3_references():
    bundle = _bundle()
    bundle["combination_rules"][0]["method_members"] = [{"method_code": "M99", "role": "primary", "order": 1}]
    validation = validate_rule_bundle_for_publish(bundle)
    assert {item["code"] for item in validation["errors"]} == {"V3_METHOD_MEMBERS_INVALID"}


def test_rule_bundle_normalization_trims_deduplicates_and_orders_items():
    bundle = _bundle()
    bundle["methods"][0]["code"] = "m01"
    bundle["methods"][0]["suitable_scenes"] = [" 案例复盘 ", "案例复盘", ""]
    payload = RuleBundleUpdate(changelog="  调整规则  ", **bundle)

    normalized = normalize_rule_bundle(payload)

    assert normalized["changelog"] == "调整规则"
    assert normalized["methods"][0]["code"] == "M01"
    assert normalized["methods"][0]["suitable_scenes"] == ["案例复盘"]
    assert [item["sort_order"] for item in normalized["methods"]] == list(range(len(METHODS)))
    assert {item["schema_version"] for item in normalized["combination_rules"]} == {3}


def test_rule_bundle_normalization_rejects_duplicate_codes():
    bundle = _bundle()
    bundle["methods"][1]["code"] = bundle["methods"][0]["code"]
    payload = RuleBundleUpdate(changelog="重复编码", **bundle)

    with pytest.raises(HTTPException) as exc_info:
        normalize_rule_bundle(payload)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"]["code"] == "CONTENT_RULE_CODE_DUPLICATED"


def test_rule_inputs_reject_whitespace_only_required_text():
    with pytest.raises(ValidationError):
        CreationMethodInput(code="M99", name="   ", principle="有效原则")


def test_disabled_formula_can_publish_without_removing_original_references():
    bundle = _bundle()
    bundle["content_formulas"][0]["enabled"] = False
    validation = validate_rule_bundle_for_publish(bundle)
    assert validation["errors"] == []
    assert validation["warnings"]
    assert "C01" in bundle["combination_rules"][4]["body_formula_candidate_codes"]


def test_combination_enabled_round_trips_through_input():
    bundle = _bundle()
    bundle["combination_rules"][0]["enabled"] = False
    normalized = normalize_rule_bundle(RuleBundleUpdate(**bundle))
    assert normalized["combination_rules"][0]["enabled"] is False


def test_active_candidates_exclude_disabled_rules_without_changing_source():
    bundle = _bundle()
    bundle["combination_rules"][0]["enabled"] = False
    bundle["title_formulas"][0]["enabled"] = False
    bundle["content_formulas"][0]["enabled"] = False
    original = deepcopy(bundle)
    active = active_combination_rules(bundle)
    assert bundle == original
    assert bundle["combination_rules"][0]["code"] not in {item["code"] for item in active}
    assert all("T01" not in item["title_formula_candidate_codes"] for item in active)
    assert all("C01" not in item["body_formula_candidate_codes"] for item in active)
    assert all(item["title_formula_candidate_codes"] and item["body_formula_candidate_codes"] for item in active)


def test_source_catalog_keeps_full_body_structure_and_feishu_candidate_order():
    import json
    from pathlib import Path

    catalog = json.loads(
        (Path(__file__).parents[3] / "package/yuxi/content/v3/fixtures/feishu_formula_catalog.json").read_text()
    )
    assert [item["name"] for item in catalog["title_formulas"]][5] == "地域+户型+数字反差"
    assert len(catalog["title_formulas"]) == 7
    assert len(catalog["content_formulas"]) == 4
    assert all(len(item["structure_schema"]) == 4 for item in catalog["content_formulas"])
    assert all(len(item["reference_examples"]) == 3 for item in catalog["content_formulas"])
    assert all(len(item["source_content"]["variables"]) == 3 for item in catalog["content_formulas"])
    matrix = load_decoration_matrix()
    assert matrix["groups"][2]["title_formula_candidate_codes"] == ["T03", "T05", "T07", "T02"]
    assert matrix["groups"][0]["scenario_description"] == "极简干货输出，主打专业工艺科普、塑造靠谱工长人设"
