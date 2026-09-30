import pytest
from langchain_core.messages import AIMessageChunk

import yuxi.content.generation as generation


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_generation_uses_only_requested_inputs(monkeypatch):
    calls = []
    bind_tool_calls = []
    load_model_calls = []
    deltas = []

    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            bind_tool_calls.append((schemas, kwargs))
            return self

        async def astream(self, messages):
            calls.append(messages)
            for arguments in (
                '{"title":"报价标',
                '题","body":"报价正',
                '文","topics":["报价"]}',
            ):
                yield AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": "ContentArtifactAIEditOutput",
                            "args": arguments,
                            "id": "call-1",
                            "index": 0,
                        }
                    ],
                )

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")

    def fake_load_chat_model(**kwargs):
        load_model_calls.append(kwargs)
        return FakeModel()

    monkeypatch.setattr(generation, "load_chat_model", fake_load_chat_model)

    async def collect_delta(update):
        deltas.append(update)

    result = await generation.generate_direct_content(
        model_spec="ark:doubao-seed-2-1-pro-260915",
        creative_style={"name": "反差价值型", "instruction": "用别人报 xx 万、我们 xx 万制造反差"},
        viral_source={"title": "爆款标题", "body": "爆款正文"},
        user_request='{"serialNo":"002","persona":{"age":"45"}}',
        generation_prompt="使用给定元素仿写，并符合北京本地口吻",
        forbidden_lexicon={"报价": ["费用"]},
        on_delta=collect_delta,
    )

    assert result.title == "费用标题"
    assert result.body == (
        "费用正文\n\n"
        "📩在下方留下【小区＋面积】\n"
        "我们将为你提供相关案例及费用参考，\n"
        "💕让装修预算更清楚，让装修更透明！"
    )
    assert result.topics == ["费用"]
    assert load_model_calls == [
        {
            "fully_specified_name": "ark:doubao-seed-2-1-pro-260915",
            "temperature": 0.7,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
    ]
    assert len(calls) == 1
    assert bind_tool_calls == [
        (
            [generation.ContentArtifactAIEditOutput],
            {"tool_choice": "ContentArtifactAIEditOutput"},
        )
    ]
    assert deltas == [
        {"field": "title", "delta": "费用标", "value": "费用标"},
        {"field": "title", "delta": "题", "value": "费用标题"},
        {"field": "body", "delta": "费用正", "value": "费用正"},
        {"field": "body", "delta": "文", "value": "费用正文"},
        {"field": "topics", "delta": ["费用"], "value": ["费用"]},
    ]
    assert len(calls[0]) == 1
    prompt = calls[0][0].content
    assert "你是一名内容创作者" not in prompt
    assert "反差价值型" in prompt
    assert "爆款正文" in prompt
    assert '"serialNo":"002"' in prompt
    assert "使用给定元素仿写，并符合北京本地口吻" in prompt
    assert "参考爆款原文的开头切入、段落顺序、信息推进、结尾收束和口语节奏" in prompt
    assert "不得因此杜撰其他人的报价、节省金额、客户经历或施工结果" in prompt
    assert "金额、单位、面积、数量及报价明细必须准确保留" in prompt
    assert "封禁词替换表" in prompt
    assert '"报价": ["费用"]' in prompt
    assert "排版与表情要求" in prompt
    assert "不得用 Emoji 替代价格、数字、面积、时间、单位、品牌名或专业信息" in prompt
    assert "标题不超过20个字" in prompt


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_generation_omits_empty_creative_style(monkeypatch):
    calls = []

    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def astream(self, messages):
            calls.append(messages)
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "ContentArtifactAIEditOutput",
                        "args": '{"title":"标题","body":"正文","topics":[]}',
                        "id": "call-1",
                        "index": 0,
                    }
                ],
            )

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(generation, "load_chat_model", lambda **kwargs: FakeModel())

    await generation.generate_direct_content(
        model_spec=None,
        creative_style={},
        viral_source={"title": "爆款标题", "body": "爆款正文"},
        user_request="写一段内容",
    )

    prompt = calls[0][0].content
    assert "创作风格：" not in prompt
    assert "爆款正文" in prompt


def test_limit_content_title_stops_at_sentence_end():
    title = "邵阳洋湖1号165㎡装修要花多少？鸿扬家装真实费用明细来啦"
    limited = generation.limit_content_title(title)
    assert limited == "邵阳洋湖1号165㎡装修要花多少？"
    assert len(limited) <= 20
    assert generation.limit_content_title("不超过二十个字的标题") == "不超过二十个字的标题"


def test_limit_content_title_completes_trailing_word_after_hard_limit():
    title = "长沙装修要花多少？洋湖天街122㎡真实费用"
    limited = generation.limit_content_title(title)
    assert limited == title
    assert limited.endswith("真实费用")
    assert not limited.endswith("真实费")


def test_place_closing_cta_moves_lead_copy_to_the_end():
    body = "开头说明。\n\n📩在下方留下【小区＋面积】\n我们将为你提供相关案例及费用参考，\n中间还有工艺说明。\n💕让装修预算更清楚，让装修更透明！"
    placed = generation.place_closing_cta(body)
    assert placed.startswith("开头说明。\n\n中间还有工艺说明。\n\n📩在下方留下")
    assert placed.endswith("💕让装修预算更清楚，让装修更透明！")
    assert placed.count("在下方留下") == 1
    assert generation.normalize_escaped_newlines("开工\\n先聊") == "开工\n先聊"
    assert "\\n" not in generation.normalize_escaped_newlines("开工\\n先聊")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_generation_rejects_forbidden_terms_without_replacements(monkeypatch):
    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def astream(self, messages):
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "ContentArtifactAIEditOutput",
                        "args": '{"title":"APP标题","body":"正文","topics":[]}',
                        "id": "call-1",
                        "index": 0,
                    }
                ],
            )

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(generation, "load_chat_model", lambda **kwargs: FakeModel())

    with pytest.raises(ValueError, match="封禁词替换后仍有残留：APP"):
        await generation.generate_direct_content(
            model_spec=None,
            creative_style={"name": "反差价值型", "instruction": "制造反差"},
            viral_source={"title": "爆款标题", "body": "爆款正文"},
            user_request="写一段内容",
            forbidden_lexicon={"APP": []},
        )
