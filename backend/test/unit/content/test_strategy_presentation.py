from copy import deepcopy

from yuxi.services.content_strategy_presentation import build_decision_presentation


def test_presentation_uses_saved_names_and_exact_nested_evidence_without_mutating_decision():
    paths = ["content_brief.form_values.audience.0", "evidence_bundle.items.0.value.area"]
    decision = {
        "strategy": {"title_assessments": [{"input_paths": paths}], "body_assessments": [], "method_assessments": []},
        "reference": {"assessments": []},
    }
    inputs = {
        "strategy_candidates": {"title_formulas": [{"code": "T01", "name": "细分人群+数字+结果"}]},
        "reference_candidates": [{"id": "ref-a", "title": "收纳改造案例"}],
        "content_brief": {"form_values": {"audience": ["新手业主"]}},
        "evidence_bundle": {
            "items": [{"variable_codes": ["area"], "value": {"area": "89㎡"}, "source_type": "manual_input"}]
        },
    }
    original = deepcopy((decision, inputs))
    display = build_decision_presentation(decision, inputs)
    assert display["candidate_names"]["title_formulas"]["T01"] == "细分人群+数字+结果"
    assert display["candidate_names"]["references"]["ref-a"] == "收纳改造案例"
    assert display["input_evidence"][paths[0]] == {"key": "audience", "value": "新手业主"}
    assert display["input_evidence"][paths[1]]["value"] == "89㎡"
    assert display["input_evidence"][paths[1]]["variable_codes"] == ["area"]
    assert (decision, inputs) == original


def test_missing_historical_input_is_marked_unavailable():
    path = "content_brief.form_values.audience"
    decision = {
        "strategy": {"title_assessments": [{"input_paths": [path]}], "body_assessments": [], "method_assessments": []},
        "reference": {"assessments": []},
    }
    display = build_decision_presentation(decision, {})
    assert display["input_evidence"][path] is None
    assert display["candidate_names"]["title_formulas"] == {}
