import json
import shutil
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
from yuxi.content.model.raw_reference import PLAN_INSTRUCTION, TOPIC_INSTRUCTION, assemble_article, project_input
from yuxi.content.v3.modular_rules import build_modular_rule_bundle
from yuxi.services.agent_delegation_service import AgentDelegationService

TOPICS = [
    "旧房装修",
    "装修预算",
    "施工明细",
    "局部改造",
    "装修经验",
    "家装设计",
    "厨房改造",
    "装修材料",
    "施工工艺",
    "家居生活",
]


@pytest.fixture
def payload():
    result = {
        "raw_business_json": {
            "persona": {"skills": ["工长", "泥瓦"], "tone": "有耐心"},
            "requirementType": {"prices": [{"content": "24墙拆除：40元/㎡"}]},
            "images": [{"objectUrl": "https://example.com/image.jpg"}],
        },
        "production_pack": {
            "reference_snapshot": {"title": "原文标题", "body": "原文正文🙂", "reference_blueprint": {"unused": True}},
            "content_rule_bundle": build_modular_rule_bundle({}, single_blueprint=True),
            "strategy_snapshot": {"title_formula": {"code": "FRT07"}, "body_formula": {"code": "FRB06"}},
        },
    }
    # 保留无首行元数据的历史输出契约测试。
    result["production_pack"]["content_rule_bundle"]["single_blueprint"].pop("article_output_format")
    return result


def test_prompt_contains_exactly_original_reference_json_and_one_instruction(payload):
    original = deepcopy(payload)
    view = project_input(payload)
    assert set(view) == {"仿写要求", "爆款原文", "原始业务JSON", "审核规则"}
    assert view["原始业务JSON"] == payload["raw_business_json"]
    assert view["爆款原文"] == {"标题": "原文标题", "正文": "原文正文🙂"}
    assert "话题" in view["仿写要求"] or "话题标签" in view["审核规则"]
    assert view["审核规则"]["话题标签"]
    assert view["审核规则"]["程序硬拦"].startswith("已关闭")
    assert "违禁词处理" in view["审核规则"]
    author = next(
        m
        for m in payload["production_pack"]["content_rule_bundle"]["modules"]
        if m["slug"] == "single-blueprint-author"
    )
    assert view["仿写要求"].startswith(author["instructions"])
    assert "恰好 10 个" in view["仿写要求"]
    assert "不重复" in view["仿写要求"]
    assert "正文末尾" in view["仿写要求"]
    assert payload == original
    for token in ("facts", "blueprint_refs", "title_limits", "FRT07", "quote_ref"):
        assert token not in json.dumps(view, ensure_ascii=False)
    view["原始业务JSON"]["persona"]["tone"] = "changed"
    assert payload == original


def test_missing_original_data_does_not_fall_back_to_compiled_facts(payload):
    payload.pop("raw_business_json")
    with pytest.raises(ValueError, match="原始业务 JSON"):
        project_input(payload)


def test_frozen_body_skill_and_explicit_writing_request_reach_model_without_changing_data(payload):
    request = "全文使用案例证明型，保留报价明细。"
    payload["production_pack"]["writing_request"] = request
    quote = "石膏板吊顶（平顶）：70 元 /㎡ ×10㎡ =280 元；整套人工合计：12060元"
    payload["raw_business_json"]["requirementType"]["prices"][0]["content"] = quote
    original = deepcopy(payload)
    modules = {m["slug"]: m for m in payload["production_pack"]["content_rule_bundle"]["modules"]}

    view = project_input(payload)

    assert view["本篇写作要求"] == request
    assert view["仿写要求"] == "\n\n".join(
        [
            modules["single-blueprint-author"]["instructions"],
            modules["viral-body-author"]["instructions"],
            TOPIC_INSTRUCTION,
        ]
    )
    assert modules["single-blueprint-reviewer"]["instructions"] not in view["仿写要求"]
    assert view["原始业务JSON"] == original["raw_business_json"]
    assert payload == original


def test_legacy_frozen_task_keeps_original_prompt_without_current_body_skill(payload):
    pack = payload["production_pack"]
    pack["content_rule_bundle"]["modules"] = [
        {"slug": "single-blueprint-author", "instructions": "历史冻结的写作要求", "version": "3.0.0"}
    ]
    pack["writing_request"] = "历史输入中未投影的要求"

    view = project_input(payload)

    assert view["仿写要求"] == f"历史冻结的写作要求\n\n{TOPIC_INSTRUCTION}"
    assert set(view) == {"仿写要求", "爆款原文", "原始业务JSON"}


def test_changing_live_body_skill_changes_new_bundle_but_not_frozen_model_input(payload, tmp_path, monkeypatch):
    from yuxi.content.v3 import modular_rules

    skill_root = tmp_path / "skills"
    shutil.copytree(modular_rules._SKILL_ROOT, skill_root)
    monkeypatch.setattr(modular_rules, "_SKILL_ROOT", skill_root)
    before = project_input(payload)
    path = skill_root / "viral-body-author/SKILL.md"
    path.write_text(path.read_text() + "\n新增的下一版本写法。\n")

    new_bundle = build_modular_rule_bundle({}, single_blueprint=True)

    old_bundle = payload["production_pack"]["content_rule_bundle"]
    old_body = next(m for m in old_bundle["modules"] if m["slug"] == "viral-body-author")
    new_body = next(m for m in new_bundle["modules"] if m["slug"] == "viral-body-author")
    assert new_body["content_hash"] != old_body["content_hash"]
    assert new_bundle["bundle_hash"] != old_bundle["bundle_hash"]
    assert new_body["instructions"] != old_body["instructions"]
    assert project_input(payload) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("title", "expected_status"),
    [
        ("长沙两卫生间翻新报价1.5万？我这边1.206万", "blocked"),
        ("长沙两卫生间翻新怕超预算？人工报价1.206万", "passed"),
    ],
)
async def test_reference_comparison_price_needs_its_own_business_evidence(payload, title, expected_status):
    payload["production_pack"]["reference_snapshot"] = {
        "title": "长沙厨房翻新报价两万？老杨3590搞定",
        "body": "厨房翻新要预备两万块？看看这个案例。",
    }
    payload["raw_business_json"] = {"requirementType": {"prices": [{"content": "整套人工报价1.206w，合计12060元"}]}}
    view = project_input(payload)
    assert "已有的金额、面积、数量、年限、单位及报价明细原样保留" in view["仿写要求"]
    state = {
        "production_pack": payload["production_pack"],
        "selected_title": {"text": title},
        "content_draft": {"body": "整套人工报价12060元，按实际项目核对范围。", "topics": TOPICS},
        "content_brief": {},
        "evidence_bundle": {"items": []},
        "runtime_config_snapshot": {"raw_business_json": payload["raw_business_json"]},
        "strategy_snapshot": {
            "creation_methods": ["FRM03"],
            **payload["production_pack"]["strategy_snapshot"],
        },
    }

    result = await V3DeterministicNodeHandler._deterministic_validate(db=None, state=state, node_run_id="test")

    report = result["validation_report"]
    assert report["status"] == expected_status
    if expected_status == "blocked":
        assert [item["code"] for item in report["checks"]] == ["FACT_NUMBER_WITHOUT_SOURCE"]
        assert "1.5" in report["checks"][0]["message"]
    else:
        assert report["checks"] == []


@pytest.mark.asyncio
async def test_raw_reference_quote_numbers_are_validated_against_original_business_json(payload):
    quote = "、".join(
        [
            "水电12088元",
            "拆除2121.6元",
            "泥瓦2182元",
            "木作3166.8元",
            "防水3285.98元",
            "人工50255元",
            "安装5763元",
            "油工6158元",
            "其他9462.9元",
        ]
    )
    state = {
        "production_pack": payload["production_pack"],
        "selected_title": {"text": "旧房施工报价明细"},
        "content_draft": {"body": quote, "topics": TOPICS},
        "content_brief": {},
        "evidence_bundle": {"items": []},
        "runtime_config_snapshot": {"raw_business_json": {"requirementType": {"prices": [{"content": quote}]}}},
        "strategy_snapshot": {
            "creation_methods": ["FRM03"],
            **payload["production_pack"]["strategy_snapshot"],
        },
    }

    result = await V3DeterministicNodeHandler._deterministic_validate(db=None, state=state, node_run_id="test")

    assert result["validation_report"] == {"status": "passed", "checks": []}


def test_plain_text_is_not_rewritten_or_replaced_during_storage_mapping(payload):
    text = (
        "**北京旧房拆除明细**\n\n厨房住久了总有想改的地方🙂\n\n✔ 24墙拆除：40元/㎡"
        "\n\n有想法，咱们慢慢聊报价。\n\n#北京装修 #旧房局改"
    )
    result = assemble_article(text, payload["production_pack"])
    assert result["title"]["text"] == "北京旧房拆除明细"
    assert result["draft"]["body"] == "厨房住久了总有想改的地方🙂\n\n✔ 24墙拆除：40元/㎡\n\n有想法，咱们慢慢聊报价。"
    assert result["draft"]["topics"] == ["北京装修", "旧房局改"]
    assert result["draft"]["raw_model_text"] == text
    assert result["draft"]["paragraph_evidence"] == []
    assert "blueprint_content" not in result["draft"]


def test_inline_trailing_hashtags_are_extracted_as_topics(payload):
    text = "北京拆除费用\n\n厨房想怎么改，咱们慢慢聊。 #北京装修 #旧房局改"
    result = assemble_article(text, payload["production_pack"])
    assert result["draft"]["body"] == "厨房想怎么改，咱们慢慢聊。"
    assert result["draft"]["topics"] == ["北京装修", "旧房局改"]


@pytest.mark.parametrize(("indent", "separator"), [(None, "\n"), (2, "\n\n"), (None, " ")])
def test_new_article_records_ai_choice_and_additions_separately_from_publishable_text(payload, indent, separator):
    pack = payload["production_pack"]
    pack["content_rule_bundle"] = build_modular_rule_bundle({}, single_blueprint=True)
    plan = {
        "method": "反差价值型",
        "tone": "有耐心",
        "creative_additions": ["同口径对比报价20000元，差额7940元。"],
    }
    body = "整套人工合计：12060元\n\n同口径对比报价20000元，差额7940元。"
    text = json.dumps(plan, ensure_ascii=False, indent=indent) + f"{separator}长沙施工报价\n\n{body}\n\n#装修 #长沙装修"

    article = assemble_article(text, pack)

    assert article["title"]["text"] == "长沙施工报价"
    assert article["draft"]["body"] == body
    assert article["draft"]["raw_model_text"] == text
    assert article["draft"]["writing_choice"] == {"method": plan["method"], "tone": plan["tone"]}
    assert article["outline"]["writing_choice"] == article["draft"]["writing_choice"]
    assert article["outline"]["creative_additions"] == ["同口径对比报价20000元，差额7940元"]
    assert PLAN_INSTRUCTION in project_input(payload)["仿写要求"]


def test_writing_record_matches_quote_transition_with_different_terminal_punctuation(payload):
    pack = payload["production_pack"]
    pack["content_rule_bundle"] = build_modular_rule_bundle({}, single_blueprint=True)
    plan = {
        "method": "案例证明型",
        "tone": "经验老道笃定，自信但不浮夸",
        "creative_additions": ["业主问得仔细，我就逐项说明。"],
    }
    body = "业主问得仔细，我就逐项说明：\n整套人工合计：12060元"
    article = assemble_article(json.dumps(plan, ensure_ascii=False) + f"\n长沙施工报价\n\n{body}", pack)

    assert article["draft"]["body"] == body
    assert article["draft"]["creative_additions"] == ["业主问得仔细，我就逐项说明"]


@pytest.mark.parametrize("change", ["missing_header", "unknown_method", "wrong_tone", "wrong_additions"])
def test_new_article_rejects_missing_or_inconsistent_writing_record(payload, change):
    pack = payload["production_pack"]
    pack["content_rule_bundle"] = build_modular_rule_bundle({}, single_blueprint=True)
    plan = {"method": "反差价值型", "tone": "有耐心", "creative_additions": []}
    if change == "unknown_method":
        plan["method"] = "FRM07"
    elif change == "wrong_tone":
        plan["tone"] = "随和型"
    elif change == "wrong_additions":
        plan["creative_additions"] = ["没有出现在文章中的补写"]
    text = "标题\n\n正文" if change == "missing_header" else json.dumps(plan, ensure_ascii=False) + "\n标题\n\n正文"

    with pytest.raises(ValueError):
        assemble_article(text, pack)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("planned", "registered", "changed_quote", "expected_codes"),
    [
        (True, True, False, []),
        (True, False, False, ["FACT_NUMBER_WITHOUT_SOURCE", "FACT_NUMBER_WITHOUT_SOURCE"]),
        (False, True, False, ["FACT_NUMBER_WITHOUT_SOURCE", "FACT_NUMBER_WITHOUT_SOURCE"]),
        (True, True, True, ["ORIGINAL_QUOTE_CHANGED"]),
    ],
)
async def test_creative_comparison_does_not_become_evidence_or_override_original_quote(
    payload, planned, registered, changed_quote, expected_codes
):
    pack = payload["production_pack"]
    if planned:
        pack["content_rule_bundle"] = build_modular_rule_bundle({}, single_blueprint=True)
    platform = pack["content_rule_bundle"]["runtime_rules"]["viral-platform-expression"]
    platform["forbidden_replacements"] = {"报价": "报J"}
    addition = "同口径对比报价20000元，差额7940元。"
    quote = "整套人工合计：12060元"
    rendered_quote = "整套人工合计：13000元" if changed_quote else quote
    state = {
        "production_pack": pack,
        "selected_title": {"text": "长沙装修差额7940元"},
        "content_draft": {
            "body": f"{rendered_quote}\n{addition.replace('报价', '报J')}",
            "topics": TOPICS,
            "creative_additions": [addition, rendered_quote] if registered else [],
        },
        "runtime_config_snapshot": {"raw_business_json": {"requirementType": {"prices": [{"content": quote}]}}},
        "content_brief": {},
        "evidence_bundle": {"items": [{"value": quote}]},
        "strategy_snapshot": {"creation_methods": ["FRM03"], **pack["strategy_snapshot"]},
    }
    before = deepcopy(state)

    result = await V3DeterministicNodeHandler._deterministic_validate(db=None, state=state, node_run_id="test")

    assert state == before
    report = result["validation_report"]
    assert [c["code"] for c in report["checks"]] == expected_codes
    assert report["status"] == ("blocked" if expected_codes else "passed")


@pytest.mark.parametrize("native_format", [False, True])
def test_trailing_topics_are_extracted_without_changing_body(payload, native_format):
    body = "报价明细保持原样：40元/㎡。\n\n正文里的 #施工记录 保留。"
    suffix = " ".join(f"#{topic}[话题]#" if native_format else f"#{topic}" for topic in TOPICS)
    text = f"原标题\n\n{body}\n\n话题标签：{suffix}"

    result = assemble_article(text, payload["production_pack"])

    assert result["title"]["text"] == "原标题"
    assert result["draft"]["body"] == body
    assert result["draft"]["topics"] == TOPICS
    assert result["draft"]["raw_model_text"] == text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("topics", "code"),
    [
        ([], "TOPIC_COUNT_MISMATCH"),
        (TOPICS[:9], "TOPIC_COUNT_MISMATCH"),
        ([*TOPICS, "房屋装修"], "TOPIC_COUNT_MISMATCH"),
        ([*TOPICS[:9], TOPICS[0]], "TOPIC_DUPLICATED"),
        ([*TOPICS[:9], ""], "TOPIC_FORMAT_INVALID"),
        ([*TOPICS[:9], "长" * 21], "TOPIC_FORMAT_INVALID"),
        (TOPICS, None),
    ],
)
async def test_raw_reference_requires_ten_unique_publishable_topics(payload, topics, code):
    state = {
        "production_pack": payload["production_pack"],
        "selected_title": {"text": "旧房装修记录"},
        "content_draft": {"body": "厨房想怎么改，咱们慢慢聊。", "topics": topics},
        "content_brief": {},
        "evidence_bundle": {"items": []},
        "strategy_snapshot": {"creation_methods": ["FRM03"], **payload["production_pack"]["strategy_snapshot"]},
    }
    before = deepcopy(state)

    result = await V3DeterministicNodeHandler._deterministic_validate(db=None, state=state, node_run_id="test")

    assert state == before
    report = result["validation_report"]
    assert report["status"] == ("blocked" if code else "passed")
    assert [item["code"] for item in report["checks"]] == ([code] if code else [])


@pytest.mark.asyncio
async def test_plain_graph_has_no_system_prompt_tools_or_writing_middlewares(monkeypatch):
    from yuxi.agents.buildin.chatbot import graph as chatbot
    from yuxi.agents.buildin.chatbot.context import ChatBotContext

    context = ChatBotContext(reasoning_effort="medium")
    context._content_runtime_prepared = True
    context._content_plain_text = True
    context.model_call_timeout_seconds = 120
    monkeypatch.setattr(chatbot, "load_chat_model", lambda **kwargs: object())
    monkeypatch.setattr(chatbot, "resolve_chat_model_spec", lambda _: "test:model")
    monkeypatch.setattr(chatbot.ChatbotAgent, "_get_checkpointer", AsyncMock(return_value=None))
    monkeypatch.setattr(chatbot, "create_agent", lambda **kwargs: kwargs)
    tool_resolver = AsyncMock(side_effect=AssertionError("不能配置任何写作工具"))
    monkeypatch.setattr(chatbot, "resolve_configured_runtime_tools", tool_resolver)
    graph = await chatbot.ChatbotAgent().get_graph(context)
    assert graph["system_prompt"] is None and graph["tools"] == []
    assert [type(m).__name__ for m in graph["middleware"]] == ["ModelCallTimeoutMiddleware", "TokenUsageMiddleware"]
    tool_resolver.assert_not_called()


@pytest.mark.asyncio
async def test_delegation_sends_only_three_inputs_without_node_envelope(payload):
    graph = SimpleNamespace(ainvoke=AsyncMock(return_value={"messages": [AIMessage(content="标题\n\n正文")]}))
    context = SimpleNamespace(_content_plain_text=True, thread_id="test", uid="test")
    request = SimpleNamespace(prompt="旧编排提示", timeout_seconds=5, cancel_event=None, max_execution_steps=5)
    view = project_input(payload)
    await AgentDelegationService._invoke_graph(graph, context, request, SimpleNamespace(payload=view))
    sent = graph.ainvoke.call_args.args[0]["messages"]
    assert len(sent) == 1 and json.loads(sent[0]) == view
    assert "旧编排提示" not in sent[0]


@pytest.mark.asyncio
async def test_postprocessing_replaces_forbidden_words_without_inserting_a_quote(payload, monkeypatch):
    pack = payload["production_pack"]
    pack["content_rule_bundle"]["runtime_rules"]["viral-platform-expression"] = {
        "forbidden_replacements": {"报价": "报J"},
    }
    state = {
        "production_pack": pack,
        "selected_title": {"text": "北京拆除报价"},
        "creative_content_draft": {"body": "✔ 24墙拆除：40元/㎡\n\n报价明细给你了，咱们慢慢聊。", "topics": []},
    }
    state["content_draft"] = deepcopy(state["creative_content_draft"])
    update = await V3DeterministicNodeHandler._adapt_to_channel(db=None, state=state, node_run_id="test")
    state.update(update)
    assert state["content_draft"]["body"] == "✔ 24墙拆除：40元/㎡\n\n报J明细给你了，咱们慢慢聊。"
    monkeypatch.setattr(
        "yuxi.content.control.workflow.deterministic_node.extract_locked_quote_block",
        lambda _: {"original_content": "24墙拆除：40元/㎡", "rendered_content": "✅ 24墙拆除：40元/㎡"},
    )
    update = await V3DeterministicNodeHandler._compose_locked_quote_block(db=None, state=state, node_run_id="test")
    assert update["content_draft"]["body"] == state["content_draft"]["body"]
    assert update["locked_block_composition"]["status"] == "not_applicable"


@pytest.mark.asyncio
async def test_plain_draft_keeps_long_title_as_warning_without_rewriting(payload):
    title = "北京旧房局改拆除报价，开工前这些费用要先问清楚"
    state = {
        "production_pack": payload["production_pack"],
        "selected_title": {"text": title},
        "content_draft": {"body": "厨房想怎么改，咱们慢慢聊。", "topics": []},
        "channel_profile": {"title_constraints": {"max_length": 20}},
    }
    result = await V3DeterministicNodeHandler._adapt_to_channel(db=None, state=state, node_run_id="test")
    assert result["selected_title"]["text"] == title
    assert result["channel_result"]["checks"][0]["code"] == "CHANNEL_TITLE_LONG"
    assert result["channel_result"]["checks"][0]["level"] == "warning"


@pytest.mark.asyncio
async def test_raw_reference_skips_evidence_path_number_and_high_risk_rules(payload):
    state = {
        "production_pack": payload["production_pack"],
        "content_brief": {},
        "evidence_bundle": {"items": []},
        "runtime_config_snapshot": {"raw_business_json": payload["raw_business_json"]},
        "selected_title": {"text": "北京拆除费用"},
        "content_draft": {
            "body": "邻居都说这是小区第一，另收999元也关注这4个细节，✔ 24墙拆除：40元/㎡",
            "topics": [],
        },
        "channel_result": {"checks": []},
    }
    report = (await V3DeterministicNodeHandler._deterministic_validate(db=None, state=state, node_run_id="test"))[
        "validation_report"
    ]
    assert not any(c["code"] == "FACT_NUMBER_WITHOUT_SOURCE" for c in report["checks"])
    assert not any(c["code"] == "CONTENT_HIGH_RISK_CLAIM" for c in report["checks"])


@pytest.mark.asyncio
async def test_raw_reference_allows_numbers_from_frozen_viral_original(payload):
    pack = payload["production_pack"]
    pack["reference_snapshot"] = {
        "title": "转角加固要盯紧",
        "body": "装完后要关注这4个细节，别只看表面漂亮。",
    }
    state = {
        "production_pack": pack,
        "content_brief": {},
        "evidence_bundle": {"items": []},
        "runtime_config_snapshot": {"raw_business_json": payload["raw_business_json"]},
        "selected_title": {"text": "北京拆除费用"},
        "content_draft": {
            "body": "装完后也关注这4个细节，✔ 24墙拆除：40元/㎡",
            "topics": [],
        },
        "channel_result": {"checks": []},
    }
    report = (await V3DeterministicNodeHandler._deterministic_validate(db=None, state=state, node_run_id="test"))[
        "validation_report"
    ]
    assert not any(c["code"] == "FACT_NUMBER_WITHOUT_SOURCE" for c in report["checks"])
    assert not any(c["code"] == "CONTENT_HIGH_RISK_CLAIM" for c in report["checks"])


@pytest.mark.asyncio
async def test_raw_reference_skips_evidence_path_high_risk_claim_rules(payload):
    state = {
        "production_pack": payload["production_pack"],
        "content_brief": {},
        "evidence_bundle": {"items": []},
        "runtime_config_snapshot": {"raw_business_json": payload["raw_business_json"]},
        "selected_title": {"text": "北京拆除费用"},
        "content_draft": {
            "body": "邻居都说这是小区第一，✔ 24墙拆除：40元/㎡",
            "topics": [],
        },
        "channel_result": {"checks": []},
    }
    report = (await V3DeterministicNodeHandler._deterministic_validate(db=None, state=state, node_run_id="test"))[
        "validation_report"
    ]
    assert not any(c["code"] == "CONTENT_HIGH_RISK_CLAIM" for c in report["checks"])


@pytest.mark.asyncio
async def test_raw_reference_validation_no_longer_blocks_on_legacy_fact_errors(payload):
    from yuxi.agents.buildin.content_workflow.graph import ContentWorkflowAgent
    from yuxi.content.v3.joint_workflow import WORKFLOW_SINGLE_BLUEPRINT

    state = {
        "production_pack": payload["production_pack"],
        "content_brief": {},
        "evidence_bundle": {"items": []},
        "runtime_config_snapshot": {"raw_business_json": payload["raw_business_json"]},
        "selected_title": {"text": "北京拆除费用"},
        "content_draft": {"body": "另收999元也说这是小区第一", "topics": []},
        "channel_result": {"checks": []},
        "current_node": "deterministic_validate",
        "validation_report": {
            "status": "blocked",
            "checks": [
                {
                    "code": "FACT_NUMBER_WITHOUT_SOURCE",
                    "level": "error",
                    "message": "出现未知价格",
                }
            ],
        },
    }
    result = await ContentWorkflowAgent()._execute_node(
        {"id": "revise_if_needed", "type": "revision_router"},
        state,
        WORKFLOW_SINGLE_BLUEPRINT,
    )
    assert result["revision_status"] == "continue"
    assert result["validation_report"]["status"] in {"passed", "warning"}
    assert not any(c.get("level") == "error" for c in result["validation_report"].get("checks") or [])


@pytest.mark.asyncio
async def test_raw_reference_asks_ai_to_replace_residual_forbidden_words(payload, monkeypatch):
    pack = payload["production_pack"]
    pack["content_rule_bundle"]["runtime_rules"]["viral-platform-expression"] = {
        "forbidden_replacements": {},
        "forbidden_lexicon": {"alternatives": {"私信": ["评论区聊聊"]}, "snapshot_hash": "x"},
    }
    called = {}

    async def fake_ai(**kwargs):
        called.update(kwargs)
        return {"title": "北京拆除费用", "body": "有想法评论区聊聊。", "topics": []}

    monkeypatch.setattr(
        "yuxi.content.generation.ai_replace_forbidden_terms",
        fake_ai,
    )
    state = {
        "production_pack": pack,
        "model_spec": "test:model",
        "content_brief": {},
        "selected_title": {"text": "北京拆除费用"},
        "creative_content_draft": {"body": "有想法私信我。", "topics": []},
        "content_draft": {"body": "有想法私信我。", "topics": []},
        "channel_profile": {},
    }
    update = await V3DeterministicNodeHandler._adapt_to_channel(db=None, state=state, node_run_id="test")
    assert called["residual_terms"] == ["私信"]
    assert update["content_draft"]["body"] == "有想法评论区聊聊。"


@pytest.mark.asyncio
async def test_plain_article_can_be_saved_without_quote_composition_gate(payload):
    from yuxi.agents.buildin.content_workflow.graph import ContentWorkflowAgent

    agent = ContentWorkflowAgent()
    agent._workflow_definition = {"semantic_review_enabled": False}
    pack = payload["production_pack"]
    pack["materials"] = [{"variable_codes": ["quote_block"]}]
    result = await agent._v3_human_review(
        {"id": "human_content_approval", "interrupt_type": "content_approval"},
        {
            "task_id": "test",
            "run_id": "run",
            "production_pack": pack,
            "selected_title": {"text": "北京拆除费用"},
            "content_draft": {"body": "厨房想怎么改，咱们慢慢聊。\n✔ 24墙拆除：40元/㎡"},
            "validation_report": {"status": "passed", "checks": []},
            "composed_content_validation_report": {"status": "not_applicable", "checks": []},
        },
    )
    assert result["artifact_version"]["status"] == "approved_content"
