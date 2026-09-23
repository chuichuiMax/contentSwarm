from __future__ import annotations

from copy import deepcopy

import pytest

from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
from yuxi.content.control.workflow.creation_plan import _missing_title_formula_variables
from yuxi.content.model.contracts import (
    ContentNodeResultCollector,
    ContractDomainContext,
    get_contract_model,
    validate_content_node_result,
)
from yuxi.content.v3.formula_lexicons import get_formula_lexicon_requirements
from yuxi.content.v3.foreman_rules import load_foreman_rule_catalog
from yuxi.content.v3.title_formula_slots import enrich_decoration_title_formula


FORMULA_EXPECTATIONS = {
    "FRT01": (["地域", "面积/房型", "业务", "价格"], "天津135平叠拼半包8.9w"),
    "FRT02": (["地域", "需求/业务", "价格", "结果"], "深圳90年代老小区翻新，4.2w爸妈觉得值"),
    "FRT03": (["价格", "房屋/业务", "情绪结果"], "半包6w的家，装成这样我真的已经尽力"),
    "FRT04": (["地域", "人群/痛点", "房屋", "价格"], "长沙预算有限，82㎡半包4.5W"),
    "FRT05": (["地域", "业务", "用户最关心的利益点"], "长沙半包报价公开，170㎡大平层10W出头"),
    "FRT06": (["地域", "身份", "业务/案例", "价格"], "深圳工长自荐｜67㎡两房改造4.5W"),
    "FRT07": (["地域", "装修工价", "年份/标准"], "深圳装修｜2026装修各工种工费标准"),
    "FRT08": (["地域", "项目", "单价/标准"], "长沙装修看这里，全房拆除工价很全"),
    "FRT09": (["地域", "面积", "业务", "总价"], "东莞99㎡三室一厅半包报价清单"),
    "FRT10": (["总价", "半包/硬装", "地域", "面积"], "5.8W半包落地｜长沙装修108平三房"),
    "FRT11": (["地域", "面积/业务", "人工/辅材", "总价"], "长沙147㎡全屋装修实价单公开"),
    "FRT12": (["地域", "身份", "业务"], "坐标长沙，一个实在的装修工长自荐🙏"),
    "FRT13": (["地域", "身份", "开工/业务"], "上海设计师🔨开工大吉🏠感谢小红书业主信任"),
    "FRT14": (["地域", "身份", "工地巡检"], "天津装修｜工长巡检细节不能偷懒"),
    "FRT16": (["人群/工种", "工艺", "情绪"], "好瓦工的手艺，都藏在细节里"),
}


FORMULA_PASSING_CASES = [
    ("FRT01", "长沙89㎡半包4.5w", {"quantity": "89㎡", "scene": "半包", "title_price": "4.5w"}),
    ("FRT02", "长沙旧改4.5w放心", {"scene": "旧改", "title_price": "4.5w", "result": "放心"}),
    ("FRT03", "4.5w三房很值", {"title_price": "4.5w", "product": "三房", "result": "很值"}),
    ("FRT04", "长沙刚需三房4.5w", {"audience": "刚需", "product": "三房", "title_price": "4.5w"}),
    ("FRT05", "长沙半包报价透明", {"scene": "半包", "advantages": ["报价透明"]}),
    (
        "FRT06",
        "长沙工长半包4.5w",
        {"persona_fact": "装修工长，从业5年", "scene": "半包", "title_price": "4.5w"},
    ),
    ("FRT07", "长沙35元/㎡2026", {"title_price": "35元/㎡", "process": "2026"}),
    ("FRT08", "长沙拆墙35元/㎡", {"product": "拆墙", "title_price": "35元/㎡"}),
    ("FRT09", "长沙89㎡半包4.5w", {"quantity": "89㎡", "scene": "半包", "title_price": "4.5w"}),
    ("FRT10", "4.5w半包长沙89㎡", {"title_price": "4.5w", "scene": "半包", "quantity": "89㎡"}),
    ("FRT11", "长沙89㎡人工4.5w", {"quantity": "89㎡", "title_price_label": "人工", "title_price": "4.5w"}),
    ("FRT12", "长沙工长半包", {"persona_fact": "装修工长，从业5年", "scene": "半包"}),
    ("FRT13", "长沙工长水电", {"persona_fact": "装修工长，从业5年", "product": "工长、水电、泥瓦"}),
    ("FRT14", "长沙工长巡检", {"persona_fact": "装修工长，从业五年", "inspection": "工地巡检"}),
]


def _formula_by_code(code: str) -> dict:
    return next(item for item in load_foreman_rule_catalog()["title_formulas"] if item["code"] == code)


def _validation_state(
    *, formula: dict, title: str, variables: dict, formula_lexicon_bundle: dict | None = None
) -> dict:
    return {
        "selected_title": {"text": title},
        "content_brief": {
            "brand": {"name": "测试品牌"},
            "form_values": {"location": "长沙市", **variables},
        },
        "strategy_snapshot": {"title_formula": formula},
        "evidence_bundle": {"items": []},
        "production_pack": {
            "strategy_snapshot": {"title_formula": formula},
            "formula_lexicon_bundle": formula_lexicon_bundle or {},
            "material_quality_report": {"bindings": []},
            "materials": [],
        },
        "product_evidence_pack": {},
        "content_draft": {"body": "这是一段长度足够的正文。" * 20, "topics": [], "paragraph_evidence": []},
    }


@pytest.mark.unit
def test_all_foreman_title_formulas_define_reviewed_slots_and_reference_examples():
    formulas = {item["code"]: item for item in load_foreman_rule_catalog()["title_formulas"]}

    assert set(formulas) == set(FORMULA_EXPECTATIONS)
    for code, (expected_labels, expected_example) in FORMULA_EXPECTATIONS.items():
        formula = formulas[code]
        slots = formula["source_content"]["slot_schema"]
        assert [slot["label"] for slot in slots] == expected_labels
        assert formula["reference_examples"] == [expected_example]
        assert set(formula["variable_schema"]).issubset(
            {variable_code for slot in slots for variable_code in slot["variable_codes"]}
        )
        for slot in slots:
            assert slot["variable_codes"] or slot["lexicon_codes"]
            if "/" in slot["label"] and slot["code"] != "craft_role":
                assert len(slot["variable_codes"]) + len(slot["lexicon_codes"]) >= 2


@pytest.mark.unit
def test_concrete_location_and_area_slots_cannot_be_substituted_by_other_semantics():
    formulas = {item["code"]: item for item in load_foreman_rule_catalog()["title_formulas"]}

    for code in FORMULA_EXPECTATIONS:
        for slot in formulas[code]["source_content"]["slot_schema"]:
            if slot["label"] == "地域" or (code == "FRT10" and slot["label"] == "面积"):
                assert slot["lexicon_codes"] == []
    frt09_business = next(
        slot for slot in formulas["FRT09"]["source_content"]["slot_schema"] if slot["code"] == "business"
    )
    assert "title.house_type" not in frt09_business["lexicon_codes"]


@pytest.mark.unit
def test_v8_formula_is_enriched_without_overwriting_explicit_configuration():
    old_formula = {
        "code": "FRT01",
        "variable_schema": ["location", "quantity", "product", "title_price"],
        "source_content": {"business_formula": "旧版冻结公式"},
    }

    enriched = enrich_decoration_title_formula(old_formula)

    assert enriched["source_content"]["business_formula"] == "旧版冻结公式"
    assert [slot["label"] for slot in enriched["source_content"]["slot_schema"]] == FORMULA_EXPECTATIONS["FRT01"][0]
    assert enriched["reference_examples"] == [FORMULA_EXPECTATIONS["FRT01"][1]]
    assert "slot_schema" not in old_formula["source_content"]


@pytest.mark.unit
@pytest.mark.parametrize("formula_code", FORMULA_EXPECTATIONS)
def test_each_foreman_formula_treats_loaded_title_lexicons_as_slot_options(formula_code: str):
    formula = deepcopy(_formula_by_code(formula_code))
    formula["lexicon_codes"] = [
        item["code"] for item in get_formula_lexicon_requirements(formula_code, "FRB01")["title"]
    ]

    context = ContractDomainContext.from_governance(
        match_decision_snapshot={"selected_group_id": "FRG01", "eligible_title_formula_codes": [formula_code]},
        formula_selection_snapshot={
            "selected_title_formula_code": formula_code,
            "selected_body_formula_code": "FRB01",
        },
        evidence_bundle={"items": []},
        locked_versions={
            "industry_pack_version_id": "industry-pack-decoration-v1",
            "channel_profile_version_id": "channel-xiaohongshu-v1",
            "persona_profile_version_id": None,
            "rule_version_id": "content-rules-platform-v8",
            "title_formula_code": formula_code,
            "body_formula_code": "FRB01",
            "artifact_version_id": None,
        },
        locked_values={},
        strategy_snapshot={"title_formula": formula, "body_formula": {"code": "FRB01"}},
    )

    assert context.required_title_lexicon_codes == frozenset()
    assert context.allowed_title_lexicon_codes == frozenset(formula["lexicon_codes"])


@pytest.mark.unit
@pytest.mark.parametrize(("formula_code", "_title", "variables"), FORMULA_PASSING_CASES)
def test_each_foreman_formula_plan_accepts_one_available_variable_per_slot(
    formula_code: str, _title: str, variables: dict
):
    available = {"location", *variables}

    assert _missing_title_formula_variables(_formula_by_code(formula_code), available) == set()


@pytest.mark.unit
def test_plan_reports_one_repair_field_for_a_missing_one_of_slot():
    formula = _formula_by_code("FRT01")
    available = {"location", "scene", "title_price"}

    assert _missing_title_formula_variables(formula, available) == {"quantity"}


@pytest.mark.unit
def test_slot_formula_reports_only_the_lexicon_term_actually_used():
    context = ContractDomainContext(
        locked_title_formula_code="FRT04",
        locked_body_formula_code="FRB01",
        allowed_title_lexicon_codes=frozenset({"title.audience", "title.pain"}),
        allowed_title_lexicon_terms={
            "title.audience": frozenset({"刚需"}),
            "title.pain": frozenset({"预算有限"}),
        },
        allowed_evidence_by_usage={"title": frozenset(), "body": frozenset()},
    )
    payload = {
        "title": {
            "text": "长沙刚需三房报价",
            "formula_code": "FRT04",
            "evidence_ids": [],
            "lexicon_usage": [{"code": "title.audience", "selected_terms": ["刚需"]}],
        },
        "outline": {
            "body_formula_code": "FRB01",
            "sections": [{"section_id": "opening", "goal": "开篇", "evidence_ids": []}],
        },
        "draft": {
            "body": "正文",
            "topics": [],
            "paragraph_evidence": [],
            "body_formula_code": "FRB01",
            "lexicon_usage": [],
        },
    }

    validate_content_node_result("GeneratedContentResultV1", payload, context)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_locked_slot_lexicons_are_normalized_to_terms_present_in_the_title():
    context = ContractDomainContext(
        locked_title_formula_code="FRT04",
        locked_body_formula_code="FRB01",
        allowed_title_lexicon_codes=frozenset({"title.audience", "title.pain"}),
        allowed_title_lexicon_terms={
            "title.audience": frozenset({"刚需"}),
            "title.pain": frozenset({"预算有限"}),
        },
        locked_title_lexicon_terms={
            "title.audience": ("刚需",),
            "title.pain": ("预算有限",),
        },
        allowed_evidence_by_usage={"title": frozenset(), "body": frozenset()},
    )
    payload = {
        "title": {
            "text": "长沙刚需三房报价",
            "formula_code": "模型返回的错误公式",
            "evidence_ids": [],
            "lexicon_usage": [],
        },
        "outline": {
            "body_formula_code": "FRB01",
            "sections": [{"section_id": "opening", "goal": "开篇", "evidence_ids": []}],
        },
        "draft": {
            "body": "正文",
            "topics": [],
            "paragraph_evidence": [],
            "body_formula_code": "FRB01",
            "lexicon_usage": [],
        },
    }
    parsed = get_contract_model("GeneratedContentResultV1").model_validate(payload)
    runtime = type("Runtime", (), {"_required_skill_closure": [], "_activated_required_skills": []})()
    collector = ContentNodeResultCollector("GeneratedContentResultV1", context, runtime)

    await collector.submit(title=parsed.title, outline=parsed.outline, draft=parsed.draft)

    assert collector.finalize()["title"]["lexicon_usage"] == [{"code": "title.audience", "selected_terms": ["刚需"]}]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(("formula_code", "title", "variables"), FORMULA_PASSING_CASES)
async def test_each_foreman_title_formula_accepts_one_supported_value_per_slot(
    monkeypatch, formula_code: str, title: str, variables: dict
):
    monkeypatch.setattr(
        "yuxi.content.control.workflow.deterministic_node.validate_content",
        lambda **kwargs: {"status": "passed", "checks": []},
    )
    formula = _formula_by_code(formula_code)

    result = await V3DeterministicNodeHandler().execute(
        db=object(),
        node={"id": "deterministic_validate"},
        state=_validation_state(formula=formula, title=title, variables=variables),
        node_run_id=f"node-{formula_code.lower()}",
    )

    assert result["validation_report"] == {"status": "passed", "checks": []}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_frt01_accepts_bound_house_type_and_business_aliases_as_one_of_slots(monkeypatch):
    monkeypatch.setattr(
        "yuxi.content.control.workflow.deterministic_node.validate_content",
        lambda **kwargs: {"status": "passed", "checks": []},
    )
    formula = {"code": "FRT01", "variable_schema": ["location", "quantity", "product", "title_price"]}
    lexicons = {
        "selection": {
            "title": {
                "title.house_type": ["大三房"],
                "title.positioning": ["旧房改造"],
            }
        },
        "fact_bindings": {
            "title": {
                "title.house_type": [{"term": "大三房", "variable_codes": ["product"]}],
                "title.positioning": [{"term": "旧房改造", "variable_codes": ["scene"]}],
            }
        },
    }
    state = _validation_state(
        formula=formula,
        title="长沙大三房旧房改造1.206w",
        variables={
            "quantity": "120㎡",
            "product": "三室二厅",
            "scene": "老房翻新",
            "title_price": "1.206w",
        },
        formula_lexicon_bundle=lexicons,
    )

    passed = await V3DeterministicNodeHandler().execute(
        db=object(),
        node={"id": "deterministic_validate"},
        state=state,
        node_run_id="node-frt01-alias-passed",
    )
    assert passed["validation_report"] == {"status": "passed", "checks": []}

    blocked_state = deepcopy(state)
    blocked_state["selected_title"] = {"text": "长沙旧房改造1.206w"}
    blocked = await V3DeterministicNodeHandler().execute(
        db=object(),
        node={"id": "deterministic_validate"},
        state=blocked_state,
        node_run_id="node-frt01-property-missing",
    )
    title_check = next(
        item for item in blocked["validation_report"]["checks"] if item["code"] == "TITLE_REQUIRED_FACT_MISSING"
    )
    assert title_check["message"].count("面积/房型") == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("fact", "expected"),
    [
        ("我从事装修行业五年了", "五年"),
        ("30岁，5年装修工龄，技能：工长、水电、泥瓦", "工长"),
    ],
)
def test_intro_identity_uses_author_facts_instead_of_audience(fact, expected):
    from yuxi.content.control.workflow.deterministic_node import _required_title_fact_options

    formula = _formula_by_code("FRT12")
    options = _required_title_fact_options(
        {"form_values": {"location": "长沙市", "persona_fact": fact, "product": "装修"}},
        {"title_formula": formula},
        {"formula_lexicon_bundle": {"selection": {"title": {"title.audience": ["自装党"]}}}},
    )
    identity = next(values for key, values in options.items() if key.startswith("identity"))
    assert expected in identity
    assert "自装党" not in identity
    if expected == "工长":
        assert "30岁" not in identity and "5年" not in identity


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "title", "values"),
    [
        ("FRT16", "瓦工贴砖对缝真香", {"craft_role": ["瓦工"], "process": ["贴砖对缝"]}),
    ],
)
async def test_craft_emotion_slots_require_selected_expression(monkeypatch, code, title, values):
    monkeypatch.setattr(
        "yuxi.content.control.workflow.deterministic_node.validate_content",
        lambda **kwargs: {"status": "passed", "checks": []},
    )
    current = _validation_state(
        formula=_formula_by_code(code),
        title=title,
        variables=values,
        formula_lexicon_bundle={"selection": {"title": {"title.oral_emotion": ["真香"]}}},
    )
    handler = V3DeterministicNodeHandler()
    report = await handler.execute(db=object(), node={"id": "deterministic_validate"}, state=current, node_run_id="n")
    assert report["validation_report"] == {"status": "passed", "checks": []}
    current["selected_title"]["text"] = title.replace("真香", "")
    blocked = await handler.execute(db=object(), node={"id": "deterministic_validate"}, state=current, node_run_id="n")
    assert blocked["validation_report"]["status"] == "blocked"
