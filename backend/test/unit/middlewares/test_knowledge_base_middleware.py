from types import SimpleNamespace

import pytest
from langchain_core.messages import SystemMessage

from yuxi.agents.middlewares.knowledge_base import KnowledgeBaseMiddleware


@pytest.mark.asyncio
async def test_content_node_receives_exact_knowledge_scope_and_limits():
    context = SimpleNamespace(
        _content_node_tool_scope=["query_kb", "submit_content_node_result"],
        _content_max_knowledge_bases=3,
        _content_max_retrieval_rounds=4,
        _visible_knowledge_bases=[
            {"kb_id": "kb-price", "name": "价格库", "description": "公司正式报价"},
            {"kb_id": "kb-viral", "name": "爆款库", "description": "内容结构样例"},
        ],
    )

    class FakeRequest:
        def __init__(self, system_message=None):
            self.runtime = SimpleNamespace(context=context)
            self.system_message = system_message or SystemMessage(content="base")

        def override(self, **kwargs):
            return FakeRequest(system_message=kwargs.get("system_message", self.system_message))

    captured = {}

    async def handler(request):
        captured["prompt"] = str(request.system_message.content)
        return "ok"

    result = await KnowledgeBaseMiddleware().awrap_model_call(FakeRequest(), handler)

    assert result == "ok"
    assert '"kb_id":"kb-price","name":"价格库"' in captured["prompt"]
    assert '"kb_id":"kb-viral","name":"爆款库"' in captured["prompt"]
    assert "不同知识库数量上限：3；检索轮次上限：4" in captured["prompt"]
    assert "禁止使用知识库名称、自造 ID" in captured["prompt"]
    assert "达到任一检索预算上限后，禁止继续调用 query_kb" in captured["prompt"]
