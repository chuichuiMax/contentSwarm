"""持续输出不应被调用或节点的固定时限取消。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.agents.middlewares.model_call_timeout import ContentModelProgress, ModelCallTimeoutMiddleware
from yuxi.services.agent_delegation_service import AgentDelegationService


class Request(SimpleNamespace):
    def override(self, **kwargs):
        return Request(**{**vars(self), **kwargs})


def context_and_request():
    context = SimpleNamespace(
        thread_id="idle-timeout-test",
        uid="test",
        _content_max_model_calls=2,
        _content_node_token_budget=1000,
    )
    request = Request(runtime=SimpleNamespace(context=context), model_settings={}, messages=[])
    return context, request


@pytest.fixture(autouse=True)
def no_runtime_events(monkeypatch):
    monkeypatch.setattr(
        "yuxi.agents.middlewares.model_call_timeout.append_content_runtime_event",
        AsyncMock(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("output_kind", ["text", "tool_arguments"])
async def test_continuous_output_survives_both_call_and_node_deadlines(output_kind):
    context, request = context_and_request()
    middleware = ModelCallTimeoutMiddleware(0.15)

    class Graph:
        async def ainvoke(self, *args, **kwargs):
            progress = kwargs["config"]["callbacks"][0]

            async def stream(req):
                for _ in range(12):
                    await asyncio.sleep(0.03)
                    if output_kind == "text":
                        await progress.on_llm_new_token("正文")
                    else:
                        await progress.on_llm_new_token(
                            "",
                            chunk=SimpleNamespace(message=SimpleNamespace(tool_call_chunks=[{"args": "正文"}])),
                        )
                return {"complete": True}

            return await middleware.awrap_model_call(request, stream)

    result = await AgentDelegationService._invoke_graph(
        Graph(),
        context,
        SimpleNamespace(prompt="test", max_execution_steps=2, cancel_event=None, timeout_seconds=0.20),
    )
    assert result == {"complete": True}
    assert context._content_model_calls == 1
    assert context._content_model_progress["chunks"] == 12


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_output", [False, True])
async def test_no_output_or_stalled_output_times_out_and_closes_request(initial_output):
    context, request = context_and_request()
    stopped = asyncio.Event()

    async def stalled(req):
        try:
            if initial_output:
                await ContentModelProgress(context).on_llm_new_token("部分正文")
            await asyncio.Event().wait()
        finally:
            stopped.set()

    with pytest.raises(TimeoutError, match="无输出|停滞"):
        await ModelCallTimeoutMiddleware(0.06).awrap_model_call(request, stalled)
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_empty_tool_heartbeat_does_not_count_as_output():
    context, request = context_and_request()
    stopped = asyncio.Event()

    async def heartbeat(req):
        try:
            while True:
                await ContentModelProgress(context).on_llm_new_token(
                    "",
                    chunk=SimpleNamespace(message=SimpleNamespace(tool_call_chunks=[{"index": 0, "args": ""}])),
                )
                await asyncio.sleep(0.01)
        finally:
            stopped.set()

    with pytest.raises(TimeoutError):
        await ModelCallTimeoutMiddleware(0.06).awrap_model_call(request, heartbeat)
    assert stopped.is_set()
    assert context._content_model_progress["chunks"] == 0


@pytest.mark.asyncio
async def test_user_cancel_stops_continuous_stream():
    context, request = context_and_request()
    cancel = asyncio.Event()
    stopped = asyncio.Event()
    started = asyncio.Event()

    class Graph:
        async def ainvoke(self, *args, **kwargs):
            async def stream(req):
                try:
                    started.set()
                    while True:
                        await ContentModelProgress(context).on_llm_new_token("正文")
                        await asyncio.sleep(0.01)
                finally:
                    stopped.set()

            return await ModelCallTimeoutMiddleware(0.15).awrap_model_call(request, stream)

    task = asyncio.create_task(
        AgentDelegationService._invoke_graph(
            Graph(),
            context,
            SimpleNamespace(prompt="test", max_execution_steps=2, cancel_event=cancel, timeout_seconds=0.20),
        )
    )
    await started.wait()
    cancel.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_node_without_model_progress_still_times_out():
    context, _ = context_and_request()
    stopped = asyncio.Event()

    class Graph:
        async def ainvoke(self, *args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    with pytest.raises(TimeoutError, match="Agent 节点"):
        await AgentDelegationService._invoke_graph(
            Graph(),
            context,
            SimpleNamespace(prompt="test", max_execution_steps=2, cancel_event=None, timeout_seconds=0.06),
        )
    assert stopped.is_set()
