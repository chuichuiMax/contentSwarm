from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.graph import END
from langgraph.types import Command

import yuxi.agents.middlewares.skills as skills_middleware
import yuxi.services.agent_delegation_service as delegation_module
from yuxi.agents.middlewares.content_node_result import ContentNodeResultMiddleware
from yuxi.agents.buildin.chatbot.graph import _build_middlewares
from yuxi.agents.middlewares.skills import (
    RequiredSkillResolutionError,
    SkillsMiddleware,
    resolve_runtime_skills_for_context,
)
from yuxi.content.control.errors import ContentApplicationError
from yuxi.content.model.contracts import ContractDomainContext
from yuxi.content.v3.agents import (
    CONTENT_AGENT_SPECS,
    migrate_system_content_agent,
    validate_existing_content_agent,
)
from yuxi.services.agent_delegation_service import (
    AgentDelegationRequest,
    AgentDelegationService,
    _bounded_run_identifier,
    build_runtime_config_snapshot,
)
from yuxi.storage.postgres.models_business import Agent
from yuxi.storage.postgres.models_content import ContentNodeRun


def _skill(
    slug: str,
    *,
    dependencies: list[str] | None = None,
    tools: list[str] | None = None,
    enabled: bool = True,
    share_config: dict | None = None,
):
    return SimpleNamespace(
        slug=slug,
        name=slug,
        description=f"{slug} description",
        instructions=f"# {slug}\nFollow {slug} instructions.",
        version="1.2.3",
        content_hash=f"hash-{slug}",
        dir_path=None,
        enabled=enabled,
        created_by="system",
        share_config=share_config or {"access_level": "global"},
        tool_dependencies=tools or [],
        mcp_dependencies=[],
        skill_dependencies=dependencies or [],
    )


@pytest.mark.asyncio
async def test_required_skills_expand_and_freeze_dependency_closure(monkeypatch):
    skills = [_skill("alpha", dependencies=["beta"], tools=["tool-a"]), _skill("beta", tools=["tool-b"])]

    async def fake_list(db=None, user=None):
        del db, user
        return skills

    monkeypatch.setattr(skills_middleware, "_list_skills_from_db", fake_list)
    monkeypatch.setattr(
        skills_middleware,
        "get_all_tool_instances",
        lambda: [SimpleNamespace(name="tool-a"), SimpleNamespace(name="tool-b")],
    )
    context = SimpleNamespace(
        skills=["alpha"],
        required_skills=["alpha"],
        skill_tool_allowlist=["tool-a", "tool-b"],
        mcps=[],
    )

    scope = await resolve_runtime_skills_for_context(context)

    assert scope["required_skill_closure"] == ["alpha", "beta"]
    assert scope["required_skill_tools"] == ["tool-a", "tool-b"]
    assert scope["runtime_skill_snapshots"] == [
        {"slug": "alpha", "version": "1.2.3", "content_hash": "hash-alpha"},
        {"slug": "beta", "version": "1.2.3", "content_hash": "hash-beta"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("required", "selected", "tools", "allowlist", "expected_code"),
    [
        (["missing"], ["missing"], [], [], "required_skill_missing"),
        (["alpha"], [], [], [], "required_skill_unauthorized"),
        (["alpha"], ["alpha"], ["tool-a"], [], "required_skill_tool_unauthorized"),
        (["alpha"], ["alpha"], ["not-installed"], ["not-installed"], "required_skill_tool_unavailable"),
    ],
)
async def test_required_skill_configuration_fails_closed(
    monkeypatch, required, selected, tools, allowlist, expected_code
):
    items = [] if required == ["missing"] else [_skill("alpha", tools=tools)]

    async def fake_list(db=None, user=None):
        del db, user
        return items

    monkeypatch.setattr(skills_middleware, "_list_skills_from_db", fake_list)
    monkeypatch.setattr(skills_middleware, "get_all_tool_instances", lambda: [])
    context = SimpleNamespace(
        skills=selected,
        required_skills=required,
        skill_tool_allowlist=allowlist,
        mcps=[],
    )

    with pytest.raises(RequiredSkillResolutionError) as exc_info:
        await resolve_runtime_skills_for_context(context)

    assert exc_info.value.code == expected_code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("item", "expected_code"),
    [
        (_skill("alpha", enabled=False), "required_skill_disabled"),
        (
            _skill("alpha", share_config={"access_level": "user", "user_uids": ["someone-else"]}),
            "required_skill_unauthorized",
        ),
    ],
)
async def test_required_skill_disabled_and_permission_are_distinguished(monkeypatch, item, expected_code):
    async def fake_list(db=None, user=None):
        del db, user
        return []

    class FakeSkillRepository:
        def __init__(self, db):
            del db

        async def list_all(self):
            return [item]

    monkeypatch.setattr(skills_middleware, "_list_skills_from_db", fake_list)
    monkeypatch.setattr(skills_middleware, "SkillRepository", FakeSkillRepository)
    context = SimpleNamespace(skills=["alpha"], required_skills=["alpha"], skill_tool_allowlist=[], mcps=[])
    user = SimpleNamespace(uid="u1", role="user", department_id=None)

    with pytest.raises(RequiredSkillResolutionError) as exc_info:
        await resolve_runtime_skills_for_context(context, db=object(), user=user)

    assert exc_info.value.code == expected_code


@pytest.mark.asyncio
async def test_middleware_auto_activates_required_skills_and_intersects_tools(monkeypatch):
    events = []

    async def fake_append(run_id, event_type, payload, *, thread_id=None):
        events.append((run_id, event_type, payload, thread_id))

    monkeypatch.setattr(delegation_module, "append_run_stream_event", fake_append)
    import yuxi.services.run_queue_service as run_queue_service

    monkeypatch.setattr(run_queue_service, "append_run_stream_event", fake_append)
    monkeypatch.setattr(
        skills_middleware,
        "get_all_tool_instances",
        lambda: [SimpleNamespace(name="tool-a"), SimpleNamespace(name="tool-b")],
    )
    context = SimpleNamespace(
        run_id="child-run",
        thread_id="content:task:node:1",
        _prompt_skills=["alpha", "beta"],
        _readable_skills=["alpha", "beta"],
        _required_skill_closure=["alpha", "beta"],
        _content_node_tool_scope=["tool-a", "submit_content_node_result"],
        _runtime_skill_metadata={
            "alpha": {
                "name": "Alpha",
                "description": "alpha",
                "path": "/home/gem/skills/alpha/SKILL.md",
                "instructions": "# Alpha\nalpha rules",
            },
            "beta": {
                "name": "Beta",
                "description": "beta",
                "path": "/home/gem/skills/beta/SKILL.md",
                "instructions": "# Beta\nbeta rules",
            },
        },
        _runtime_skill_dependency_map={
            "alpha": {"tools": ["tool-a"], "mcps": [], "skills": ["beta"]},
            "beta": {"tools": ["tool-b"], "mcps": [], "skills": []},
        },
        _runtime_skill_snapshots=[
            {"slug": "alpha", "version": "1", "content_hash": "ha"},
            {"slug": "beta", "version": "2", "content_hash": "hb"},
        ],
        mcps=[],
    )

    class FakeRequest:
        def __init__(self, *, system_message=None, tools=None):
            self.runtime = SimpleNamespace(context=context)
            self.state = {}
            self.tools = tools or [SimpleNamespace(name="tool-x"), SimpleNamespace(name="submit_content_node_result")]
            self.system_message = system_message or SystemMessage(content="base")

        def override(self, **kwargs):
            request = FakeRequest(
                system_message=kwargs.get("system_message", self.system_message),
                tools=kwargs.get("tools", self.tools),
            )
            request.state = self.state
            return request

    captured = {}

    async def handler(request):
        captured["prompt"] = str(request.system_message.content)
        captured["tools"] = [tool.name for tool in request.tools]
        return "ok"

    result = await SkillsMiddleware().awrap_model_call(FakeRequest(), handler)

    assert result == "ok"
    assert "alpha rules" in captured["prompt"] and "beta rules" in captured["prompt"]
    assert "不要再调用 read_file" in captured["prompt"]
    assert captured["tools"] == ["submit_content_node_result", "tool-a"]
    assert context._activated_required_skills == ["alpha", "beta"]
    assert [(event[2]["skill_slug"], event[2]["content_hash"]) for event in events] == [
        ("alpha", "ha"),
        ("beta", "hb"),
    ]


@pytest.mark.asyncio
async def test_content_node_redundant_required_skill_read_is_safe_and_budgeted():
    context = SimpleNamespace(
        _content_node_max_tool_calls=1,
        _content_node_tool_scope=["submit_content_node_result"],
        _required_skill_closure=["content-evidence-researcher"],
    )
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        tool_call={
            "id": "call-read-skill",
            "name": "read_file",
            "args": {"file_path": "/home/gem/skills/content-evidence-researcher/SKILL.md"},
        },
    )
    handler_called = False

    async def handler(_request):
        nonlocal handler_called
        handler_called = True
        return ToolMessage(content="unexpected", tool_call_id="call-read-skill", name="read_file")

    result = await SkillsMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert "已经在本节点运行前注入并激活" in result.content
    assert handler_called is False
    assert context._content_node_tool_calls_used == 1

    with pytest.raises(RuntimeError, match="工具调用超过节点上限"):
        await SkillsMiddleware().awrap_tool_call(request, handler)


def test_content_node_reserves_structured_result_submission_after_business_tool_budget_is_used():
    context = SimpleNamespace(
        _content_node_max_tool_calls=4,
        _content_node_tool_scope=["query_kb", "submit_content_node_result"],
        _content_node_result_tool_name="submit_content_node_result",
    )

    for _ in range(4):
        SkillsMiddleware._claim_content_tool_call(
            SimpleNamespace(
                runtime=SimpleNamespace(context=context),
                tool_call={"name": "query_kb"},
            )
        )

    assert context._content_node_tool_calls_used == 4
    assert context._content_force_result_submission_reason == "tool_call_limit_reached"

    SkillsMiddleware._claim_content_tool_call(
        SimpleNamespace(
            runtime=SimpleNamespace(context=context),
            tool_call={"name": "submit_content_node_result"},
        )
    )
    assert context._content_node_tool_calls_used == 4

    with pytest.raises(RuntimeError, match="工具调用超过节点上限"):
        SkillsMiddleware._claim_content_tool_call(
            SimpleNamespace(
                runtime=SimpleNamespace(context=context),
                tool_call={"name": "query_kb"},
            )
        )


@pytest.mark.asyncio
async def test_content_node_read_file_outside_required_skill_is_rejected_without_failing_node():
    context = SimpleNamespace(
        _content_node_max_tool_calls=2,
        _content_node_tool_scope=["submit_content_node_result"],
        _required_skill_closure=["content-evidence-researcher"],
    )
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        tool_call={
            "id": "call-read-other",
            "name": "read_file",
            "args": {"file_path": "/home/gem/skills/other-skill/SKILL.md"},
        },
    )

    handler_called = False

    async def handler(_request):
        nonlocal handler_called
        handler_called = True
        return ToolMessage(content="unexpected", tool_call_id="call-read-other", name="read_file")

    result = await SkillsMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert "不开放 read_file" in result.content
    assert handler_called is False
    assert context._content_node_tool_calls_used == 1


@pytest.mark.asyncio
async def test_content_node_with_only_result_tool_forces_it_on_first_call():
    collector = SimpleNamespace(submission_count=0)
    context = SimpleNamespace(
        _content_node_result_collector=collector,
        _content_node_tool_scope=["submit_content_node_result"],
        _required_skill_closure=[],
        _readable_skills=[],
        _runtime_skill_dependency_map={},
    )

    class FakeRequest:
        def __init__(self, *, tools=None, tool_choice=None):
            self.runtime = SimpleNamespace(context=context)
            self.state = {}
            self.tools = tools or [SimpleNamespace(name="submit_content_node_result")]
            self.system_message = SystemMessage(content="base")
            self.tool_choice = tool_choice

        def override(self, **kwargs):
            return FakeRequest(
                tools=kwargs.get("tools", self.tools),
                tool_choice=kwargs.get("tool_choice", self.tool_choice),
            )

    calls = []

    async def handler(request):
        calls.append(request.tool_choice)
        if request.tool_choice:
            return ModelResponse(
                result=[
                    AIMessage(
                        content="",
                        tool_calls=[{"id": "call-1", "name": request.tool_choice, "args": {}}],
                    )
                ]
            )
        return ModelResponse(result=[AIMessage(content="plain result")])

    response = await ContentNodeResultMiddleware().awrap_model_call(FakeRequest(), handler)

    assert calls == ["submit_content_node_result"]
    assert response.result[0].tool_calls[0]["name"] == "submit_content_node_result"


@pytest.mark.asyncio
async def test_content_node_retries_once_when_provider_ignores_forced_result_tool():
    collector = SimpleNamespace(submission_count=0)
    context = SimpleNamespace(_content_node_result_collector=collector)

    class FakeRequest:
        def __init__(self, *, system_message=None, tools=None, tool_choice=None):
            self.runtime = SimpleNamespace(context=context)
            self.tools = tools or [SimpleNamespace(name="submit_content_node_result")]
            self.system_message = system_message or SystemMessage(content="base")
            self.tool_choice = tool_choice

        def override(self, **kwargs):
            return FakeRequest(
                system_message=kwargs.get("system_message", self.system_message),
                tools=kwargs.get("tools", self.tools),
                tool_choice=kwargs.get("tool_choice", self.tool_choice),
            )

    calls = []

    async def handler(request):
        calls.append((request.tool_choice, str(request.system_message.content)))
        if len(calls) == 1:
            return ModelResponse(result=[AIMessage(content="plain result")])
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "call-result",
                            "name": "submit_content_node_result",
                            "args": {},
                        }
                    ],
                )
            ]
        )

    response = await ContentNodeResultMiddleware().awrap_model_call(FakeRequest(), handler)

    assert len(calls) == 2
    assert all(call[0] == "submit_content_node_result" for call in calls)
    assert "禁止返回普通文本" in calls[1][1]
    assert response.result[0].tool_calls[0]["name"] == "submit_content_node_result"


def test_forced_result_submission_removes_historical_tool_call_scaffolding():
    messages = [
        HumanMessage(content="节点输入"),
        AIMessage(
            content="",
            tool_calls=[{"id": "call-query", "name": "query_kb", "args": {"query_text": "价格"}}],
        ),
        ToolMessage(content='{"results":[{"content":"标准价"}]}', tool_call_id="call-query", name="query_kb"),
    ]

    sanitized = ContentNodeResultMiddleware._result_submission_messages(messages)

    assert len(sanitized) == 2
    assert all(isinstance(message, HumanMessage) for message in sanitized)
    assert sanitized[0].content == "节点输入"
    assert "标准价" in str(sanitized[1].content)
    assert "已经完成的工具返回数据" in str(sanitized[1].content)


@pytest.mark.asyncio
async def test_content_node_fails_explicitly_after_two_missing_result_tool_calls():
    collector = SimpleNamespace(submission_count=0)
    context = SimpleNamespace(_content_node_result_collector=collector)

    class FakeRequest:
        def __init__(self, *, system_message=None, tools=None, tool_choice=None):
            self.runtime = SimpleNamespace(context=context)
            self.tools = tools or [SimpleNamespace(name="submit_content_node_result")]
            self.system_message = system_message or SystemMessage(content="base")
            self.tool_choice = tool_choice

        def override(self, **kwargs):
            return FakeRequest(
                system_message=kwargs.get("system_message", self.system_message),
                tools=kwargs.get("tools", self.tools),
                tool_choice=kwargs.get("tool_choice", self.tool_choice),
            )

    async def handler(_request):
        return ModelResponse(result=[AIMessage(content="plain result")])

    with pytest.raises(RuntimeError, match="连续两次未调用结构化结果工具"):
        await ContentNodeResultMiddleware().awrap_model_call(FakeRequest(), handler)


@pytest.mark.asyncio
async def test_content_node_result_tool_is_removed_after_submission():
    collector = SimpleNamespace(submission_count=1)
    context = SimpleNamespace(
        _content_node_result_collector=collector,
        _content_node_tool_scope=["tool-a", "submit_content_node_result"],
        _required_skill_closure=[],
        _readable_skills=[],
        _runtime_skill_dependency_map={},
    )

    class FakeRequest:
        def __init__(self, *, tools=None):
            self.runtime = SimpleNamespace(context=context)
            self.state = {}
            self.tools = tools or [
                SimpleNamespace(name="tool-a"),
                SimpleNamespace(name="submit_content_node_result"),
            ]
            self.system_message = SystemMessage(content="base")

        def override(self, **kwargs):
            return FakeRequest(tools=kwargs.get("tools", self.tools))

    captured = []

    async def handler(request):
        captured.extend(tool.name for tool in request.tools)
        return ModelResponse(result=[AIMessage(content="done")])

    await ContentNodeResultMiddleware().awrap_model_call(FakeRequest(), handler)

    assert captured == ["tool-a"]


@pytest.mark.asyncio
async def test_knowledge_budget_rejection_forces_structured_result_on_next_model_call():
    collector = SimpleNamespace(submission_count=0)
    context = SimpleNamespace(
        _content_node_result_collector=collector,
        _content_force_result_submission_reason="retrieval_round_limit_reached",
    )

    class FakeRequest:
        def __init__(self, *, tools=None, tool_choice=None):
            self.runtime = SimpleNamespace(context=context)
            self.tools = tools or [
                SimpleNamespace(name="query_kb"),
                SimpleNamespace(name="submit_content_node_result"),
            ]
            self.tool_choice = tool_choice

        def override(self, **kwargs):
            return FakeRequest(
                tools=kwargs.get("tools", self.tools),
                tool_choice=kwargs.get("tool_choice", self.tool_choice),
            )

    captured = {}

    async def handler(request):
        captured["tools"] = [tool.name for tool in request.tools]
        captured["tool_choice"] = request.tool_choice
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "call-result",
                            "name": "submit_content_node_result",
                            "args": {},
                        }
                    ],
                )
            ]
        )

    await ContentNodeResultMiddleware().awrap_model_call(FakeRequest(), handler)

    assert captured == {
        "tools": ["submit_content_node_result"],
        "tool_choice": "submit_content_node_result",
    }


@pytest.mark.asyncio
async def test_successful_structured_result_submission_ends_content_agent_loop():
    collector = SimpleNamespace(submission_count=0)
    context = SimpleNamespace(_content_node_result_collector=collector)
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        tool_call={"name": "submit_content_node_result"},
    )
    tool_message = ToolMessage(
        content='{"accepted":true}',
        tool_call_id="call-result",
        name="submit_content_node_result",
    )

    async def handler(_request):
        collector.submission_count = 1
        return tool_message

    result = await ContentNodeResultMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, Command)
    assert result.goto == END
    assert result.update == {"messages": [tool_message]}


def test_successful_structured_result_submission_ends_sync_content_agent_loop():
    collector = SimpleNamespace(submission_count=0)
    context = SimpleNamespace(_content_node_result_collector=collector)
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        tool_call={"name": "submit_content_node_result"},
    )
    tool_message = ToolMessage(
        content='{"accepted":true}',
        tool_call_id="call-result",
        name="submit_content_node_result",
    )

    def handler(_request):
        collector.submission_count = 1
        return tool_message

    result = ContentNodeResultMiddleware().wrap_tool_call(request, handler)

    assert isinstance(result, Command)
    assert result.goto == END
    assert result.update == {"messages": [tool_message]}


@pytest.mark.asyncio
async def test_accepted_result_stops_before_async_model_reentry():
    runtime = SimpleNamespace(
        context=SimpleNamespace(
            _content_node_result_collector=SimpleNamespace(submission_count=1),
        )
    )

    result = await ContentNodeResultMiddleware().abefore_model({}, runtime)

    assert result == {"jump_to": "end"}


def test_unsubmitted_result_keeps_model_loop_open():
    runtime = SimpleNamespace(
        context=SimpleNamespace(
            _content_node_result_collector=SimpleNamespace(submission_count=0),
        )
    )

    result = ContentNodeResultMiddleware().before_model({}, runtime)

    assert result is None


@pytest.mark.asyncio
async def test_accepted_result_ends_compiled_agent_graph_without_second_model_call():
    collector = SimpleNamespace(submission_count=0)

    @tool
    def submit_content_node_result() -> str:
        """提交已通过业务校验的内容节点结果。"""
        collector.submission_count += 1
        return '{"accepted":true}'

    class SingleCallModel(BaseChatModel):
        call_count: int = 0

        @property
        def _llm_type(self) -> str:
            return "single-call-content-result-test"

        def bind_tools(self, tools, **kwargs):  # noqa: ARG002
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
            self.call_count += 1
            if self.call_count > 1:
                raise AssertionError("结果提交成功后不应再次调用模型")
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "id": "call-result",
                                    "name": "submit_content_node_result",
                                    "args": {},
                                }
                            ],
                        )
                    )
                ]
            )

    model = SingleCallModel()
    graph = create_agent(
        model=model,
        tools=[submit_content_node_result],
        middleware=[ContentNodeResultMiddleware()],
    )

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="提交审核结果")]},
        context=SimpleNamespace(_content_node_result_collector=collector),
    )

    assert model.call_count == 1
    assert collector.submission_count == 1
    assert isinstance(result["messages"][-1], ToolMessage)


@pytest.mark.asyncio
async def test_rejected_structured_result_submission_keeps_content_agent_loop_open():
    collector = SimpleNamespace(submission_count=0)
    context = SimpleNamespace(_content_node_result_collector=collector)
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        tool_call={"name": "submit_content_node_result"},
    )
    tool_message = ToolMessage(
        content="结果未通过业务校验，请修正后重新提交",
        tool_call_id="call-result",
        name="submit_content_node_result",
        status="error",
    )

    async def handler(_request):
        return tool_message

    result = await ContentNodeResultMiddleware().awrap_tool_call(request, handler)

    assert result is tool_message


@pytest.mark.asyncio
async def test_successful_cover_creation_forces_structured_result_as_second_tool_call():
    collector = SimpleNamespace(submission_count=0)
    context = SimpleNamespace(
        _content_node_result_collector=collector,
        _content_cover_job_submission={
            "cover_job_id": "job-1",
            "plan_hash": "a" * 64,
            "source_asset_ids": [],
        },
    )

    class FakeRequest:
        def __init__(self, *, tools=None, tool_choice=None):
            self.runtime = SimpleNamespace(context=context)
            self.tools = tools or [
                SimpleNamespace(name="create_content_cover_job"),
                SimpleNamespace(name="submit_content_node_result"),
            ]
            self.tool_choice = tool_choice

        def override(self, **kwargs):
            return FakeRequest(
                tools=kwargs.get("tools", self.tools),
                tool_choice=kwargs.get("tool_choice", self.tool_choice),
            )

    captured = {}

    async def handler(request):
        captured["tools"] = [tool.name for tool in request.tools]
        captured["tool_choice"] = request.tool_choice
        return ModelResponse(
            result=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "call-result",
                            "name": "submit_content_node_result",
                            "args": {},
                        }
                    ],
                )
            ]
        )

    await ContentNodeResultMiddleware().awrap_model_call(FakeRequest(), handler)

    assert captured == {
        "tools": ["submit_content_node_result"],
        "tool_choice": "submit_content_node_result",
    }


@pytest.mark.asyncio
async def test_content_agent_uses_minimal_runtime_middlewares():
    middlewares = await _build_middlewares(
        SimpleNamespace(
            _content_node_result_collector=object(),
            model_retry_times=1,
            model_call_timeout_seconds=70,
        )
    )

    assert [type(item).__name__ for item in middlewares] == [
        "KnowledgeBaseMiddleware",
        "SkillsMiddleware",
        "ContentNodeResultMiddleware",
        "ModelRetryMiddleware",
        "ModelCallTimeoutMiddleware",
        "TokenUsageMiddleware",
    ]
    assert middlewares[3].max_retries == 1
    assert middlewares[4].timeout_seconds == 70


@pytest.mark.asyncio
async def test_content_agent_without_model_call_timeout_keeps_existing_middlewares():
    middlewares = await _build_middlewares(
        SimpleNamespace(_content_node_result_collector=object(), model_retry_times=2)
    )

    assert [type(item).__name__ for item in middlewares] == [
        "KnowledgeBaseMiddleware",
        "SkillsMiddleware",
        "ContentNodeResultMiddleware",
        "ModelRetryMiddleware",
        "TokenUsageMiddleware",
    ]


def _delegation_request(**overrides):
    strategy_snapshot = {
        "content_direction": "CT01",
        "selected_group_id": "group-1",
        "creation_methods": ["M1"],
        "creation_method_definitions": [
            {
                "code": "M1",
                "name": "价值法",
                "method_type": "core",
                "principle": "表达价值",
                "suitable_scenes": [],
                "sentence_patterns": [],
                "variable_schema": ["advantages"],
                "risk_rules": [],
            }
        ],
        "title_formula": {"code": "T1"},
        "body_formula": {"code": "B1"},
        "rule_version_id": "rules-v3",
        "match_snapshot_id": "match-1",
        "formula_snapshot_id": "formula-1",
    }
    strategy_snapshot["snapshot_hash"] = hashlib.sha256(
        json.dumps(strategy_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    governance = {
        "match_decision_snapshot": {
            "selected_group_id": "group-1",
            "eligible_title_formula_codes": ["T1"],
            "eligible_body_formula_codes": ["B1"],
        },
        "formula_selection_snapshot": {
            "combination_group_id": "group-1",
            "eligible_title_formula_codes": ["T1"],
            "eligible_body_formula_codes": ["B1"],
            "selected_title_formula_code": "T1",
            "selected_body_formula_code": "B1",
        },
        "evidence_bundle": {
            "id": "bundle-1",
            "version": 2,
            "bundle_hash": "e" * 64,
            "items": [
                {
                    "id": "e-body",
                    "value": "fact",
                    "verified_status": "confirmed",
                    "allowed_usage": ["body"],
                }
            ],
        },
        "locked_versions": {
            "industry_pack_version_id": "industry-v3",
            "channel_profile_version_id": "channel-v1",
            "persona_profile_version_id": None,
            "rule_version_id": "rules-v3",
            "title_formula_code": "T1",
            "body_formula_code": "B1",
            "artifact_version_id": None,
        },
        "locked_values": {"selected_title": "locked title"},
    }
    product_evidence_pack = {
        "strategy_snapshot_hash": strategy_snapshot["snapshot_hash"],
        "evidence_bundle_id": governance["evidence_bundle"]["id"],
        "evidence_bundle_version": governance["evidence_bundle"]["version"],
        "evidence_bundle_hash": governance["evidence_bundle"]["bundle_hash"],
        "slot_mappings": [],
        "unresolved_questions": [],
    }
    product_evidence_pack["pack_hash"] = hashlib.sha256(
        json.dumps(product_evidence_pack, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    values = {
        "task_id": "task-1",
        "parent_content_run_id": "parent-run",
        "node_run": SimpleNamespace(id="node-run-1", node_id="generate_body", node_type="agent", attempt=1),
        "user": SimpleNamespace(uid="u1", role="user", department_id=None),
        "agent_slug": "content-body-agent",
        "required_skills": ("content-body-generator",),
        "input_contract": "GenerateBodyInputV1",
        "input_payload": {
            "content_brief": {"brand": {"name": "x"}},
            "strategy_snapshot": strategy_snapshot,
            "product_evidence_pack": product_evidence_pack,
            "evidence_bundle": governance["evidence_bundle"],
            "channel_profile": {},
            "persona_profile": {},
            "selected_title": {"id": "title-1", "text": "locked title"},
            "content_outline": {"body_formula_code": "B1", "sections": [{"section_id": "s1"}]},
        },
        "input_snapshot_hash": "input-hash",
        "domain_context": ContractDomainContext.from_governance(**governance),
        "governance_values": governance,
        "prompt": "generate",
        "output_contract": "ContentDraftResultV1",
    }
    values.update(overrides)
    return AgentDelegationRequest(**values)


def test_delegated_run_identifiers_fit_database_columns_and_remain_stable():
    short = "content:task-1:generate_body:1"
    long_value = "content-node:85ef3eaa-fa49-4ba9-8fcf-5e360f699f0c:cnr_e293eb9d814c427f8e797b0226a293df"

    assert _bounded_run_identifier(short) == short
    assert len(_bounded_run_identifier(long_value)) == 64
    assert _bounded_run_identifier(long_value) == _bounded_run_identifier(long_value)
    assert _bounded_run_identifier(long_value).startswith("content-node:")


@pytest.mark.asyncio
async def test_delegation_creates_traceable_child_run_and_runtime_snapshot(monkeypatch):
    context_holder = {}

    class FakeContext:
        def __init__(self, *, thread_id, uid):
            self.thread_id = thread_id
            self.uid = uid
            self.model = "provider:model"
            self.skills = []
            self.skill_tool_allowlist = []
            self.knowledges = ["kb-a", "kb-b"]
            self.mcps = []
            self.max_execution_steps = 30
            context_holder["context"] = self

        def update_from_dict(self, data):
            for key, value in data.items():
                setattr(self, key, value)

    class FakeGraph:
        async def ainvoke(self, state, *, context, config):
            model_input = json.loads(state["messages"][0])
            assert model_input["node_id"] == "generate_body"
            assert "output_json_schema" not in model_input
            assert "runtime_config_snapshot" not in model_input
            assert config["recursion_limit"] == 12
            context._activated_required_skills = ["content-body-generator"]
            await context._content_node_result_collector.submit(
                body="body text",
                topics=["topic"],
                paragraph_evidence=[{"paragraph_id": "p1", "evidence_ids": ["e-body"]}],
                body_formula_code="B1",
            )
            return {"messages": []}

    class FakeBackend:
        context_schema = FakeContext

        async def get_graph(self, *, context):
            assert context.run_id.startswith("run_")
            return FakeGraph()

    agent = SimpleNamespace(
        slug="content-body-agent",
        backend_id="ChatbotAgent",
        config_version=4,
        config_json={"context": {}},
    )

    async def fake_normalize(*args, **kwargs):
        return {
            "model": "provider:model",
            "skills": ["content-body-generator"],
            "skill_tool_allowlist": ["get_business_facts"],
            "knowledges": ["kb-a", "kb-b"],
        }

    async def fake_prepare(context, **kwargs):
        context._required_skill_closure = ["content-body-generator"]
        context._required_skill_tools = ["get_business_facts"]
        context._required_skill_mcps = []
        context._runtime_skill_snapshots = [
            {"slug": "content-body-generator", "version": "2.0.0", "content_hash": "hash-2"}
        ]
        return context

    created = {}

    class FakeRunRepo:
        async def create_run(self, **kwargs):
            created.update(kwargs)
            return SimpleNamespace(id=kwargs["run_id"])

        async def mark_running(self, run_id):
            created["running"] = run_id

        async def set_terminal_status(self, run_id, **kwargs):
            created["terminal"] = (run_id, kwargs)

    class FakeContentRepo:
        async def attach_delegated_agent_run(self, node_run, run_id):
            node_run.delegated_agent_run_id = run_id

    class FakeDB:
        async def commit(self):
            created["commits"] = created.get("commits", 0) + 1

    events = []

    async def fake_append(run_id, event_type, payload, *, thread_id=None):
        events.append((run_id, event_type, payload, thread_id))

    monkeypatch.setattr(delegation_module, "normalize_agent_context_config", fake_normalize)
    monkeypatch.setattr(delegation_module, "prepare_agent_runtime_context", fake_prepare)
    monkeypatch.setattr(delegation_module, "append_run_stream_event", fake_append)
    import yuxi.services.run_queue_service as run_queue_service

    monkeypatch.setattr(run_queue_service, "append_run_stream_event", fake_append)
    service = AgentDelegationService.__new__(AgentDelegationService)
    service.db = FakeDB()
    service.run_repo = FakeRunRepo()
    service.content_repo = FakeContentRepo()

    async def fake_resolve(_request):
        return agent, FakeBackend()

    service._resolve_agent = fake_resolve
    request = _delegation_request()

    result = await service.execute(request)

    assert created["run_type"] == "content_node_agent"
    assert created["parent_agent_run_id"] == "parent-run"
    assert created["thread_id"] == "content:task-1:generate_body:1"
    assert created["agent_id"] == "content-body-agent"
    assert created["input_payload"]["input"]["payload"]["content_outline"]["body_formula_code"] == "B1"
    assert "locked_values" not in created["input_payload"]["input"]
    assert request.node_run.input_snapshot["input_contract"] == "GenerateBodyInputV1"
    assert request.node_run.input_snapshot["input_snapshot_hash"] == "input-hash"
    assert request.node_run.delegated_agent_run_id == result.delegated_agent_run_id
    assert result.runtime_config_snapshot["agent"]["config_version"] == 4
    assert result.runtime_config_snapshot["skills"][0]["content_hash"] == "hash-2"
    assert result.runtime_config_snapshot["knowledges"] == []
    assert result.runtime_config_snapshot["tools"] == [
        "get_business_facts",
        "submit_content_node_result",
    ]
    assert created["terminal"][1]["status"] == "completed"
    assert [event[1] for event in events] == [
        "content.agent.started",
        "content.agent.started",
        "content.tool.called",
        "content.tool.called",
        "content.tool.completed",
        "content.tool.completed",
        "content.agent.completed",
        "content.agent.completed",
    ]
    assert {event[0] for event in events} == {result.delegated_agent_run_id, "parent-run"}
    assert all(event[2]["delegated_agent_run_id"] == result.delegated_agent_run_id for event in events)


@pytest.mark.asyncio
async def test_delegation_timeout_and_cancel_are_not_swallowed():
    class SlowGraph:
        async def ainvoke(self, *args, **kwargs):
            await asyncio.sleep(1)
            return {}

    context = SimpleNamespace(thread_id="thread", uid="u1")
    timeout_request = _delegation_request(timeout_seconds=0.01)
    with pytest.raises(TimeoutError):
        await AgentDelegationService._invoke_graph(SlowGraph(), context, timeout_request)

    cancel_event = asyncio.Event()
    cancel_event.set()
    cancel_request = _delegation_request(cancel_event=cancel_event)
    with pytest.raises(asyncio.CancelledError):
        await AgentDelegationService._invoke_graph(SlowGraph(), context, cancel_request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent", "accessible", "backend_error", "expected_code"),
    [
        (None, True, False, "agent_not_found"),
        (SimpleNamespace(enabled=False, slug="a", backend_id="ChatbotAgent"), True, False, "agent_disabled"),
        (SimpleNamespace(enabled=True, slug="a", backend_id="ChatbotAgent"), False, False, "agent_forbidden"),
        (SimpleNamespace(enabled=True, slug="a", backend_id="MissingBackend"), True, True, "agent_backend_unavailable"),
    ],
)
async def test_delegation_agent_resolution_fails_closed(monkeypatch, agent, accessible, backend_error, expected_code):
    class FakeAgentRepo:
        async def get_by_slug(self, slug):
            del slug
            return agent

    service = AgentDelegationService.__new__(AgentDelegationService)
    service.agent_repo = FakeAgentRepo()
    monkeypatch.setattr(delegation_module, "user_can_access_agent", lambda user, item: accessible)

    def fake_get_agent(backend_id):
        if backend_error:
            raise KeyError(backend_id)
        return object()

    monkeypatch.setattr(delegation_module.agent_manager, "get_agent", fake_get_agent)

    with pytest.raises(ContentApplicationError) as exc_info:
        await service._resolve_agent(_delegation_request(agent_slug="a"))

    assert exc_info.value.code == expected_code


@pytest.mark.parametrize(
    ("node_id", "expected_names"),
    [
        ("collect_business_rule_evidence", {"品牌知识库", "平台规则"}),
        ("collect_price_evidence", {"价格库"}),
        ("collect_compliance_evidence", {"封禁词库"}),
        ("collect_viral_candidates", {"爆款库"}),
    ],
)
def test_parallel_research_agents_receive_only_their_knowledge_scope(node_id, expected_names):
    context = SimpleNamespace(
        knowledges=["kb-brand", "kb-rules", "kb-price", "kb-forbidden", "kb-viral"],
        _visible_knowledge_bases=[
            {"kb_id": "kb-brand", "name": "品牌知识库"},
            {"kb_id": "kb-rules", "name": "平台规则"},
            {"kb_id": "kb-price", "name": "价格库"},
            {"kb_id": "kb-forbidden", "name": "封禁词库"},
            {"kb_id": "kb-viral", "name": "爆款库"},
        ],
    )

    AgentDelegationService._restrict_research_knowledge_scope(context, node_id)

    assert {item["name"] for item in context._visible_knowledge_bases} == expected_names
    assert set(context.knowledges) == {item["kb_id"] for item in context._visible_knowledge_bases}


def test_formal_content_agent_catalog_and_conflict_policy():
    assert len(CONTENT_AGENT_SPECS) == 12
    assert {item.slug for item in CONTENT_AGENT_SPECS} == {
        "content-strategy-agent",
        "content-research-agent",
        "content-business-rule-research-agent",
        "content-price-research-agent",
        "content-compliance-research-agent",
        "content-viral-candidate-agent",
        "content-viral-selection-agent",
        "content-title-agent",
        "content-body-agent",
        "content-generation-agent",
        "content-review-agent",
        "content-visual-agent",
    }
    title_spec = next(item for item in CONTENT_AGENT_SPECS if item.slug == "content-title-agent")
    assert title_spec.skill_tools == ()
    assert title_spec.config_version == 4
    strategy_spec = next(item for item in CONTENT_AGENT_SPECS if item.slug == "content-strategy-agent")
    assert strategy_spec.reasoning_effort == "medium"
    assert strategy_spec.model_call_timeout_seconds == 70
    assert strategy_spec.model_retry_times == 1
    assert strategy_spec.config_version == 5
    research_spec = next(item for item in CONTENT_AGENT_SPECS if item.slug == "content-research-agent")
    assert "viral-reference-selector" in research_spec.skills
    assert research_spec.reasoning_effort == "low"
    assert research_spec.model_call_timeout_seconds == 60
    assert research_spec.model_retry_times == 1
    assert research_spec.config_version == 6
    specialist_specs = [item for item in CONTENT_AGENT_SPECS if item.inherit_context_from == "content-research-agent"]
    assert len(specialist_specs) == 5
    assert all(item.reasoning_effort == "low" for item in specialist_specs)
    assert all(item.model_call_timeout_seconds <= 65 for item in specialist_specs)
    assert all(item.model_retry_times == 0 for item in specialist_specs)
    business_spec = next(item for item in specialist_specs if item.slug == "content-business-rule-research-agent")
    assert business_spec.config_version == 4
    price_spec = next(item for item in specialist_specs if item.slug == "content-price-research-agent")
    assert price_spec.model == ""
    assert price_spec.model_call_timeout_seconds == 55
    assert price_spec.config_version == 7
    assert all(item.config_version >= 2 for item in specialist_specs)
    research_collectors = [item for item in specialist_specs if item.slug != "content-viral-selection-agent"]
    assert all(item.skill_tools == ("query_kb",) for item in research_collectors)
    generation_spec = next(item for item in CONTENT_AGENT_SPECS if item.slug == "content-generation-agent")
    assert generation_spec.skills == (
        "content-title-generator",
        "content-outline-builder",
        "content-body-generator",
        "viral-structure-rewriter",
        "viral-layout-formatter",
        "content-human-expression",
    )
    assert generation_spec.config_version == 4
    spec = CONTENT_AGENT_SPECS[0]
    existing = Agent(
        slug=spec.slug,
        backend_id="SubAgentBackend",
        name="conflict",
        config_json={"context": {}},
        enabled=True,
        is_subagent=True,
    )
    with pytest.raises(ValueError, match="显式迁移"):
        validate_existing_content_agent(existing, spec)


def test_system_content_agent_migration_is_versioned_and_preserves_extra_context():
    spec = next(item for item in CONTENT_AGENT_SPECS if item.slug == "content-review-agent")
    existing = Agent(
        slug=spec.slug,
        backend_id="ChatbotAgent",
        name=spec.name,
        config_json={
            "context": {
                "system_prompt": "旧提示词",
                "skills": ["content-reviewer"],
                "skill_tool_allowlist": ["validate_content_facts"],
                "model": "provider:model",
            }
        },
        enabled=True,
        config_version=1,
        is_subagent=False,
        created_by="system",
        updated_by="system",
    )

    assert migrate_system_content_agent(existing, spec) is True
    assert existing.config_version == 3
    assert existing.config_json["context"]["model"] == "provider:model"
    assert set(existing.config_json["context"]["skill_tool_allowlist"]) == set(spec.skill_tools)
    validate_existing_content_agent(existing, spec)


def test_system_content_agent_migration_does_not_overwrite_user_changes():
    spec = next(item for item in CONTENT_AGENT_SPECS if item.slug == "content-visual-agent")
    existing = Agent(
        slug=spec.slug,
        backend_id="ChatbotAgent",
        name=spec.name,
        config_json={"context": {"skills": list(spec.skills), "skill_tool_allowlist": []}},
        enabled=True,
        config_version=1,
        is_subagent=False,
        created_by="system",
        updated_by="user-1",
    )

    assert migrate_system_content_agent(existing, spec) is False
    assert existing.config_version == 1
    assert existing.updated_by == "user-1"


def test_research_agent_additive_migration_preserves_user_configuration():
    spec = next(item for item in CONTENT_AGENT_SPECS if item.slug == "content-research-agent")
    existing = Agent(
        slug=spec.slug,
        backend_id="ChatbotAgent",
        name=spec.name,
        config_json={
            "context": {
                "skills": ["content-evidence-researcher", "strategy-product-researcher", "user-extra-skill"],
                "skill_tool_allowlist": list(spec.skill_tools),
                "model": "provider:user-model",
            }
        },
        enabled=True,
        config_version=4,
        is_subagent=False,
        created_by="system",
        updated_by="user-1",
    )

    assert migrate_system_content_agent(existing, spec) is True
    assert existing.config_version == spec.config_version
    assert existing.updated_by == "user-1"
    assert existing.config_json["context"]["model"] == "provider:user-model"
    assert set(existing.config_json["context"]["skills"]) == {*spec.skills, "user-extra-skill"}
    assert existing.config_json["context"]["reasoning_effort"] == "low"
    assert existing.config_json["context"]["model_call_timeout_seconds"] == 60
    assert existing.config_json["context"]["model_retry_times"] == 1


def test_research_agent_runtime_budget_migration_preserves_user_knowledge_bases():
    spec = next(item for item in CONTENT_AGENT_SPECS if item.slug == "content-research-agent")
    existing = Agent(
        slug=spec.slug,
        backend_id="ChatbotAgent",
        name=spec.name,
        config_json={
            "context": {
                "skills": list(spec.skills),
                "skill_tool_allowlist": list(spec.skill_tools),
                "knowledges": ["kb-user-selected"],
                "model": "provider:user-model",
                "model_retry_times": 2,
            }
        },
        enabled=True,
        config_version=5,
        is_subagent=False,
        created_by="system",
        updated_by="user-1",
    )

    assert migrate_system_content_agent(existing, spec) is True
    assert existing.config_version == 6
    assert existing.updated_by == "user-1"
    assert existing.config_json["context"]["knowledges"] == ["kb-user-selected"]
    assert existing.config_json["context"]["model"] == "provider:user-model"
    assert existing.config_json["context"]["reasoning_effort"] == "low"
    assert existing.config_json["context"]["model_call_timeout_seconds"] == 60
    assert existing.config_json["context"]["model_retry_times"] == 1


def test_generation_agent_additive_migration_installs_viral_skills():
    spec = next(item for item in CONTENT_AGENT_SPECS if item.slug == "content-generation-agent")
    existing = Agent(
        slug=spec.slug,
        backend_id="ChatbotAgent",
        name=spec.name,
        config_json={
            "context": {
                "skills": [
                    "content-title-generator",
                    "content-outline-builder",
                    "content-body-generator",
                    "content-human-expression",
                ],
                "skill_tool_allowlist": [],
                "model": "provider:user-model",
            }
        },
        enabled=True,
        config_version=2,
        is_subagent=False,
        created_by="system",
        updated_by="user-1",
    )

    assert migrate_system_content_agent(existing, spec) is True
    assert existing.config_version == 4
    assert existing.updated_by == "user-1"
    assert existing.config_json["context"]["model"] == "provider:user-model"
    assert set(existing.config_json["context"]["skills"]) == set(spec.skills)


def test_generation_agent_additive_migration_installs_viral_layout_formatter():
    spec = next(item for item in CONTENT_AGENT_SPECS if item.slug == "content-generation-agent")
    existing = Agent(
        slug=spec.slug,
        backend_id="ChatbotAgent",
        name=spec.name,
        config_json={
            "context": {
                "skills": [
                    "content-title-generator",
                    "content-outline-builder",
                    "content-body-generator",
                    "viral-structure-rewriter",
                    "content-human-expression",
                    "user-extra-skill",
                ],
                "skill_tool_allowlist": [],
                "model": "provider:user-model",
            }
        },
        enabled=True,
        config_version=3,
        is_subagent=False,
        created_by="system",
        updated_by="user-1",
    )

    assert migrate_system_content_agent(existing, spec) is True
    assert existing.config_version == 4
    assert existing.updated_by == "user-1"
    assert existing.config_json["context"]["model"] == "provider:user-model"
    assert set(existing.config_json["context"]["skills"]) == {*spec.skills, "user-extra-skill"}


def test_delegation_schema_has_unique_child_run_and_parent_node_index():
    delegated = ContentNodeRun.__table__.c.delegated_agent_run_id
    assert delegated.unique is True
    indexes = {index.name: tuple(column.name for column in index.columns) for index in ContentNodeRun.__table__.indexes}
    assert indexes["idx_content_node_runs_parent_node_attempt"] == ("agent_run_id", "node_id", "attempt")
    assert Agent.__table__.c.enabled.nullable is False
    assert Agent.__table__.c.config_version.default.arg == 1


def test_runtime_snapshot_hash_changes_with_skill_hash():
    agent = SimpleNamespace(slug="a", backend_id="ChatbotAgent", config_version=1)
    request = _delegation_request()
    context = SimpleNamespace(
        model="m",
        _runtime_skill_snapshots=[{"slug": "s", "version": "1", "content_hash": "h1"}],
        _required_skill_tools=[],
        _required_skill_mcps=[],
        knowledges=[],
    )
    first = build_runtime_config_snapshot(agent=agent, context=context, request=request)
    context._runtime_skill_snapshots[0]["content_hash"] = "h2"
    second = build_runtime_config_snapshot(agent=agent, context=context, request=request)
    assert first["snapshot_hash"] != second["snapshot_hash"]
