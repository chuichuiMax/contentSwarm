from types import SimpleNamespace

import pytest

from yuxi.content.schemas import ContentBriefPayload
from yuxi.services.content_service import compile_content_brief


def test_compile_brief_maps_dynamic_form_to_canonical_protocol():
    task = SimpleNamespace(id="ct_1", content_goal="acquire", mode="quick")
    template = SimpleNamespace(
        slug="education",
        quick_form_schema=[
            {"key": "brand_name", "label": "品牌", "required": True},
            {"key": "product", "label": "产品", "required": True},
        ],
        pro_form_schema=[],
    )
    brief = ContentBriefPayload(
        form_values={"brand_name": "青禾成长中心", "product": "英语启蒙课"},
        audience=["6-10岁孩子家长"],
    )

    compiled, missing = compile_content_brief(task=task, template=template, brief=brief)

    assert missing == []
    assert compiled["task_id"] == "ct_1"
    assert compiled["brand"] == {"name": "青禾成长中心"}
    assert compiled["business_variables"]["product"] == "英语启蒙课"
    assert compiled["audience"] == ["6-10岁孩子家长"]


def test_compile_brief_returns_specific_required_fields():
    task = SimpleNamespace(id="ct_2", content_goal="traffic", mode="quick")
    template = SimpleNamespace(
        slug="food",
        quick_form_schema=[{"key": "result", "label": "真实结果", "required": True}],
        pro_form_schema=[],
    )

    _, missing = compile_content_brief(task=task, template=template, brief=ContentBriefPayload())

    assert missing == [{"field": "result", "label": "真实结果"}]


def test_compile_brief_accepts_single_user_request_without_legacy_required_fields():
    task = SimpleNamespace(id="ct_simple", content_goal="traffic", mode="pro")
    template = SimpleNamespace(
        slug="decoration",
        quick_form_schema=[],
        pro_form_schema=[
            {"key": "brand_name", "label": "品牌", "required": True},
            {"key": "scenario", "label": "场景", "required": True, "variable_code": "scenario"},
        ],
    )
    brief = ContentBriefPayload(form_values={"user_request": "杭州装修公司，做爆款仿写小红书内容"})

    compiled, missing = compile_content_brief(task=task, template=template, brief=brief)

    assert missing == []
    assert compiled["form_values"]["user_request"] == "杭州装修公司，做爆款仿写小红书内容"
    assert compiled["business_variables"]["user_request"] == "杭州装修公司，做爆款仿写小红书内容"


def test_compile_single_user_request_discards_stale_legacy_form_values():
    task = SimpleNamespace(id="ct_latest", content_goal="acquire", mode="pro")
    template = SimpleNamespace(
        slug="decoration",
        quick_form_schema=[],
        pro_form_schema=[
            {"key": "brand_name", "label": "品牌", "required": True},
            {"key": "project_type", "label": "项目类型", "required": True},
        ],
    )
    brief = ContentBriefPayload(
        user_request="最新需求：只生成一篇杭州小户型收纳改造笔记",
        brand={"name": "旧品牌"},
        audience=["旧人群"],
        business_variables={"project_type": "旧项目"},
        form_values={"user_request": "旧输入", "brand_name": "旧品牌", "project_type": "旧项目"},
    )

    compiled, missing = compile_content_brief(task=task, template=template, brief=brief)

    assert missing == []
    assert compiled["user_request"] == "最新需求：只生成一篇杭州小户型收纳改造笔记"
    assert compiled["form_values"] == {"user_request": "最新需求：只生成一篇杭州小户型收纳改造笔记"}
    assert compiled["business_variables"] == {
        "user_request": "最新需求：只生成一篇杭州小户型收纳改造笔记"
    }
    assert compiled["brand"] == {}
    assert compiled["audience"] == []


@pytest.mark.parametrize("form_channel", ["", "stale-channel"])
def test_pro_brief_validates_channel_bound_to_task_instead_of_stale_form(form_channel):
    task = SimpleNamespace(
        id="ct_pro", content_goal="acquire", mode="pro", channel_profile_version_id="channel-xiaohongshu-v1"
    )
    template = SimpleNamespace(
        slug="decoration",
        pro_form_schema=[
            {"key": "brand_name", "label": "品牌", "required": True},
            {"key": "channel_profile_version_id", "label": "发布渠道", "required": True},
        ],
    )
    brief = ContentBriefPayload(form_values={"brand_name": "测试品牌", "channel_profile_version_id": form_channel})

    compiled, missing = compile_content_brief(task=task, template=template, brief=brief)

    assert missing == []
    assert compiled["channel_profile_version_id"] == "channel-xiaohongshu-v1"


def test_pro_brief_requires_real_task_channel_even_if_form_claims_one():
    task = SimpleNamespace(id="ct_pro", content_goal="acquire", mode="pro", channel_profile_version_id=None)
    template = SimpleNamespace(
        slug="decoration",
        pro_form_schema=[{"key": "channel_profile_version_id", "label": "发布渠道", "required": True}],
    )
    brief = ContentBriefPayload(form_values={"channel_profile_version_id": "channel-xiaohongshu-v1"})

    _, missing = compile_content_brief(task=task, template=template, brief=brief)

    assert missing == [{"field": "channel_profile_version_id", "label": "发布渠道"}]


@pytest.mark.parametrize("payload", [
    {"user_request": ""}, {"user_request": "   "},
    {"form_values": {"user_request": ""}}, {"form_values": {"user_request": "  "}},
])
def test_empty_single_input_only_requests_visible_content_requirement(payload):
    task = SimpleNamespace(id="ct_empty", content_goal="acquire", mode="pro")
    template = SimpleNamespace(slug="decoration", quick_form_schema=[], pro_form_schema=[
        {"key": "brand_name", "label": "品牌", "required": True},
        {"key": "project_type", "label": "户型", "required": True},
    ])
    _, missing = compile_content_brief(task=task, template=template, brief=ContentBriefPayload(**payload))
    assert missing == [{"field": "user_request", "label": "内容需求"}]
