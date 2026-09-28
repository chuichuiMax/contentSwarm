from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.content.control.workflow.creation_plan import (
    analyze_plan_gaps,
    build_creation_plan,
    build_fact_index,
    compile_production_order_and_manifest,
    merge_extracted_creation_facts,
    preview_creation_plan,
    rank_reference_candidates,
)
from yuxi.content.control.errors import ContentApplicationError


def catalog():
    return {
        "industry_slug": "test-industry",
        "strategy_mode": "direction_scoped",
        "direction_code": "CT01",
        "rule_version_id": "rules-v1",
        "policy_hash": "p" * 64,
        "reference_candidate_limit": 10,
        "methods": [
            {
                "code": "M01",
                "name": "身份叙事",
                "method_type": "core",
                "principle": "真实介绍",
                "suitable_scenes": [],
                "sentence_patterns": [],
                "variable_schema": [],
                "risk_rules": [],
            }
        ],
        "variables": [
            {
                "code": "missing",
                "name": "缺失业务字段",
                "value_type": "string",
                "unit_schema": {},
                "evidence_policy": {"required": False},
                "sensitivity": "normal",
                "allowed_usages": ["title", "body"],
                "validation_schema": {},
            },
            {
                "code": "pain",
                "value_type": "string",
                "unit_schema": {},
                "evidence_policy": {"required": False},
                "sensitivity": "normal",
                "allowed_usages": ["title", "body"],
                "validation_schema": {},
            },
        ],
        "title_formulas": [
            {
                "code": "T_MISSING",
                "name": "缺资料公式",
                "variable_schema": ["missing"],
                "compatible_methods": ["M01"],
            },
            {
                "code": "T01",
                "name": "痛点标题",
                "variable_schema": ["pain"],
                "compatible_methods": ["M01"],
            },
        ],
        "content_formulas": [
            {
                "code": "B01",
                "name": "自我介绍正文",
                "structure_schema": ["身份", "优势"],
                "required_variables": ["pain"],
                "compatible_methods": ["M01"],
            }
        ],
        "source_rules": [
            {
                "id": "GROUP-CT01",
                "content_type_codes": ["CT01"],
                "method_members": [{"method_code": "M01", "role": "primary", "order": 1}],
                "title_formula_candidate_codes": ["T01", "T_MISSING"],
                "body_formula_candidate_codes": ["B01"],
                "required_variable_codes": ["pain"],
                "required_evidence_types": [],
                "source_metadata": {},
            }
        ],
    }


def reference(asset_id="asset-a", *, retrieval_score=1, content_type="CT01"):
    return {
        "id": asset_id,
        "source_hash": asset_id[0] * 64,
        "retrieval_score": retrieval_score,
        "reference_card": {
            "schema_version": 2,
            "content_type_code": content_type,
            "required_slots": [
                {
                    "slot_key": "pain",
                    "name": "用户痛点",
                    "variable_codes": ["pain"],
                    "match_mode": "all",
                    "evidence_required": False,
                    "required": True,
                }
            ],
        },
    }


def state():
    return {
        "task_id": "task-1",
        "run_id": "run-1",
        "uid": "user-1",
        "content_brief": {"form_values": {"pain": "担心报价不透明"}},
        "evidence_bundle": {"items": [], "bundle_hash": "e" * 64},
        "runtime_config_snapshot": {"creation_mode": "viral_rewrite"},
        "strategy_catalog": catalog(),
        "reference_candidates": [reference()],
    }


def test_fact_index_excludes_viral_reference_as_business_fact():
    index = build_fact_index(
        {"form_values": {"pain": "真实痛点"}},
        {
            "items": [
                {
                    "value": "爆款里的价格",
                    "variable_codes": ["price"],
                    "metadata": {"material_type": "viral_example"},
                }
            ]
        },
    )
    assert index["available_variable_codes"] == ["pain"]


def test_fact_index_treats_scalar_and_singleton_list_as_same_compiled_fact():
    index = build_fact_index(
        {
            "audience": ["长沙准备装修的业主"],
            "form_values": {"audience": "长沙准备装修的业主"},
        },
        {"items": []},
    )

    assert index["conflicting_variable_codes"] == []


def test_fact_index_still_blocks_distinct_compiled_facts():
    index = build_fact_index(
        {
            "audience": ["长沙准备装修的业主"],
            "form_values": {"audience": "北京准备装修的业主"},
        },
        {"items": []},
    )

    assert index["conflicting_variable_codes"] == ["audience"]


def test_formula_locks_configured_default_and_reports_missing_input_without_fallback():
    locked = catalog()
    locked["source_rules"][0]["title_formula_candidate_codes"] = ["T_MISSING", "T01"]
    result = analyze_plan_gaps(
        catalog=locked,
        references=[reference()],
        content_brief={"form_values": {"pain": "担心报价不透明"}},
        evidence_bundle={"items": []},
        runtime_config_snapshot={},
    )
    assert result["title_formula_available"] is True
    assert result["eligible_reference_ids"] == ["asset-a"]
    assert result["has_missing"] is True
    assert result["missing_variable_codes"] == ["missing"]
    assert result["missing_variable_definitions"] == [
        {"code": "missing", "name": "缺失业务字段", "value_type": "string"}
    ]


def test_production_order_and_manifest_keep_default_formula_when_material_is_missing():
    locked = catalog()
    locked["source_rules"][0]["title_formula_candidate_codes"] = ["T_MISSING", "T01"]
    order, manifest = compile_production_order_and_manifest(
        task_id="task-1",
        catalog=locked,
        fact_index=build_fact_index({"form_values": {"pain": "真实痛点"}}, {"items": []}),
    )

    assert order["title_formula_code"] == "T_MISSING"
    assert order["body_formula_code"] == "B01"
    assert {item["variable_code"] for item in manifest["requirements"]} == {"missing", "pain"}


def craft_catalog():
    from yuxi.content.v3.foreman_rules import load_foreman_rule_catalog

    rules = load_foreman_rule_catalog()
    craft = next(g for g in rules["combination_rules"] if g["content_type_codes"] == ["CT06"])
    craft["method_members"] = [{"method_code": "FRM04", "role": "primary", "order": 1}]
    craft["title_formula_candidate_codes"] = ["FRT05"]
    craft["body_formula_candidate_codes"] = ["FRB04"]
    craft["source_metadata"].pop("formula_selection_policy", None)
    return {
        **catalog(),
        "industry_slug": "decoration",
        "direction_code": "CT06",
        "methods": rules["methods"],
        "title_formulas": rules["title_formulas"],
        "content_formulas": rules["content_formulas"],
        "source_rules": rules["combination_rules"],
        "variables": [
            {
                "code": code,
                "name": "人设事实" if code == "persona_fact" else code,
                "value_type": "list" if code in {"process", "advantages", "advantage"} else "string",
            }
            for code in ("persona_fact", "product", "location", "process", "advantages", "advantage", "scene")
        ],
    }


@pytest.mark.parametrize("persona_source", ["missing", "brief", "evidence", "rejected"])
def test_craft_plan_extracts_manifest_persona_requirement(persona_source):
    brief = {
        "user_request": '{"persona":{"workYears":"5","introduction":"我从事装修行业五年了"}}',
        "form_values": {
            "product": "装修",
            "location": "长沙",
            "process": ["拆除"],
            "advantages": ["自有工人无转包"],
            "pain": "担心施工质量",
        },
    }
    evidence = {"items": []}
    if persona_source == "brief":
        brief["form_values"]["persona_fact"] = "我从事装修行业五年了"
    elif persona_source in {"evidence", "rejected"}:
        evidence["items"].append(
            {
                "variable_codes": ["persona_fact"],
                "value": "我从事装修行业五年了",
                "verified_status": "rejected" if persona_source == "rejected" else "user_confirmed",
            }
        )

    gaps = analyze_plan_gaps(
        catalog=craft_catalog(),
        references=[reference(content_type="CT06")],
        content_brief=brief,
        evidence_bundle=evidence,
        runtime_config_snapshot={},
    )

    missing = persona_source in {"missing", "rejected"}
    assert gaps["missing_variable_codes"] == (["persona_fact"] if missing else [])
    assert gaps["has_missing"] is missing
    assert gaps["missing_variable_definitions"] == (
        [{"code": "persona_fact", "name": "人设事实", "value_type": "string"}] if missing else []
    )
    assert gaps["price_research_questions"] == []


@pytest.mark.asyncio
async def test_build_plan_blocks_missing_manifest_persona_before_reference_loading():
    current = {
        **state(),
        "strategy_catalog": craft_catalog(),
        "content_brief": {
            "form_values": {
                "product": "装修",
                "location": "长沙",
                "process": ["拆除"],
                "advantages": ["自有工人无转包"],
            }
        },
    }

    with pytest.raises(ContentApplicationError, match="persona_fact") as caught:
        await build_creation_plan(db=SimpleNamespace(), state=current, node_run_id="node-1")

    assert caught.value.code == "CONTENT_PLAN_INPUT_MISSING"


@pytest.mark.parametrize("value_code", [None, "process", "advantage", "advantages"])
def test_persona_value_group_only_requests_one_missing_alternative(value_code):
    rules = catalog()
    rules["industry_slug"] = "decoration"
    rules["variables"].extend(
        {"code": code, "value_type": "string"}
        for code in ("persona_fact", "process", "advantage", "advantages", "result")
    )
    values = {"pain": "担心增项", "persona_fact": "我做装修五年了", "result": "节点验收通过"}
    if value_code:
        values[value_code] = "按节点验收"

    gaps = analyze_plan_gaps(
        catalog=rules,
        references=[reference()],
        content_brief={"form_values": values},
        evidence_bundle={"items": []},
        runtime_config_snapshot={},
    )

    if value_code:
        assert gaps["missing_variable_codes"] == []
    else:
        assert len(gaps["missing_variable_codes"]) == 1
        assert set(gaps["missing_variable_codes"]) <= {"process", "advantage", "advantages"}


def test_plan_does_not_request_program_derived_material_from_extractor():
    rules = catalog()
    rules["content_formulas"][0]["output_schema"] = {"deterministic_calculation_required": True}
    brief = {"form_values": {"pain": "担心增项"}}
    _, manifest = compile_production_order_and_manifest(
        task_id="task-1", catalog=rules, fact_index=build_fact_index(brief, {"items": []})
    )
    assert any(item["requirement_id"] == "derived:calculated_total" for item in manifest["requirements"])

    gaps = analyze_plan_gaps(
        catalog=rules,
        references=[reference()],
        content_brief=brief,
        evidence_bundle={"items": []},
        runtime_config_snapshot={},
    )

    assert gaps["missing_variable_codes"] == []


def test_reference_filter_and_tie_break_are_deterministic():
    index = build_fact_index({"form_values": {"pain": "真实痛点"}}, {"items": []})
    ranked = rank_reference_candidates(
        [
            reference("asset-b", retrieval_score=3),
            reference("asset-a", retrieval_score=3),
            reference("asset-z", retrieval_score=99, content_type="CT07"),
        ],
        direction_code="CT01",
        fact_index=index,
        runtime_config_snapshot={},
    )
    assert [item["id"] for item in ranked] == ["asset-a", "asset-b"]


def test_reference_context_match_breaks_tie_after_retrieval_score():
    index = build_fact_index(
        {"form_values": {"pain": "真实痛点", "scene": "老房翻新"}},
        {"items": []},
    )
    generic = reference("asset-a", retrieval_score=3)
    matched = reference("asset-b", retrieval_score=3)
    generic["reference_card"]["scene"] = "新房开工"
    matched["reference_card"]["scene"] = "长沙老房翻新"

    ranked = rank_reference_candidates(
        [generic, matched],
        direction_code="CT01",
        fact_index=index,
        runtime_config_snapshot={},
    )

    assert [item["id"] for item in ranked] == ["asset-b", "asset-a"]


def test_mapped_facts_only_allows_minimum_slots_without_all_required_slots():
    card = reference()["reference_card"]
    card["required_slots"].append(
        {
            "slot_key": "result",
            "name": "真实结果",
            "variable_codes": ["result"],
            "match_mode": "all",
            "evidence_required": True,
            "required": True,
        }
    )
    runtime = {
        "content_rule_bundle": {
            "runtime_rules": {
                "viral-author-core": {
                    "reference_policy": {"required_slot_mode": "mapped_facts_only", "minimum_mapped_slots": 1}
                }
            }
        }
    }

    ranked = rank_reference_candidates(
        [{**reference(), "reference_card": card}],
        direction_code="CT01",
        fact_index=build_fact_index({"form_values": {"pain": "真实痛点"}}, {"items": []}),
        runtime_config_snapshot=runtime,
    )

    assert ranked[0]["omitted_required_slots"] == ["result"]


@pytest.mark.asyncio
async def test_preview_allows_user_request_when_fact_extraction_can_complete_plan(monkeypatch):
    from yuxi.content.infrastructure.postgres import strategy_preview_repository
    from yuxi.repositories import content_repository
    from yuxi.services import content_viral_assets

    task = SimpleNamespace(
        workflow_version_id="content-workflow-standardized-factory-v1",
        brief_json={"user_request": "长沙业主最担心装修报价不透明", "form_values": {}},
        evidence_json={"items": []},
        runtime_config_snapshot_json={},
    )
    monkeypatch.setattr(
        content_repository,
        "ContentRepository",
        lambda _db: SimpleNamespace(get_task_for_user=AsyncMock(return_value=task)),
    )
    monkeypatch.setattr(
        strategy_preview_repository,
        "PostgresStrategyPreviewRepository",
        lambda _db: SimpleNamespace(load_candidates=AsyncMock(return_value={"strategy_candidates": catalog()})),
    )
    monkeypatch.setattr(content_viral_assets, "search_ready_viral_assets", AsyncMock(return_value=[reference()]))

    result = await preview_creation_plan(
        db=SimpleNamespace(),
        user=SimpleNamespace(uid="user-1", role="admin", department_id="dept-1"),
        task_id="task-1",
    )

    assert result["can_generate"] is True
    assert result["requires_fact_extraction"] is True
    assert result["plan"]["title_formula"]["code"] == "T01"
    assert result["plan"]["reference"] is None
    assert result["production_order"]["title_formula_code"] == "T01"


@pytest.mark.asyncio
async def test_preview_uses_runtime_policy_for_partial_reference_mapping(monkeypatch):
    from yuxi.content.infrastructure.postgres import strategy_preview_repository
    from yuxi.repositories import content_repository
    from yuxi.services import content_viral_assets

    candidate = reference()
    candidate["reference_card"]["required_slots"].append(
        {
            "slot_key": "result",
            "name": "真实结果",
            "variable_codes": ["result"],
            "match_mode": "all",
            "evidence_required": True,
            "required": True,
        }
    )
    task = SimpleNamespace(
        workflow_version_id="content-workflow-standardized-factory-v1",
        brief_json={"form_values": {"pain": "担心报价不透明"}},
        evidence_json={"items": []},
        runtime_config_snapshot_json={},
    )
    monkeypatch.setattr(
        content_repository,
        "ContentRepository",
        lambda _db: SimpleNamespace(get_task_for_user=AsyncMock(return_value=task)),
    )
    monkeypatch.setattr(
        strategy_preview_repository,
        "PostgresStrategyPreviewRepository",
        lambda _db: SimpleNamespace(load_candidates=AsyncMock(return_value={"strategy_candidates": catalog()})),
    )
    monkeypatch.setattr(content_viral_assets, "search_ready_viral_assets", AsyncMock(return_value=[candidate]))

    result = await preview_creation_plan(
        db=SimpleNamespace(),
        user=SimpleNamespace(uid="user-1", role="admin", department_id="dept-1"),
        task_id="task-1",
    )

    assert result["can_generate"] is True
    assert result["plan"]["reference"]["id"] == "asset-a"
    assert result["plan"]["reference"]["mapped_slots"] == ["pain"]


@pytest.mark.parametrize("wrong_type", ["CT02", "CT03", "CT04", "CT05"])
def test_price_reference_types_never_cross_match(wrong_type):
    ranked = rank_reference_candidates(
        [reference(content_type=wrong_type)],
        direction_code="CT02" if wrong_type != "CT02" else "CT03",
        fact_index=build_fact_index({"form_values": {"pain": "真实痛点"}}, {"items": []}),
        runtime_config_snapshot={},
    )
    assert ranked == []


@pytest.mark.asyncio
async def test_same_input_builds_same_plan_hash_without_joint_strategy_agent(monkeypatch):
    from yuxi.content.control.workflow import creation_plan

    asset = SimpleNamespace(
        id="asset-a",
        article_id="article-a",
        kb_id="kb-a",
        file_id="file-a",
        status="ready",
        source_hash="a" * 64,
        preparation_skill_hash="skill-v2",
        source_json={"locator": "article:1"},
        prepared_json={
            "schema_version": 2,
            "reference_card": reference()["reference_card"],
            "reference_blueprint": {"content_block_sequence": ["身份", "优势"]},
        },
    )
    monkeypatch.setattr(creation_plan, "require_asset", AsyncMock(return_value=asset))
    monkeypatch.setattr(creation_plan, "check_asset_source", AsyncMock(return_value=True))
    monkeypatch.setattr(creation_plan, "preparation_skill_hash", lambda: "skill-v2")
    monkeypatch.setattr(creation_plan, "append_run_stream_event", AsyncMock())
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one=lambda: SimpleNamespace(uid="user-1")))
    )

    results = [await build_creation_plan(db=db, state=state(), node_run_id=f"node-{index}") for index in range(20)]
    first = results[0]

    assert {result["strategy_snapshot"]["snapshot_hash"] for result in results} == {
        first["strategy_snapshot"]["snapshot_hash"]
    }
    assert first["formula_selection_snapshot"]["selected_title_formula_code"] == "T01"
    assert first["strategy_snapshot"]["decision"]["selection_mode"] == "deterministic"
    assert first["strategy_snapshot"]["planner_version"] == "deterministic_creation_plan_v1"
    assert len(first["strategy_snapshot"]["input_snapshot_hash"]) == 64
    selection_basis = first["viral_reference_selection"]["selection_basis"]
    assert selection_basis["input_variable_paths"]
    assert selection_basis["structure_fillability"]["unfilled_required_slots"] == []
    assert selection_basis["candidate_comparison"][0]["decision"] == "selected"
    assert "joint_strategy_decision" not in first
    assert first["production_order"]["title_formula_code"] == "T01"
    assert first["material_manifest"]["title_formula_code"] == "T01"


@pytest.mark.asyncio
async def test_changed_business_input_changes_plan_hash(monkeypatch):
    from yuxi.content.control.workflow import creation_plan

    asset = SimpleNamespace(
        id="asset-a",
        article_id="article-a",
        kb_id="kb-a",
        file_id="file-a",
        status="ready",
        source_hash="a" * 64,
        preparation_skill_hash="skill-v2",
        source_json={"locator": "article:1"},
        prepared_json={
            "schema_version": 2,
            "reference_card": reference()["reference_card"],
            "reference_blueprint": {"content_block_sequence": ["身份", "优势"]},
        },
    )
    monkeypatch.setattr(creation_plan, "require_asset", AsyncMock(return_value=asset))
    monkeypatch.setattr(creation_plan, "check_asset_source", AsyncMock(return_value=True))
    monkeypatch.setattr(creation_plan, "preparation_skill_hash", lambda: "skill-v2")
    monkeypatch.setattr(creation_plan, "append_run_stream_event", AsyncMock())
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one=lambda: SimpleNamespace(uid="user-1")))
    )
    changed = deepcopy(state())
    changed["content_brief"]["form_values"]["pain"] = "另一个真实痛点"

    first = await build_creation_plan(db=db, state=state(), node_run_id="node-1")
    second = await build_creation_plan(db=db, state=changed, node_run_id="node-2")

    assert first["strategy_snapshot"]["input_snapshot_hash"] != second["strategy_snapshot"]["input_snapshot_hash"]
    assert first["strategy_snapshot"]["snapshot_hash"] != second["strategy_snapshot"]["snapshot_hash"]


@pytest.mark.asyncio
async def test_build_blocks_missing_required_evidence(monkeypatch):
    plan_state = state()
    plan_state["strategy_catalog"] = deepcopy(plan_state["strategy_catalog"])
    plan_state["strategy_catalog"]["source_rules"][0]["required_evidence_types"] = ["project_quote"]
    from yuxi.content.control.workflow import creation_plan

    monkeypatch.setattr(creation_plan, "append_run_stream_event", AsyncMock())
    with pytest.raises(ContentApplicationError, match="缺少创作计划必需 Evidence"):
        await build_creation_plan(db=SimpleNamespace(), state=plan_state, node_run_id="node-1")


@pytest.mark.asyncio
async def test_extracted_fact_must_be_verbatim_user_input():
    extraction_state = {
        **state(),
        "content_brief": {
            "user_request": "长沙老房翻新，最担心增项",
            "form_values": {},
        },
        "creation_plan_gap_analysis": {"missing_variable_codes": ["pain"]},
        "extracted_creation_facts": {
            "facts": [
                {
                    "variable_code": "pain",
                    "value": "担心报价不透明",
                    "source_quote": "担心报价不透明",
                }
            ]
        },
    }

    with pytest.raises(ContentApplicationError, match="不是用户原文逐字引用"):
        await merge_extracted_creation_facts(db=SimpleNamespace(), state=extraction_state, node_run_id="node-1")


@pytest.mark.asyncio
async def test_merge_without_new_facts_uses_bound_evidence_handler():
    from yuxi.content.model.evidence import freeze_evidence_bundle

    extraction_state = {
        **state(),
        "creation_plan_gap_analysis": {"missing_variable_codes": []},
        "extracted_creation_facts": {"facts": []},
        "evidence_bundle": freeze_evidence_bundle(task_id="task-1", version=1, items=[]).model_dump(mode="json"),
    }

    result = await merge_extracted_creation_facts(
        db=SimpleNamespace(),
        state=extraction_state,
        node_run_id="node-1",
    )

    assert result["creation_plan_gap_analysis"]["has_missing"] is False


@pytest.mark.asyncio
async def test_verbatim_extracted_fact_is_frozen_and_recomputes_plan(monkeypatch):
    from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler

    freeze = AsyncMock(
        return_value={
            "evidence_bundle": {
                "items": [
                    {
                        "variable_codes": ["pain"],
                        "value": "最担心增项",
                        "verified_status": "user_confirmed",
                    }
                ],
                "bundle_hash": "f" * 64,
            }
        }
    )
    monkeypatch.setattr(V3DeterministicNodeHandler, "_freeze_evidence_bundle", freeze)
    extraction_state = {
        **state(),
        "content_brief": {
            "user_request": "长沙老房翻新，最担心增项",
            "form_values": {},
        },
        "creation_plan_gap_analysis": {"missing_variable_codes": ["pain"]},
        "extracted_creation_facts": {
            "facts": [
                {
                    "variable_code": "pain",
                    "value": "最担心增项",
                    "source_quote": "最担心增项",
                }
            ]
        },
    }

    result = await merge_extracted_creation_facts(
        db=SimpleNamespace(),
        state=extraction_state,
        node_run_id="node-1",
    )

    frozen_item = freeze.await_args.kwargs["state"]["evidence_collection"]["evidence_items"][0]
    assert frozen_item["value"] == "最担心增项"
    assert frozen_item["metadata"]["extraction_mode"] == "verbatim"
    assert result["creation_plan_gap_analysis"]["has_missing"] is False


@pytest.mark.asyncio
async def test_list_variable_accepts_multiple_distinct_verbatim_facts(monkeypatch):
    from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler

    freeze = AsyncMock(
        return_value={
            "evidence_bundle": {
                "items": [
                    {
                        "variable_codes": ["advantages"],
                        "value": "决策快效率高",
                        "verified_status": "user_confirmed",
                    },
                    {
                        "variable_codes": ["advantages"],
                        "value": "自有工人无转包",
                        "verified_status": "user_confirmed",
                    },
                ],
                "bundle_hash": "f" * 64,
            }
        }
    )
    monkeypatch.setattr(V3DeterministicNodeHandler, "_freeze_evidence_bundle", freeze)
    extraction_state = {
        **state(),
        "strategy_catalog": deepcopy(catalog()),
        "content_brief": {
            "user_request": '"serviceAdvantages": ["决策快效率高", "自有工人无转包"]',
            "form_values": {"pain": "担心报价不透明"},
        },
        "creation_plan_gap_analysis": {"missing_variable_codes": ["advantages"]},
        "extracted_creation_facts": {
            "facts": [
                {
                    "variable_code": "advantages",
                    "value": "决策快效率高",
                    "source_quote": "决策快效率高",
                },
                {
                    "variable_code": "advantages",
                    "value": "自有工人无转包",
                    "source_quote": "自有工人无转包",
                },
            ]
        },
    }
    extraction_state["strategy_catalog"]["variables"].append(
        {
            "code": "advantages",
            "value_type": "list",
            "unit_schema": {},
            "evidence_policy": {"required": False},
            "sensitivity": "normal",
            "allowed_usages": ["body"],
            "validation_schema": {},
        }
    )

    result = await merge_extracted_creation_facts(
        db=SimpleNamespace(),
        state=extraction_state,
        node_run_id="node-1",
    )

    frozen_items = freeze.await_args.kwargs["state"]["evidence_collection"]["evidence_items"]
    assert [item["value"] for item in frozen_items] == ["决策快效率高", "自有工人无转包"]
    assert result["creation_plan_gap_analysis"]["has_missing"] is False


@pytest.mark.asyncio
async def test_scalar_variable_still_rejects_multiple_facts():
    extraction_state = {
        **state(),
        "content_brief": {
            "user_request": "长沙老房翻新，我从事装修行业10年了",
            "form_values": {"pain": "担心报价不透明"},
        },
        "creation_plan_gap_analysis": {"missing_variable_codes": ["missing"]},
        "extracted_creation_facts": {
            "facts": [
                {"variable_code": "missing", "value": "长沙", "source_quote": "长沙"},
                {
                    "variable_code": "missing",
                    "value": "我从事装修行业10年了",
                    "source_quote": "我从事装修行业10年了",
                },
            ]
        },
    }

    with pytest.raises(ContentApplicationError, match="重复提交了单值变量"):
        await merge_extracted_creation_facts(
            db=SimpleNamespace(),
            state=extraction_state,
            node_run_id="node-1",
        )
