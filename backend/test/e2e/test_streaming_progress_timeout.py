"""用真实 HTTP SSE、ChatOpenAI 和 LangGraph 验证持续输出跨越旧时限。"""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from yuxi.agents.middlewares.model_call_timeout import ModelCallTimeoutMiddleware
from yuxi.services.agent_delegation_service import AgentDelegationService


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_http_stream_survives_call_and_agent_timeout(monkeypatch):
    monkeypatch.setattr(
        "yuxi.agents.middlewares.model_call_timeout.append_content_runtime_event",
        AsyncMock(),
    )
    requests = []

    async def completion(request):
        body = await request.json()
        requests.append(body)
        assert body["stream"] is True
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for index in range(12):
            await asyncio.sleep(0.075)
            chunk = {
                "id": "stream-progress-test",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "文"},
                        "finish_reason": None,
                    }
                ],
            }
            if index == 0:
                chunk["choices"][0]["delta"]["role"] = "assistant"
            await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await response.write(b"data: [DONE]\n\n")
        return response

    app = web.Application()
    app.router.add_post("/v1/chat/completions", completion)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    model = ChatOpenAI(
        model="test-model",
        api_key="test-only",
        base_url=f"http://127.0.0.1:{port}/v1",
        streaming=True,
        max_retries=0,
        timeout=10,
    )
    context = SimpleNamespace(
        thread_id="stream-progress-e2e",
        uid="test",
        _content_max_model_calls=2,
        _content_node_token_budget=1000,
    )
    graph = create_agent(model=model, middleware=[ModelCallTimeoutMiddleware(0.4)])
    try:
        started = time.monotonic()
        result = await AgentDelegationService._invoke_graph(
            graph,
            context,
            SimpleNamespace(prompt="测试持续输出", max_execution_steps=10, cancel_event=None, timeout_seconds=0.6),
        )
        assert result["messages"][-1].content == "文" * 12
        assert time.monotonic() - started > 0.6
        assert len(requests) == 1
        assert context._content_model_progress["chunks"] == 12
        assert context._content_model_calls == 1
    finally:
        await model.root_async_client.close()
        model.root_client.close()
        await runner.cleanup()
