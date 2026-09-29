import hashlib
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError
from yuxi.content.control.workflow.deterministic_node import _derive_formula_calculation_evidence
from yuxi.content.control.workflow.generation_input import project_generation_input
from yuxi.content.model.materials import (
    FrozenProductionPackV1,
    MaterialEnvelopeV2,
    MaterialRequirementManifestV1,
    MaterialRequirementV1,
    PriceFactPayloadV2,
    ProductionOrderV1,
    build_expression_policy,
    build_formula_lexicon_constraints,
    build_material_manifest,
    compile_generation_slots,
    freeze_production_pack,
    select_formula_lexicon_terms,
    standardize_evidence_materials,
    validate_material_gate,
)


def test_optional_title_slot_lexicon_can_remain_unselected_without_blocking_freeze():
    constraints = {
        "title": {"title.positive_result": []},
        "body": {"ending.quotation_cta": ["报价对比"]},
    }

    selection = select_formula_lexicon_terms(
        constraints,
        optional_title_codes=frozenset({"title.positive_result"}),
    )

    assert selection["title"] == {}
    assert selection["body"] == {"ending.quotation_cta": ["报价对比"]}

    with pytest.raises(ValueError, match="title.positive_result"):
        select_formula_lexicon_terms(constraints)


def _catalog():
    return {
        "industry_slug": "decoration",
        "direction_code": "CT02",
        "rule_version_id": "rules-v1",
        "policy_hash": "p" * 64,
        "methods": [
            {
                "code": "FRM06",
                "variable_schema": ["product", "price"],
                "compatible_methods": [],
            }
        ],
        "title_formulas": [
            {
                "code": "FRT07",
                "name": "项目单价标题",
                "variable_schema": ["product", "price"],
                "compatible_methods": ["FRM06"],
            },
            {
                "code": "FRT08",
                "name": "备用标题",
                "variable_schema": ["product"],
                "compatible_methods": ["FRM06"],
            },
        ],
        "content_formulas": [
            {
                "code": "FRB06",
                "name": "项目单价正文",
                "required_variables": ["product", "price", "quote_type"],
                "compatible_methods": ["FRM06"],
            }
        ],
        "variables": [
            {
                "code": "product",
                "value_type": "string",
                "unit_schema": {},
                "evidence_policy": {"required": False},
                "sensitivity": "normal",
                "allowed_usages": ["title", "body"],
                "validation_schema": {},
            },
            {
                "code": "price",
                "value_type": "money",
                "unit_schema": {"required": True, "allowed_units": ["元/㎡"]},
                "evidence_policy": {"required": True},
                "sensitivity": "high_risk",
                "allowed_usages": ["title", "body"],
                "validation_schema": {},
            },
            {
                "code": "quote_type",
                "value_type": "string",
                "unit_schema": {},
                "evidence_policy": {"required": True},
                "sensitivity": "high_risk",
                "allowed_usages": ["body"],
                "validation_schema": {},
            },
        ],
        "source_rules": [
            {
                "id": "GROUP-CT02",
                "content_type_codes": ["CT02"],
                "method_members": [{"method_code": "FRM06", "role": "primary", "order": 1}],
                "title_formula_candidate_codes": ["FRT07", "FRT08"],
                "body_formula_candidate_codes": ["FRB06"],
                "required_variable_codes": ["product", "price", "quote_type"],
                "required_evidence_types": [],
            }
        ],
    }


def _order() -> ProductionOrderV1:
    return ProductionOrderV1(
        task_id="task-1",
        industry_slug="decoration",
        content_type_code="CT02",
        group_id="GROUP-CT02",
        rule_version_id="rules-v1",
        policy_hash="p" * 64,
        creation_method_codes=("FRM06",),
        title_formula_code="FRT07",
        body_formula_code="FRB06",
        selection_mode="fixed",
        order_hash="o" * 64,
    )


def test_single_blueprint_manifest_is_independent_of_body_formula_and_optional_persona():
    catalog = _catalog()
    for code in ("persona_fact", "process", "advantages", "quote_block"):
        catalog["variables"].append({"code": code, "value_type": "string", "allowed_usages": ["body"]})
    first = build_material_manifest(catalog=catalog, order=_order(), single_blueprint=True)
    catalog["content_formulas"][0]["required_variables"] = ["persona_fact", "process"]
    catalog["methods"][0]["variable_schema"] = ["advantages"]
    catalog["source_rules"][0]["required_variable_codes"] = ["persona_fact", "process"]
    changed = build_material_manifest(catalog=catalog, order=_order(), single_blueprint=True)
    assert first.requirements == changed.requirements
    required = {r.variable_code for r in changed.requirements if r.required}
    assert required >= {"price", "quote_type", "product"}
    assert "quote_block" not in required
    assert not required & {"persona_fact", "process", "advantages"}
    assert not any("persona:value" in r.validation_schema.get("alternative_groups", []) for r in changed.requirements)


def test_single_blueprint_preserves_optional_confirmed_result_through_material_gate():
    catalog = _catalog()
    catalog["variables"].append(
        {
            "code": "result",
            "value_type": "string",
            "allowed_usages": ["body"],
            "evidence_policy": {"allowed_sources": ["manual_input"], "review_policy": "user_confirmed"},
        }
    )
    legacy = build_material_manifest(catalog=catalog, order=_order())
    assert "result" not in {r.variable_code for r in legacy.requirements}
    manifest = build_material_manifest(catalog=catalog, order=_order(), single_blueprint=True)
    requirement = next(r for r in manifest.requirements if r.variable_code == "result")
    assert not requirement.required
    evidence = {
        "items": [
            {
                "id": "completion",
                "variable_codes": ["result"],
                "value": "验收记录：保留原墙面，无新增铲墙施工。",
                "source_type": "manual_input",
                "source_id": "record",
                "source_version": "1",
                "verified_status": "user_confirmed",
                "allowed_usage": ["body"],
            }
        ]
    }
    materials = standardize_evidence_materials(evidence_bundle=evidence, manifest=manifest)
    assert any("result" in m.variable_codes for m in materials)
    report = validate_material_gate(manifest=manifest, materials=materials)
    assert any(b.requirement_id == "variable:result" for b in report.bindings)
    evidence["items"][0]["verified_status"] = "retrieved"
    report = validate_material_gate(
        manifest=manifest,
        materials=standardize_evidence_materials(
            evidence_bundle=evidence,
            manifest=manifest,
        ),
    )
    assert not any(b.requirement_id == "variable:result" for b in report.bindings)


def _business_material(code: str, value: object, *, approved: bool = True) -> MaterialEnvelopeV2:
    return MaterialEnvelopeV2.model_validate(
        {
            "schema_version": 2,
            "id": f"mat-{code}",
            "material_type": "business_fact",
            "variable_codes": [code],
            "payload": {"value": value},
            "source": {
                "source_type": "manual_input",
                "source_id": f"field_{code}",
                "source_version": "brief-v1",
                "source_hash": (code * 16)[:128],
            },
            "governance": {
                "review_status": "approved" if approved else "needs_review",
                "verified_status": "user_confirmed" if approved else "retrieved",
                "risk_level": "normal",
                "allowed_usage": ["title", "body"],
            },
        }
    )


def test_material_gate_requires_one_approved_material_per_title_alternative_group():
    common = {
        "material_types": ["business_fact"],
        "value_type": "string",
        "required": False,
        "allowed_sources": ["manual_input"],
        "allowed_usage": ["title"],
        "review_policy": "user_confirmed",
        "risk_level": "normal",
        "validation_schema": {"alternative_groups": ["title:area_or_house_type"]},
        "fallback_policy": "block",
    }
    manifest = MaterialRequirementManifestV1.model_validate(
        {
            "schema_version": 1,
            "order_hash": "o" * 64,
            "content_type_code": "CT03",
            "title_formula_code": "FRT01",
            "body_formula_code": "FRB07",
            "requirements": [
                {"requirement_id": "variable:quantity", "variable_code": "quantity", **common},
                {"requirement_id": "variable:product", "variable_code": "product", **common},
            ],
            "reference_required": True,
            "manifest_hash": "m" * 64,
        }
    )

    passed = validate_material_gate(manifest=manifest, materials=[_business_material("product", "三室二厅")])
    assert passed.status == "passed"
    assert [binding.requirement_id for binding in passed.bindings] == ["variable:product"]

    blocked = validate_material_gate(manifest=manifest, materials=[])
    assert blocked.status == "blocked"
    assert blocked.missing_requirement_ids == ("title:area_or_house_type",)
    assert blocked.issues[0].code == "MANIFEST_ALTERNATIVE_GROUP_MISSING"


def _price_material(*, confirmed: bool = True) -> MaterialEnvelopeV2:
    return MaterialEnvelopeV2.model_validate(
        {
            "schema_version": 2,
            "id": "mat-price",
            "material_type": "price_fact",
            "variable_codes": ["price", "quote_type"],
            "payload": {
                "quoted_value": "水电人工 120 元/㎡",
                "amounts": [120],
                "currency": "CNY",
                "unit": "元/㎡",
                "price_basis": "standard_unit_price",
                "scope": "水电人工",
            },
            "source": {
                "source_type": "knowledge_base",
                "source_id": "kb-price/chunk-1",
                "source_version": "v1",
                "source_hash": "a" * 64,
            },
            "governance": {
                "review_status": "approved" if confirmed else "needs_review",
                "verified_status": "user_confirmed" if confirmed else "retrieved",
                "risk_level": "high_risk",
                "allowed_usage": ["title", "body"],
            },
        }
    )


def _reference_material() -> MaterialEnvelopeV2:
    anchor = {"section": "body", "start": 0, "end": 4, "quote": "报价范围"}
    return MaterialEnvelopeV2.model_validate(
        {
            "schema_version": 2,
            "id": "mat-reference",
            "material_type": "viral_reference",
            "evidence_ids": ["ev-reference"],
            "payload": {
                "reference_asset_id": "asset-1",
                "reference_card": {
                    "schema_version": 2,
                    "content_type_code": "CT02",
                    "content_type_reason": "报价主题匹配",
                    "audience": "装修业主",
                    "scene": "水电报价",
                    "goal": "说明价格范围",
                    "channel": "小红书",
                    "summary": "解释报价范围和口径",
                    "required_slots": [
                        {
                            "slot_key": "price",
                            "name": "报价",
                            "description": "真实报价资料",
                            "variable_codes": ["price"],
                            "match_mode": "all",
                            "evidence_required": True,
                            "required": True,
                            "anchor": anchor,
                        }
                    ],
                    "anchors": [anchor],
                },
                "reference_blueprint": {"opening_hook": "先明确报价口径"},
                "slot_mapping": {"price": ["ev-price"]},
            },
            "source": {
                "source_type": "knowledge_base",
                "source_id": "asset-1",
                "source_version": "v1",
                "source_hash": "r" * 64,
            },
            "governance": {
                "review_status": "approved",
                "verified_status": "confirmed",
                "risk_level": "normal",
                "allowed_usage": ["style_reference"],
            },
        }
    )


def test_expression_policy_freezes_three_applicable_semantic_categories():
    policy = build_expression_policy(
        materials=[
            _business_material("product", "施工报价"),
            _business_material("persona_fact", "长沙工长，从业5年"),
            _business_material("process", "现场核对施工范围"),
            _price_material(),
        ],
        strategy_snapshot={"body_formula": {"output_schema": {"cta_required": True}}},
        channel_profile={"body_constraints": {"emoji_allowed": True}},
    )

    assert policy["minimum_semantic_categories"] == 3
    assert [item["code"] for item in policy["required_categories"]] == [
        "verified_data",
        "identity_trust",
        "process_action",
    ]


def test_generation_slots_compile_review_items_and_grounded_persona_sources():
    manifest = MaterialRequirementManifestV1(
        order_hash="o" * 64,
        content_type_code="CT06",
        title_formula_code="FRT05",
        body_formula_code="FRB04",
        requirements=tuple(
            MaterialRequirementV1(
                requirement_id=f"variable:{code}",
                variable_code=code,
                material_types=("business_fact",),
                value_type="string",
                required=True,
                allowed_sources=("manual_input",),
                allowed_usage=("body",),
                review_policy="user_confirmed",
                risk_level="normal",
            )
            for code in ("persona_fact", "advantages", "process")
        ),
        manifest_hash="m" * 64,
    )
    materials = [
        _business_material("persona_fact", "长沙装修工长，从业5年"),
        _business_material("advantages", ["决策快", "自有工人"]),
        _business_material("process", "现场逐项核对施工节点"),
    ]
    slots = compile_generation_slots(
        material_manifest=manifest,
        material_quality_report=validate_material_gate(manifest=manifest, materials=materials),
        materials=materials,
        strategy_snapshot={
            "title_formula": {"variable_schema": ["persona_fact"]},
            "body_formula": {"required_variables": ["advantages", "process"]},
        },
        expression_policy={
            "emoji_allowed": True,
            "required_categories": [
                {"code": "identity_trust", "semantic_role": "身份与可信依据", "target": "身份旁"},
                {"code": "process_action", "semantic_role": "施工动作", "target": "动作旁"},
                {"code": "result_benefit", "semantic_role": "服务价值", "target": "价值旁"},
            ],
        },
        channel_profile={"code": "xiaohongshu"},
        content_rule_bundle={"runtime_rules": {"viral-topic-author": {"topic_count": 10}}},
    )

    by_id = {item.slot_id: item for item in slots}
    assert {"persona_identity", "persona_value", "emoji:identity_trust", "emoji:process_action", "topics"} <= set(by_id)
    assert by_id["persona_identity"].source_variable_codes == ("persona_fact",)
    assert "PERSONA_GROUNDING" in by_id["persona_value"].review_codes
    assert "EMOJI_COVERAGE" in by_id["emoji:identity_trust"].review_codes
    assert "CHANNEL_TOPIC_COUNT" in by_id["topics"].review_codes
    assert by_id["topics"].target == "topics"


def test_price_material_requires_numeric_amount_currency_unit_and_scope():
    payload = _price_material().model_dump(mode="json")
    del payload["payload"]["unit"]

    with pytest.raises(ValidationError, match="unit"):
        MaterialEnvelopeV2.model_validate(payload)


def test_manifest_uses_locked_default_formula_even_when_its_material_is_missing():
    manifest = build_material_manifest(catalog=_catalog(), order=_order())

    assert manifest.title_formula_code == "FRT07"
    assert manifest.body_formula_code == "FRB06"
    assert {item.variable_code for item in manifest.requirements} == {"product", "price", "quote_type"}
    assert next(item for item in manifest.requirements if item.variable_code == "price").review_policy == "human_review"


def test_manifest_preserves_title_one_of_slots_as_alternative_groups():
    catalog = deepcopy(_catalog())
    catalog["methods"][0]["variable_schema"] = []
    catalog["content_formulas"][0]["required_variables"] = ["quote_type"]
    catalog["source_rules"][0]["required_variable_codes"] = ["quote_type"]
    catalog["title_formulas"][0]["source_content"] = {
        "slot_schema": [
            {
                "code": "project_or_price",
                "label": "项目/价格",
                "variable_codes": ["product", "price"],
                "lexicon_codes": [],
            }
        ]
    }

    manifest = build_material_manifest(catalog=catalog, order=_order())
    requirements = {item.variable_code: item for item in manifest.requirements}

    assert requirements["product"].required is False
    assert requirements["price"].required is False
    assert requirements["product"].validation_schema["alternative_groups"] == ["title:project_or_price"]
    assert requirements["price"].validation_schema["alternative_groups"] == ["title:project_or_price"]
    assert requirements["quote_type"].required is True


def test_frt06_business_or_case_slot_does_not_force_result_material():
    catalog = deepcopy(_catalog())
    catalog["title_formulas"][0]["code"] = "FRT06"
    catalog["variables"].append(
        {
            "code": "result",
            "value_type": "string",
            "unit_schema": {},
            "evidence_policy": {"required": True},
            "sensitivity": "high_risk",
            "allowed_usages": ["title", "body"],
            "validation_schema": {},
        }
    )
    order = _order().model_copy(update={"title_formula_code": "FRT06"})

    manifest = build_material_manifest(catalog=catalog, order=order)

    assert "result" not in {item.variable_code for item in manifest.requirements}


def test_formula_fact_requirements_do_not_leak_across_industry_packs():
    catalog = deepcopy(_catalog())
    catalog["industry_slug"] = "test-industry"
    catalog["title_formulas"][0]["code"] = "T01"
    order = _order().model_copy(update={"industry_slug": "test-industry", "title_formula_code": "T01"})

    manifest = build_material_manifest(catalog=catalog, order=order)

    assert "result" not in {item.variable_code for item in manifest.requirements}


def test_frt01_does_not_add_a_separate_scene_requirement_outside_its_slot_schema():
    catalog = deepcopy(_catalog())
    catalog["title_formulas"][0]["code"] = "FRT01"
    catalog["variables"].append(
        {
            "code": "scene",
            "value_type": "string",
            "unit_schema": {},
            "evidence_policy": {"required": False},
            "sensitivity": "normal",
            "allowed_usages": ["title", "body"],
            "validation_schema": {},
        }
    )
    order = _order().model_copy(update={"title_formula_code": "FRT01"})

    manifest = build_material_manifest(catalog=catalog, order=order)

    assert "scene" not in {item.variable_code for item in manifest.requirements}


@pytest.mark.parametrize(
    ("body_formula_code", "expected_fact_codes"),
    [
        ("C01", {"advantages"}),
        ("C02", {"pain", "advantages", "persona_fact"}),
        ("FRB01", {"advantages"}),
        ("FRB02", {"pain", "advantages", "persona_fact"}),
        ("FRB03", {"advantages"}),
        ("FRB09", {"advantages"}),
    ],
)
def test_decoration_body_formula_manifest_declares_fact_bound_lexicon_materials(
    body_formula_code,
    expected_fact_codes,
):
    catalog = deepcopy(_catalog())
    catalog["content_formulas"][0]["code"] = body_formula_code
    catalog["variables"].extend(
        {
            "code": code,
            "value_type": "list" if code in {"pain", "advantages"} else "string",
            "unit_schema": {},
            "evidence_policy": {"required": False},
            "sensitivity": "normal",
            "allowed_usages": ["title", "body"],
            "validation_schema": {},
        }
        for code in ("pain", "advantages", "persona_fact")
    )
    order = _order().model_copy(update={"body_formula_code": body_formula_code})

    manifest = build_material_manifest(catalog=catalog, order=order)
    manifest_codes = {item.variable_code for item in manifest.requirements}

    assert expected_fact_codes <= manifest_codes


def test_decoration_quantity_manifest_requires_square_meter_unit():
    catalog = deepcopy(_catalog())
    catalog["title_formulas"][0]["variable_schema"].append("quantity")
    catalog["variables"].append(
        {
            "code": "quantity",
            "value_type": "number",
            "unit_schema": {},
            "evidence_policy": {"required": True},
            "sensitivity": "sensitive",
            "allowed_usages": ["title", "body"],
            "validation_schema": {},
        }
    )

    manifest = build_material_manifest(catalog=catalog, order=_order())
    quantity = next(item for item in manifest.requirements if item.variable_code == "quantity")

    assert quantity.unit_schema == {"required": True, "allowed_units": ["㎡"]}


def test_decoration_manifest_always_prepares_identity_and_one_value_fact():
    catalog = deepcopy(_catalog())
    catalog["variables"].extend(
        {
            "code": code,
            "value_type": "list" if code in {"pain", "advantages"} else "string",
            "unit_schema": {},
            "evidence_policy": {"required": False},
            "sensitivity": "normal",
            "allowed_usages": ["title", "body"],
            "validation_schema": {},
        }
        for code in ("scene", "pain", "persona_fact", "advantages")
    )

    manifest = build_material_manifest(catalog=catalog, order=_order())

    requirements = {item.variable_code: item for item in manifest.requirements}

    assert set(requirements) == {"product", "price", "quote_type", "persona_fact", "advantages"}
    assert requirements["persona_fact"].required is True
    assert requirements["advantages"].required is False
    assert requirements["advantages"].validation_schema["alternative_groups"] == ["persona:value"]


def test_persona_value_alternative_group_blocks_before_generation_when_missing():
    catalog = deepcopy(_catalog())
    catalog["variables"].extend(
        {
            "code": code,
            "value_type": "list" if code == "advantages" else "string",
            "unit_schema": {},
            "evidence_policy": {"required": False},
            "sensitivity": "normal",
            "allowed_usages": ["body"],
            "validation_schema": {},
        }
        for code in ("persona_fact", "process", "advantages")
    )
    manifest = build_material_manifest(catalog=catalog, order=_order())
    materials = [
        _business_material("product", "水电改造"),
        _business_material("persona_fact", "长沙工长，从业5年"),
        _price_material(),
        _reference_material(),
    ]

    blocked = validate_material_gate(manifest=manifest, materials=materials)
    assert blocked.status == "blocked"
    assert "persona:value" in blocked.missing_requirement_ids

    passed = validate_material_gate(
        manifest=manifest,
        materials=[*materials, _business_material("advantages", ["自有工人无转包"])],
    )
    assert passed.status == "passed"


def test_standardization_normalizes_common_square_meter_aliases():
    catalog = deepcopy(_catalog())
    catalog["content_formulas"][0]["required_variables"].append("quantity")
    catalog["variables"].append(
        {
            "code": "quantity",
            "value_type": "number",
            "unit_schema": {"required": True, "allowed_units": ["㎡"]},
            "evidence_policy": {"required": False},
            "sensitivity": "normal",
            "allowed_usages": ["title", "body"],
            "validation_schema": {},
        }
    )
    manifest = build_material_manifest(catalog=catalog, order=_order())
    evidence = {
        "id": "bundle-1",
        "version": 1,
        "items": [
            {
                "id": "ev-quantity",
                "variable_codes": ["quantity"],
                "value": "80平",
                "source_type": "manual_input",
                "source_id": "field-quantity",
                "source_version": "v1",
                "source_hash": "q" * 64,
                "verified_status": "user_confirmed",
                "allowed_usage": ["title", "body"],
                "risk_level": "normal",
            }
        ],
    }

    material = next(
        item
        for item in standardize_evidence_materials(evidence_bundle=evidence, manifest=manifest)
        if "quantity" in item.variable_codes
    )

    assert material.payload.value == 80
    assert material.payload.unit == "㎡"


def test_standardized_quantity_extracts_unit_from_source_value():
    catalog = deepcopy(_catalog())
    catalog["title_formulas"][0]["variable_schema"].append("quantity")
    catalog["variables"].append(
        {
            "code": "quantity",
            "value_type": "number",
            "unit_schema": {},
            "evidence_policy": {"required": True},
            "sensitivity": "sensitive",
            "allowed_usages": ["title", "body"],
            "validation_schema": {},
        }
    )
    manifest = build_material_manifest(catalog=catalog, order=_order())
    materials = standardize_evidence_materials(
        evidence_bundle={
            "id": "bundle-quantity",
            "version": 1,
            "items": [
                {
                    "id": "ev-quantity",
                    "variable_codes": ["quantity"],
                    "value": "89㎡",
                    "source_type": "manual_input",
                    "source_id": "field_quantity",
                    "source_version": "brief-v1",
                    "source_hash": "q" * 64,
                    "verified_status": "user_confirmed",
                    "allowed_usage": ["title", "body"],
                    "risk_level": "sensitive",
                    "metadata": {},
                }
            ],
        },
        manifest=manifest,
    )
    quantity = next(item for item in materials if "quantity" in item.variable_codes)

    assert quantity.payload.value == 89
    assert quantity.payload.unit == "㎡"


def test_calculation_formula_requires_and_derives_program_verified_total():
    catalog = deepcopy(_catalog())
    catalog["content_formulas"][0].update(
        code="FRB07",
        output_schema={"deterministic_calculation_required": True},
    )
    order = _order().model_copy(update={"body_formula_code": "FRB07"})
    manifest = build_material_manifest(catalog=catalog, order=order)

    calculation = next(item for item in manifest.requirements if item.variable_code == "calculated_total")
    assert calculation.requirement_id == "derived:calculated_total"
    assert calculation.review_policy == "user_confirmed"

    evidence = _derive_formula_calculation_evidence(
        {
            "task_id": "task-1",
            "material_manifest": manifest.model_dump(mode="json"),
            "content_brief": {
                "form_values": {
                    "quantity": 89,
                    "price": "120元/㎡",
                    "quote_type": "project_quote",
                }
            },
        }
    )
    assert evidence is not None
    assert evidence.value == 10680
    assert evidence.variable_codes == ("calculated_total",)
    assert evidence.metadata["derivation"]["expression"] == "89㎡×120元/㎡=10680元"


def test_trade_total_formula_requires_structured_breakdown_and_matching_sum():
    catalog = deepcopy(_catalog())
    catalog["content_formulas"][0]["code"] = "FRB08"
    next(item for item in catalog["variables"] if item["code"] == "price")["unit_schema"] = {
        "required": True,
        "allowed_units": ["元"],
    }
    order = _order().model_copy(update={"body_formula_code": "FRB08"})
    manifest = build_material_manifest(catalog=catalog, order=order)
    requirement = next(item for item in manifest.requirements if item.variable_code == "trade_breakdown")
    assert requirement.requirement_id == "formula:trade_breakdown"
    assert requirement.review_policy == "user_confirmed"

    evidence = {
        "id": "bundle-trade",
        "version": 1,
        "items": [
            {
                "id": "ev-product",
                "variable_codes": ["product"],
                "value": "泥瓦",
                "source_type": "manual_input",
                "source_id": "field_product",
                "source_version": "brief-v1",
                "source_hash": "a" * 64,
                "verified_status": "user_confirmed",
                "allowed_usage": ["title", "body"],
                "metadata": {},
            },
            {
                "id": "ev-price",
                "variable_codes": ["price"],
                "value": "28600元",
                "source_type": "manual_input",
                "source_id": "field_price",
                "source_version": "brief-v1",
                "source_hash": "b" * 64,
                "verified_status": "user_confirmed",
                "allowed_usage": ["title", "body"],
                "risk_level": "high_risk",
                "metadata": {},
            },
            {
                "id": "ev-quote-type",
                "variable_codes": ["quote_type"],
                "value": "项目报价",
                "source_type": "manual_input",
                "source_id": "field_quote_type",
                "source_version": "brief-v1",
                "source_hash": "c" * 64,
                "verified_status": "user_confirmed",
                "allowed_usage": ["body"],
                "risk_level": "high_risk",
                "metadata": {},
            },
            {
                "id": "ev-trades",
                "variable_codes": ["trade_breakdown"],
                "value": [
                    {"trade": "砌筑找平", "amount": 6800, "unit": "元", "included_items": ["墙地面找平"]},
                    {"trade": "防水施工", "amount": 5200, "unit": "元", "included_items": ["厨卫防水"]},
                    {"trade": "铺砖人工", "amount": 9600, "unit": "元", "included_items": ["墙地砖铺贴"]},
                    {"trade": "泥瓦辅材", "amount": 7000, "unit": "元", "included_items": ["水泥砂浆辅材"]},
                ],
                "source_type": "manual_input",
                "source_id": "field_trade_breakdown",
                "source_version": "brief-v1",
                "source_hash": "d" * 64,
                "verified_status": "user_confirmed",
                "allowed_usage": ["body"],
                "risk_level": "high_risk",
                "metadata": {},
            },
        ],
    }
    materials = standardize_evidence_materials(evidence_bundle=evidence, manifest=manifest)
    assert validate_material_gate(manifest=manifest, materials=materials).status == "passed"

    mismatched = deepcopy(evidence)
    mismatched["items"][-1]["value"][-1]["amount"] = 7100
    mismatch_report = validate_material_gate(
        manifest=manifest,
        materials=standardize_evidence_materials(evidence_bundle=mismatched, manifest=manifest),
    )
    assert mismatch_report.status == "blocked"
    assert mismatch_report.issues[-1].code == "TRADE_BREAKDOWN_SUM_MISMATCH"


def test_labor_aux_formula_requires_structured_breakdown_and_matching_project_total():
    catalog = deepcopy(_catalog())
    catalog["content_formulas"][0]["code"] = "FRB09"
    catalog["variables"].append(
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
    next(item for item in catalog["variables"] if item["code"] == "price")["unit_schema"] = {
        "required": True,
        "allowed_units": ["元"],
    }
    order = _order().model_copy(update={"body_formula_code": "FRB09"})
    manifest = build_material_manifest(catalog=catalog, order=order)
    requirement = next(item for item in manifest.requirements if item.variable_code == "labor_aux_breakdown")
    assert requirement.requirement_id == "formula:labor_aux_breakdown"

    breakdown = {
        "labor_total": 12800,
        "auxiliary_total": 9000,
        "unit": "元",
        "trades": [
            {
                "trade": "基层处理",
                "labor_amount": 6000,
                "auxiliary_amount": 4000,
                "included_items": ["铲除修补"],
            },
            {
                "trade": "墙面涂刷",
                "labor_amount": 6800,
                "auxiliary_amount": 5000,
                "included_items": ["底漆面漆"],
            },
        ],
    }
    evidence = {
        "id": "bundle-labor-aux",
        "version": 1,
        "items": [
            {
                "id": "ev-product",
                "variable_codes": ["product"],
                "value": "墙面",
                "source_type": "manual_input",
                "source_id": "field_product",
                "source_version": "brief-v1",
                "source_hash": "a" * 64,
                "verified_status": "user_confirmed",
                "allowed_usage": ["title", "body"],
                "metadata": {},
            },
            {
                "id": "ev-price",
                "variable_codes": ["price"],
                "value": "21800元",
                "source_type": "manual_input",
                "source_id": "field_price",
                "source_version": "brief-v1",
                "source_hash": "b" * 64,
                "verified_status": "user_confirmed",
                "allowed_usage": ["title", "body"],
                "risk_level": "high_risk",
                "metadata": {},
            },
            {
                "id": "ev-quote-type",
                "variable_codes": ["quote_type"],
                "value": "项目报价",
                "source_type": "manual_input",
                "source_id": "field_quote_type",
                "source_version": "brief-v1",
                "source_hash": "c" * 64,
                "verified_status": "user_confirmed",
                "allowed_usage": ["body"],
                "risk_level": "high_risk",
                "metadata": {},
            },
            {
                "id": "ev-advantages",
                "variable_codes": ["advantages"],
                "value": ["报价逐项列明", "材料透明"],
                "source_type": "manual_input",
                "source_id": "field_advantages",
                "source_version": "brief-v1",
                "source_hash": "e" * 64,
                "verified_status": "user_confirmed",
                "allowed_usage": ["body"],
                "risk_level": "normal",
                "metadata": {},
            },
            {
                "id": "ev-labor-aux",
                "variable_codes": ["labor_aux_breakdown"],
                "value": breakdown,
                "source_type": "manual_input",
                "source_id": "field_labor_aux_breakdown",
                "source_version": "brief-v1",
                "source_hash": "d" * 64,
                "verified_status": "user_confirmed",
                "allowed_usage": ["body"],
                "risk_level": "high_risk",
                "metadata": {},
            },
        ],
    }
    materials = standardize_evidence_materials(evidence_bundle=evidence, manifest=manifest)
    assert validate_material_gate(manifest=manifest, materials=materials).status == "passed"

    mismatched = deepcopy(evidence)
    mismatched["items"][-1]["value"]["auxiliary_total"] = 9100
    mismatch_report = validate_material_gate(
        manifest=manifest,
        materials=standardize_evidence_materials(evidence_bundle=mismatched, manifest=manifest),
    )
    assert mismatch_report.status == "blocked"
    assert mismatch_report.issues[-1].code == "MATERIAL_VALUE_INVALID"


def test_gate_blocks_missing_and_unreviewed_high_risk_material_before_generation():
    manifest = build_material_manifest(catalog=_catalog(), order=_order())

    report = validate_material_gate(
        manifest=manifest,
        materials=[_business_material("product", "水电改造"), _price_material(confirmed=False)],
    )

    assert report.status == "blocked"
    assert "price" in report.unapproved_variable_codes
    assert "quote_type" in report.unapproved_variable_codes


def test_gate_passes_only_when_every_manifest_requirement_has_approved_binding():
    manifest = build_material_manifest(catalog=_catalog(), order=_order())

    report = validate_material_gate(
        manifest=manifest,
        materials=[_business_material("product", "水电改造"), _price_material()],
    )

    assert report.status == "passed"
    assert report.missing_requirement_ids == ()
    assert {binding.requirement_id for binding in report.bindings} == {
        "variable:product",
        "variable:price",
        "variable:quote_type",
    }


def test_gate_blocks_conflicting_values_for_one_variable():
    manifest = build_material_manifest(catalog=_catalog(), order=_order())

    report = validate_material_gate(
        manifest=manifest,
        materials=[
            _business_material("product", "水电改造"),
            MaterialEnvelopeV2.model_validate(
                {
                    **_business_material("product", "局部改造").model_dump(mode="json"),
                    "id": "mat-product-2",
                }
            ),
            _price_material(),
        ],
    )

    assert report.status == "blocked"
    assert report.conflicting_variable_codes == ("product",)


def test_gate_binds_multiple_approved_chunks_for_list_variable_without_conflict():
    catalog = deepcopy(_catalog())
    catalog["content_formulas"][0]["required_variables"].append("advantages")
    catalog["variables"].append(
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
    manifest = build_material_manifest(catalog=catalog, order=_order())
    first = _business_material("advantages", ["材料型号写入合同"]).model_copy(update={"id": "mat-advantage-1"})
    second = _business_material("advantages", ["报价明细公开拆解"]).model_copy(update={"id": "mat-advantage-2"})

    report = validate_material_gate(
        manifest=manifest,
        materials=[_business_material("product", "水电改造"), _price_material(), first, second],
    )

    assert report.status == "passed"
    assert report.conflicting_variable_codes == ()
    binding = next(item for item in report.bindings if item.requirement_id == "variable:advantages")
    assert binding.material_ids == ("mat-advantage-1", "mat-advantage-2")


def test_gate_prefers_user_confirmed_fact_over_retrieved_knowledge():
    manifest = build_material_manifest(catalog=_catalog(), order=_order())
    retrieved = _business_material("product", "知识库通用产品").model_dump(mode="json")
    retrieved.update(id="mat-product-kb")
    retrieved["source"].update(
        source_type="knowledge_base",
        source_id="kb-product/chunk-1",
        source_version="v1",
    )
    retrieved["governance"]["verified_status"] = "retrieved"

    report = validate_material_gate(
        manifest=manifest,
        materials=[
            _business_material("product", "本次用户确认的水电改造"),
            MaterialEnvelopeV2.model_validate(retrieved),
            _price_material(),
        ],
    )

    assert report.status == "passed"
    binding = next(item for item in report.bindings if item.requirement_id == "variable:product")
    assert binding.material_ids == ("mat-product",)


def test_gate_blocks_wrong_value_type_and_unit_before_generation():
    manifest = build_material_manifest(catalog=_catalog(), order=_order())
    wrong_product = _business_material("product", "水电改造").model_dump(mode="json")
    wrong_product["payload"]["value"] = ["水电改造"]
    wrong_price = _price_material().model_dump(mode="json")
    wrong_price["payload"]["unit"] = "元/米"

    report = validate_material_gate(
        manifest=manifest,
        materials=[
            MaterialEnvelopeV2.model_validate(wrong_product),
            MaterialEnvelopeV2.model_validate(wrong_price),
        ],
    )

    assert report.status == "blocked"
    assert [issue.code for issue in report.issues].count("MATERIAL_VALUE_INVALID") == 2


def test_standardize_evidence_materials_builds_typed_price_from_confirmed_bundle():
    manifest = build_material_manifest(catalog=_catalog(), order=_order())
    materials = standardize_evidence_materials(
        evidence_bundle={
            "id": "bundle-1",
            "version": 1,
            "status": "frozen",
            "bundle_hash": "e" * 64,
            "items": [
                {
                    "id": "ev-product",
                    "variable_codes": ["product"],
                    "value": "水电改造",
                    "source_type": "manual_input",
                    "source_id": "field_product",
                    "source_version": "brief-v1",
                    "source_hash": "a" * 64,
                    "verified_status": "user_confirmed",
                    "allowed_usage": ["title", "body"],
                    "risk_level": "normal",
                    "metadata": {},
                },
                {
                    "id": "ev-price",
                    "variable_codes": ["price"],
                    "value": "120元/㎡",
                    "source_type": "human_confirmation",
                    "source_id": "price-confirmation",
                    "source_version": "v1",
                    "source_hash": "b" * 64,
                    "verified_status": "user_confirmed",
                    "allowed_usage": ["title", "body"],
                    "risk_level": "high_risk",
                    "metadata": {"scope": "水电人工"},
                },
                {
                    "id": "ev-quote-type",
                    "variable_codes": ["quote_type"],
                    "value": "标准单价",
                    "source_type": "manual_input",
                    "source_id": "field_quote_type",
                    "source_version": "brief-v1",
                    "source_hash": "c" * 64,
                    "verified_status": "user_confirmed",
                    "allowed_usage": ["body"],
                    "risk_level": "high_risk",
                    "metadata": {},
                },
            ],
        },
        manifest=manifest,
    )

    price = next(item for item in materials if item.material_type == "price_fact")
    quote_type = next(item for item in materials if "quote_type" in item.variable_codes)
    assert set(price.variable_codes) == {"price"}
    assert quote_type.material_type == "business_fact"
    assert quote_type.payload.value == "标准单价"
    assert price.payload.amounts == (120.0,)
    assert price.payload.unit == "元/㎡"
    assert price.payload.price_basis == "standard_unit_price"
    assert validate_material_gate(manifest=manifest, materials=materials).status == "passed"


def test_studio_budget_quote_type_manual_input_passes_foreman_like_gate():
    """内容工作室注入的 budget/manual_input 必须能通过工长包 quote_type 门禁。"""

    catalog = _catalog()
    for item in catalog["variables"]:
        if item["code"] == "quote_type":
            item["evidence_policy"] = {
                "required": True,
                "review_policy": "user_confirmed",
                "allowed_sources": ["business_record", "manual_input", "human_confirmation"],
            }
            item["validation_schema"] = {
                "enum": ["standard_unit_price", "project_quote", "budget", "settlement"]
            }
            item["sensitivity"] = "high_risk"
            item["allowed_usages"] = ["body"]
    manifest = build_material_manifest(catalog=catalog, order=_order(), single_blueprint=True)
    materials = standardize_evidence_materials(
        evidence_bundle={
            "id": "bundle-budget",
            "version": 1,
            "status": "frozen",
            "bundle_hash": "e" * 64,
            "items": [
                {
                    "id": "ev-product",
                    "variable_codes": ["product"],
                    "value": "洋湖天街定制化家装项目",
                    "source_type": "manual_input",
                    "source_id": "field_product",
                    "source_version": "brief-v1",
                    "source_hash": "a" * 64,
                    "verified_status": "user_confirmed",
                    "allowed_usage": ["title", "body"],
                    "risk_level": "normal",
                    "metadata": {},
                },
                {
                    "id": "ev-price",
                    "variable_codes": ["price"],
                    "value": ["基础 9万", "木制品 4万"],
                    "source_type": "manual_input",
                    "source_id": "field_price",
                    "source_version": "brief-v1",
                    "source_hash": "b" * 64,
                    "verified_status": "user_confirmed",
                    "allowed_usage": ["title", "body"],
                    "risk_level": "normal",
                    "metadata": {"unit": "元/㎡"},
                },
                {
                    "id": "ev-quote-type",
                    "variable_codes": ["quote_type"],
                    "value": "budget",
                    "source_type": "manual_input",
                    "source_id": "field_quote_type",
                    "source_version": "brief-v1",
                    "source_hash": "c" * 64,
                    "verified_status": "user_confirmed",
                    "allowed_usage": ["title", "body", "visual"],
                    "risk_level": "normal",
                    "metadata": {},
                },
            ],
        },
        manifest=manifest,
    )
    price = next(item for item in materials if item.material_type == "price_fact")
    quote_type = next(item for item in materials if item.material_type == "business_fact" and "quote_type" in item.variable_codes)
    assert price.payload.price_basis == "budget"
    assert quote_type.payload.value == "budget"
    assert quote_type.governance.risk_level == "high_risk"
    report = validate_material_gate(manifest=manifest, materials=materials)
    assert report.status == "passed"
    assert "variable:quote_type" not in report.missing_requirement_ids
    assert any(binding.requirement_id == "variable:quote_type" for binding in report.bindings)


def test_standardize_evidence_materials_defaults_blank_price_unit_to_yuan():
    manifest = build_material_manifest(catalog=_catalog(), order=_order())
    materials = standardize_evidence_materials(
        evidence_bundle={
            "id": "bundle-blank-unit",
            "version": 1,
            "status": "frozen",
            "bundle_hash": "e" * 64,
            "items": [
                {
                    "id": "ev-product",
                    "variable_codes": ["product"],
                    "value": "整装交付",
                    "source_type": "manual_input",
                    "source_id": "field_product",
                    "source_version": "brief-v1",
                    "source_hash": "a" * 64,
                    "verified_status": "user_confirmed",
                    "allowed_usage": ["title", "body"],
                    "risk_level": "normal",
                    "metadata": {},
                },
                {
                    "id": "ev-price",
                    "variable_codes": ["price"],
                    "value": "128000",
                    "source_type": "human_confirmation",
                    "source_id": "price-confirmation",
                    "source_version": "v1",
                    "source_hash": "b" * 64,
                    "verified_status": "user_confirmed",
                    "allowed_usage": ["title", "body"],
                    "risk_level": "high_risk",
                    "metadata": {"unit": "", "scope": "整装预算"},
                },
            ],
        },
        manifest=manifest,
    )

    price = next(item for item in materials if item.material_type == "price_fact")
    assert price.payload.amounts == (128000.0,)
    assert price.payload.unit == "元"
    assert PriceFactPayloadV2.model_validate(
        {
            "quoted_value": "128000",
            "amounts": [128000],
            "unit": "",
            "price_basis": "budget",
            "scope": "整装预算",
        }
    ).unit == "元"


@pytest.mark.asyncio
async def test_default_factory_freezes_live_forbidden_knowledge(monkeypatch):
    from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
    from yuxi.content.v3.modular_rules import build_modular_rule_bundle
    from yuxi.services import content_forbidden_words_service

    calls = []

    async def load(uid, name):
        calls.append((uid, name))
        return {"snapshot_hash": "f" * 64, "alternatives": {"报价": ["报J", "费用"], "APP": []}}

    monkeypatch.setattr(content_forbidden_words_service, "load_forbidden_words", load)
    manifest = build_material_manifest(catalog=_catalog(), order=_order())
    materials = [_business_material("product", "水电改造"), _price_material(), _reference_material()]
    bundle = build_modular_rule_bundle({})
    bundle["topic_candidates"] = ["装修报价", "APP"]
    state = {
        "task_id": "task-1",
        "uid": "user-1",
        "production_order": _order().model_dump(mode="json"),
        "material_manifest": manifest.model_dump(mode="json"),
        "material_quality_report": validate_material_gate(manifest=manifest, materials=materials).model_dump(
            mode="json"
        ),
        "standardized_materials": [item.model_dump(mode="json") for item in materials],
        "strategy_snapshot": {
            "title_formula": {"code": "FRT07"},
            "body_formula": {"code": "FRB06"},
            "reference_snapshot": {"id": "asset-1", "source_hash": "r" * 64},
        },
        "evidence_bundle": {"id": "bundle-1", "version": 2, "status": "frozen", "bundle_hash": "e" * 64},
        "formula_lexicon_bundle": {"bundle_hash": "l" * 64},
        "runtime_config_snapshot": {"content_rule_bundle": bundle},
    }
    update = await V3DeterministicNodeHandler._freeze_production_pack(db=None, state=state, node_run_id="node")
    frozen = update["production_pack"]["content_rule_bundle"]
    assert calls == [("user-1", "封禁词库")]
    assert "single_blueprint" not in frozen
    assert frozen["runtime_rules"]["viral-platform-expression"]["forbidden_replacements"] == {"报价": "报J"}
    assert frozen["topic_candidates"] == ["装修报J"]
    assert update["runtime_config_snapshot"]["content_rule_bundle"] == frozen
    assert "forbidden_lexicon" not in bundle["runtime_rules"]["viral-platform-expression"]


def test_frozen_production_pack_hash_is_stable_and_covers_materials():
    manifest = build_material_manifest(catalog=_catalog(), order=_order())
    materials = [_business_material("product", "水电改造"), _price_material(), _reference_material()]
    report = validate_material_gate(manifest=manifest, materials=materials)
    inputs = {
        "task_id": "task-1",
        "production_order": _order(),
        "material_manifest": manifest,
        "material_quality_report": report,
        "materials": materials,
        "strategy_snapshot": {
            "snapshot_hash": "s" * 64,
            "title_formula": {"code": "FRT07"},
            "body_formula": {"code": "FRB06"},
        },
        "evidence_bundle": {"id": "bundle-1", "version": 2, "status": "frozen", "bundle_hash": "e" * 64},
        "formula_lexicon_bundle": {"bundle_hash": "l" * 64},
        "reference_snapshot": {
            "id": "asset-1",
            "source_hash": "r" * 64,
            "title": "参考文章标题",
            "body": "参考开头。\n✔ 原文中的报价仅供写法参考\n这是完整的报价后叙述和结尾。",
        },
        "expression_guidance": None,
        "writing_request": "写一篇水电报价内容",
        "channel_profile": {"code": "xiaohongshu"},
        "persona_profile": {"tone": "专业"},
        "content_rule_bundle": {"bundle_hash": "c" * 64},
        "compliance_policy_version_ids": ["compliance-v1"],
    }

    first = freeze_production_pack(**inputs)
    second = freeze_production_pack(**inputs)
    changed = deepcopy(inputs)
    changed["materials"] = [_business_material("product", "全屋改造"), _price_material(), _reference_material()]
    changed["material_quality_report"] = validate_material_gate(
        manifest=manifest,
        materials=changed["materials"],
    )
    third = freeze_production_pack(**changed)

    assert isinstance(first, FrozenProductionPackV1)
    assert first.production_pack_hash == second.production_pack_hash
    assert first.production_pack_hash != third.production_pack_hash
    assert first.expression_policy["emoji_allowed"] is True
    assert first.expression_policy["policy_hash"]
    assert [item["code"] for item in first.expression_policy["required_categories"]] == [
        "verified_data",
        "scene_context",
    ]

    changed_after_gate = deepcopy(inputs)
    changed_after_gate["materials"] = changed["materials"]
    with pytest.raises(ValueError, match="质量门通过后发生变化"):
        freeze_production_pack(**changed_after_gate)

    model_view = project_generation_input(
        {
            "runtime_config_snapshot": {"creation_mode": "viral_rewrite"},
            "production_pack": first.model_dump(mode="json"),
            "content_brief": {"should_not": "be exposed"},
            "evidence_bundle": {"should_not": "be exposed"},
        }
    )
    assert set(model_view) == {"production_pack", "lexicon_constraints"}
    assert model_view["production_pack"]["reference_snapshot"] == inputs["reference_snapshot"]
    assert model_view["lexicon_constraints"] == {
        "schema_version": 2,
        "title": {},
        "body": {},
        "fact_bindings": {"title": {}, "body": {}},
        "selection": {"title": {}, "body": {}},
        "invalid_locked_selection": [],
        "fact_bound_title_codes": [],
        "fact_bound_body_codes": [],
        "unresolved_fact_bound_codes": [],
    }
    assert model_view["production_pack"]["generation_slots"]
    assert "review_contract" in {item["slot_id"] for item in model_view["production_pack"]["generation_slots"]}
    assert "original_content" not in json.dumps(model_view["production_pack"]["generation_slots"], ensure_ascii=False)
    assert first.formula_lexicon_bundle["selection"]["policy"] == "title_candidates_body_shortest_v2"
    assert "content_brief" not in model_view
    assert "evidence_bundle" not in model_view

    repair_view = project_generation_input(
        {
            "runtime_config_snapshot": {"creation_mode": "viral_rewrite"},
            "production_pack": first.model_dump(mode="json"),
            "validation_report": {"status": "passed", "checks": []},
            "review_report": {
                "status": "blocked",
                "checks": [{"code": "PERSONA_OPENING", "status": "blocked"}],
            },
            "selected_title": {"text": "冻结标题"},
            "content_outline": {"sections": [{"id": "s1"}]},
            "content_draft": {
                "body": "旧首段\n\n冻结中段一\n\n冻结中段二\n\n旧末段",
                "topics": ["装修"],
            },
        }
    )
    constraints = repair_view["repair_constraints"]
    assert constraints["mode"] == "persona_paragraphs_only"
    assert constraints["editable_paragraph_numbers"] == [1, 2]
    assert constraints["immutable_title"] == {"text": "冻结标题"}
    assert constraints["immutable_topics"] == ["装修"]

    tampered = first.model_dump(mode="json")
    tampered["writing_request"] = "篡改后的要求"
    with pytest.raises(ValidationError, match="生产包 Hash"):
        FrozenProductionPackV1.model_validate(tampered)

    historical = first.model_dump(mode="json")
    historical.pop("expression_policy")
    historical_payload = {
        key: value for key, value in historical.items() if key not in {"id", "production_pack_hash", "frozen_at"}
    }
    historical["production_pack_hash"] = hashlib.sha256(
        json.dumps(
            historical_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    assert FrozenProductionPackV1.model_validate(historical).expression_policy is None

    # 更早的生产包没有槽位；读取和生成视图投影不能回写或重编译历史包。
    historical.pop("generation_slots")
    historical_payload.pop("generation_slots")
    historical["production_pack_hash"] = hashlib.sha256(
        json.dumps(historical_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    before_projection = deepcopy(historical)
    parsed = FrozenProductionPackV1.model_validate(historical)
    assert parsed.generation_slots == ()
    project_generation_input(
        {
            "production_pack": historical,
            "runtime_config_snapshot": {"creation_mode": "viral_rewrite"},
        }
    )
    assert historical == before_projection
    assert (
        FrozenProductionPackV1.model_validate(parsed.model_dump(mode="json")).production_pack_hash
        == historical["production_pack_hash"]
    )


def test_formula_lexicon_constraints_only_keep_fact_grounded_title_terms():
    materials = [
        _business_material("product", "长沙同城装修墙面施工"),
        _business_material("scene", "长沙小户型墙面施工现场"),
        _business_material("result", "墙面已经省心完工"),
        _business_material("pain", "旧房水电老化需要检查"),
        _business_material("advantages", "改造后功能分区明确"),
        _business_material("persona_fact", "这是长沙本地实景工地"),
    ]
    constraints = build_formula_lexicon_constraints(
        materials=materials,
        formula_lexicon_bundle={
            "required": True,
            "title": [
                {"code": "title.positioning", "chunks": ["同城装修\n全案设计"]},
                {"code": "title.house_type", "chunks": ["小户型\n两居室"]},
                {"code": "title.positive_result", "chunks": ["省心完工\n安心入住"]},
                {"code": "title.beneficial_result", "chunks": ["报价透明\n节点清楚"]},
            ],
            "body": [
                {"code": "body.old_house_pain", "chunks": ["水电老化\n墙面发霉"]},
                {"code": "body.renovation_advantage", "chunks": ["功能分区明确\n动线合理"]},
                {"code": "persona.delivery_endorsement", "chunks": ["本地实景工地\n全程实拍"]},
                {"code": "ending.case_cta", "chunks": ["想看工地实拍\n案例参考"]},
            ],
        },
    )

    assert constraints["title"] == {
        "title.positioning": ["同城装修"],
        "title.house_type": ["小户型"],
        "title.positive_result": ["省心完工"],
        "title.beneficial_result": ["报价透明", "节点清楚"],
    }
    assert constraints["body"] == {
        "body.old_house_pain": ["水电老化"],
        "body.renovation_advantage": ["功能分区明确"],
        "persona.delivery_endorsement": ["本地实景工地"],
        "ending.case_cta": ["想看工地实拍", "案例参考"],
    }
    assert constraints["fact_bound_body_codes"] == [
        "body.old_house_pain",
        "body.renovation_advantage",
        "persona.delivery_endorsement",
    ]
    assert constraints["unresolved_fact_bound_codes"] == []


def test_formula_lexicon_constraints_derive_house_type_and_local_positioning_from_approved_facts():
    constraints = build_formula_lexicon_constraints(
        materials=[
            _business_material("product", "三室二厅"),
            _business_material("location", "长沙市"),
        ],
        formula_lexicon_bundle={
            "required": True,
            "title": [
                {"code": "title.positioning", "chunks": ["同城装修\n旧房改造"]},
                {"code": "title.house_type", "chunks": ["小户型\n大三房\n两居室"]},
            ],
            "body": [],
        },
    )

    assert constraints["title"] == {
        "title.positioning": ["同城装修"],
        "title.house_type": ["三室二厅"],
    }
    assert constraints["unresolved_fact_bound_codes"] == []


def test_formula_lexicon_constraints_bind_remaining_expression_codes_to_approved_fact_values():
    constraints = build_formula_lexicon_constraints(
        materials=[
            _business_material("result", "业主已现场确认验收"),
            _business_material("pain", ["墙面返潮起皮"]),
            _business_material("advantages", ["自有工人无转包", "方案按需求调整"]),
            _business_material("persona_fact", "我在长沙服务过10个真实工地"),
        ],
        formula_lexicon_bundle={
            "required": True,
            "title": [{"code": "title.positive_result", "chunks": ["省心完工\n安心入住"]}],
            "body": [
                {"code": "body.old_house_pain", "chunks": ["水电老化\n墙面发霉"]},
                {"code": "body.renovation_advantage", "chunks": ["格局优化\n动线合理"]},
                {"code": "persona.delivery_endorsement", "chunks": ["本地实景工地\n全程实拍"]},
                {"code": "persona.service_contrast", "chunks": ["材料透明\n无中途增项"]},
            ],
        },
    )

    assert constraints["title"]["title.positive_result"] == ["业主已现场确认验收"]
    assert constraints["body"] == {
        "body.old_house_pain": ["墙面返潮起皮"],
        "body.renovation_advantage": ["方案按需求调整", "自有工人无转包"],
        "persona.delivery_endorsement": ["我在长沙服务过10个真实工地"],
        "persona.service_contrast": ["方案按需求调整", "自有工人无转包"],
    }
    assert constraints["unresolved_fact_bound_codes"] == []
    assert constraints["fact_bindings"]["title"]["title.positive_result"] == [
        {
            "term": "业主已现场确认验收",
            "resolution_type": "approved_fact",
            "variable_codes": ["result"],
            "material_ids": ["mat-result"],
            "evidence_ids": [],
        }
    ]
    assert all(
        binding["resolution_type"] == "approved_fact"
        for bindings in constraints["fact_bindings"]["body"].values()
        for binding in bindings
    )


def test_formula_lexicon_constraints_do_not_bind_unapproved_fact_values():
    constraints = build_formula_lexicon_constraints(
        materials=[_business_material("result", "业主已现场确认验收", approved=False)],
        formula_lexicon_bundle={
            "required": True,
            "title": [{"code": "title.positive_result", "chunks": ["省心完工\n安心入住"]}],
            "body": [],
        },
    )

    assert constraints["title"]["title.positive_result"] == []
    assert constraints["fact_bindings"]["title"]["title.positive_result"] == []
    assert constraints["unresolved_fact_bound_codes"] == ["title.positive_result"]


def test_formula_lexicon_constraints_use_body_fact_materials_bound_by_manifest():
    catalog = deepcopy(_catalog())
    catalog["content_formulas"][0]["code"] = "C01"
    catalog["variables"].append(
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
    order = _order().model_copy(update={"body_formula_code": "C01"})
    manifest = build_material_manifest(catalog=catalog, order=order)
    materials = [
        _business_material("product", "墙面施工"),
        _business_material("advantages", ["自有工人无转包"]),
        _price_material(),
        _reference_material(),
    ]
    report = validate_material_gate(manifest=manifest, materials=materials)

    constraints = build_formula_lexicon_constraints(
        materials=materials,
        material_quality_report=report,
        formula_lexicon_bundle={
            "required": True,
            "title": [],
            "body": [{"code": "persona.service_contrast", "chunks": ["材料透明\n无中途增项"]}],
        },
    )

    assert report.status == "passed"
    assert constraints["body"]["persona.service_contrast"] == ["自有工人无转包"]
    assert constraints["unresolved_fact_bound_codes"] == []


def test_frozen_production_pack_freezes_title_candidates_and_body_terms():
    manifest = build_material_manifest(catalog=_catalog(), order=_order())
    materials = [_business_material("product", "长沙同城装修水电改造"), _price_material(), _reference_material()]
    report = validate_material_gate(manifest=manifest, materials=materials)
    pack = freeze_production_pack(
        task_id="task-1",
        production_order=_order(),
        material_manifest=manifest,
        material_quality_report=report,
        materials=materials,
        strategy_snapshot={
            "snapshot_hash": "s" * 64,
            "title_formula": {"code": "FRT07"},
            "body_formula": {"code": "FRB06"},
        },
        evidence_bundle={"id": "bundle-1", "version": 2, "status": "frozen", "bundle_hash": "e" * 64},
        formula_lexicon_bundle={
            "bundle_hash": "l" * 64,
            "title": [
                {"code": "title.positioning", "chunks": ["同城装修\n水电改造"]},
                {"code": "title.instruction_value", "chunks": ["装修人必看\n先存后看"]},
            ],
            "body": [{"code": "ending.quotation_cta", "chunks": ["免费量房报价\n报价对比"]}],
        },
        reference_snapshot={"id": "asset-1", "source_hash": "r" * 64},
        expression_guidance=None,
        writing_request=None,
        channel_profile={"code": "xiaohongshu"},
        persona_profile={},
        content_rule_bundle={"bundle_hash": "c" * 64},
        compliance_policy_version_ids=[],
    )

    assert pack.formula_lexicon_bundle["selection"]["title"] == {
        "title.instruction_value": ["先存后看", "装修人必看"],
        "title.positioning": ["同城装修", "水电改造"],
    }
    assert pack.formula_lexicon_bundle["selection"]["body"] == {"ending.quotation_cta": ["报价对比"]}
    assert pack.formula_lexicon_bundle["fact_bindings"] == {
        "title": {
            "title.positioning": [
                {
                    "term": "同城装修",
                    "resolution_type": "exact",
                    "variable_codes": ["product"],
                    "material_ids": ["mat-product"],
                    "evidence_ids": [],
                },
                {
                    "term": "水电改造",
                    "resolution_type": "exact",
                    "variable_codes": ["product"],
                    "material_ids": ["mat-product"],
                    "evidence_ids": [],
                },
            ]
        },
        "body": {},
    }

    unresolved = build_formula_lexicon_constraints(
        materials=[_business_material("product", "墙面施工")],
        formula_lexicon_bundle={
            "required": True,
            "title": [{"code": "title.house_type", "chunks": ["小户型\n两居室"]}],
            "body": [],
        },
    )
    assert unresolved["title"]["title.house_type"] == []
    assert unresolved["unresolved_fact_bound_codes"] == ["title.house_type"]


def test_frozen_production_pack_rejects_blocked_quality_report():
    manifest = MaterialRequirementManifestV1.model_validate(
        build_material_manifest(catalog=_catalog(), order=_order()).model_dump(mode="json")
    )
    materials = [_business_material("product", "水电改造")]
    report = validate_material_gate(manifest=manifest, materials=materials)

    with pytest.raises(ValueError, match="质量门"):
        freeze_production_pack(
            task_id="task-1",
            production_order=_order(),
            material_manifest=manifest,
            material_quality_report=report,
            materials=materials,
            strategy_snapshot={
                "snapshot_hash": "s" * 64,
                "title_formula": {"code": "FRT07"},
                "body_formula": {"code": "FRB06"},
            },
            evidence_bundle={"id": "bundle-1", "version": 1, "status": "frozen", "bundle_hash": "e" * 64},
            formula_lexicon_bundle={"bundle_hash": "l" * 64},
            reference_snapshot={"id": "asset-1", "source_hash": "r" * 64},
            expression_guidance=None,
            writing_request=None,
            channel_profile={},
            persona_profile={},
            content_rule_bundle={"bundle_hash": "c" * 64},
            compliance_policy_version_ids=[],
        )


@pytest.mark.parametrize("content_type", ["CT01", "CT02", "CT03", "CT04", "CT05", "CT06", "CT07"])
def test_persona_alternative_bindings_compile_value_slot_without_unselected_evidence(content_type):
    manifest = MaterialRequirementManifestV1(
        order_hash="o" * 64,
        content_type_code=content_type,
        title_formula_code="FRT07",
        body_formula_code="FRB06",
        manifest_hash="m" * 64,
        requirements=tuple(
            MaterialRequirementV1(
                requirement_id=f"variable:{code}",
                variable_code=code,
                material_types=("business_fact",),
                value_type="list",
                required=False,
                allowed_sources=("manual_input",),
                allowed_usage=("body",),
                review_policy="retrieved",
                risk_level="normal",
                validation_schema={"alternative_groups": ["persona:value"]},
            )
            for code in ("advantages", "process")
        ),
    )
    selected = _business_material("advantages", ["决策快", "自有工人"]).model_copy(
        update={"evidence_ids": ("ev-selected",)}
    )
    candidate = selected.model_copy(
        update={
            "id": "mat-candidate",
            "evidence_ids": ("ev-unselected",),
            "payload": selected.payload.model_copy(update={"value": ["候选优势"]}),
            "governance": selected.governance.model_copy(update={"verified_status": "retrieved"}),
        }
    )
    materials = [selected, candidate]
    report = validate_material_gate(manifest=manifest, materials=materials)
    assert report.status == "passed"
    slots = compile_generation_slots(
        material_manifest=manifest,
        materials=materials,
        material_quality_report=report,
        strategy_snapshot={},
        expression_policy={},
        channel_profile={},
        content_rule_bundle={},
    )
    value_slot = next(slot for slot in slots if slot.slot_id == "persona_value")
    assert value_slot.source_variable_codes == ("advantages",)
    assert value_slot.evidence_ids == ("ev-selected",)
    assert validate_material_gate(manifest=manifest, materials=[]).status == "blocked"


def test_title_lexicon_freezes_candidates_without_preselecting_emotion():
    constraints = {
        "title": {"title.oral_emotion": ["劝退", "听劝", "谁懂啊"], "title.positioning": ["旧房改造", "同城装修"]},
        "body": {"ending.quotation_cta": ["免费量房报价", "报价对比"]},
    }
    selection = select_formula_lexicon_terms(constraints)
    assert set(selection["title"]["title.oral_emotion"]) == {"劝退", "听劝", "谁懂啊"}
    assert set(selection["title"]["title.positioning"]) == {"旧房改造", "同城装修"}
    assert selection["body"] == {"ending.quotation_cta": ["报价对比"]}
    constraints["title"]["title.oral_emotion"].reverse()
    assert select_formula_lexicon_terms(constraints) == selection


def test_title_generation_slot_lists_hyb_process_and_locked_emotion():
    manifest = MaterialRequirementManifestV1(
        order_hash="o" * 64,
        content_type_code="CT06",
        title_formula_code="FRT16",
        body_formula_code="FRB11",
        requirements=tuple(
            MaterialRequirementV1(
                requirement_id=f"variable:{code}",
                variable_code=code,
                material_types=("business_fact",),
                value_type="list" if code == "process" else "string",
                required=True,
                allowed_sources=("manual_input",),
                allowed_usage=("title", "body"),
                review_policy="user_confirmed",
                risk_level="normal",
            )
            for code in ("craft_role", "process")
        ),
        reference_required=False,
        manifest_hash="m" * 64,
    )
    materials = [
        _business_material("process", ["功能舒适系统", "HYB-地面不积水工艺"]),
        _business_material("craft_role", "水电工"),
    ]
    slots = compile_generation_slots(
        material_manifest=manifest,
        material_quality_report=validate_material_gate(manifest=manifest, materials=materials),
        materials=materials,
        strategy_snapshot={
            "title_formula": {
                "code": "FRT16",
                "variable_schema": ["craft_role", "process"],
                "source_content": {
                    "slot_schema": [
                        {"code": "craft_role", "variable_codes": ["craft_role"], "lexicon_codes": []},
                        {"code": "process", "variable_codes": ["process"], "lexicon_codes": []},
                        {"code": "emotion", "variable_codes": [], "lexicon_codes": ["title.oral_emotion"]},
                    ]
                },
            }
        },
        expression_policy={},
        channel_profile={},
        content_rule_bundle={},
        formula_lexicon_bundle={"selection": {"title": {"title.oral_emotion": ["劝退"]}}},
    )
    title_slot = next(slot for slot in slots if slot.slot_id == "title_formula")
    assert "地面不积水" in title_slot.instruction
    assert "劝退" in title_slot.instruction
    assert any("地面不积水" in item for item in title_slot.acceptance)
    assert any("劝退" in item for item in title_slot.acceptance)
