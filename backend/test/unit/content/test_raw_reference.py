import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
from yuxi.content.model.raw_reference import assemble_article, project_input
from yuxi.content.v3.modular_rules import build_modular_rule_bundle
from yuxi.services.agent_delegation_service import AgentDelegationService


@pytest.fixture
def payload():
    return {
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
    assert payload == original
    for token in ("facts", "blueprint_refs", "title_limits", "FRT07", "quote_ref"):
        assert token not in json.dumps(view, ensure_ascii=False)
    view["原始业务JSON"]["persona"]["tone"] = "changed"
    assert payload == original


def test_missing_original_data_does_not_fall_back_to_compiled_facts(payload):
    payload.pop("raw_business_json")
    with pytest.raises(ValueError, match="原始业务 JSON"):
        project_input(payload)


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
