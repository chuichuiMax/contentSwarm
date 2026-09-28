import hashlib
from copy import deepcopy

import pytest
from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
from yuxi.content.model.single_blueprint import project_input, repair_scope, validate_and_assemble
from yuxi.content.v3.modular_rules import build_modular_rule_bundle


@pytest.fixture
def payload():
    def material(code, value, usage):
        return dict(
            material_type="business_fact",
            variable_codes=[code],
            evidence_ids=[code],
            payload={"value": value},
            governance={"allowed_usage": usage},
            source={"source_type": "manual_input"},
        )

    quote = "拆除：40元/㎡；水电：50元/㎡"
    digest = hashlib.sha256(quote.encode()).hexdigest()
    materials = [
        material("location", "北京", ["title", "body"]),
        material("persona_fact", "北京工长，6年工龄。沟通语气：有耐心。", ["body"]),
        material("process", ["工种能力：水电；服务优势：自有工人无转包"], ["body"]),
        material("title_price", "40元/㎡", ["title"]),
        material(
            "quote_block",
            dict(
                original_content=quote,
                content_hash=digest,
                insertion_policy="after-opening-paragraph-v1",
                render_policy="checkmark-lines-v1",
            ),
            ["body"],
        ),
    ]
    materials[-1]["id"] = "quote-material"
    materials[-1]["source"]["source_hash"] = digest
    pack = dict(
        frozen_at="2026-09-27T08:00:00Z",
        materials=materials,
        content_rule_bundle=build_modular_rule_bundle({}, single_blueprint=True),
        strategy_snapshot=dict(
            content_direction="CT02",
            title_formula=dict(
                code="FRT07",
                source_content=dict(slot_schema=[dict(code="location", variable_codes=["location"], lexicon_codes=[])]),
            ),
            body_formula=dict(code="FRB06"),
        ),
        channel_profile=dict(title_constraints={"max_length": 20}, body_constraints={"max_length": 1000}),
        reference_snapshot=dict(
            reference_blueprint=dict(
                content_block_sequence=["问题", "身份", "报价", "真实效果"], opening_hook="先提出问题"
            ),
            slot_mapping={"location": ["evidence_bundle.items.0.value"]},
        ),
    )
    # Existing assertions exercise historical frozen policy.
    pack["content_rule_bundle"]["single_blueprint"].pop("writing_mode", None)
    return dict(production_pack=pack, evidence_bundle={"items": [{"id": "location"}]}, content_brief={})


@pytest.fixture
def result():
    return dict(
        title={"text": "北京拆除单价40元/㎡", "facts": ["F1", "F4"]},
        topics=["装修"],
        blocks=[
            dict(id="b1", kind="text", text="看单价时也要看对应项目。", facts=[], blueprint_refs=["R1"]),
            dict(id="b2", kind="text", text="我在北京做工长6年。", facts=["F2"], blueprint_refs=["R2"]),
            dict(id="quote_block", kind="quote_ref", text="", facts=[], blueprint_refs=["R3"]),
        ],
        omissions=[{"ref": "R4", "reason": "没有本篇效果资料"}],
    )


def test_projection_has_one_structure_and_correct_fact_roles(payload):
    view = project_input(payload)
    assert view["reference"]["blocks"]["R1"] == "问题"
    assert set(view["reference"]) == {"id", "source_hash", "title", "body", "blocks"}
    assert view["facts"][2]["variables"] == ["capability_description"]
    assert "沟通语气" not in view["facts"][1]["value"]
    assert not {"body_formula", "generation_slots", "material_manifest"} & view.keys()
    assert view["quote"]["context"].startswith("✅ 拆除")
    assert "国标施工规范" not in str(view)


def test_ct02_title_uses_labor_standard_guidance_without_price_slots(payload, result):
    formula = payload["production_pack"]["strategy_snapshot"]["title_formula"]
    formula["source_content"]["slot_schema"].append(
        {"code": "price", "variable_codes": ["title_price"], "lexicon_codes": []}
    )
    view = project_input(payload)
    assert view["title_requirements"] == {"location（地域）": ("北京",)}
    assert view["title_guidance"]["publication_year"] == "2026"
    result["title"] = {"text": "北京装修丨2026工费标准（附明细）", "facts": ["F1"]}
    assert validate_and_assemble(result, payload)["title"]["text"] == result["title"]["text"]
    payload["production_pack"]["content_rule_bundle"]["single_blueprint"].pop("title_styles")
    with pytest.raises(ValueError, match="缺少"):
        validate_and_assemble(result, payload)
    assert "title_guidance" not in project_input(payload)


@pytest.mark.parametrize(
    "title,body,valid",
    [
        ("北京装修丨2026工费标准", "拆除明细", True),
        ("北京装修丨2026年工费标准", "拆除明细", True),
        ("北京装修丨2027工费标准", "拆除明细", False),
        ("北京拆除2026元", "拆除明细", False),
        ("北京装修丨2026工费标准", "人工2026元", False),
    ],
)
def test_publication_year_is_not_a_source_for_arbitrary_prices(title, body, valid):
    from yuxi.content.validators import validate_content

    report = validate_content(
        title=title,
        body=body,
        topics=[],
        brief={},
        evidence_bundle={"items": []},
        strategy={},
        title_publication_year="2026",
    )
    assert (not any(c["code"] == "FACT_NUMBER_WITHOUT_SOURCE" for c in report["checks"])) == valid


def test_publication_year_only_passes_title_contract_validation(payload):
    from yuxi.content.model.contracts.content_nodes import (
        ContractDomainContext,
        ContractDomainValidationError,
        _validate_numbers,
    )

    context = ContractDomainContext(single_blueprint_input=payload)
    _validate_numbers("北京装修丨2026工费标准", context, "title.text", "title")
    with pytest.raises(ContractDomainValidationError):
        _validate_numbers("从业2026年", context, "draft.body", "body")


def test_writing_requirements_reach_author_reviewer_and_repair(payload, result):
    payload["production_pack"]["writing_request"] = "面向第一次装修的业主，保留参考的叙述节奏。"
    payload["content_brief"].update(required_terms=["旧房改造"], forbidden_terms=["闭眼入"])
    expected = {
        "request": payload["production_pack"]["writing_request"],
        "required_terms": ["旧房改造"],
        "forbidden_terms": ["闭眼入"],
    }
    assert project_input(payload)["writing_requirements"] == expected
    payload.update(
        content_draft=validate_and_assemble(result, payload)["draft"],
        validation_report={"checks": []},
        required_review_codes=["BODY_VALUE"],
    )
    assert project_input(payload, review=True)["writing_requirements"] == expected
    payload["validation_report"] = {"checks": [{"code": "CHANNEL_TITLE_LONG", "level": "error", "location": "title"}]}
    assert project_input(payload)["writing_requirements"] == expected


def test_content_prompt_uses_writing_contract_without_chat_style():
    from types import SimpleNamespace

    from yuxi.agents.buildin.chatbot.prompt import build_prompt_with_context

    context = SimpleNamespace(
        system_prompt="仅执行当前创作职责。",
        _content_node_input=SimpleNamespace(input_contract="SingleBlueprintPromptV1"),
    )
    prompt = build_prompt_with_context(context)
    assert context.system_prompt in prompt
    assert "尽可能详细" not in prompt and "减少使用 Emoji" not in prompt
    context._content_node_input = None
    assert "尽可能详细" in build_prompt_with_context(context)


@pytest.mark.asyncio
async def test_short_blueprint_body_is_not_padded_to_a_generic_minimum(payload, result):
    from yuxi.content.model.locked_blocks import quote_body_limits

    view = project_input(payload)
    assert view["body_limits"]["min_chars"] == 0
    assert (
        view["body_limits"]["max_chars"]
        == quote_body_limits(payload["production_pack"], view["quote"]["context"])["creative_body_max_chars"]
    )
    assembled = validate_and_assemble(result, payload)
    update = await V3DeterministicNodeHandler._compose_locked_quote_block(
        db=None,
        state={"production_pack": payload["production_pack"], "creative_content_draft": assembled["draft"]},
        node_run_id="test",
    )
    assert update["locked_block_composition"]["status"] == "composed"


def test_reference_anchors_are_style_only_and_slot_match_is_not_support(payload):
    payload["production_pack"]["content_rule_bundle"]["single_blueprint"].pop("compact_reference")
    payload["production_pack"]["reference_snapshot"]["reference_card"] = {
        "anchors": [{"section": "body", "start": 0, "end": 8, "quote": "客户怕啥？怕加钱！"}],
        "required_slots": [
            {
                "slot_key": "offer",
                "name": "行动收束",
                "description": "改写时必须邀请参观工地",
                "variable_codes": ["location"],
                "anchor": {"quote": "来我的工地看看"},
            }
        ],
    }
    view = project_input(payload)
    assert view["reference"]["expression_anchors"][0]["quote"] == "客户怕啥？怕加钱！"
    assert view["reference"]["slot_candidates"]["offer"]["support_status"] == "unassessed"
    assert view["reference"]["slot_candidates"]["offer"]["purpose"] == "行动收束"
    assert "改写时必须" not in str(view["reference"])
    assert "来我的工地看看" not in str(view["facts"])


def test_location_overlap_does_not_mark_service_offer_as_supported():
    from yuxi.content.control.workflow.creation_plan import build_fact_index, map_reference_slots

    card = {
        "schema_version": 2,
        "required_slots": [
            {
                "slot_key": "conversion_offer",
                "variable_codes": ["call_to_action", "audience", "location"],
                "match_mode": "any",
                "required": True,
            }
        ],
    }
    mapped = map_reference_slots(
        card, build_fact_index({"business_variables": {"location": "广州"}}, {}), mapped_facts_only=True, minimum=1
    )
    assert mapped["slot_mapping"]["conversion_offer"] == ["content_brief.business_variables.location"]
    assert mapped["support_status"] == "unassessed"
    assert mapped["field_coverage"] == pytest.approx(1 / 3)


def test_projection_excludes_unbound_rules_and_restricts_usage(payload):
    pack = payload["production_pack"]
    for m in pack["materials"]:
        m.setdefault("id", m["evidence_ids"][0])
    pack["materials"].append({**deepcopy(pack["materials"][0]), "material_type": "business_rule"})
    pack["material_manifest"] = {
        "requirements": [
            {
                "requirement_id": "location",
                "variable_code": "location",
                "allowed_usage": ["body"],
            }
        ]
    }
    pack["material_quality_report"] = {"bindings": [{"requirement_id": "location", "material_ids": ["location"]}]}
    view = project_input(payload)
    assert len(view["facts"]) == 1
    assert view["facts"][0]["allowed_usage"] == ["body"]


def test_empty_blueprint_adoption_cannot_claim_rewrite(payload, result):
    for block in result["blocks"]:
        block["blueprint_refs"] = []
    result["omissions"] = []
    with pytest.raises(ValueError, match="采用"):
        validate_and_assemble(result, payload)


def test_quote_alone_is_not_a_written_body(payload, result):
    result["blocks"] = [result["blocks"][-1]]
    with pytest.raises(ValueError, match="实际创作"):
        validate_and_assemble(result, payload)


def test_metadata_digits_are_not_promoted_to_single_blueprint_facts():
    from yuxi.content.rules import canonical_brief_facts

    brief = {"business_variables": {"external_serial_no": "002", "quantity": "85㎡"}}
    facts = canonical_brief_facts(brief, derive_numbers=False)
    assert not any("number" in codes for _, _, codes in facts)
    assert any(value == "85㎡" for _, value, _ in facts)
    brief["business_variables"]["number"] = 3
    assert ("number", 3, ("number",)) in canonical_brief_facts(brief, derive_numbers=False)


@pytest.mark.parametrize("code", ["CHANNEL_BODY_SHORT", "BODY_LENGTH_OUT_OF_RANGE"])
def test_information_repair_can_use_unwritten_facts_and_reorganize(payload, result, code):
    payload.update(
        content_draft=validate_and_assemble(result, payload)["draft"],
        validation_report={"checks": [{"code": code, "level": "error", "location": "body"}]},
    )
    view = project_input(payload)
    assert view["structure_editable"]
    assert "F3" in {f["id"] for f in view["facts"]}


def test_title_repair_is_scoped_and_preserves_body(payload, result):
    draft = validate_and_assemble(result, payload)["draft"]
    payload.update(
        content_draft=draft,
        validation_report={"checks": [{"code": "CHANNEL_TITLE_LONG", "level": "error", "location": "title"}]},
    )
    view = project_input(payload)
    assert view["current"] == draft["blueprint_content"]
    assert view["reference"] and view["topics"] and len(view["facts"]) == 4
    patch = dict(title={"text": "北京拆除40元/㎡", "facts": ["F1", "F4"]}, topics=None, blocks=[], omissions=None)
    fixed = validate_and_assemble(patch, payload, patch=True)
    assert fixed["draft"] == draft | {"blueprint_content": result | {"title": patch["title"]}}
    patch["blocks"] = [result["blocks"][0]]
    with pytest.raises(ValueError, match="未授权"):
        validate_and_assemble(patch, payload, patch=True)


@pytest.mark.parametrize(
    "change", ["duplicate_quote", "missing_quote", "unknown_fact", "wrong_usage", "unknown_blueprint"]
)
def test_invalid_references_fail(payload, result, change):
    if change == "duplicate_quote":
        result["blocks"].append(deepcopy(result["blocks"][-1]))
    elif change == "missing_quote":
        result["blocks"].pop()
    elif change == "unknown_fact":
        result["title"]["facts"] = ["F99"]
    elif change == "wrong_usage":
        result["blocks"][0]["facts"] = ["F4"]
    elif change == "unknown_blueprint":
        result["blocks"][0]["blueprint_refs"] = ["R99"]
    with pytest.raises(ValueError):
        validate_and_assemble(result, payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("persona_after_quote", [False, True])
async def test_quote_and_persona_have_no_fixed_position(payload, result, persona_after_quote):
    if persona_after_quote:
        result["blocks"][1], result["blocks"][2] = result["blocks"][2], result["blocks"][1]
    assembled = validate_and_assemble(result, payload)
    handler = V3DeterministicNodeHandler()
    # 足够原创文字，避免触发容量下限；报价仍处于两个文本块之后。
    result["blocks"][0]["text"] += "报价对应哪些项目、单位和范围，要一起看清楚。" * 5
    assembled = validate_and_assemble(result, payload)
    update = await handler._compose_locked_quote_block(
        db=None,
        state={"production_pack": payload["production_pack"], "creative_content_draft": assembled["draft"]},
        node_run_id="test",
    )
    body = update["content_draft"]["body"]
    assert (body.index("我在北京") > body.index("✅ 拆除")) == persona_after_quote
    assert body.count("✅ 拆除") == 1
    assert update["locked_block_composition"]["status"] == "composed"


@pytest.mark.asyncio
async def test_compliance_replacement_persists_in_ordered_blocks(payload, result):
    result["blocks"][0]["text"] = "超值报价对应哪些项目，要一起看清楚。" * 7
    assembled = validate_and_assemble(result, payload)
    state = {
        "production_pack": payload["production_pack"],
        "content_draft": assembled["draft"],
        "selected_title": assembled["title"],
        "compliance_policies": [
            {"rules": [{"id": "test", "pattern": "超值", "replacement": "项目", "action": "replace"}]}
        ],
    }
    adapted = await V3DeterministicNodeHandler._adapt_to_channel(db=None, state=state, node_run_id="test")
    assert "超值" not in adapted["content_draft"]["blueprint_content"]["blocks"][0]["text"]
    assert adapted["content_draft"]["body"] == "\n\n".join(
        block["text"] for block in adapted["content_draft"]["blueprint_content"]["blocks"] if block["kind"] == "text"
    )
    composed = await V3DeterministicNodeHandler._compose_locked_quote_block(
        db=None, state={**state, **adapted}, node_run_id="test"
    )
    assert "超值" not in composed["content_draft"]["body"]
    assert composed["content_draft"]["body"].count("✅ 拆除") == 1


@pytest.mark.asyncio
async def test_knowledge_replacements_cover_title_blocks_topics_and_quote(payload, result):
    pack = payload["production_pack"]
    rules = pack["content_rule_bundle"]["runtime_rules"]["viral-platform-expression"]
    rules["forbidden_replacements"] = {"报价": "报J", "价": "jia"}
    rules["forbidden_lexicon"] = {"snapshot_hash": "test", "alternatives": {"报价": ["报J"], "价": ["jia"], "APP": []}}
    quote = pack["materials"][-1]
    original = "总价：100元；拆除：40元/㎡"
    digest = hashlib.sha256(original.encode()).hexdigest()
    quote["payload"]["value"].update(original_content=original, content_hash=digest)
    quote["source"]["source_hash"] = digest
    result["title"]["text"] = "北京拆除报价"
    result["blocks"][0]["text"] = "这份报价含拆除单价。"
    result["topics"] = ["装修报价"]
    assembled = validate_and_assemble(result, payload)
    state = {"production_pack": pack, "content_draft": assembled["draft"], "selected_title": assembled["title"]}
    update = await V3DeterministicNodeHandler._adapt_to_channel(db=None, state=state, node_run_id="test")
    assert update["selected_title"]["text"] == "北京拆除报J"
    assert update["content_draft"]["topics"] == ["装修报J"]
    assert update["content_draft"]["blueprint_content"]["blocks"][0]["text"] == "这份报J含拆除单jia。"
    composed = await V3DeterministicNodeHandler._compose_locked_quote_block(
        db=None, state={**state, **update}, node_run_id="test"
    )
    assert "✅ 总jia：100元" in composed["content_draft"]["body"]
    assert composed["locked_block_composition"]["content_hash"] == digest
    assert quote["payload"]["value"]["original_content"] == original
    assert project_input(payload)["platform_rules"]["forbidden_alternatives"]["APP"] == []


def test_legacy_rule_bundle_unchanged():
    old = build_modular_rule_bundle({})
    new = build_modular_rule_bundle({}, single_blueprint=True)
    assert "single_blueprint" not in old
    assert old["runtime_rules"]["viral-persona-author"]["opening_required"] is True
    assert "viral-persona-author" not in new["runtime_rules"]
    assert old["bundle_hash"] != new["bundle_hash"]


def test_compact_reference_keeps_original_without_duplicate_extracted_instructions(payload):
    reference = payload["production_pack"]["reference_snapshot"]
    reference["body"] = "原文用具体交流展开，再进入清单。"
    reference["reference_blueprint"]["paragraph_rhythm"] = "报价后必须写提醒"
    reference["reference_card"] = {
        "required_slots": [{"slot_key": "advice", "description": "改写时必须保留提醒"}],
        "anchors": [{"quote": "原文提醒锚点"}],
    }
    view = project_input(payload)
    assert view["reference"]["body"] == reference["body"]
    assert view["reference"]["blocks"]
    assert "报价后必须写提醒" not in str(view)
    assert "改写时必须保留提醒" not in str(view)
    assert "原文提醒锚点" not in str(view)


def test_writing_design_reaches_review_but_not_visible_body(payload, result):
    result["writing_design"] = "从读者的具体问题展开，用报价回应，再接着聊由谁施工。"
    draft = validate_and_assemble(result, payload)["draft"]
    assert draft["blueprint_content"]["writing_design"] == result["writing_design"]
    assert result["writing_design"] not in draft["body"]
    payload["content_draft"] = draft
    payload["validation_report"] = {"status": "passed", "checks": []}
    payload["required_review_codes"] = ["FACTUAL_ACCURACY", "WRITING_QUALITY"]
    assert project_input(payload, review=True)["draft"]["writing_design"] == result["writing_design"]
    review_input = project_input(payload, review=True)
    assert review_input["quote"]["read_only"] is True
    assert review_input["quote"]["context"] == project_input({**payload, "content_draft": None})["quote"]["context"]
    payload["review_report"] = {
        "checks": [{"code": "WRITING_QUALITY", "status": "blocked", "location": "content", "message": "重复"}]
    }
    patch = {**result, "title": None, "topics": None, "writing_design": "删掉重复的结尾，报价回应完问题后结束。"}
    repaired = validate_and_assemble(patch, payload, patch=True)["draft"]
    assert repaired["blueprint_content"]["writing_design"] == patch["writing_design"]
    assert patch["writing_design"] not in repaired["body"]


def test_legacy_result_does_not_gain_empty_writing_design(payload, result):
    from yuxi.content.model.contracts.content_nodes import SingleBlueprintResultV1

    normalized = SingleBlueprintResultV1.model_validate(result).model_dump(mode="json")
    assert validate_and_assemble(normalized, payload)["draft"]["blueprint_content"] == result


@pytest.mark.asyncio
async def test_result_tool_preserves_text_and_quote_union(payload, result, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from yuxi.content.model.contracts.content_nodes import (
        ContentNodeResultCollector,
        ContractDomainContext,
        build_content_result_tool,
    )

    monkeypatch.setattr("yuxi.services.run_queue_service.append_content_runtime_event", AsyncMock())
    collector = ContentNodeResultCollector(
        contract_name="SingleBlueprintResultV1",
        domain_context=ContractDomainContext(single_blueprint_input=payload, allowed_numbers=frozenset({"40", "6"})),
        runtime_context=SimpleNamespace(),
    )
    response = await build_content_result_tool(collector).ainvoke(result)
    assert response["accepted"]
    assert collector.finalize()["draft"]["blueprint_content"] == result


def test_partial_blueprint_adoption_can_explain_omitted_details(payload, result):
    result["omissions"].append({"ref": "R1", "reason": "仅采用问题角度，没有自身案例事实"})
    result["blocks"][-1]["facts"] = ["F3"]
    assembled = validate_and_assemble(result, payload)
    assert assembled["draft"]["blueprint_content"]["blocks"][-1]["facts"] == []


def test_legacy_frozen_policy_keeps_local_repair_behavior(payload, result):
    payload["production_pack"]["content_rule_bundle"]["single_blueprint"].pop("full_context_repair")
    draft = validate_and_assemble(result, payload)["draft"]
    payload.update(
        content_draft=draft,
        review_report={
            "checks": [{"code": "NATURAL_EXPRESSION", "status": "blocked", "location": "b1", "message": "修改重复表述"}]
        },
    )
    scope = repair_scope(payload)
    assert scope["editable_blocks"] == ["b1"]
    view = project_input(payload)
    assert "title_requirements" not in view and "topics" not in view
    assert "F3" not in {fact["id"] for fact in view["facts"]}
    patch = {
        "title": None,
        "topics": None,
        "blocks": [{**result["blocks"][0], "text": "先把这份项目单价看清楚。"}],
        "omissions": None,
    }
    fixed = validate_and_assemble(patch, payload, patch=True)["draft"]["blueprint_content"]
    assert fixed["blocks"][1:] == result["blocks"][1:]
    assert fixed["title"] == result["title"] and fixed["topics"] == result["topics"]


def test_app_capabilities_are_not_process_and_tone_is_not_fact():
    from types import SimpleNamespace

    from yuxi.services.content_service import _separate_app_persona_facts

    structured = {
        "age": "45",
        "workYears": "6",
        "serviceCity": "北京",
        "introduction": "干活踏实",
        "skills": ["工长"],
        "serviceAdvantages": ["自有工人无转包"],
        "tone": "有耐心",
    }
    compiled = {
        "persona": {"structured": structured, "tone": "有耐心"},
        "business_variables": {
            "process": "工种能力：工长；服务优势：自有工人无转包",
            "scene": "旧房改造 大三房 三室二厅",
            "product": "三室二厅",
            "content_tags": ["旧房改造"],
            "project_site": "成都青羊老小区",
        },
        "form_values": {"craft_and_materials": "工种能力：工长；服务优势：自有工人无转包"},
    }
    legacy = deepcopy(compiled)
    _separate_app_persona_facts(legacy, SimpleNamespace(workflow_version_id="legacy"))
    assert legacy == compiled
    _separate_app_persona_facts(compiled, SimpleNamespace(workflow_version_id="content-workflow-single-blueprint-v1"))
    assert "process" not in compiled["business_variables"]
    assert "craft_and_materials" not in compiled["form_values"]
    assert "有耐心" not in compiled["business_variables"]["persona_fact"]
    assert compiled["persona"]["structured"] == structured
    assert compiled["business_variables"]["scene"] == "旧房改造，三室二厅"
    assert compiled["business_variables"]["case_background"] == "成都青羊老小区"


@pytest.mark.parametrize("source", ["dangjia", "content_studio_case", "manual"])
def test_only_app_generated_pain_is_removed(source):
    from types import SimpleNamespace

    from yuxi.services.content_service import _separate_app_persona_facts

    compiled = {
        "persona": {"structured": {"introduction": "我在北京做工长", "serviceCity": "北京"}},
        "business_variables": {"external_source": source, "pain": ["想了解报价"], "pain_points": ["想了解报价"]},
        "form_values": {"pain": ["想了解报价"]},
    }
    legacy = deepcopy(compiled)
    _separate_app_persona_facts(legacy, SimpleNamespace(workflow_version_id="legacy"))
    assert legacy == compiled
    _separate_app_persona_facts(compiled, SimpleNamespace(workflow_version_id="content-workflow-single-blueprint-v3"))
    assert ("pain" in compiled["business_variables"]) == (source == "manual")
    assert ("pain_points" in compiled["business_variables"]) == (source == "manual")
    assert ("pain" in compiled["form_values"]) == (source == "manual")


def test_app_quote_format_is_not_a_tutorial_writing_request(payload):
    payload["content_brief"]["business_variables"] = {"external_source": "content_studio_case"}
    payload["production_pack"]["writing_request"] = "长沙115㎡三室二厅，单价面积施工报价"
    view = project_input(payload)
    assert "单价面积" not in view["writing_requirements"]["request"]
    assert "所选参考" in view["writing_requirements"]["request"]


def test_global_repetition_repair_allows_merging_but_not_dropping_quote(payload, result):
    payload.update(
        content_draft=validate_and_assemble(result, payload)["draft"],
        review_report={"checks": [{"code": "NATURAL_EXPRESSION", "status": "blocked", "location": "content"}]},
    )
    assert repair_scope(payload)["structure_editable"]
    merged = {
        **result["blocks"][0],
        "text": "看单价要看项目。我在北京做工长6年。",
        "facts": ["F2"],
        "blueprint_refs": ["R1", "R2"],
    }
    patch = {"title": None, "topics": None, "blocks": [merged, result["blocks"][-1]], "omissions": result["omissions"]}
    assert len(validate_and_assemble(patch, payload, patch=True)["draft"]["blueprint_content"]["blocks"]) == 2
    patch["blocks"].pop()
    with pytest.raises(ValueError, match="报价引用数量"):
        validate_and_assemble(patch, payload, patch=True)


@pytest.mark.parametrize("code,name", [("CT01", "自我介绍"), ("CT06", "施工工艺"), ("CT07", "日常工作")])
def test_non_quote_app_persona_preserves_separate_tone(code, name):
    import json
    from types import SimpleNamespace

    from yuxi.content.schemas import ContentBriefPayload
    from yuxi.services.content_service import compile_content_brief

    structured = {
        "introduction": "我在北京做工长。",
        "serviceCity": "北京",
        "skills": ["工长"],
        "tone": "有耐心",
        "serviceAdvantages": ["自有工人无转包"],
    }
    task = SimpleNamespace(
        id="test",
        mode="quick",
        content_goal="测试",
        content_type_code=code,
        workflow_version_id="content-workflow-single-blueprint-v1",
    )
    brief, issues = compile_content_brief(
        task=task,
        template=SimpleNamespace(slug="decoration"),
        brief=ContentBriefPayload(
            user_request=json.dumps({"persona": structured, "requirementType": {"typeName": name}}, ensure_ascii=False)
        ),
    )
    assert not issues
    assert brief["persona"]["tone"] == "有耐心"
    assert brief["persona"]["structured"] == structured
    assert "有耐心" not in brief["business_variables"]["persona_fact"]


def test_full_context_body_repair_can_reorder_even_for_local_fact_issue(payload, result):
    payload.update(
        content_draft=validate_and_assemble(result, payload)["draft"],
        review_report={"checks": [{"code": "FACTUAL_ACCURACY", "status": "blocked", "location": "b2"}]},
    )
    view = project_input(payload)
    assert view["structure_editable"]
    assert len(view["current"]["blocks"]) == 3
    assert {fact["id"] for fact in view["facts"]} == {"F1", "F2", "F3", "F4"}
    assert "R4" in view["reference"]["blocks"]


def test_reference_full_text_and_source_reach_writer_and_reviewer(payload, result):
    reference = payload["production_pack"]["reference_snapshot"]
    reference.update(id="ref-1", source_hash="a" * 64, title="参考标题", body="原文开头。\n\n完整衔接与结尾。")
    payload["production_pack"]["materials"][0]["source"].update(locator="persona.serviceCity", source_hash="b" * 64)
    first = project_input(payload)
    assert first["reference"]["body"] == reference["body"]
    assert first["reference"]["source_hash"] == reference["source_hash"]
    assert first["facts"][0]["source"]["locator"] == "persona.serviceCity"
    payload.update(
        content_draft=validate_and_assemble(result, payload)["draft"], validation_report={}, required_review_codes=[]
    )
    assert project_input(payload, review=True)["reference"] == first["reference"]


def test_candidate_body_capacity_has_no_extra_650_limit(payload):
    view = project_input(payload)
    assert view["body_limits"]["max_chars"] == 1000 - len(view["quote"]["context"]) - 4
    assert view["layout"].get("max_paragraph_chars") is None


@pytest.mark.asyncio
async def test_candidate_graph_skips_semantic_review_and_keeps_quote_validation():
    from langgraph.checkpoint.memory import InMemorySaver
    from yuxi.agents.buildin.content_workflow.context import ContentWorkflowContext
    from yuxi.agents.buildin.content_workflow.graph import ContentWorkflowAgent
    from yuxi.content.v3.joint_workflow import WORKFLOW_SINGLE_BLUEPRINT, WORKFLOW_STANDARDIZED_FACTORY

    agent = ContentWorkflowAgent()
    agent.checkpointer = InMemorySaver()
    graph = await agent.get_graph(context=ContentWorkflowContext(workflow_definition=WORKFLOW_SINGLE_BLUEPRINT))
    diagram = graph.get_graph()
    assert "semantic_review" not in diagram.nodes
    assert any(
        edge.source == "validate_composed_content" and edge.target == "human_content_approval" for edge in diagram.edges
    )
    assert any(node["id"] == "semantic_review" for node in WORKFLOW_STANDARDIZED_FACTORY["nodes"])


@pytest.mark.asyncio
@pytest.mark.parametrize("review_enabled", [True, False])
@pytest.mark.parametrize("validation_status", ["passed", "blocked"])
async def test_candidate_approval_without_review_still_requires_program_checks(review_enabled, validation_status):
    from yuxi.agents.buildin.content_workflow.graph import ContentWorkflowAgent
    from yuxi.content.control.errors import ContentApplicationError

    agent = ContentWorkflowAgent()
    agent._workflow_definition = {"semantic_review_enabled": review_enabled}
    state = {
        "task_id": "task-1",
        "run_id": "run-1",
        "uid": "user-1",
        "state_version": 1,
        "selected_title": {"text": "标题"},
        "content_draft": {"body": "正文"},
        "validation_report": {"status": validation_status, "checks": []},
        "review_report": None,
    }
    node = {"id": "human_content_approval", "interrupt_type": "content_approval"}
    if review_enabled or validation_status == "blocked":
        with pytest.raises(ContentApplicationError, match="最终审批前仍有阻断报告"):
            await agent._v3_human_review(node, state)
    else:
        result = await agent._v3_human_review(node, state)
        assert result["approval_result"]["status"] == "approved"
        assert state["review_report"] is None


def test_candidate_review_codes_and_revision_budget_are_consistent(payload):
    from yuxi.content.control.workflow.revision import RevisionRouteController, resolve_revision_reason
    from yuxi.content.v3.joint_workflow import WORKFLOW_SINGLE_BLUEPRINT
    from yuxi.content.v3.modular_rules import required_review_codes

    assert required_review_codes(payload) == ("FACTUAL_ACCURACY", "WRITING_QUALITY")
    assert "expression_knowledge_policy" not in WORKFLOW_SINGLE_BLUEPRINT
    assert all("expression_guidance" not in n.get("state_inputs", []) for n in WORKFLOW_SINGLE_BLUEPRINT["nodes"])
    reason = resolve_revision_reason(
        title_validation_report=None,
        validation_report=None,
        review_report={"status": "blocked", "checks": [{"code": "FACTUAL_ACCURACY", "status": "blocked"}]},
    )
    assert reason == "BODY_EVIDENCE_FAILED"
    controller = RevisionRouteController()
    first = controller.decide(definition=WORKFLOW_SINGLE_BLUEPRINT, reason_code=reason, retry_counts={})
    assert first.status == "route"
    assert (
        controller.decide(
            definition=WORKFLOW_SINGLE_BLUEPRINT, reason_code=reason, retry_counts=first.retry_counts
        ).status
        == "limit_reached"
    )


@pytest.mark.asyncio
async def test_candidate_does_not_load_unused_expression_databases(payload):
    from yuxi.content.control.workflow.deterministic_node import load_expression_knowledge

    state = {
        "runtime_config_snapshot": {
            "content_rule_bundle": payload["production_pack"]["content_rule_bundle"],
            "expression_knowledge_policy": {"sources": [{"name": "不存在的资料库"}], "required": True},
        }
    }
    assert await load_expression_knowledge(state) == {
        "evidence_items": [],
        "citations": [],
        "expression_guidance": None,
    }


def test_candidate_compilation_keeps_original_request_and_user_terms():
    from types import SimpleNamespace
    from yuxi.content.schemas import ContentBriefPayload
    from yuxi.services.content_service import compile_content_brief

    task = SimpleNamespace(
        id="test",
        mode="quick",
        content_goal="测试",
        content_type_code="CT02",
        workflow_version_id="content-workflow-single-blueprint-v2",
    )
    brief, _ = compile_content_brief(
        task=task,
        template=SimpleNamespace(slug="decoration"),
        brief=ContentBriefPayload(
            user_request="用业主能听懂的话介绍报价，允许适当虚构叙事情境。",
            required_terms=["旧房改造"],
            forbidden_terms=["闭眼入"],
        ),
    )
    assert brief["original_user_request"] == "用业主能听懂的话介绍报价，允许适当虚构叙事情境。"
    assert brief["required_terms"] == ["旧房改造"]
    assert brief["forbidden_terms"] == ["闭眼入"]


@pytest.mark.asyncio
async def test_final_terms_and_duplicate_quote_amounts_block_before_review(payload, result):
    payload["production_pack"]["content_rule_bundle"]["single_blueprint"].pop("allow_price_anchor")
    payload["content_brief"].update(required_terms=["拆除"], forbidden_terms=["无忧"])
    result["blocks"][0]["text"] = "这项拆除40元，无忧开工。"
    assembled = validate_and_assemble(result, payload)
    state = {**payload, "content_draft": assembled["draft"], "selected_title": assembled["title"]}
    report = (await V3DeterministicNodeHandler._deterministic_validate(db=None, state=state, node_run_id="test"))[
        "validation_report"
    ]
    codes = {c["code"] for c in report["checks"]}
    assert report["status"] == "blocked"
    assert "QUOTE_AMOUNT_REPEATED" in codes and "CONTENT_FORBIDDEN_TERM" in codes
    assert "CONTENT_REQUIRED_TERM_MISSING" not in codes
    state["content_brief"]["required_terms"] = ["必须提及"]
    report = (await V3DeterministicNodeHandler._deterministic_validate(db=None, state=state, node_run_id="test"))[
        "validation_report"
    ]
    assert any(c["code"] == "CONTENT_REQUIRED_TERM_MISSING" and c["level"] == "error" for c in report["checks"])


@pytest.mark.asyncio
@pytest.mark.parametrize("price,supported", [("40", True), ("999991", False)])
async def test_price_anchor_can_reuse_real_quote_but_cannot_invent_amount(payload, result, price, supported):
    payload["evidence_bundle"]["items"].append({"id": "quote-source", "value": "拆除：40元/㎡；水电：50元/㎡"})
    result["blocks"][0]["text"] = f"拆除{price}元/㎡，这笔单价对应清单里的拆除项目。"
    assembled = validate_and_assemble(result, payload)
    state = {**payload, "content_draft": assembled["draft"], "selected_title": assembled["title"]}
    report = (await V3DeterministicNodeHandler._deterministic_validate(db=None, state=state, node_run_id="test"))[
        "validation_report"
    ]
    assert not any(c["code"] == "QUOTE_AMOUNT_REPEATED" for c in report["checks"])
    errors = [c for c in report["checks"] if c["code"] == "FACT_NUMBER_WITHOUT_SOURCE" and price in c["message"]]
    assert bool(errors) == (not supported)


@pytest.mark.asyncio
async def test_candidate_channel_does_not_warn_for_short_original(payload, result):
    assembled = validate_and_assemble(result, payload)
    state = {
        **payload,
        "content_draft": assembled["draft"],
        "selected_title": assembled["title"],
        "channel_profile": {"body_constraints": {"min_length": 100, "max_length": 1000}},
    }
    adapted = await V3DeterministicNodeHandler._adapt_to_channel(db=None, state=state, node_run_id="test")
    assert not any(c["code"] == "CHANNEL_BODY_SHORT" for c in adapted["channel_result"]["checks"])


def test_contradictory_user_terms_fail_before_writing(payload):
    payload["content_brief"].update(required_terms=["免费"], forbidden_terms=["免费"])
    with pytest.raises(ValueError, match="要求词与禁用词冲突"):
        project_input(payload)


def test_title_and_quote_contract_errors_are_reported_together(payload, result):
    result["title"]["text"] = "没有地域的标题"
    result["blocks"][-1]["text"] = "不能由作者改写报价"
    with pytest.raises(ValueError) as exc:
        validate_and_assemble(result, payload)
    assert "标题每个必需槽位" in str(exc.value)
    assert "报价块 id 必须为 quote_block" in str(exc.value)


def test_quote_reference_identifier_is_program_owned(payload, result):
    result["blocks"][-1]["id"] = "b3"
    assembled = validate_and_assemble(result, payload)
    assert assembled["draft"]["blueprint_content"]["blocks"][-1]["id"] == "quote_block"
    assert result["blocks"][-1]["id"] == "b3"


def test_full_repair_accepts_identical_title_without_authorizing_a_title_change(payload, result):
    payload.update(
        content_draft=validate_and_assemble(result, payload)["draft"],
        review_report={"checks": [{"code": "WRITING_QUALITY", "status": "blocked", "location": "b1"}]},
    )
    patch = deepcopy(result)
    patch["blocks"][0]["text"] = "这份报价按项目展开，先看自家需要哪些施工。"
    assert validate_and_assemble(patch, payload, patch=True)["title"]["text"] == result["title"]["text"]
    patch["title"]["text"] = "北京另外一篇标题"
    with pytest.raises(ValueError, match="不允许修改 title"):
        validate_and_assemble(patch, payload, patch=True)


def test_direct_reference_preserves_data_without_formula_or_blueprint_rules(payload, result):
    payload["production_pack"]["content_rule_bundle"]["single_blueprint"]["writing_mode"] = "direct_reference"
    payload["production_pack"]["reference_snapshot"].update(title="参考标题", body="参考完整正文")
    payload["content_brief"]["business_variables"] = {"content_tags": ["旧房局改"]}
    view = project_input(payload)
    assert view["reference"] == {"id": None, "title": "参考标题", "body": "参考完整正文", "blocks": {}}
    assert view["title_requirements"] == {}
    assert view["writing_requirements"]["tags"] == ["旧房局改"]
    assert "candidates" not in view["topics"]
    assert not {"requirements", "narrative_policy", "layout"} & view.keys()
    assert "forbidden_direct_cta_examples" not in view["platform_rules"]
    assert view["facts"] and view["quote"]["context"]
    result["title"] = {"text": "拆除工费明细", "facts": []}
    for block in result["blocks"]:
        block["blueprint_refs"] = []
    result["omissions"] = []
    assembled = validate_and_assemble(result, payload)
    assert any(b["kind"] == "quote_ref" for b in assembled["draft"]["blueprint_content"]["blocks"])
    result["blocks"][1]["facts"] = ["F999"]
    with pytest.raises(ValueError, match="未知或未授权事实"):
        validate_and_assemble(result, payload)


def test_direct_reference_does_not_require_topic_quota_or_cta_template():
    from yuxi.content.validators import validate_modular_content

    bundle = build_modular_rule_bundle({}, single_blueprint=True)
    checks = validate_modular_content(
        title="北京拆除工费",
        body="老房想怎么改，咱们慢慢聊。",
        topics=["北京装修", "旧房局改"],
        draft={},
        brief={},
        evidence_bundle={"items": []},
        rule_bundle=bundle,
    )
    assert not checks
    assert not bundle["topic_candidates"]
    checks = validate_modular_content(
        title="北京拆除工费",
        body="咱们慢慢聊。",
        topics=["北京装修", "北京装修"],
        draft={},
        brief={},
        evidence_bundle={"items": []},
        rule_bundle=bundle,
    )
    assert any(c["code"] == "TOPIC_DUPLICATED" for c in checks)


def test_direct_reference_tool_schema_omits_planning_and_empty_enum(payload):
    from types import SimpleNamespace
    from yuxi.content.model.contracts.content_nodes import (
        ContentNodeResultCollector,
        ContractDomainContext,
        build_content_result_tool,
    )

    payload["production_pack"]["content_rule_bundle"]["single_blueprint"]["writing_mode"] = "direct_reference"
    collector = ContentNodeResultCollector(
        contract_name="SingleBlueprintResultV1",
        domain_context=ContractDomainContext(single_blueprint_input=payload),
        runtime_context=SimpleNamespace(),
    )
    schema = build_content_result_tool(collector).args_schema
    assert "writing_design" not in schema["properties"]
    refs = schema["$defs"]["BlueprintBlockV1"]["properties"]["blueprint_refs"]
    assert refs["maxItems"] == 0
    assert "enum" not in refs["items"]


def test_direct_reference_keeps_interface_skills_without_internal_source_metadata(payload):
    payload["production_pack"]["content_rule_bundle"]["single_blueprint"]["writing_mode"] = "direct_reference"
    payload["content_brief"]["persona"] = {"structured": {"skills": ["工长", "水电", "泥瓦"]}}
    view = project_input(payload)
    persona = next(f for f in view["facts"] if "persona_fact" in f["variables"])
    assert persona["value"]["skills"] == ["工长", "水电", "泥瓦"]
    assert all("source" not in f and "source_type" not in f for f in view["facts"])
