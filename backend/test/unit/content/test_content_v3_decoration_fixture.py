from __future__ import annotations

import json

import pytest

from yuxi.content.v3.fixtures import (
    BODY_FORMULA_CODES,
    MATRIX_FIXTURE,
    SEMANTIC_LEXICON_FIXTURE,
    TITLE_FORMULA_CODES,
    FixtureValidationError,
    load_decoration_matrix,
    load_decoration_semantic_lexicons,
    validate_decoration_matrix,
    validate_decoration_semantic_lexicons,
)


def test_decoration_matrix_preserves_28_91_48_166_invariants() -> None:
    fixture = load_decoration_matrix()
    groups = fixture["groups"]

    direction_counts: dict[str, int] = {}
    for group in groups:
        code = group["content_direction"]["code"]
        direction_counts[code] = direction_counts.get(code, 0) + 1

    assert len(groups) == 28
    assert len({group["code"] for group in groups}) == 28
    assert len(direction_counts) == 7
    assert set(direction_counts.values()) == {4}
    assert sum(len(group["title_formula_candidate_codes"]) for group in groups) == 91
    assert sum(len(group["body_formula_candidate_codes"]) for group in groups) == 48
    assert (
        sum(
            len(group["title_formula_candidate_codes"]) * len(group["body_formula_candidate_codes"]) for group in groups
        )
        == 166
    )


def test_decoration_matrix_preserves_formula_membership_and_method_sizes() -> None:
    fixture = load_decoration_matrix()
    expected_sizes = {"single": 1, "double": 2, "triple": 3, "quadruple": 4}

    for group in fixture["groups"]:
        assert len(group["method_members"]) == expected_sizes[group["combination_type"]]
        assert set(group["title_formula_candidate_codes"]) <= TITLE_FORMULA_CODES
        assert set(group["body_formula_candidate_codes"]) <= BODY_FORMULA_CODES
        assert group["source_metadata"]["source_row"]
        assert group["scenario_description"]


def test_decoration_semantic_lexicons_are_expression_only() -> None:
    fixture = load_decoration_semantic_lexicons()
    categories = fixture["categories"]

    assert len(categories) == 34
    assert sum(category["scope"] == "title" for category in categories) == 14
    assert sum(category["scope"] != "title" for category in categories) == 20
    assert all(category["domain"] == "expression" for category in categories)
    assert all(category["evidence_eligible"] is False for category in categories)
    assert not {"户型", "面积", "材料品牌"} & {category["name"] for category in categories}


@pytest.mark.parametrize(
    ("fixture_path", "validator", "mutate"),
    [
        (MATRIX_FIXTURE, validate_decoration_matrix, lambda payload: payload["groups"].pop()),
        (
            MATRIX_FIXTURE,
            validate_decoration_matrix,
            lambda payload: payload["groups"][0]["title_formula_candidate_codes"].append("T99"),
        ),
        (
            MATRIX_FIXTURE,
            validate_decoration_matrix,
            lambda payload: payload["groups"][0].update({"combination_type": "double"}),
        ),
        (SEMANTIC_LEXICON_FIXTURE, validate_decoration_semantic_lexicons, lambda payload: payload["categories"].pop()),
    ],
)
def test_fixture_validator_rejects_mutated_source_data(fixture_path, validator, mutate) -> None:
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    mutate(payload)

    with pytest.raises(FixtureValidationError):
        validator(payload)
