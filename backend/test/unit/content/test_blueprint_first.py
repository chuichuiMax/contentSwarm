from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test.unit.content.test_industry_strategy_candidates import rule_bundle
from test.unit.content.test_industry_strategy_decision import example
from yuxi.content.model.contracts.strategy import validate_strategy_decision
from yuxi.content.model.strategy import build_strategy_candidates
from yuxi.content.model.workflows.definition import WorkflowDefinitionPolicy
from yuxi.content.v3.joint_workflow import WORKFLOW_BLUEPRINT_FIRST, WORKFLOW_JOINT


def test_automatic_candidates_keep_direction_formula_boundaries():
    bundle = rule_bundle()
    bundle["title_formulas"][2]["enabled"] = True
    bundle["content_types"] = [{"code": "CT01", "name": "案例"}, {"code": "CT02", "name": "报价"}]
    before = deepcopy(bundle)
    candidates = build_strategy_candidates(
        bundle,
        industry_slug="decoration",
        direction_code=None,
        rule_version_id="v1",
        auto_direction=True,
    )
    assert candidates["direction_code"] is None
    assert candidates["direction_options"][0]["title_formula_codes"] == ["T1", "T2"]
    assert candidates["direction_options"][1]["title_formula_codes"] == ["T3"]
    assert candidates["direction_options"][1]["valid_formula_pairs"] == [["T3", "C2"]]
    assert "formula" not in candidates["scoring"]
    assert bundle == before


@pytest.mark.parametrize("invalid", [None, "direction", "formula", "pair"])
def test_agent_selects_direction_but_cannot_cross_its_formula_scope(invalid):
    candidates, result = example("decoration")
    code = result["direction_code"]
    candidates.update(
        auto_direction=True,
        direction_code=None,
        direction_options=[
            {
                "code": code,
                "title_formula_codes": [item["code"] for item in candidates["title_formulas"]],
                "body_formula_codes": [item["code"] for item in candidates["content_formulas"]],
                "valid_formula_pairs": candidates["valid_formula_pairs"],
            }
        ],
    )
    if invalid == "direction":
        result["direction_code"] = "CT99"
    elif invalid == "formula":
        candidates["direction_options"][0]["title_formula_codes"] = []
    elif invalid == "pair":
        candidates["direction_options"][0]["valid_formula_pairs"] = []
    kwargs = {"content_brief": {"form_values": {"pain": "痛点"}}, "evidence_bundle": {"items": []}}
    if invalid:
        with pytest.raises(ValueError):
            validate_strategy_decision(result, candidates, **kwargs)
    else:
        assert validate_strategy_decision(result, candidates, **kwargs).direction_code == code


def test_other_industries_still_score_without_decoration_directions():
    candidates = build_strategy_candidates(
        rule_bundle(),
        industry_slug="education",
        direction_code=None,
        rule_version_id="v1",
        auto_direction=True,
    )
    assert candidates["direction_options"] == []
    assert candidates["strategy_mode"] == "scored"
    assert candidates["direction_code"] is None
    assert candidates["scoring"]["formula"]


def test_blueprint_workflow_adds_no_extra_model_call_and_preserves_legacy():
    assert len(WORKFLOW_JOINT["nodes"]) == len(WORKFLOW_BLUEPRINT_FIRST["nodes"]) == 25
    assert WORKFLOW_JOINT["selection_policy"] == "agent_skill_v1"
    assert WORKFLOW_BLUEPRINT_FIRST["selection_policy"] == "blueprint_first_v1"
    assert not any(node["id"] == "assess_creation_materials" for node in WORKFLOW_BLUEPRINT_FIRST["nodes"])
    WorkflowDefinitionPolicy.validate(WORKFLOW_BLUEPRINT_FIRST)


@pytest.mark.asyncio
@pytest.mark.parametrize("uploaded_only", [False, True])
async def test_retrieval_uses_fact_values_and_returns_bounded_blueprint_cards(monkeypatch, uploaded_only):
    from yuxi.content.control.workflow import joint_strategy

    candidates = build_strategy_candidates(
        rule_bundle(),
        industry_slug="education",
        direction_code=None,
        rule_version_id="v1",
        auto_direction=True,
    )
    loader = AsyncMock(return_value={"strategy_candidates": candidates})
    monkeypatch.setattr(joint_strategy.PostgresStrategyPreviewRepository, "load_candidates", loader)
    search = AsyncMock(return_value=[{"id": "case-a"}])
    monkeypatch.setattr(joint_strategy, "search_ready_viral_assets", search)
    runtime_loader = AsyncMock(side_effect=AssertionError("自动爆款匹配不应读取智能体手动绑定知识库"))
    monkeypatch.setattr("yuxi.services.agent_runtime_service.resolve_agent_runtime_context", runtime_loader)
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalar_one=lambda: SimpleNamespace(uid="u", role="admin", department_id=1),
            )
        )
    )
    state = {
        "uid": "u",
        "task_id": "t",
        "content_brief": {"form_values": {"process": "真实改造过程", "missing": ""}},
        "evidence_bundle": {"items": []},
        "runtime_config_snapshot": {
            "workflow_version_id": "content-workflow-blueprint-first-v1",
            "creation_mode": "viral_rewrite",
        },
    }
    if uploaded_only:
        state["content_brief"]["form_values"] = {}
        state["evidence_bundle"]["items"] = [
            {"value": "真实改造过程"},
            {"value": "参考文章的无关报价", "evidence_type": "style_reference"},
        ]
    result = await joint_strategy.prepare_strategy_candidates(db=db, state=state, node_run_id="n")
    assert loader.call_args.kwargs["auto_direction"] is True
    assert search.call_args.kwargs["query"] == "真实改造过程"
    assert search.call_args.kwargs["include_structure"] is True
    assert search.call_count == 1
    assert "kb_ids" not in search.call_args.kwargs
    runtime_loader.assert_not_called()
    assert result["strategy_candidates"]["available_input_paths"] == [
        "evidence_bundle.items.0.value" if uploaded_only else "content_brief.form_values.process"
    ]
    assert result["reference_search_queries"] == ["真实改造过程"]


def test_disabled_direction_formulas_do_not_block_other_directions():
    bundle = rule_bundle()
    bundle["content_types"] = [{"code": "CT01", "name": "案例"}, {"code": "CT02", "name": "报价"}]
    candidates = build_strategy_candidates(
        bundle,
        industry_slug="decoration",
        direction_code=None,
        rule_version_id="v1",
        auto_direction=True,
    )
    assert [item["code"] for item in candidates["direction_options"]] == ["CT01"]


@pytest.mark.parametrize("direction", ["CT01", "CT02"])
def test_explicit_creation_type_limits_blueprint_candidates(direction):
    bundle = rule_bundle()
    bundle["title_formulas"][2]["enabled"] = True
    bundle["content_types"] = [{"code": "CT01", "name": "案例"}, {"code": "CT02", "name": "报价"}]
    candidates = build_strategy_candidates(
        bundle,
        industry_slug="decoration",
        direction_code=direction,
        rule_version_id="v1",
        auto_direction=True,
    )
    assert {item["code"] for item in candidates["direction_options"]} == {direction}
    assert all(direction in item["content_type_codes"] for item in candidates["source_rules"])
