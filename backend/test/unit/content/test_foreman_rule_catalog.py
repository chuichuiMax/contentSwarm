from __future__ import annotations

from copy import deepcopy

from yuxi.content.catalog import CONTENT_TYPES, VARIABLES
from yuxi.content.model.materials import build_material_manifest, create_production_order
from yuxi.content.model.strategy import build_strategy_candidates
from yuxi.content.rules import BODY_FORMULAS, METHODS, TITLE_FORMULAS
from yuxi.content.v3.foreman_rules import (
    BODY_CODES,
    DIRECTION_BINDINGS,
    GROUP_CODES,
    METHOD_CODES,
    TITLE_CODES,
    import_foreman_rules,
    load_foreman_rule_catalog,
)
from yuxi.services.content_service import validate_rule_bundle_for_publish


def _source_bundle() -> dict:
    return {
        "methods": deepcopy(METHODS),
        "title_formulas": deepcopy(TITLE_FORMULAS),
        "content_formulas": deepcopy(BODY_FORMULAS),
        "content_types": deepcopy(CONTENT_TYPES),
        "combination_rules": [],
        "formula_patterns": [],
        "variables": [
            {
                "code": code,
                "name": name,
                "value_type": value_type,
                "unit_schema": {},
                "evidence_policy": {"required": evidence_required},
                "sensitivity": sensitivity,
                "allowed_usages": ["title", "body"],
                "validation_schema": {},
            }
            for code, name, value_type, sensitivity, evidence_required in VARIABLES
            if code != "quote_type"
        ],
    }


def test_foreman_catalog_keeps_case_modes_and_adds_daily_work_mode() -> None:
    catalog = load_foreman_rule_catalog()

    assert {item["code"] for item in catalog["methods"]} == METHOD_CODES
    assert {item["code"] for item in catalog["title_formulas"]} == TITLE_CODES
    assert {item["code"] for item in catalog["content_formulas"]} == BODY_CODES
    assert {item["id"] for item in catalog["combination_rules"]} == GROUP_CODES
    assert {code for group in catalog["combination_rules"] for code in group["content_type_codes"]} == {
        item["code"] for item in CONTENT_TYPES
    }
    assert all(item["industry_scope"] == ["decoration"] for item in catalog["methods"])
    assert all((item["output_schema"].get("max_persona_advantages") or 0) <= 3 for item in catalog["content_formulas"])
    price_modes = {"FRB01", "FRB06", "FRB07", "FRB08", "FRB09"}
    assert all(
        "quote_type" in item["required_variables"]
        for item in catalog["content_formulas"]
        if item["code"] in price_modes
    )

    groups = {item["content_type_codes"][0]: item for item in catalog["combination_rules"]}
    assert set(groups) == set(DIRECTION_BINDINGS)
    assert groups["CT01"]["title_formula_candidate_codes"] == ["FRT20", "FRT05"]
    assert groups["CT01"]["body_formula_candidate_codes"] == ["FRB02"]
    assert groups["CT02"]["title_formula_candidate_codes"] == ["FRT21", "FRT01", "FRT05", "FRT07", "FRT08", "FRT09"]
    assert groups["CT02"]["body_formula_candidate_codes"] == ["FRB01"]
    assert groups["CT07"]["title_formula_candidate_codes"] == ["FRT22", "FRT12"]
    assert groups["CT07"]["body_formula_candidate_codes"] == ["FRB05"]
    assert groups["CT06"]["title_formula_candidate_codes"] == ["FRT23", "FRT16"]
    assert all(
        "FRT06" not in group["title_formula_candidate_codes"]
        for direction, group in groups.items()
        if direction not in {"CT03", "CT04", "CT05"}
    )
    for direction, expected in DIRECTION_BINDINGS.items():
        group = groups[direction]
        blueprint = group["source_metadata"]["composition_blueprint"]
        assert group["method_members"] == [{"method_code": expected["method"], "role": "primary", "order": 1}]
        assert group["body_formula_candidate_codes"] == (
            ["FRB11", "FRB13", "FRB14", "FRB15", "FRB16"] if direction == "CT06" else [expected["body"]]
        )
        assert group["source_metadata"]["topic_type"] == expected["topic_type"]
        assert next(item for item in blueprint["phrase_composition"] if item["layer_code"] == "content_purpose")[
            "allowed_groups"
        ] == [expected["content_group"]]
        assert [item["layer_code"] for item in blueprint["phrase_composition"]] == [
            item["code"] for item in blueprint["layer_sequence"]
        ]
    assert "evidence" not in {
        item["code"] for item in groups["CT02"]["source_metadata"]["composition_blueprint"]["layer_sequence"]
    }
    expected_phrase_rules = {
        "CT01": [
            ("persona", "random", 1, 2, []),
            ("business", "random", 2, 4, []),
            ("content_purpose", "fixed", 1, 1, ["案例"]),
            ("user_value", "random", 1, 2, ["案例", "方案", "工艺"]),
            ("content_structure", "all", 1, None, []),
            ("evidence", "available", 1, 2, ["工地照片", "工艺节点"]),
            ("conversion", "random", 2, 2, []),
        ],
        "CT02": [
            ("persona", "random", 2, 4, []),
            ("business", "random", 1, 2, []),
            ("content_purpose", "fixed", 1, 1, ["报价清单"]),
            ("user_value", "random", 1, 1, ["价格参考", "预算"]),
            ("content_structure", "all", 1, None, []),
            ("conversion", "random", 2, 2, []),
        ],
    }
    quote_rule = [
        ("persona", "random", 3, 5, []),
        ("business", "random", 1, 1, []),
        ("content_purpose", "fixed", 1, 1, ["施工报价"]),
        ("user_value", "random", 1, 1, ["价格参考", "案例", "预算"]),
        ("content_structure", "all", 1, None, []),
        ("evidence", "fixed", 1, 1, ["真实报价"]),
        ("conversion", "random", 2, 2, []),
    ]
    expected_phrase_rules.update({code: quote_rule for code in ("CT03", "CT04", "CT05")})
    expected_phrase_rules["CT06"] = [
        ("persona", "random", 3, 5, []),
        ("business", "random", 1, 1, []),
        ("content_purpose", "fixed", 1, 1, ["工艺展示"]),
        ("user_value", "random", 1, 1, ["避坑", "工艺", "案例"]),
        ("content_structure", "all", 1, None, []),
        ("evidence", "available", 1, 2, ["工地照片", "工艺节点"]),
        ("conversion", "random", 2, 2, []),
    ]
    expected_phrase_rules["CT07"] = [
        ("persona", "all", 1, None, []),
        ("business", "random", 2, 4, []),
        ("content_purpose", "fixed", 1, 1, ["自我介绍"]),
        ("user_value", "random", 1, 2, ["避坑", "方案", "工艺", "预算"]),
        ("content_structure", "all", 1, None, []),
        ("evidence", "available", 1, 2, []),
        ("conversion", "random", 2, 2, []),
    ]
    for direction, expected in expected_phrase_rules.items():
        assert [
            (
                item["layer_code"],
                item["selection"],
                item["min_groups"],
                item["max_groups"],
                item["allowed_groups"],
            )
            for item in groups[direction]["source_metadata"]["composition_blueprint"]["phrase_composition"]
        ] == expected
    assert all(
        "quote_type" in item["required_variable_codes"]
        for item in catalog["combination_rules"]
        if set(item["body_formula_candidate_codes"]) & price_modes
    )


def test_each_foreman_title_formula_compiles_a_material_manifest_with_one_of_groups() -> None:
    bundle = import_foreman_rules(_source_bundle())

    for title_code in sorted(TITLE_CODES):
        rule = next(item for item in bundle["combination_rules"] if title_code in item["title_formula_candidate_codes"])
        direction = rule["content_type_codes"][0]
        catalog = build_strategy_candidates(
            bundle,
            industry_slug="decoration",
            direction_code=direction,
            rule_version_id="content-rules-platform-test",
        )
        selected_rule = next(item for item in catalog["source_rules"] if item["id"] == rule["id"])
        method_codes = [item["method_code"] for item in selected_rule["method_members"]]
        order = create_production_order(
            task_id=f"task-{title_code.lower()}",
            catalog=catalog,
            group_id=selected_rule["id"],
            creation_method_codes=method_codes,
            title_formula_code=title_code,
            body_formula_code=selected_rule["body_formula_candidate_codes"][0],
        )

        manifest = build_material_manifest(catalog=catalog, order=order)
        formula = next(item for item in catalog["title_formulas"] if item["code"] == title_code)
        requirement_by_code = {item.variable_code: item for item in manifest.requirements}
        for slot in formula["source_content"]["slot_schema"]:
            variable_codes = slot["variable_codes"]
            if not variable_codes:
                continue
            group = f"title:{slot['code']}"
            assert any(
                group in (requirement_by_code[code].validation_schema.get("alternative_groups") or [])
                for code in variable_codes
            )


def test_import_scopes_legacy_rules_away_from_decoration_and_is_idempotent() -> None:
    source = _source_bundle()
    imported = import_foreman_rules(source)

    assert source == _source_bundle()
    assert import_foreman_rules(imported) == imported
    assert {item["code"] for item in imported["methods"] if item["industry_scope"] == ["decoration"]} == METHOD_CODES
    assert all(
        "decoration" not in item["industry_scope"]
        for item in imported["methods"]
        if item["code"] in {"M01", "M02", "M03", "M04", "S01"}
    )
    assert validate_rule_bundle_for_publish(imported)["errors"] == []


def test_decoration_candidates_use_only_foreman_rules() -> None:
    bundle = import_foreman_rules(_source_bundle())

    candidates = build_strategy_candidates(
        bundle,
        industry_slug="decoration",
        direction_code="CT03",
        rule_version_id="content-rules-platform-test",
    )

    assert {item["code"] for item in candidates["methods"]} == METHOD_CODES
    assert {item["code"] for item in candidates["title_formulas"]}.issubset(TITLE_CODES)
    assert {item["code"] for item in candidates["content_formulas"]} == {"FRB07"}
    assert all(row[0].startswith("FRT") and row[1].startswith("FRB") for row in candidates["valid_formula_pairs"])
    assert all(row[1] == "FRB07" for row in candidates["valid_formula_pairs"])
    assert candidates["direction_blueprint"]["content_type"] == "单价+面积"
    assert next(
        item for item in candidates["direction_blueprint"]["phrase_composition"] if item["layer_code"] == "evidence"
    )["allowed_groups"] == ["真实报价"]


def test_auto_direction_candidates_keep_each_foreman_blueprint_isolated() -> None:
    bundle = import_foreman_rules(_source_bundle())

    candidates = build_strategy_candidates(
        bundle,
        industry_slug="decoration",
        direction_code=None,
        rule_version_id="content-rules-platform-test",
        auto_direction=True,
    )

    assert [item["code"] for item in candidates["direction_options"]] == [f"CT{index:02d}" for index in range(1, 8)]
    assert {item["code"]: item["body_formula_codes"] for item in candidates["direction_options"]} == {
        code: (["FRB11", "FRB13", "FRB14", "FRB15", "FRB16"] if code == "CT06" else [binding["body"]])
        for code, binding in DIRECTION_BINDINGS.items()
    }
    assert all(item["direction_blueprint"] for item in candidates["direction_options"])


def test_non_decoration_candidates_keep_legacy_catalog() -> None:
    bundle = import_foreman_rules(_source_bundle())
    bundle["combination_rules"] = [
        {
            "id": "food-test",
            "enabled": True,
            "schema_version": 3,
            "content_type_codes": ["CT01"],
            "industry_scope": ["food"],
            "combination_type": "single",
            "method_members": [{"method_code": "M01", "role": "primary", "order": 1}],
            "title_formula_candidate_codes": ["T01"],
            "body_formula_candidate_codes": ["C01"],
            "scenario_description": "餐饮测试",
        }
    ] + bundle["combination_rules"]

    candidates = build_strategy_candidates(
        bundle,
        industry_slug="food",
        direction_code=None,
        rule_version_id="content-rules-platform-test",
    )

    assert {item["code"] for item in candidates["methods"]} == {"M01", "M02", "M03", "M04", "S01"}
    assert {item["code"] for item in candidates["title_formulas"]} == {"T01"}
    assert {item["code"] for item in candidates["content_formulas"]} == {"C01"}


def test_persona_self_recommend_allows_identity_without_case_pain_or_quote():
    from yuxi.content.control.workflow.creation_plan import _resolve_rule_and_formulas, build_fact_index

    bundle = import_foreman_rules(_source_bundle())
    catalog = build_strategy_candidates(
        bundle, industry_slug="decoration", direction_code="CT07", rule_version_id="rules-test"
    )
    catalog["title_formulas"] = bundle["title_formulas"]
    catalog["content_formulas"] = bundle["content_formulas"]
    fact_index = build_fact_index(
        {
            "form_values": {
                "location": "长沙市",
                "product": "定制化家装咨询",
                "persona_fact": "家装顾问，专注落地沟通",
                "advantages": ["设计施工主辅材一站式衔接", "开工前对齐尺寸材质与节点"],
            }
        },
        {"items": []},
    )
    rule, title, body, methods, missing = _resolve_rule_and_formulas(catalog, fact_index)
    assert missing == []
    assert title["code"] in {"FRT22", "FRT12"}
    assert body["code"] == "FRB05"
    assert [item["code"] for item in methods] == ["FRM05"]
    assert rule["content_type_codes"] == ["CT07"]
    order = create_production_order(
        task_id="persona",
        catalog=catalog,
        group_id=rule["id"],
        creation_method_codes=["FRM05"],
        title_formula_code=title["code"],
        body_formula_code="FRB05",
    )
    required = {
        item.variable_code
        for item in build_material_manifest(catalog=catalog, order=order).requirements
        if item.required
    }
    assert not required & {"pain", "result", "quote_type"}


def test_intro_daily_upgrade_preserves_cases_quotes_and_custom_rules():
    from yuxi.content.v3.foreman_rules import upgrade_intro_daily_rules

    before = import_foreman_rules(_source_bundle())
    title = next(item for item in before["title_formulas"] if item["code"] == "FRT12")
    title["source_content"]["slot_schema"][1]["lexicon_codes"] = ["title.audience"]
    persona = next(item for item in before["combination_rules"] if item["content_type_codes"] == ["CT07"])
    persona["title_formula_candidate_codes"] = ["FRT12"]
    persona["method_members"][0]["method_code"] = "FRM02"
    persona["body_formula_candidate_codes"] = ["FRB02"]
    persona["hard_conditions"]["allowed_formula_pairs"] = [["FRT12", "FRB02"]]
    before["methods"][0]["principle"] = "保留运营自定义原则"
    original = deepcopy(before)

    after = upgrade_intro_daily_rules(before)

    assert before == original
    assert upgrade_intro_daily_rules(after) == after
    fixed = next(item for item in after["combination_rules"] if item["content_type_codes"] == ["CT07"])
    assert fixed["method_members"] == [{"method_code": "FRM05", "role": "primary", "order": 1}]
    assert fixed["title_formula_candidate_codes"] == ["FRT22", "FRT12"]
    assert fixed["body_formula_candidate_codes"] == ["FRB05"]
    assert [item for item in after["combination_rules"] if item["content_type_codes"] != ["CT07"]] == [
        item for item in before["combination_rules"] if item["content_type_codes"] != ["CT07"]
    ]
    assert next(item for item in after["title_formulas"] if item["code"] == "FRT12")["source_content"]["slot_schema"][
        1
    ]["lexicon_codes"] == []
    assert validate_rule_bundle_for_publish(after)["errors"] == []
