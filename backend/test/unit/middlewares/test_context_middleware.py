from types import SimpleNamespace

import pytest

from yuxi.agents.middlewares import context as context_middleware


@pytest.mark.asyncio
async def test_context_model_overrides_provider_reasoning_effort(monkeypatch):
    captured: dict = {}
    selected_model = object()

    monkeypatch.setattr(context_middleware, "resolve_chat_model_spec", lambda model: "provider:model")

    def fake_load_chat_model(model_spec, **kwargs):
        captured.update({"model_spec": model_spec, "kwargs": kwargs})
        return selected_model

    monkeypatch.setattr(context_middleware, "load_chat_model", fake_load_chat_model)

    request = SimpleNamespace(
        runtime=SimpleNamespace(
            context=SimpleNamespace(model="", reasoning_effort="medium"),
        ),
        messages=[SimpleNamespace(content="test")],
    )

    def override(**kwargs):
        captured["request_model"] = kwargs["model"]
        return request

    request.override = override

    async def handler(_request):
        return "ok"

    result = await context_middleware.context_based_model.awrap_model_call(request, handler)

    assert result == "ok"
    assert captured == {
        "model_spec": "provider:model",
        "kwargs": {"reasoning_effort": "medium"},
        "request_model": selected_model,
    }
