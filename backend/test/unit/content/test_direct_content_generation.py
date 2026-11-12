import pytest
from langchain_core.messages import AIMessage

import yuxi.content.generation as generation


def _tool_response(arguments: dict) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "ContentArtifactAIEditOutput",
                "args": arguments,
                "id": "call-1",
            }
        ],
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_generation_uses_only_requested_inputs(monkeypatch):
    calls = []
    bind_tool_calls = []
    load_model_calls = []

    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            bind_tool_calls.append((schemas, kwargs))
            return self

        async def ainvoke(self, messages):
            calls.append(messages)
            return _tool_response({"title": "报价标题", "body": "报价正文", "topics": ["报价"]})

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(
        generation,
        "choose_sampling",
        lambda: {"temperature": 0.7, "top_p": 1},
    )

    def fake_load_chat_model(**kwargs):
        load_model_calls.append(kwargs)
        return FakeModel()

    monkeypatch.setattr(generation, "load_chat_model", fake_load_chat_model)

    result = await generation.generate_direct_content(
        model_spec="ark:doubao-seed-2-1-pro-260915",
        creative_style={"name": "反差价值型", "instruction": "用别人报 xx 万、我们 xx 万制造反差"},
        viral_source={"title": "爆款标题", "body": "爆款正文"},
        user_request='{"serialNo":"002","persona":{"age":"45"}}',
        generation_prompt="使用给定元素仿写，并符合北京本地口吻",
        forbidden_lexicon={"报价": ["费用"]},
    )

    assert result.title == "费用标题"
    assert result.body == "费用正文"
    assert result.topics == ["装修避坑", "费用"]
    assert load_model_calls == [
        {
            "fully_specified_name": "ark:doubao-seed-2-1-pro-260915",
            "temperature": 0.7,
            "top_p": 1,
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
    assert len(calls[0]) == 1
    prompt = calls[0][0].content
    assert "用户生成提示词" in prompt
    assert "使用给定元素仿写，并符合北京本地口吻" in prompt
    assert prompt.index("用户生成提示词") < prompt.index("业务数据只用来核对事实")
    assert '"serialNo":"002"' in prompt
    assert "反差价值型" not in prompt
    assert "爆款正文" not in prompt
    assert "话题必须包含装修避坑" in prompt
    assert "装修公司推荐" in prompt
    assert "封禁词替换表" in prompt
    assert '"报价": ["费用"]' in prompt
    assert prompt.index("封禁词替换表") < prompt.index("自我查验")
    assert "不要写出查验过程" in prompt


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_generation_pins_region_company_topic(monkeypatch):
    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def ainvoke(self, messages):
            return _tool_response({"title": "标题", "body": "正文", "topics": ["#装修避坑", "装修日记"]})

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(generation, "load_chat_model", lambda **kwargs: FakeModel())

    result = await generation.generate_direct_content(
        model_spec=None,
        creative_style={},
        viral_source={"title": "爆款标题", "body": "爆款正文"},
        user_request='{"businessVariables":{"所在区域":"株洲市"}}',
    )

    assert result.topics[0] == "株洲装修公司推荐"
    assert result.topics[1] == "装修避坑"
    assert "装修日记" in result.topics
    assert all(not topic.startswith("#") for topic in result.topics)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_generation_omits_empty_creative_style(monkeypatch):
    calls = []

    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def ainvoke(self, messages):
            calls.append(messages)
            return _tool_response({"title": "标题", "body": "正文", "topics": []})

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
    assert "爆款正文" not in prompt


@pytest.mark.unit
@pytest.mark.asyncio
async def test_review_notes_generation_uses_owner_voice_and_knowledge(monkeypatch):
    calls = []

    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def ainvoke(self, messages):
            calls.append(messages)
            return _tool_response({"title": "看工地才放心", "body": "我去工地看了水电。", "topics": ["装修日记"]})

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(generation, "load_chat_model", lambda **kwargs: FakeModel())

    await generation.generate_direct_content(
        model_spec=None,
        creative_style={},
        viral_source={"title": "", "body": ""},
        user_request='{"项目经理":"王五"}',
        generation_prompt="站在业主角度写好评笔记",
        knowledge_context="业主去未完工的工地看隐蔽工程",
    )

    prompt = calls[0][0].content
    assert "用户生成提示词" in prompt
    assert "站在业主角度写好评笔记" in prompt
    assert "用好评笔记知识库里的业主口吻" in prompt
    assert "话题写 8 到 10 个" in prompt
    assert "业主去未完工的工地看隐蔽工程" in prompt
    assert '"项目经理":"王五"' in prompt
    assert "爆款原文" not in prompt


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


def test_normalize_escaped_newlines_keeps_real_breaks():
    assert generation.normalize_escaped_newlines("开工\\n先聊") == "开工\n先聊"
    assert "\\n" not in generation.normalize_escaped_newlines("开工\\n先聊")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_generation_rejects_forbidden_terms_without_replacements(monkeypatch):
    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def ainvoke(self, messages):
            return _tool_response({"title": "APP标题", "body": "正文", "topics": []})

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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_generation_sends_prompt_with_first_image(monkeypatch):
    from io import BytesIO

    from PIL import Image

    calls = []

    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def ainvoke(self, messages):
            calls.append(messages)
            return _tool_response({"title": "标题", "body": "正文", "topics": []})

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(generation, "load_chat_model", lambda **kwargs: FakeModel())
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")

    await generation.generate_direct_content(
        model_spec=None,
        creative_style={"name": "信息流", "instruction": "强调公司工艺"},
        viral_source={"title": "爆款标题", "body": "爆款正文"},
        user_request="写一段内容",
        generation_prompt="根据我提供的图片写文案",
        reference_image=(buffer.getvalue(), "image/png"),
    )

    content = calls[0][0].content
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "根据我提供的图片写文案" in content[0]["text"]
    assert "已附上第一张图片" in content[0]["text"]
    assert "根据这张图片生成 3 到 5 个小红书类型标签" in content[0]["text"]
    assert "空间加画面里的具体细节" in content[0]["text"]
    assert "创作风格里的示例词" in content[0]["text"]
    assert "所有标签合计不超过 10 个" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_text_only_model_retries_without_the_image(monkeypatch):
    from io import BytesIO

    from PIL import Image

    calls = []

    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def ainvoke(self, messages):
            calls.append(messages)
            if len(calls) == 1:
                raise RuntimeError(
                    "Error code: 400 - {'code': 20041, 'message': "
                    "'The model is not a VLM (Vision Language Model). Please use text-only prompts.', 'data': None}"
                )
            return _tool_response({"title": "标题", "body": "正文", "topics": []})

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(generation, "load_chat_model", lambda **kwargs: FakeModel())
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    usage = {}

    await generation.generate_direct_content(
        model_spec="siliconflow-cn:Qwen/Qwen2.5-7B-Instruct",
        creative_style={},
        viral_source={"title": "", "body": ""},
        user_request="写一段内容",
        generation_prompt="根据提示词写",
        reference_image=(buffer.getvalue(), "image/png"),
        usage_out=usage,
    )

    assert isinstance(calls[0][0].content, list)
    assert isinstance(calls[1][0].content, str)
    assert "已附上第一张图片" not in calls[1][0].content
    assert "小红书类型标签" not in calls[1][0].content
    assert usage["image_attached"] is False
    assert usage["elapsed_seconds"] >= 0
    assert "不能看图" in usage["image_omitted_reason"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deepseek_flash_disables_thinking_before_tool_choice(monkeypatch):
    load_calls = []

    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def ainvoke(self, messages):
            return _tool_response({"title": "标题", "body": "正文", "topics": []})

    def fake_load_chat_model(**kwargs):
        load_calls.append(kwargs)
        return FakeModel()

    monkeypatch.setattr(generation, "load_chat_model", fake_load_chat_model)

    await generation.generate_direct_content(
        model_spec="deepseek:deepseek-flash",
        creative_style={},
        viral_source={"title": "", "body": ""},
        user_request="写一段内容",
    )

    assert load_calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_siliconflow_deepseek_disables_thinking(monkeypatch):
    load_calls = []

    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def ainvoke(self, messages):
            return _tool_response({"title": "标题", "body": "正文", "topics": []})

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(
        generation,
        "load_chat_model",
        lambda **kwargs: load_calls.append(kwargs) or FakeModel(),
    )

    await generation.generate_direct_content(
        model_spec="siliconflow-cn:deepseek-ai/DeepSeek-V4.1-Flash",
        creative_style={},
        viral_source={"title": "", "body": ""},
        user_request="写一段内容",
    )

    assert load_calls[0]["extra_body"] == {"enable_thinking": False}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_each_generation_uses_its_own_sampling(monkeypatch):
    sampling = {"temperature": 1.02, "top_p": 0.86}
    usage: dict = {}
    load_calls = []

    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def ainvoke(self, messages):
            self.messages = messages
            return _tool_response({"title": "标题", "body": "正文", "topics": []})

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(
        generation,
        "load_chat_model",
        lambda **kwargs: load_calls.append(kwargs) or FakeModel(),
    )
    monkeypatch.setattr(generation, "choose_sampling", lambda: sampling)

    await generation.generate_direct_content(
        model_spec="deepseek:deepseek-flash",
        creative_style={},
        viral_source={"title": "", "body": ""},
        user_request="写一段内容",
        usage_out=usage,
    )

    assert load_calls[0]["temperature"] == 1.02
    assert load_calls[0]["top_p"] == 0.86
    assert "seed" not in load_calls[0]
    assert usage["temperature"] == 1.02
    assert usage["top_p"] == 0.86


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_model_response_is_reported_clearly(monkeypatch):
    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def ainvoke(self, messages):
            raise AttributeError("'NoneType' object has no attribute 'model_dump'")

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(generation, "load_chat_model", lambda **kwargs: FakeModel())

    with pytest.raises(ValueError, match="模型没有返回内容"):
        await generation.generate_direct_content(
            model_spec="deepseek:deepseek-flash",
            creative_style={},
            viral_source={"title": "", "body": ""},
            user_request="写一段内容",
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unbroken_body_is_split_into_paragraphs(monkeypatch):
    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def ainvoke(self, messages):
            return _tool_response(
                {
                    "title": "客厅标题",
                    "body": "长沙润樾府，145㎡，雅致现代风。客厅这块，住进去才知道有多顺。柜子别贪多，留白才耐看。",
                    "topics": ["装修避坑"],
                }
            )

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(generation, "load_chat_model", lambda **kwargs: FakeModel())

    result = await generation.generate_direct_content(
        model_spec="siliconflow-cn:deepseek-ai/DeepSeek-V4.1-Flash",
        creative_style={},
        viral_source={"title": "", "body": ""},
        user_request="写一段内容",
    )

    assert result.body == "长沙润樾府，145㎡，雅致现代风。\n\n客厅这块，住进去才知道有多顺。\n\n柜子别贪多，留白才耐看。"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_body_with_line_breaks_stays_unchanged(monkeypatch):
    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def ainvoke(self, messages):
            return _tool_response({"title": "标题", "body": "第一段。\n\n第二段。", "topics": []})

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(generation, "load_chat_model", lambda **kwargs: FakeModel())

    result = await generation.generate_direct_content(
        model_spec="deepseek:deepseek-flash",
        creative_style={},
        viral_source={"title": "", "body": ""},
        user_request="写一段内容",
    )

    assert result.body == "第一段。\n\n第二段。"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_placeholder_body_is_requested_again(monkeypatch):
    calls = 0

    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def ainvoke(self, messages):
            nonlocal calls
            calls += 1
            body = "..." if calls == 1 else "卫生间这处工艺要盯紧"
            return _tool_response({"title": "水电标题", "body": body, "topics": ["装修避坑"]})

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(generation, "load_chat_model", lambda **kwargs: FakeModel())

    result = await generation.generate_direct_content(
        model_spec="siliconflow-cn:deepseek-ai/DeepSeek-V4.1-Flash",
        creative_style={},
        viral_source={"title": "", "body": ""},
        user_request="写一段内容",
    )

    assert calls == 2
    assert result.body == "卫生间这处工艺要盯紧"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_placeholder_body_fails_when_retry_is_still_empty(monkeypatch):
    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def ainvoke(self, messages):
            return _tool_response({"title": "水电标题", "body": "...", "topics": []})

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(generation, "load_chat_model", lambda **kwargs: FakeModel())

    with pytest.raises(ValueError, match="没有返回可用正文"):
        await generation.generate_direct_content(
            model_spec="siliconflow-cn:deepseek-ai/DeepSeek-V4.1-Flash",
            creative_style={},
            viral_source={"title": "", "body": ""},
            user_request="写一段内容",
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_thinking_mode_tool_choice_retries_with_auto(monkeypatch):
    choices = []

    class FakeModel:
        def __init__(self):
            self.choice = None

        def bind_tools(self, schemas, **kwargs):
            self.choice = kwargs.get("tool_choice")
            return self

        async def ainvoke(self, messages):
            choices.append(self.choice)
            if self.choice != "auto":
                raise RuntimeError(
                    "Error code: 400 - {'error': {'message': "
                    "'Thinking mode does not support this tool_choice', "
                    "'type': 'invalid_request_error', 'code': 'invalid_request_error'}}"
                )
            return _tool_response({"title": "标题", "body": "正文", "topics": ["装修避坑"]})

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(generation, "load_chat_model", lambda **kwargs: FakeModel())

    result = await generation.generate_direct_content(
        model_spec="deepseek:deepseek-flash",
        creative_style={},
        viral_source={"title": "", "body": ""},
        user_request="写一段内容",
    )

    assert choices == ["ContentArtifactAIEditOutput", "auto"]
    assert result.title == "标题"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_named_tool_choice_rejection_retries_with_auto(monkeypatch):
    choices = []

    class FakeModel:
        def __init__(self):
            self.choice = None

        def bind_tools(self, schemas, **kwargs):
            self.choice = kwargs.get("tool_choice")
            return self

        async def ainvoke(self, messages):
            choices.append(self.choice)
            if self.choice != "auto":
                raise RuntimeError(
                    "Error code: 400 - {'code': 20015, 'message': "
                    "'Value error, Specifying functions for tool_choice is not yet supported.', 'data': None}"
                )
            return _tool_response({"title": "标题", "body": "正文", "topics": ["装修避坑"]})

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(generation, "load_chat_model", lambda **kwargs: FakeModel())

    result = await generation.generate_direct_content(
        model_spec="siliconflow-cn:Qwen/Qwen3-VL-8B-Instruct",
        creative_style={},
        viral_source={"title": "", "body": ""},
        user_request="写一段内容",
        generation_prompt="根据图片写",
    )

    assert choices == ["ContentArtifactAIEditOutput", "auto"]
    assert result.title == "标题"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_plain_text_response_is_used_as_copy(monkeypatch):
    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def ainvoke(self, messages):
            return AIMessage(content="阳台收纳做好了\n\n靠墙的柜子把洗衣机藏起来，台面还能放绿植。")

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(generation, "load_chat_model", lambda **kwargs: FakeModel())

    result = await generation.generate_direct_content(
        model_spec="siliconflow-cn:Qwen/Qwen3-VL-8B-Instruct",
        creative_style={},
        viral_source={"title": "", "body": ""},
        user_request="{}",
        generation_prompt="根据图片写阳台收纳",
    )

    assert result.title == "阳台收纳做好了"
    assert "洗衣机" in result.body
    assert result.topics == ["装修避坑"]


def test_publish_tags_are_removed_from_the_body():
    body, topics = generation.detach_publish_tags(
        "今天去看了水电。\n\n#装修避坑 #水电改造\n发布标签：厨房收纳 工地实拍",
        ["装修日记"],
    )
    assert body == "今天去看了水电。"
    assert topics == ["装修日记", "装修避坑", "水电改造", "厨房收纳", "工地实拍"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_model_body_tags_land_in_publish_topics(monkeypatch):
    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def ainvoke(self, messages):
            return _tool_response(
                {
                    "title": "标题",
                    "body": "我去工地看了水电。\n#水电改造 #工地实拍",
                    "topics": [],
                }
            )

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(generation, "load_chat_model", lambda **kwargs: FakeModel())

    result = await generation.generate_direct_content(
        model_spec=None,
        creative_style={},
        viral_source={"title": "", "body": ""},
        user_request="写一段内容",
    )

    assert result.body == "我去工地看了水电。"
    assert "水电改造" in result.topics
    assert "工地实拍" in result.topics
    assert result.topics[0] == "装修避坑"


def test_topics_are_filled_from_generic_tags_up_to_ten():
    topics = generation.append_topics_within_limit(
        ["株洲装修公司推荐", "装修避坑"],
        ["装修日记", "#装修避坑", "水电改造", "泥瓦", "木工", "油漆", "收纳", "软装", "旧改", "厨房"],
    )
    assert topics[0] == "株洲装修公司推荐"
    assert topics[1] == "装修避坑"
    assert "装修日记" in topics
    assert len(topics) == 10
    assert "厨房" not in topics


def test_recent_image_topics_skip_pinned_tags():
    topics = generation.avoid_recent_image_topics(
        ["长沙装修公司推荐", "装修避坑", "无主灯", "#客餐厅线性灯", "无主灯", "电视墙石材"]
    )
    assert topics == ["无主灯", "客餐厅线性灯", "电视墙石材"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recent_topics_are_sent_with_the_image(monkeypatch):
    calls = []

    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def ainvoke(self, messages):
            calls.append(messages)
            return _tool_response({"title": "标题", "body": "正文", "topics": []})

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(generation, "load_chat_model", lambda **kwargs: FakeModel())

    await generation.generate_direct_content(
        model_spec="siliconflow-cn:Qwen/Qwen3-VL-8B-Instruct",
        creative_style={},
        viral_source={"title": "", "body": ""},
        user_request="写一段内容",
        generation_prompt="根据图片写",
        reference_image=(b"image-bytes", "image/jpeg"),
        recent_topics=["装修避坑", "无主灯", "电视墙石材"],
    )

    text = calls[0][0].content[0]["text"]
    assert "最近用过这些标签：无主灯、电视墙石材" in text
    assert "装修避坑" not in text.split("最近用过这些标签：", 1)[1]


def test_other_knowledge_bases_are_used_when_content_type_is_missing():
    from yuxi.services.content_direct_worker import other_copy_knowledge_names

    names = other_copy_knowledge_names(
        "装修案例分享",
        [
            {"name": "装修案例分享", "viral_content_type": "CT06"},
            {"name": "封禁词库"},
            {"name": "创作提示词"},
            {"name": "目标人群-通用-标签"},
            {"name": "好评笔记知识库"},
            {"name": "工艺施工展示知识库", "viral_content_type": "CT01"},
            {"name": "通用工艺说明"},
            {"name": "装修报价知识库", "viral_content_type": ""},
        ],
    )
    assert names == ["通用工艺说明", "装修报价知识库"]


def test_reference_knowledge_is_written_into_the_prompt():
    prompt = generation._direct_generation_prompt(
        style_block="",
        viral_source={"title": "", "body": ""},
        user_request="{}",
        generation_prompt="按提示词写",
        forbidden_lexicon={},
        knowledge_context="",
        reference_knowledge="现代风格常用浅色柜体。",
    )
    assert "已命中的知识库用来写文案" in prompt
    assert "现代风格常用浅色柜体。" in prompt
    assert prompt.index("用户生成提示词") < prompt.index("已命中的知识库用来写文案")


def test_prepare_reference_image_keeps_a_readable_jpeg():
    from io import BytesIO

    from PIL import Image

    image = Image.new("RGB", (2400, 1800), (180, 90, 40))
    raw = BytesIO()
    image.save(raw, format="PNG")
    mime, encoded = generation.prepare_reference_image(raw.getvalue(), "image/png")
    assert mime == "image/jpeg"
    import base64

    data = base64.b64decode(encoded)
    assert len(data) <= 480_000
    with Image.open(BytesIO(data)) as compressed:
        assert compressed.format == "JPEG"
        assert max(compressed.size) <= 1600


def test_response_token_usage_reads_model_counts():
    response = _tool_response({"title": "标题", "body": "正文", "topics": []})
    response.usage_metadata = {"input_tokens": 120, "output_tokens": 40, "total_tokens": 160}
    assert generation.response_token_usage(response) == {
        "input_tokens": 120,
        "output_tokens": 40,
        "total_tokens": 160,
    }
    response.usage_metadata = None
    response.response_metadata = {"token_usage": {"prompt_tokens": 8, "completion_tokens": 3}}
    assert generation.response_token_usage(response)["total_tokens"] == 11


def test_title_rules_drop_area_next_to_a_room():
    from yuxi.content.title_rules import apply_title_rules

    assert apply_title_rules("120㎡客厅终于装完") == "120㎡终于装完"
    assert apply_title_rules("客厅89平米太小了") == "客厅太小了"
    assert apply_title_rules("98㎡的厨房收纳") == "98㎡收纳"
    assert apply_title_rules("120㎡全屋定制") == "120㎡全屋定制"
    assert apply_title_rules("客餐厅无主灯") == "客餐厅无主灯"


def test_incomplete_body_reaction_titles_are_detected():
    from yuxi.content.title_rules import title_is_incomplete

    assert title_is_incomplete("业主说想一抬头就软下来")
    assert not title_is_incomplete("业主说一抬头脖子就软下来")


def test_image_prompt_distinguishes_sofa_wall_from_tv_wall():
    prompt = generation._direct_generation_prompt(
        style_block="",
        viral_source={"title": "", "body": ""},
        user_request="{}",
        generation_prompt="根据图片写",
        forbidden_lexicon={},
        knowledge_context="",
        image_attached=True,
    )
    assert "看不到电视机，写沙发背景墙" in prompt
    assert "只有看得到电视屏幕时，才写电视背景墙" in prompt


def test_bound_content_type_knowledge_uses_the_selected_type():
    from yuxi.services.content_direct_worker import bound_content_type_knowledge_names

    names = bound_content_type_knowledge_names(
        "CT06",
        "工艺施工展示",
        [
            {"name": "工艺施工展示", "viral_content_type": "CT06"},
            {"name": "工地工艺库", "viral_content_type": "CT06"},
            {"name": "开工记录", "viral_content_type": "开工"},
            {"name": "封禁词库", "viral_content_type": "CT06"},
            {"name": "目标人群-通用-标签", "viral_content_type": "CT06"},
            {"name": "未绑定资料", "viral_content_type": ""},
        ],
    )
    assert names == ["工地工艺库"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blank_prompt_uses_content_type_default_and_title_rule(monkeypatch):
    calls = []

    class FakeModel:
        def bind_tools(self, schemas, **kwargs):
            return self

        async def ainvoke(self, messages):
            calls.append(messages)
            return _tool_response({"title": "120㎡客厅终于装完", "body": "客厅做了线性灯。", "topics": []})

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda value: value or "default")
    monkeypatch.setattr(generation, "choose_sampling", lambda: {"temperature": 0.9, "top_p": 0.9})
    monkeypatch.setattr(generation, "load_chat_model", lambda **kwargs: FakeModel())

    result = await generation.generate_direct_content(
        model_spec="deepseek:deepseek-flash",
        creative_style={},
        viral_source={},
        user_request='{"contentType":{"typeName":"开工","contentTypeCode":""}}',
        generation_prompt=None,
    )

    prompt = calls[0][0].content
    assert "内容类型是「开工」" in prompt
    assert "标题限制：" in prompt
    assert result.title == "120㎡终于装完"
