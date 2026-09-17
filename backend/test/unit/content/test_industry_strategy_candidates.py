from copy import deepcopy

import pytest

from yuxi.content.model.strategy import build_strategy_candidates, load_selection_policy


def rule_bundle():
    return {
        "methods": [{"code": "M1"}, {"code": "M2"}, {"code": "M3", "enabled": False}],
        "title_formulas": [{"code": "T1"}, {"code": "T2"}, {"code": "T3", "enabled": False}],
        "content_formulas": [{"code": "C1"}, {"code": "C2"}],
        "combination_rules": [
            {
                "id": "G1",
                "industry_scope": ["decoration"],
                "content_type_codes": ["CT01"],
                "method_members": [{"method_code": "M1"}],
                "title_formula_candidate_codes": ["T2", "T1"],
                "body_formula_candidate_codes": ["C1"],
                "compatibility": "compatible",
            },
            {
                "id": "G2",
                "industry_scope": ["decoration"],
                "content_type_codes": ["CT02"],
                "method_members": [{"method_code": "M2"}],
                "title_formula_candidate_codes": ["T3"],
                "body_formula_candidate_codes": ["C2"],
                "compatibility": "compatible",
            },
            {
                "id": "G3",
                "industry_scope": ["education"],
                "content_type_codes": ["lesson"],
                "method_members": [{"method_code": "M2"}],
                "title_formula_candidate_codes": ["T1"],
                "body_formula_candidate_codes": ["C2"],
                "compatibility": "compatible",
            },
        ],
    }


def test_decoration_candidates_are_direction_scoped_and_methods_are_independent():
    source = rule_bundle()
    original = deepcopy(source)
    result = build_strategy_candidates(source, industry_slug="decoration", direction_code="CT01", rule_version_id="v1")
    assert result["strategy_mode"] == "direction_scoped"
    assert [item["code"] for item in result["title_formulas"]] == ["T1", "T2"]
    assert [item["code"] for item in result["content_formulas"]] == ["C1"]
    assert [item["code"] for item in result["methods"]] == ["M1", "M2"]
    assert "formula" not in result["scoring"]
    assert source == original


def test_other_industry_needs_no_decoration_direction():
    result = build_strategy_candidates(
        rule_bundle(), industry_slug="education", direction_code=None, rule_version_id="v1"
    )
    assert result["strategy_mode"] == "scored"
    assert result["direction_code"] is None
    assert [item["code"] for item in result["title_formulas"]] == ["T1"]
    assert [item["code"] for item in result["content_formulas"]] == ["C2"]
    assert "formula" in result["scoring"]


@pytest.mark.parametrize("direction", [None, "missing", "CT02"])
def test_decoration_missing_direction_or_empty_active_pool_is_explicit(direction):
    with pytest.raises(ValueError):
        build_strategy_candidates(
            rule_bundle(), industry_slug="decoration", direction_code=direction, rule_version_id="v1"
        )


def test_candidate_order_does_not_depend_on_configuration_order():
    source = rule_bundle()
    first = build_strategy_candidates(source, industry_slug="decoration", direction_code="CT01", rule_version_id="v1")
    for items in source.values():
        items.reverse()
    second = build_strategy_candidates(source, industry_slug="decoration", direction_code="CT01", rule_version_id="v1")
    assert first == second


def test_disabled_method_does_not_remove_otherwise_valid_formula_pool():
    source = rule_bundle()
    source["combination_rules"][0]["method_members"] = [{"method_code": "M3"}]
    result = build_strategy_candidates(source, industry_slug="decoration", direction_code="CT01", rule_version_id="v1")
    assert len(result["title_formulas"]) == 2
    assert "M3" not in {item["code"] for item in result["methods"]}


def test_explicit_formula_pairs_are_preserved():
    source = rule_bundle()
    source["combination_rules"][0]["hard_conditions"] = {
        "allowed_formula_pairs": [["T2", "C1"]],
    }
    result = build_strategy_candidates(source, industry_slug="decoration", direction_code="CT01", rule_version_id="v1")
    assert result["valid_formula_pairs"] == [["T2", "C1"]]


def test_unknown_industry_cannot_borrow_decoration_rules():
    with pytest.raises(ValueError, match="候选"):
        build_strategy_candidates(rule_bundle(), industry_slug="unknown", direction_code=None, rule_version_id="v1")


def test_policy_is_versioned_and_scales_sum_to_one_hundred():
    policy = load_selection_policy()
    assert policy["version"]
    assert len(policy["policy_hash"]) == 64
    assert all(sum(scale["weights"].values()) == 100 for scale in policy["scoring"].values())
