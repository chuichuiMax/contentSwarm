import json
from copy import deepcopy
from pathlib import Path

import pytest

from yuxi.content.catalog import CONTENT_TYPES, INDUSTRY_CONFIG
from yuxi.content.industry_matrix import import_industry_matrix, resolve_industry_formula
from yuxi.content.model.rules.engine import CombinationGroup, CombinationMatcher, MatchRequest
from yuxi.content.rules import METHODS
from yuxi.content.schemas import RuleBundleUpdate
from yuxi.services.content_service import normalize_rule_bundle, validate_rule_bundle_for_publish


def source_bundle():
    from yuxi.content import industry_matrix

    source = json.loads((Path(industry_matrix.__file__).parent / "v3/fixtures/feishu_formula_catalog.json").read_text())
    return {**source, "methods": deepcopy(METHODS), "content_types": deepcopy(CONTENT_TYPES), "combination_rules": []}


def test_each_industry_has_distinct_candidates_for_all_seven_directions():
    bundle = import_industry_matrix(source_bundle())
    normalized = normalize_rule_bundle(RuleBundleUpdate(**bundle))
    assert validate_rule_bundle_for_publish(normalized)["errors"] == []
    groups = [
        CombinationGroup.from_mapping(
            {**row, "code": row["id"], "content_direction_code": row["content_type_codes"][0]}, rule_version_id="test"
        )
        for row in bundle["combination_rules"]
    ]
    assert len(groups) == 70
    assert len({row.scenario_description for row in groups}) == 70
    industry_signatures = set()
    for slug in set(INDUSTRY_CONFIG) - {"decoration"}:
        signature = []
        for direction in CONTENT_TYPES:
            match = CombinationMatcher().match(
                groups, MatchRequest(content_direction_code=direction["code"], industry_slug=slug)
            )
            assert len(match.eligible_groups) == 2
            codes = {row.group_code for row in match.eligible_groups}
            selected = [row for row in groups if row.code in codes]
            assert all(row.industry_scope == (slug,) for row in selected)
            variants = [
                (
                    tuple(m.method_code for m in row.method_members),
                    row.title_formula_candidate_codes,
                    row.body_formula_candidate_codes,
                )
                for row in selected
            ]
            assert variants[0] != variants[1]
            signature.extend(variants)
        industry_signatures.add(tuple(signature))
    assert len(industry_signatures) == 5


def test_import_preserves_decoration_and_custom_groups_and_does_not_duplicate():
    original = source_bundle()
    original["combination_rules"] = [
        {"id": "decoration", "industry_scope": ["decoration"], "source_metadata": {"source": "feishu"}},
        {"id": "custom", "industry_scope": ["food"], "source_metadata": {}},
        {"id": "old", "industry_scope": ["food"], "source_metadata": {"source": "published-v2-industry-migration"}},
    ]
    before = deepcopy(original)
    imported = import_industry_matrix(original)
    assert original == before
    assert imported["combination_rules"][:2] == before["combination_rules"][:2]
    assert len(imported["combination_rules"]) == 72
    assert import_industry_matrix(imported) == imported
    for section in ("title_formulas", "content_formulas"):
        for actual, expected in zip(imported[section], original[section], strict=True):
            assert actual["name"] == expected["name"]
            assert actual["reference_examples"] == expected["reference_examples"]


@pytest.mark.parametrize("slug", sorted(set(INDUSTRY_CONFIG) - {"decoration"}))
def test_runtime_uses_editable_general_formula_and_original_decorating_text_is_unchanged(slug):
    bundle = import_industry_matrix(source_bundle())
    for formula in bundle["title_formulas"] + bundle["content_formulas"]:
        original = deepcopy(formula)
        resolved = resolve_industry_formula(formula, industry_slug=slug, scenario="行业专属场景")
        assert resolved["code"] == formula["code"]
        assert resolved["source_content"]["scenario_description"] == "行业专属场景"
        assert not any(word in json.dumps(resolved, ensure_ascii=False) for word in ("工长", "户型", "装修", "施工"))
        if "structure_schema" in formula:
            assert len(resolved["structure_schema"]) == resolved["output_schema"]["sections"] == 4
        assert formula == original
        assert resolve_industry_formula(formula, industry_slug="decoration", scenario="") == original
    formula = bundle["content_formulas"][0]
    formula["source_content"]["cross_industry"]["structure_schema"][0] = "管理员编辑后的通用段落"
    assert (
        resolve_industry_formula(formula, industry_slug=slug, scenario="")["structure_schema"][0]
        == "管理员编辑后的通用段落"
    )


def test_cross_industry_application_cannot_publish_with_empty_structure():
    bundle = import_industry_matrix(source_bundle())
    bundle["content_formulas"][0]["source_content"]["cross_industry"]["structure_schema"] = []
    validation = validate_rule_bundle_for_publish(bundle)
    assert "CROSS_INDUSTRY_FORMULA_INVALID" in {item["code"] for item in validation["errors"]}


def test_agent_rule_tool_uses_same_industry_application_and_candidate_scope():
    from yuxi.agents.toolkits.content.tools import _filter_strategy_rule_bundle

    bundle = import_industry_matrix(source_bundle())
    filtered = _filter_strategy_rule_bundle(bundle, industry_slug="food", content_type_code="CT02")
    assert len(filtered["combination_rules"]) == 2
    assert {item["code"] for item in filtered["content_formulas"]} == {"C01", "C03"}
    for item in filtered["title_formulas"] + filtered["content_formulas"]:
        assert item["source_content"]["industry"] == "food"
        assert item["source_content"]["scenario_description"]
        assert not any(word in json.dumps(item, ensure_ascii=False) for word in ("工长", "户型", "装修", "施工"))
