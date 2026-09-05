from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import yuxi.services.content_service as content_service
from yuxi.content.control.industry.pack import (
    EvaluateIndustryPackRegressionHandler,
    ValidateIndustryPackHandler,
)
from yuxi.content.model.formulas.selector import (
    FormulaCandidateDefinition,
    FormulaCandidatePool,
    FormulaSelectionRequest,
    FormulaSelector,
)
from yuxi.content.model.industry.pack import IndustryPackPolicy, IndustryPackRegressionMetrics
from yuxi.content.schemas import IndustryPackRegressionSubmission, IndustryPackTransitionRequest
from yuxi.content.model.rules.engine import CombinationGroup, CombinationMatcher, MatchRequest
from yuxi.content.catalog import CONTENT_TYPES, INDUSTRY_CONFIG, VARIABLES, content_form_fields
from yuxi.content.rules import BODY_FORMULAS, INDUSTRIES, METHODS, TITLE_FORMULAS
from yuxi.content.v3.fixtures import load_decoration_matrix
from yuxi.content.v3.seed import GENERIC_DIRECTION_FORMULAS
from yuxi.storage.postgres.models_content import IndustryContentPackVersion


def _groups_for(slug: str) -> list[dict]:
    if slug == "decoration":
        return [
            {
                "id": group["code"],
                "schema_version": 3,
                "content_type_codes": [group["content_direction"]["code"]],
                "combination_type": group["combination_type"],
                "method_members": group["method_members"],
                "title_formula_candidate_codes": group["title_formula_candidate_codes"],
                "body_formula_candidate_codes": group["body_formula_candidate_codes"],
                "required_variable_codes": [],
                "source_metadata": group["source_metadata"],
            }
            for group in load_decoration_matrix()["groups"]
        ]
    groups = []
    for content_type in CONTENT_TYPES:
        code = content_type["code"]
        method, titles, bodies = GENERIC_DIRECTION_FORMULAS[code]
        groups.append(
            {
                "id": f"{slug}-{code.lower()}-platform-single-v3",
                "schema_version": 3,
                "content_type_codes": [code],
                "combination_type": "single",
                "method_members": [{"method_code": method, "role": "primary", "order": 1}],
                "title_formula_candidate_codes": titles,
                "body_formula_candidate_codes": bodies,
                "required_variable_codes": content_type["required_variable_codes"],
                "source_metadata": {"source": "published-v2-industry-migration", "industry": slug},
            }
        )
    return groups


def test_industry_pack_persistence_exposes_schema_version():
    column = IndustryContentPackVersion.__table__.c.schema_version
    assert column.nullable is False
    assert column.default.arg == 2


@pytest.mark.unit
def test_content_form_does_not_duplicate_agent_knowledge_configuration():
    for config in INDUSTRY_CONFIG.values():
        assert "knowledge_scope" not in {field["key"] for field in content_form_fields(config, pro=True)}


def _pack_record(slug: str, groups: list[dict]):
    source = next(item for item in INDUSTRIES if item["slug"] == slug)
    config = INDUSTRY_CONFIG[slug]
    aliases = {
        content_type["code"]: alias for content_type, alias in zip(CONTENT_TYPES, config["aliases"], strict=True)
    }
    first_group = {
        code: next(group["id"] for group in groups if code in group["content_type_codes"]) for code in aliases
    }
    return SimpleNamespace(
        id=f"industry-pack-{slug}-v3",
        slug=slug,
        version=3,
        schema_version=3,
        status="draft",
        name=f"{source['name']} V3",
        description=source["description"],
        content_type_aliases=aliases,
        lexicon_version_ids=[f"lexicon-{slug}-v1"],
        knowledge_scope=[],
        evidence_policy={"unsupported_numbers": "block"},
        compliance_policy={"unsupported_promises": "block"},
        persona_templates=[{"name": config["persona"]}],
        visual_policy={"styles": ["documentary"], "require_source_asset_provenance": True},
        golden_samples=[
            {
                "id": f"{slug}-{code}-golden",
                "content_direction_code": code,
                "input_variables": {"fixture": "reviewed-industry-sample"},
                "expected_group_id": group_id,
            }
            for code, group_id in first_group.items()
        ],
        negative_examples=[
            {
                "id": f"{slug}-{code}-missing-variable",
                "content_direction_code": code,
                "input_variables": {},
                "expected_error_code": "MISSING_REQUIRED_VARIABLE",
            }
            for code in aliases
        ],
        minimum_coverage=1.0,
        source_metadata={"source": "published-v2-industry-migration"},
        changelog="V3 行业包迁移",
        rollback_target_version_id=None,
    )


def _rule_bundle() -> dict:
    return {
        "methods": METHODS,
        "title_formulas": TITLE_FORMULAS,
        "content_formulas": BODY_FORMULAS,
        "variables": [{"code": item[0]} for item in VARIABLES],
    }


def _passing_regression_metrics() -> IndustryPackRegressionMetrics:
    return IndustryPackRegressionMetrics(
        rule_hit_rate=1,
        no_eligible_group_rate=0,
        evidence_missing_rate=0,
        cross_group_violation_rate=0,
        multi_formula_violation_rate=0,
        fact_citation_coverage=1,
        numeric_citation_coverage=1,
        deterministic_check_pass_rate=1,
        review_pass_rate=1,
        manual_reselection_rate=0.1,
        rework_rate=0.1,
        final_approval_rate=1,
        agent_success_rate=1,
        skill_success_rate=1,
        average_duration_ms=1200,
        average_token_count=800,
        average_cost=0.12,
        cover_job_success_rate=1,
        visual_review_pass_rate=1,
        cover_manual_reselection_rate=0.1,
    )


@pytest.mark.parametrize("slug", list(INDUSTRY_CONFIG))
def test_every_supported_industry_has_an_independent_valid_v3_pack(slug: str):
    groups = _groups_for(slug)
    mappings = [
        SimpleNamespace(
            field_key=field_key,
            variable_code=variable_code,
            transform_type="identity",
            transform_config={},
            required_by_content_types=[],
        )
        for field_key, _label, variable_code in INDUSTRY_CONFIG[slug]["fields"]
    ]
    report = ValidateIndustryPackHandler().execute(
        record=_pack_record(slug, groups),
        variable_mappings=mappings,
        combination_groups=groups,
        rule_bundle=_rule_bundle(),
    )
    assert report["validation"]["errors"] == []
    assert report["evaluation"]["passed"] is True
    assert len(groups) == (28 if slug == "decoration" else 7)
    if slug != "decoration":
        assert all(not group["id"].startswith("decoration-") for group in groups)


def test_non_decoration_canary_matches_one_group_and_selects_one_formula_pair():
    slug = "retail"
    mapping = _groups_for(slug)[4]
    group = CombinationGroup.from_mapping(
        {
            "code": mapping["id"],
            "content_direction_code": mapping["content_type_codes"][0],
            "content_direction_name": "产品能力",
            "industry_scope": [slug],
            **mapping,
        },
        rule_version_id="content-rules-platform-v3",
    )
    request = MatchRequest(
        content_direction_code="CT05",
        industry_slug=slug,
        available_variable_codes=frozenset(group.required_variable_codes),
    )
    match = CombinationMatcher().match([group], request)
    assert match.status == "matched"
    assert match.selected_group_code == group.code

    pool = FormulaCandidatePool(
        combination_group_id=group.code,
        rule_version_id=group.rule_version_id,
        title_formula_codes=group.title_formula_candidate_codes,
        body_formula_codes=group.body_formula_candidate_codes,
    )
    definitions = [
        FormulaCandidateDefinition(code=code, kind="title", rule_version_id=group.rule_version_id)
        for code in pool.title_formula_codes
    ] + [
        FormulaCandidateDefinition(code=code, kind="body", rule_version_id=group.rule_version_id)
        for code in pool.body_formula_codes
    ]
    selection = FormulaSelector().select(pool, definitions, FormulaSelectionRequest())
    assert selection.status == "selected"
    assert selection.selected_title_formula_code in pool.title_formula_codes
    assert selection.selected_body_formula_code in pool.body_formula_codes


def test_missing_industry_fields_are_blocked_without_decoration_fallback():
    mapping = _groups_for("retail")[4]
    group = CombinationGroup.from_mapping(
        {
            "code": mapping["id"],
            "content_direction_code": "CT05",
            "content_direction_name": "产品能力",
            "industry_scope": ["retail"],
            **mapping,
        },
        rule_version_id="content-rules-platform-v3",
    )
    decision = CombinationMatcher().match(
        [group],
        MatchRequest(content_direction_code="CT05", industry_slug="retail"),
    )
    assert decision.status == "blocked_by_rule"
    assert decision.rejected_groups[0].missing_variable_codes == tuple(sorted(group.required_variable_codes))
    assert all("decoration" not in item for item in decision.rejected_groups[0].missing_variable_codes)


def test_pack_lifecycle_requires_ordered_canary_and_supports_version_rollback():
    IndustryPackPolicy.assert_transition("draft", "validated")
    IndustryPackPolicy.assert_transition("validated", "canary")
    IndustryPackPolicy.assert_transition("canary", "published")
    IndustryPackPolicy.assert_transition("published", "deprecated")
    IndustryPackPolicy.assert_transition("deprecated", "published")
    with pytest.raises(ValueError, match="不允许"):
        IndustryPackPolicy.assert_transition("draft", "published")


def test_canary_regression_covers_all_required_quality_and_cost_metrics():
    groups = _groups_for("retail")
    structural = ValidateIndustryPackHandler().execute(
        record=_pack_record("retail", groups),
        variable_mappings=[
            SimpleNamespace(
                field_key=field_key,
                variable_code=variable_code,
                transform_type="identity",
                transform_config={},
                required_by_content_types=[],
            )
            for field_key, _label, variable_code in INDUSTRY_CONFIG["retail"]["fields"]
        ],
        combination_groups=groups,
        rule_bundle=_rule_bundle(),
    )
    result = EvaluateIndustryPackRegressionHandler().execute(
        pack=structural["pack"],
        metrics=_passing_regression_metrics(),
        source_run_ids=[f"retail-canary-run-{index}" for index in range(1, 8)],
        sample_count=7,
        candidate_recommendations=[{"type": "weight_candidate", "metric": "manual_reselection_rate"}],
    )
    assert result["passed"] is True
    assert result["failed_gates"] == []
    assert result["candidate_only"] is True
    assert result["pack_hash"] == structural["pack_hash"]
    assert set(result["metrics"]) == {
        "rule_hit_rate",
        "no_eligible_group_rate",
        "evidence_missing_rate",
        "cross_group_violation_rate",
        "multi_formula_violation_rate",
        "fact_citation_coverage",
        "numeric_citation_coverage",
        "deterministic_check_pass_rate",
        "review_pass_rate",
        "manual_reselection_rate",
        "rework_rate",
        "final_approval_rate",
        "agent_success_rate",
        "skill_success_rate",
        "average_duration_ms",
        "average_token_count",
        "average_cost",
        "cover_job_success_rate",
        "visual_review_pass_rate",
        "cover_manual_reselection_rate",
    }


def test_canary_regression_rejects_cross_group_or_multi_formula_violations():
    groups = _groups_for("retail")
    structural = ValidateIndustryPackHandler().execute(
        record=_pack_record("retail", groups),
        variable_mappings=[
            SimpleNamespace(
                field_key=field_key,
                variable_code=variable_code,
                transform_type="identity",
                transform_config={},
                required_by_content_types=[],
            )
            for field_key, _label, variable_code in INDUSTRY_CONFIG["retail"]["fields"]
        ],
        combination_groups=groups,
        rule_bundle=_rule_bundle(),
    )
    metrics = _passing_regression_metrics().model_copy(
        update={"cross_group_violation_rate": 0.01, "multi_formula_violation_rate": 0.01}
    )
    result = EvaluateIndustryPackRegressionHandler().execute(
        pack=structural["pack"],
        metrics=metrics,
        source_run_ids=[f"retail-canary-run-{index}" for index in range(1, 8)],
        sample_count=7,
        candidate_recommendations=[],
    )
    assert result["passed"] is False
    assert {item["metric"] for item in result["failed_gates"]} == {
        "cross_group_violation_rate",
        "multi_formula_violation_rate",
    }


@pytest.mark.asyncio
async def test_pack_publish_requires_a_passed_regression_report(monkeypatch: pytest.MonkeyPatch):
    record = SimpleNamespace(
        id="industry-pack-retail-v3",
        slug="retail",
        tenant_id=None,
        status="canary",
    )

    class FakeRepository:
        def __init__(self, db):
            del db

        async def get_industry_pack_for_update(self, version_id):
            assert version_id == record.id
            return record

    async def fake_validate(db, user, version_id, *, commit=True):
        del db, user, version_id, commit
        return {
            "validation": {"valid": True, "errors": [], "warnings": []},
            "evaluation": {"passed": True, "metrics": {}},
        }

    monkeypatch.setattr(content_service, "ContentRepository", FakeRepository)
    monkeypatch.setattr(content_service, "validate_content_industry_pack", fake_validate)

    with pytest.raises(HTTPException) as exc_info:
        await content_service.transition_content_industry_pack(
            SimpleNamespace(),
            SimpleNamespace(uid="admin"),
            record.id,
            IndustryPackTransitionRequest(target_status="published"),
        )
    assert exc_info.value.detail["error"]["code"] == "CONTENT_INDUSTRY_PACK_REGRESSION_REQUIRED"


@pytest.mark.asyncio
async def test_regression_submission_requires_auditable_completed_runs_for_all_directions(
    monkeypatch: pytest.MonkeyPatch,
):
    groups = _groups_for("retail")
    structural = ValidateIndustryPackHandler().execute(
        record=_pack_record("retail", groups),
        variable_mappings=[
            SimpleNamespace(
                field_key=field_key,
                variable_code=variable_code,
                transform_type="identity",
                transform_config={},
                required_by_content_types=[],
            )
            for field_key, _label, variable_code in INDUSTRY_CONFIG["retail"]["fields"]
        ],
        combination_groups=groups,
        rule_bundle=_rule_bundle(),
    )
    record = SimpleNamespace(
        id="industry-pack-retail-v3",
        slug="retail",
        tenant_id=None,
        status="canary",
        evaluation_report={},
    )
    run_ids = [f"retail-canary-run-{index}" for index in range(1, 8)]
    tracked: list[dict] = []

    class FakeRepository:
        def __init__(self, db):
            del db

        async def get_industry_pack_for_update(self, version_id):
            assert version_id == record.id
            return record

        async def list_industry_pack_canary_runs(self, version_id, requested_run_ids):
            assert version_id == record.id
            assert requested_run_ids == run_ids
            return [
                {"run_id": run_id, "status": "completed", "content_type_code": f"CT0{index}"}
                for index, run_id in enumerate(run_ids, start=1)
            ]

        async def track(self, event_name, **kwargs):
            tracked.append({"event_name": event_name, **kwargs})

    class FakeDB:
        async def commit(self):
            return None

    async def fake_validate(db, user, version_id, *, commit=True):
        del db, user, version_id, commit
        return structural

    monkeypatch.setattr(content_service, "ContentRepository", FakeRepository)
    monkeypatch.setattr(content_service, "validate_content_industry_pack", fake_validate)
    response = await content_service.submit_content_industry_pack_regression(
        FakeDB(),
        SimpleNamespace(uid="admin"),
        record.id,
        IndustryPackRegressionSubmission(
            source_run_ids=run_ids,
            sample_count=7,
            metrics=_passing_regression_metrics(),
            candidate_recommendations=[{"type": "weight_candidate"}],
        ),
    )
    assert response["report"]["regression"]["passed"] is True
    assert response["report"]["regression"]["candidate_only"] is True
    assert tracked[0]["event_name"] == "content_industry_pack_regression_submitted"
