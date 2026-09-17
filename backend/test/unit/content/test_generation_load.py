from copy import deepcopy
from types import SimpleNamespace

import pytest

from yuxi.content.control.workflow.generation_input import project_generation_input
from yuxi.content.rules import canonical_brief_facts
from yuxi.agents.middlewares.model_call_timeout import (
    ModelCallTimeoutMiddleware,
    ModelExecutionBudgetExceeded,
)


@pytest.mark.parametrize("node_id", ["select_creation_strategy", "reselect_creation_strategy"])
def test_strategy_has_time_for_two_calls_and_preserves_explicit_reasoning(node_id):
    from yuxi.services.agent_delegation_service import AgentDelegationService, CONTENT_NODE_EXECUTION_LIMITS

    request = SimpleNamespace(node_run=SimpleNamespace(node_id=node_id), knowledge_policy="frozen_evidence_only")
    context = SimpleNamespace(reasoning_effort=None)
    AgentDelegationService._apply_node_constraints(context, request)
    assert context.reasoning_effort == "low"
    assert context.model_call_timeout_seconds == 65
    assert context.model_retry_times == 1
    assert context._content_max_model_calls == 2
    assert CONTENT_NODE_EXECUTION_LIMITS[node_id][0] >= 2 * context.model_call_timeout_seconds + 3 + 15
    context.reasoning_effort = "medium"
    AgentDelegationService._apply_node_constraints(context, request)
    assert context.reasoning_effort == "medium"


@pytest.mark.asyncio
async def test_graph_preserves_prepared_generation_scope(monkeypatch):
    from unittest.mock import AsyncMock
    import yuxi.agents.buildin.chatbot.graph as graph_module
    import yuxi.content.model.contracts as contracts

    context = SimpleNamespace(
        _content_runtime_prepared=True,
        _content_max_model_calls=2,
        _content_node_result_collector=object(),
        _required_skill_closure=["content-body-generator"],
        _prompt_skills=[],
        model="provider:model",
        reasoning_effort="medium",
    )
    prepare = AsyncMock(side_effect=AssertionError("must not expand frozen scope again"))
    monkeypatch.setattr(graph_module, "prepare_agent_runtime_context", prepare)
    monkeypatch.setattr(graph_module, "resolve_chat_model_spec", lambda model: model)
    monkeypatch.setattr(graph_module, "load_chat_model", lambda **kwargs: kwargs)
    monkeypatch.setattr(graph_module, "resolve_configured_runtime_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(graph_module, "_build_middlewares", AsyncMock(return_value=[]))
    monkeypatch.setattr(graph_module, "build_prompt_with_context", lambda ctx: "test")
    monkeypatch.setattr(contracts, "build_content_result_tool", lambda collector: "result-tool")
    monkeypatch.setattr(graph_module, "create_agent", lambda **kwargs: kwargs)
    agent = graph_module.ChatbotAgent.__new__(graph_module.ChatbotAgent)
    agent._get_checkpointer = AsyncMock(return_value=None)
    graph = await agent.get_graph(context=context)
    prepare.assert_not_awaited()
    assert context._required_skill_closure == ["content-body-generator"]
    assert context._prompt_skills == []
    assert graph["model"]["max_retries"] == 0
    assert graph["model"]["streaming"] is True


@pytest.mark.parametrize("mode", ["original", "viral_rewrite"])
def test_layout_instructions_project_mode_and_record_applied_hash(mode):
    from pathlib import Path
    from yuxi.agents.middlewares.skills import SkillsMiddleware
    from yuxi.agents.skills.buildin import BUILTIN_SKILLS

    spec = next(s for s in BUILTIN_SKILLS if s.slug == "viral-layout-formatter")
    instructions = (Path(spec.source_dir) / "SKILL.md").read_text()
    context = SimpleNamespace(
        _content_node_id="generate_content",
        _content_max_model_calls=2,
        _content_node_input=SimpleNamespace(payload={"runtime_config_snapshot": {"creation_mode": mode}}),
        _runtime_skill_metadata={
            spec.slug: {"instructions": instructions, "version": spec.version, "content_hash": "full"}
        },
    )
    text = SkillsMiddleware()._build_required_skills_section([spec.slug], context)
    if mode == "original":
        assert "## 原创模式" in text
        assert "## 一、读取参考排版" not in text
        assert "排版映射表" not in text
    else:
        assert "## 原创模式" not in text
        assert "## 一、读取参考排版" in text
    applied = context._content_applied_skill_instructions[spec.slug]
    assert applied["instruction_chars"] < len(instructions)
    assert len(applied["applied_hash"]) == 64


@pytest.mark.asyncio
async def test_parent_cancel_stops_delegated_invocation():
    import asyncio
    from yuxi.services.agent_delegation_service import AgentDelegationService

    started = asyncio.Event()
    stopped = asyncio.Event()

    class Graph:
        async def ainvoke(self, *args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    invocation = asyncio.create_task(
        AgentDelegationService._invoke_graph(
            Graph(),
            SimpleNamespace(thread_id="test", uid="test"),
            SimpleNamespace(prompt="test", max_execution_steps=2, cancel_event=None, timeout_seconds=30),
        )
    )
    await started.wait()
    invocation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await invocation
    assert stopped.is_set()


def test_fact_aliases_keep_budget_meaning_and_exclude_control_numbers():
    brief = {
        "form_values": {"area": "89㎡", "budget": "预算18万元", "channel_profile_version_id": "channel-9999"},
        "business_variables": {"area": "89㎡", "quantity": "89㎡", "budget": "预算18万元", "price": "预算18万元"},
    }
    facts = {key: (value, codes) for key, value, codes in canonical_brief_facts(brief)}
    assert facts["budget"] == ("预算18万元", ("budget", "price"))
    assert facts["area"] == ("89㎡", ("area", "quantity"))
    assert "price" not in facts and "quantity" not in facts
    assert "channel_profile_version_id" not in facts
    assert "9999" not in str(facts["number"])


def test_generated_evidence_feedback_reports_all_occurrences_and_allowed_ids():
    from yuxi.content.model.contracts import (
        ContractDomainContext,
        ContractDomainValidationError,
        validate_content_node_result,
    )

    context = ContractDomainContext(
        allowed_evidence_by_usage={"title": frozenset({"ev-real"}), "body": frozenset({"ev-real"})},
    )
    payload = {
        "title": {"text": "标题", "formula_code": "T01", "evidence_ids": ["ev-short"]},
        "outline": {
            "body_formula_code": "C02",
            "sections": [
                {"section_id": "s1", "goal": "开篇", "evidence_ids": ["ev-short"]},
                {"section_id": "s2", "goal": "结果", "evidence_ids": ["ev-short"]},
            ],
        },
        "draft": {
            "body": "正文",
            "topics": [],
            "body_formula_code": "C02",
            "paragraph_evidence": [{"paragraph_id": "p1", "evidence_ids": ["ev-short"]}],
        },
    }
    with pytest.raises(ContractDomainValidationError) as error:
        validate_content_node_result("GeneratedContentResultV1", payload, context)
    assert error.value.code == "evidence_forbidden"
    for path in ("title.evidence_ids", "outline.sections.0", "outline.sections.1", "draft.paragraph_evidence.0"):
        assert path in str(error.value)
    assert "ev-real" in str(error.value)


@pytest.mark.parametrize("text,blocked", [("第一次刷到，第一阶段先看预算", False), ("全国第一，行业第一", True)])
def test_ordinal_is_not_mistaken_for_ranking_claim(text, blocked):
    from yuxi.content.validators import validate_content

    report = validate_content(
        title="案例",
        body=text,
        topics=[],
        brief={},
        evidence_bundle={"items": []},
        strategy={"methods": ["M01"], "title_formula_code": "T01", "body_formula_code": "C02"},
    )
    assert any(c["code"] == "CONTENT_HIGH_RISK_CLAIM" for c in report["checks"]) is blocked


@pytest.mark.parametrize("price", ["预算18万元", "成交18万元"])
def test_separately_entered_price_is_not_merged_with_budget(price):
    facts = {
        key: value
        for key, value, _ in canonical_brief_facts(
            {
                "form_values": {"budget": "预算18万元", "price": price},
            }
        )
    }
    assert facts["price"] == price
    assert facts["budget"] == "预算18万元"


def test_generation_projection_keeps_price_sources_rules_and_revision_without_mutation():
    payload = {
        "strategy_snapshot": {
            "snapshot_hash": "s" * 64,
            "decision": {"scores": [1, 2]},
            "body_formula": {"full_rules": "不能改动"},
        },
        "content_brief": {"business_variables": {"budget": "18万"}, "visual_material": {"template": "large"}},
        "evidence_bundle": {
            "bundle_hash": "frozen",
            "items": [
                {
                    "id": "price-1",
                    "value": "标准单价100元/㎡，含基层",
                    "source_id": "报价表-2",
                    "source_hash": "h" * 64,
                    "created_at": "yesterday",
                    "source_version": "1",
                    "allowed_usage": ["body"],
                    "verified_status": "user_confirmed",
                    "risk_level": "high",
                    "metadata": {"price_basis": "standard_unit_price", "scope": "不含主材"},
                }
            ],
        },
        "runtime_config_snapshot": {
            "creation_mode": "original",
            "visual_material": {},
            "selection_policy_snapshot": {},
        },
        "formula_lexicon_bundle": {"body": ["原版词条"]},
        "channel_profile": {"emoji_allowed": False},
        "persona_profile": {},
        "content_draft": {"body": "原稿"},
        "validation_report": {"status": "blocked"},
    }
    before = deepcopy(payload)
    result = project_generation_input(payload)
    assert payload == before
    assert "decision" not in result["strategy_snapshot"]
    assert result["strategy_snapshot"]["body_formula"] == payload["strategy_snapshot"]["body_formula"]
    assert result["runtime_config_snapshot"] == {"creation_mode": "original"}
    assert result["content_draft"] == payload["content_draft"]
    evidence = result["evidence_bundle"]["items"][0]
    for key in ("id", "value", "source_id", "allowed_usage", "verified_status", "risk_level", "metadata"):
        assert evidence[key] == payload["evidence_bundle"]["items"][0][key]
    assert "source_hash" not in evidence


@pytest.mark.asyncio
async def test_timeout_and_correction_share_two_calls_and_preserve_cancel():
    import asyncio
    from langchain.agents.middleware import ModelResponse
    from langchain_core.messages import AIMessage

    context = SimpleNamespace(_content_max_model_calls=2, _content_node_token_budget=12000)

    class Request(SimpleNamespace):
        def override(self, **kwargs):
            return Request(**{**vars(self), **kwargs})

    request = Request(runtime=SimpleNamespace(context=context), model_settings={}, messages=[])
    middleware = ModelCallTimeoutMiddleware(0.01)
    calls = []

    async def slow(req):
        calls.append(req.model_settings["max_tokens"])
        await asyncio.sleep(1)

    async def good(req):
        calls.append(req.model_settings["max_tokens"])
        return ModelResponse(result=[AIMessage(content="ok")])

    with pytest.raises(TimeoutError):
        await middleware.awrap_model_call(request, slow)
    await middleware.awrap_model_call(request, good)
    with pytest.raises(ModelExecutionBudgetExceeded):
        await middleware.awrap_model_call(request, good)
    assert calls == [6000, 6000]

    context._content_model_calls = 0

    async def cancelled(req):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await middleware.awrap_model_call(request, cancelled)
    assert context._content_model_calls == 1
