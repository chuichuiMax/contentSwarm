from copy import deepcopy

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from yuxi.content.catalog import CONTENT_TYPES
from yuxi.content.rules import BODY_FORMULAS, METHODS, TITLE_FORMULAS
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
