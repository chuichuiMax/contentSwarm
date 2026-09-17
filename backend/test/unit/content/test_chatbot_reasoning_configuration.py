from unittest.mock import AsyncMock

import pytest

from yuxi.agents.buildin.chatbot import graph as chatbot
from yuxi.agents.buildin.chatbot.context import ChatBotContext


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["low", None])
async def test_compiled_agent_honors_configured_reasoning_effort(monkeypatch, effort):
    context = ChatBotContext(reasoning_effort=effort)
    monkeypatch.setattr(chatbot, "prepare_agent_runtime_context", AsyncMock(return_value=context))
    monkeypatch.setattr(chatbot, "resolve_configured_runtime_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(chatbot, "_build_middlewares", AsyncMock(return_value=[]))
    monkeypatch.setattr(chatbot, "resolve_chat_model_spec", lambda _: "provider:test")
    monkeypatch.setattr(chatbot, "build_prompt_with_context", lambda _: "test")
    monkeypatch.setattr(chatbot.ChatbotAgent, "_get_checkpointer", AsyncMock(return_value=None))
    captured = {}

    def load(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(chatbot, "load_chat_model", load)
    monkeypatch.setattr(chatbot, "create_agent", lambda **kwargs: kwargs)
    await chatbot.ChatbotAgent().get_graph(context)
    assert captured == {"fully_specified_name": "provider:test", **({"reasoning_effort": effort} if effort else {})}
