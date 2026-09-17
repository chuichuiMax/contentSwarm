from copy import deepcopy

import pytest

from test.unit.content.test_industry_strategy_candidates import rule_bundle
from yuxi.content.model.contracts.strategy import StrategyDecisionV2, validate_strategy_decision
from yuxi.content.model.strategy import build_strategy_candidates


def example(industry="decoration"):
    candidates = build_strategy_candidates(
        rule_bundle(),
        industry_slug=industry,
        direction_code="CT01" if industry == "decoration" else None,
        rule_version_id="v1",
    )
    result = {
        "status": "selected",
        **{
            key: candidates[key]
            for key in ("industry_slug", "strategy_mode", "direction_code", "rule_version_id", "policy_hash")
        },
        "title_formula_code": "T1",
        "body_formula_code": "C1" if industry == "decoration" else "C2",
        "creation_method_codes": ["M2"],
        "reason": "输入目标与表达方式对应",
    }
    for output, section, scoring in (
        ("title_assessments", "title_formulas", "formula"),
        ("body_assessments", "content_formulas", "formula"),
        ("method_assessments", "methods", "method"),
    ):
        scale = candidates["scoring"].get(scoring)
        result[output] = [
            {
                "candidate_id": item["code"],
                "eligible": True,
                "reason": "适合说明本次痛点",
                "input_paths": ["content_brief.form_values.pain"],
                "dimensions": dict.fromkeys(scale["weights"], 4 if item["code"] == "M2" else 3) if scale else {},
                "total": (100 if item["code"] == "M2" else 75) if scale else None,
            }
            for item in candidates[section]
        ]
    return candidates, result


def validate(candidates, result):
    return validate_strategy_decision(
        result,
        candidates,
        content_brief={"form_values": {"pain": "选方案困难"}},
        evidence_bundle={"items": []},
    )


@pytest.mark.parametrize("industry", ["decoration", "education"])
def test_each_mode_accepts_its_contract_and_independent_method(industry):
    candidates, result = example(industry)
    decision = validate(candidates, result)
    assert decision.creation_method_codes == ["M2"]
    if industry == "decoration":
        assert all(item.total is None for item in decision.title_assessments)
    else:
        assert decision.direction_code is None
        assert decision.title_assessments[0].total == 75


@pytest.mark.parametrize(
    "key,value",
    [
        ("direction_code", "CT02"),
        ("industry_slug", "finance"),
        ("rule_version_id", "other"),
        ("policy_hash", "0" * 64),
        ("strategy_mode", "scored"),
    ],
)
def test_model_cannot_change_locked_scope(key, value):
    candidates, result = example()
    result[key] = value
    with pytest.raises(ValueError):
        validate(candidates, result)


def test_decoration_rejects_formula_scores():
    candidates, result = example()
    result["title_assessments"][0]["total"] = 90
    with pytest.raises(ValueError, match="不允许公式数值评分"):
        validate(candidates, result)


@pytest.mark.parametrize(
    "change",
    [
        "missing_dimension",
        "wrong_total",
        "out_of_range",
        "float",
        "missing_path",
        "empty_path",
        "duplicate",
        "missing_candidate",
    ],
)
def test_scored_mode_rejects_unverifiable_scores(change):
    candidates, result = example("education")
    score = result["title_assessments"][0]
    if change == "missing_dimension":
        del score["dimensions"]["goal"]
    elif change == "wrong_total":
        score["total"] = 99
    elif change == "out_of_range":
        score["dimensions"]["goal"] = 5
    elif change == "float":
        score["dimensions"]["goal"] = 3.5
    elif change == "missing_path":
        score["input_paths"] = ["content_brief.fake"]
    elif change == "empty_path":
        score["input_paths"] = []
    elif change == "duplicate":
        result["title_assessments"].append(deepcopy(score))
    elif change == "missing_candidate":
        result["title_assessments"] = []
    with pytest.raises(ValueError):
        validate(candidates, result)


def test_rejected_candidate_cannot_be_selected():
    candidates, result = example()
    result["title_assessments"][0]["eligible"] = False
    with pytest.raises(ValueError, match="合格候选"):
        validate(candidates, result)


def test_explicit_no_candidate_preserves_reason_without_fake_selection():
    candidates, result = example()
    result.update(
        status="no_candidate",
        title_formula_code=None,
        body_formula_code=None,
        creation_method_codes=[],
        unresolved_questions=["缺少真实分项金额"],
    )
    assert validate(candidates, result).status == "no_candidate"


def test_formula_pair_ranking_rejects_lower_score_even_when_first_in_list():
    source = rule_bundle()
    source["combination_rules"][2]["title_formula_candidate_codes"] = ["T1", "T2"]
    candidates, result = example("education")
    candidates = build_strategy_candidates(source, industry_slug="education", direction_code=None, rule_version_id="v1")
    better = deepcopy(result["title_assessments"][0])
    better.update(candidate_id="T2", total=100, dimensions=dict.fromkeys(better["dimensions"], 4))
    result["title_assessments"].append(better)
    with pytest.raises(ValueError, match="评分最高"):
        validate(candidates, result)
    result["title_formula_code"] = "T2"
    assert validate(candidates, result).title_formula_code == "T2"


def test_new_contract_accepts_non_decoration_direction_identifiers():
    _, result = example("education")
    result["direction_code"] = "lesson-analysis"
    assert StrategyDecisionV2.model_validate(result).direction_code == "lesson-analysis"


def test_primary_method_must_use_score_not_configuration_position():
    candidates, result = example()
    result["creation_method_codes"] = ["M1"]
    with pytest.raises(ValueError, match="主手法"):
        validate(candidates, result)


def test_tied_methods_report_exact_correction_without_changing_scores():
    candidates, result = example()
    for item in result["method_assessments"]:
        item["dimensions"] = dict.fromkeys(item["dimensions"], 3)
        item["total"] = 75
    scores = deepcopy(result["method_assessments"])
    with pytest.raises(ValueError, match=r"creation_method_codes\[0\] 应为 M1（75 分）"):
        validate(candidates, result)
    result["creation_method_codes"] = ["M1"]
    assert validate(candidates, result).creation_method_codes == ["M1"]
    assert result["method_assessments"] == scores


def test_runtime_contract_registry_uses_the_same_scope_and_score_validation():
    from yuxi.content.model.contracts import (
        ContractDomainContext,
        get_input_contract_model,
        validate_content_node_result,
    )

    candidates, result = example()
    input_payload = get_input_contract_model("SelectStrategyInputV2").model_validate(
        {
            "content_brief": {"form_values": {"pain": "选方案困难"}},
            "evidence_bundle": {"items": []},
            "strategy_candidates": candidates,
            "runtime_config_snapshot": {},
        }
    )
    context = ContractDomainContext(
        strategy_candidates=candidates,
        strategy_brief=input_payload.content_brief,
        strategy_evidence=input_payload.evidence_bundle,
    )
    assert validate_content_node_result("StrategyDecisionV2", result, context).title_formula_code == "T1"
    result["title_formula_code"] = "outside"
    with pytest.raises(ValueError, match="合格候选"):
        validate_content_node_result("StrategyDecisionV2", result, context)
