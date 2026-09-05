from __future__ import annotations

from itertools import product

import pytest

from yuxi.content.model.formulas.selector import (
    FormulaCandidateDefinition,
    FormulaCandidatePool,
    FormulaSelectionRequest,
    FormulaSelector,
)
from yuxi.content.v3.fixtures import load_decoration_matrix
from yuxi.content.v3.seed import PLATFORM_RULE_V3_ID
from yuxi.storage.postgres.models_content import ContentFormulaSelectionSnapshot


FIXTURE_GROUPS = load_decoration_matrix()["groups"]
FIXTURE_PAIRS = [
    (group, title_code, body_code)
    for group in FIXTURE_GROUPS
    for title_code, body_code in product(
        group["title_formula_candidate_codes"],
        group["body_formula_candidate_codes"],
    )
]


def _pool(group: dict | None = None, **overrides) -> FormulaCandidatePool:
    source = group or FIXTURE_GROUPS[0]
    values = {
        "combination_group_id": source["code"],
        "rule_version_id": PLATFORM_RULE_V3_ID,
        "title_formula_codes": tuple(source["title_formula_candidate_codes"]),
        "body_formula_codes": tuple(source["body_formula_candidate_codes"]),
    }
    values.update(overrides)
    return FormulaCandidatePool(**values)


def _definition(
    code: str,
    kind: str,
    *,
    enabled: bool = True,
    version: str = PLATFORM_RULE_V3_ID,
    variables: tuple[str, ...] = (),
    evidence: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
) -> FormulaCandidateDefinition:
    return FormulaCandidateDefinition(
        code=code,
        kind=kind,
        rule_version_id=version,
        enabled=enabled,
        required_variable_codes=variables,
        required_evidence_types=evidence,
        semantic_tags=tags,
    )


@pytest.mark.unit
@pytest.mark.parametrize(("group", "title_code", "body_code"), FIXTURE_PAIRS)
def test_all_166_fixture_pairs_are_valid_within_their_group(group, title_code, body_code):
    _pool(group).validate_pair(title_code, body_code)


@pytest.mark.unit
def test_cross_group_and_unknown_formulas_are_rejected():
    pool = _pool(FIXTURE_GROUPS[0])
    other = next(
        group for group in FIXTURE_GROUPS if set(group["title_formula_candidate_codes"]) - set(pool.title_formula_codes)
    )
    outside_title = next(
        code for code in other["title_formula_candidate_codes"] if code not in pool.title_formula_codes
    )

    with pytest.raises(ValueError, match="同一个命中组合组"):
        pool.validate_pair(outside_title, pool.body_formula_codes[0])
    with pytest.raises(ValueError, match="候选池外"):
        FormulaSelector().select(
            pool,
            [],
            FormulaSelectionRequest(agent_title_ranking=(outside_title,)),
        )


@pytest.mark.unit
def test_disabled_missing_evidence_and_wrong_version_are_hard_rejections():
    pool = _pool(
        title_formula_codes=("T01", "T02", "T03"),
        body_formula_codes=("C01",),
    )
    definitions = [
        _definition("T01", "title", enabled=False),
        _definition("T02", "title", evidence=("number",)),
        _definition("T03", "title", version="content-rules-platform-v3-other"),
        _definition("C01", "body"),
    ]

    decision = FormulaSelector().select(pool, definitions, FormulaSelectionRequest())

    assert decision.status == "blocked_by_formula"
    assert decision.selected_title_formula_code is None
    reasons = {item.formula_code: item.reasons for item in decision.rejected_title_formulas}
    assert reasons == {
        "T01": ("disabled",),
        "T02": ("missing_evidence",),
        "T03": ("rule_version_mismatch",),
    }


@pytest.mark.unit
def test_title_and_body_are_scored_independently_not_selected_by_array_position():
    pool = _pool(
        title_formula_codes=("T07", "T01"),
        body_formula_codes=("C04", "C02"),
    )
    definitions = [
        _definition("T07", "title"),
        _definition("T01", "title", tags=("audience",)),
        _definition("C04", "body"),
        _definition("C02", "body", tags=("case_material",)),
    ]
    request = FormulaSelectionRequest(
        title_signals=frozenset({"audience"}),
        body_signals=frozenset({"case_material"}),
    )

    decision = FormulaSelector().select(pool, list(reversed(definitions)), request)

    assert decision.status == "selected"
    assert decision.selected_title_formula_code == "T01"
    assert decision.selected_body_formula_code == "C02"
    assert decision.title_selection_reason
    assert decision.body_selection_reason


@pytest.mark.unit
def test_agent_can_only_reorder_eligible_fixed_candidates():
    pool = _pool(title_formula_codes=("T01", "T07"), body_formula_codes=("C01",))
    definitions = [
        _definition("T01", "title", tags=("audience",)),
        _definition("T07", "title"),
        _definition("C01", "body"),
    ]

    decision = FormulaSelector().select(
        pool,
        definitions,
        FormulaSelectionRequest(
            title_signals=frozenset({"audience"}),
            agent_title_ranking=("T07", "T01"),
        ),
    )

    assert decision.status == "selected"
    assert decision.selection_mode == "agent_assisted"
    assert decision.selected_title_formula_code == "T07"
    assert "固定校验" in decision.title_selection_reason


@pytest.mark.unit
def test_explicit_allowed_pairs_are_enforced_after_independent_scoring():
    pool = _pool(
        title_formula_codes=("T01", "T02"),
        body_formula_codes=("C01", "C02"),
        allowed_formula_pairs=frozenset({("T02", "C02")}),
    )
    definitions = [
        _definition("T01", "title", tags=("preferred",)),
        _definition("T02", "title"),
        _definition("C01", "body", tags=("preferred",)),
        _definition("C02", "body"),
    ]

    decision = FormulaSelector().select(
        pool,
        definitions,
        FormulaSelectionRequest(
            title_signals=frozenset({"preferred"}),
            body_signals=frozenset({"preferred"}),
        ),
    )

    assert (decision.selected_title_formula_code, decision.selected_body_formula_code) == ("T02", "C02")
    with pytest.raises(ValueError, match="不在允许配对"):
        pool.validate_pair("T01", "C01")


@pytest.mark.unit
def test_formula_snapshot_schema_has_independent_selection_fields_and_active_index():
    columns = ContentFormulaSelectionSnapshot.__table__.columns
    assert columns["selected_title_formula_code"].nullable is True
    assert columns["selected_body_formula_code"].nullable is True
    assert columns["match_snapshot_id"].nullable is False
    assert columns["evidence_bundle_hash"].nullable is False
    assert any(
        index.name == "uq_content_formula_snapshot_active_run" and index.unique
        for index in ContentFormulaSelectionSnapshot.__table__.indexes
    )


def test_fixture_pair_count_is_exactly_166():
    assert len(FIXTURE_PAIRS) == 166
