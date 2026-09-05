import asyncio
from types import SimpleNamespace

import pytest
from langchain.agents.middleware import ModelResponse, ModelRetryMiddleware
from langchain_core.messages import AIMessage

from yuxi.agents.middlewares.model_call_timeout import ModelCallTimeoutMiddleware


@pytest.mark.asyncio
async def test_model_call_timeout_returns_completed_response():
    middleware = ModelCallTimeoutMiddleware(0.1)
    expected = ModelResponse(result=[AIMessage(content="ok")])

    async def handler(request):
        del request
        return expected

    result = await middleware.awrap_model_call(SimpleNamespace(), handler)

    assert result is expected


@pytest.mark.asyncio
async def test_model_call_timeout_raises_retryable_timeout_before_node_budget_is_consumed():
    middleware = ModelCallTimeoutMiddleware(0.01)

    async def handler(request):
        del request
        await asyncio.sleep(0.1)
        raise AssertionError("unreachable")

    with pytest.raises(TimeoutError, match=r"模型单次调用超时（0.01s）"):
        await middleware.awrap_model_call(SimpleNamespace(), handler)


@pytest.mark.asyncio
async def test_model_retry_retries_after_one_model_call_timeout():
    timeout = ModelCallTimeoutMiddleware(0.01)
    retry = ModelRetryMiddleware(max_retries=1, initial_delay=0, jitter=False, on_failure="error")
    calls = 0

    async def handler(request):
        nonlocal calls
        del request
        calls += 1
        if calls == 1:
            await asyncio.sleep(0.1)
        return ModelResponse(result=[AIMessage(content="recovered")])

    request = SimpleNamespace()
    result = await retry.awrap_model_call(
        request,
        lambda current: timeout.awrap_model_call(current, handler),
    )

    assert calls == 2
    assert result.result[0].content == "recovered"
