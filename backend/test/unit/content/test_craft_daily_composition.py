from copy import deepcopy

import pytest

from yuxi.content.model.strategy import build_strategy_candidates
from yuxi.content.control.workflow.creation_plan import build_fact_index, compile_production_order_and_manifest
from yuxi.content.v3.foreman_rules import import_foreman_rules
from test.unit.content.test_foreman_rule_catalog import _source_bundle


def candidates(direction="CT06"):
    return build_strategy_candidates(
        import_foreman_rules(_source_bundle()),
        industry_slug="decoration",
        direction_code=direction,
        rule_version_id="rules-test",
    )


def lock(values, direction="CT06", seed="task-a", catalog=None):
    from yuxi.content.v3.foreman_composition import lock_evidence_composition

    return lock_evidence_composition(
        catalog or candidates(direction),
        build_fact_index({"form_values": values}, {"items": []}),
        seed=seed,
    )


BASE = {
    "location": "长沙",
    "persona_fact": "装修工长，五年经验",
    "product": "泥瓦施工",
    "process": ["贴砖对缝"],
    "advantages": ["自有工人"],
    "craft_role": ["瓦工"],
}


def test_latest_rules_remove_deleted_formulas_and_upgrade_keeps_old_bundle_intact():
    from yuxi.content.v3.foreman_rules import upgrade_craft_daily_rules
    from yuxi.services.content_service import validate_rule_bundle_for_publish

    bundle = import_foreman_rules(_source_bundle())
    for section, retired, replacement in (
        ("title_formulas", {"FRT15", "FRT17", "FRT18", "FRT19"}, "FRT16"),
        ("content_formulas", {"FRB12"}, "FRB11"),
    ):
        assert not retired & {item["code"] for item in bundle[section]}
        template = next(item for item in bundle[section] if item["code"] == replacement)
        bundle[section].extend({**deepcopy(template), "code": code} for code in sorted(retired))
    for group in bundle["combination_rules"]:
        if group["content_type_codes"] == ["CT06"]:
            group["title_formula_candidate_codes"] = ["FRT15", "FRT17", "FRT16", "FRT18", "FRT19"]
        if group["content_type_codes"] in (["CT06"], ["CT07"]):
            group["body_formula_candidate_codes"] = [f"FRB{i:02d}" for i in range(11, 17)]
            group["hard_conditions"]["allowed_formula_pairs"] = [
                [title, body]
                for title in group["title_formula_candidate_codes"]
                for body in group["body_formula_candidate_codes"]
            ]
    original = deepcopy(bundle)

    upgraded = upgrade_craft_daily_rules(bundle)

    assert bundle == original
    assert not {"FRT15", "FRT17", "FRT18", "FRT19"} & {item["code"] for item in upgraded["title_formulas"]}
    assert "FRB12" not in {item["code"] for item in upgraded["content_formulas"]}
    for group in upgraded["combination_rules"]:
        if group["content_type_codes"] == ["CT06"]:
            assert group["title_formula_candidate_codes"] == ["FRT16"]
        if group["content_type_codes"] in (["CT06"], ["CT07"]):
            assert group["body_formula_candidate_codes"] == ["FRB11", "FRB13", "FRB14", "FRB15", "FRB16"]
            assert not any(
                title in {"FRT15", "FRT17", "FRT18", "FRT19"} or body == "FRB12"
                for title, body in group["hard_conditions"]["allowed_formula_pairs"]
            )
    assert validate_rule_bundle_for_publish(upgraded)["errors"] == []
    assert upgrade_craft_daily_rules(upgraded) == upgraded


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({}, "FRT16"),
        ({"craft_count": "18道工序", "result": "墙面验收通过"}, "FRT16"),
        ({"craft_duration": "12天", "result": "瓷砖铺贴完工"}, "FRT16"),
        ({"project": "卫生间", "location": "长沙"}, "FRT16"),
    ],
)
def test_craft_title_uses_only_supported_specific_facts(extra, expected):
    values = {**BASE, **extra, "number": "30", "duration": "5年"}
    catalog = lock(values)
    order, manifest = compile_production_order_and_manifest(
        task_id="task-a",
        catalog=catalog,
        fact_index=build_fact_index({"form_values": values}, {"items": []}),
    )
    assert order["title_formula_code"] == expected
    assert "number" not in {x["variable_code"] for x in manifest["requirements"]}
    assert "duration" not in {x["variable_code"] for x in manifest["requirements"]}


@pytest.mark.parametrize(("extra", "title"), [({}, "FRT13"), ({"inspection": "工地巡检"}, "FRT14")])
def test_daily_title_and_four_components_do_not_require_pain_or_result(extra, title):
    values = {**BASE, **extra}
    catalog = lock(values, "CT07")
    order, manifest = compile_production_order_and_manifest(
        task_id="task-a",
        catalog=catalog,
        fact_index=build_fact_index({"form_values": values}, {"items": []}),
    )
    assert order["title_formula_code"] == title
    assert order["body_formula_code"] == "FRB16"
    body = next(x for x in catalog["content_formulas"] if x["code"] == "FRB16")
    assert len(body["source_content"]["selected_components"]) == 4
    assert not {"pain", "result", "case_background"} & {
        x["variable_code"] for x in manifest["requirements"] if x["required"]
    }


def test_selection_is_frozen_when_extraction_adds_new_facts():
    frozen = lock(BASE)
    before = deepcopy(frozen)
    updated = lock(
        {**BASE, "craft_count": "18道工序", "result": "已验收", "case_background": "梅溪湖工地"}, catalog=frozen
    )
    assert updated == before


@pytest.mark.parametrize("direction", ["CT06", "CT07"])
def test_fixed_combinations_and_four_component_composition_are_reachable(direction):
    values = {
        **BASE,
        "case_background": "梅溪湖工地",
        "solution": "先排版再铺贴",
        "result": "已验收",
        "owner_need": "方便排水",
        "cost_explanation": "费用按面积计算",
    }
    selected = set()
    for i in range(80):
        catalog = lock(values, direction, seed=f"task-{i}")
        selected.add(catalog["source_rules"][0]["body_formula_candidate_codes"][0])
    assert selected == {"FRB11", "FRB13", "FRB14", "FRB15", "FRB16"}


def test_legacy_policy_is_unchanged():
    catalog = candidates("CT01")
    assert lock(BASE, catalog=catalog) == catalog


def test_upgrade_is_narrow_and_idempotent_after_database_normalization():
    from yuxi.content.schemas import RuleBundleUpdate
    from yuxi.content.v3.foreman_rules import upgrade_craft_daily_rules
    from yuxi.services.content_service import normalize_rule_bundle, validate_rule_bundle_for_publish

    before = import_foreman_rules(_source_bundle())
    before["methods"][0]["enabled"] = True
    before["methods"][0]["sort_order"] = 30
    after = upgrade_craft_daily_rules(before)
    for section, prefix, max_old in (
        ("methods", "FRM", 10),
        ("title_formulas", "FRT", 12),
        ("content_formulas", "FRB", 10),
    ):
        old_codes = {f"{prefix}{i:02}" for i in range(1, max_old + 1)}
        assert [x for x in after[section] if x["code"] in old_codes] == [
            x for x in before[section] if x["code"] in old_codes
        ]
    assert [x for x in after["combination_rules"] if x["content_type_codes"][0] not in {"CT06", "CT07"}] == [
        x for x in before["combination_rules"] if x["content_type_codes"][0] not in {"CT06", "CT07"}
    ]
    assert validate_rule_bundle_for_publish(after)["errors"] == []
    normalized = normalize_rule_bundle(RuleBundleUpdate(**after))
    assert upgrade_craft_daily_rules(normalized) == normalized


def test_four_component_shortage_reports_missing_facts_without_inventing_them():
    from yuxi.content.control.workflow.creation_plan import _resolve_rule_and_formulas

    values = {"location": "长沙", "persona_fact": "装修工长", "product": "泥瓦"}
    catalog = lock(values, "CT07")
    missing = _resolve_rule_and_formulas(catalog, build_fact_index({"form_values": values}, {"items": []}))[-1]
    assert missing
    assert "result" not in values and "process" not in values


@pytest.mark.asyncio
async def test_preparation_freezes_selection_through_plan_and_material_manifest(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from yuxi.content.control.workflow import creation_plan, joint_strategy
    from test.unit.content.test_creation_plan import reference, state

    current = state()
    current["content_brief"] = {"form_values": dict(BASE)}
    refs = [reference(content_type="CT06")]
    refs[0]["reference_card"]["required_slots"][0].update(slot_key="process", variable_codes=["process"])
    monkeypatch.setattr(
        joint_strategy,
        "prepare_strategy_candidates",
        AsyncMock(
            return_value={
                "strategy_catalog": candidates(),
                "reference_candidates": refs,
            }
        ),
    )
    prepared = await creation_plan.prepare_creation_plan_inputs(db=object(), state=current, node_run_id="prepare")
    assert prepared["production_order"] == {}
    assert prepared["creation_plan_gap_analysis"]["selection_pending"] is True
    current.update(prepared)
    current["extracted_creation_facts"] = {"facts": []}
    from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler

    monkeypatch.setattr(
        V3DeterministicNodeHandler,
        "_freeze_evidence_bundle",
        AsyncMock(
            return_value={
                "evidence_bundle": current["evidence_bundle"],
            }
        ),
    )
    prepared = await creation_plan.merge_extracted_creation_facts(db=object(), state=current, node_run_id="merge")
    current.update(prepared)
    current["content_brief"]["form_values"].update(craft_count="18道工序", result="验收通过")
    asset = SimpleNamespace(
        id="asset-a",
        article_id="a",
        kb_id="kb-a",
        file_id="file-a",
        status="ready",
        source_hash="a" * 64,
        preparation_skill_hash="skill-v2",
        source_json={"locator": "article:1"},
        prepared_json={"reference_card": refs[0]["reference_card"], "reference_blueprint": {}},
    )
    monkeypatch.setattr(creation_plan, "require_asset", AsyncMock(return_value=asset))
    monkeypatch.setattr(creation_plan, "check_asset_source", AsyncMock(return_value=True))
    monkeypatch.setattr(creation_plan, "preparation_skill_hash", lambda: "skill-v2")
    monkeypatch.setattr(creation_plan, "append_run_stream_event", AsyncMock())
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one=lambda: SimpleNamespace(uid="u"))))
    built = await creation_plan.build_creation_plan(db=db, state=current, node_run_id="build")
    assert built["production_order"] == prepared["production_order"]
    assert built["material_manifest"] == prepared["material_manifest"]
    body = built["strategy_snapshot"]["body_formula"]
    assert body["code"] == "FRB16"
    assert len(body["body_calling"]["sections"]) == 4
    assert len(body["source_content"]["selected_components"]) == 4


@pytest.mark.asyncio
async def test_plain_text_candidates_are_extracted_before_formula_is_frozen(monkeypatch):
    from unittest.mock import AsyncMock
    from yuxi.content.control.workflow import creation_plan, joint_strategy
    from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
    from test.unit.content.test_creation_plan import reference, state

    current = state()
    current["content_brief"] = {"user_request": "长沙工长，油工18道完整工序，墙面验收通过，自己带班。"}
    monkeypatch.setattr(
        joint_strategy,
        "prepare_strategy_candidates",
        AsyncMock(
            return_value={
                "strategy_catalog": candidates(),
                "reference_candidates": [reference(content_type="CT06")],
            }
        ),
    )
    prepared = await creation_plan.prepare_creation_plan_inputs(db=object(), state=current, node_run_id="prepare")
    gap = prepared["creation_plan_gap_analysis"]
    assert "process" in set(gap["candidate_variable_codes"]) | set(gap["missing_variable_codes"])
    assert not {"craft_count", "craft_duration"} & (
        set(gap["candidate_variable_codes"]) | set(gap["missing_variable_codes"])
    )
    assert prepared["production_order"] == {}
    current.update(prepared)
    facts = {
        "persona_fact": "工长",
        "craft_role": "油工",
        "process": "18道完整工序",
        "result": "墙面验收通过",
        "product": "油工",
    }
    current["extracted_creation_facts"] = {
        "facts": [{"variable_code": k, "value": v, "source_quote": v} for k, v in facts.items()]
    }
    monkeypatch.setattr(
        V3DeterministicNodeHandler,
        "_freeze_evidence_bundle",
        AsyncMock(
            return_value={
                "evidence_bundle": {
                    "items": [
                        {"variable_codes": [k], "value": v, "verified_status": "user_confirmed"}
                        for k, v in facts.items()
                    ]
                },
            }
        ),
    )
    merged = await creation_plan.merge_extracted_creation_facts(db=object(), state=current, node_run_id="merge")
    assert merged["production_order"]["title_formula_code"] == "FRT16"
    assert merged["creation_plan_gap_analysis"]["missing_variable_codes"] == []
    assert "case_background" not in facts and "craft_duration" not in facts
