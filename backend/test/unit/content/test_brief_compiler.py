from types import SimpleNamespace
import json

import pytest
from fastapi import HTTPException

import yuxi.services.content_service as content_service
from yuxi.content.schemas import ContentBriefPayload
from yuxi.services.content_service import _parse_content_studio_quote_case, compile_content_brief


def _quote_request(*, price_format: str = "单价面积", title_price: dict | None = None) -> str:
    payload = {
        "serialNo": "001",
        "persona": {
            "age": "30",
            "workYears": "5",
            "serviceCity": "长沙市",
            "introduction": "我在长沙从事装修五年，自己带队施工。",
            "skills": ["工长", "水电", "泥瓦"],
            "serviceAdvantages": ["决策快效率高", "自有工人无转包"],
        },
        "requirementType": {
            "typeName": "施工报价",
            "quotationInfo": {"houseArea": "115平", "houseType": "三室二厅"},
            "prices": [{"format": price_format, "content": "拆除：1000元；水电：2400元"}],
            "titlePrice": title_price or {"label": "整套人工合计", "displayText": "1.16w"},
            "mySite": "湖南省长沙市岳麓区梅溪湖街道金茂府",
        },
        "tags": ["营销报价", "旧房局改"],
        "images": [
            {
                "templateId": "1",
                "objectKey": "cover.jpg",
                "objectUrl": "https://example.com/cover.jpg",
            }
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


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


@pytest.mark.parametrize(
    ("price_format", "content_type_code", "quote_type"),
    [
        ("项目单价", "CT02", "standard_unit_price"),
        ("单价面积", "CT03", "standard_unit_price"),
        ("工种总价", "CT04", "project_quote"),
        ("人工辅材", "CT05", "project_quote"),
    ],
)
def test_compile_standard_quote_case_builds_sanitized_production_facts(
    price_format,
    content_type_code,
    quote_type,
):
    task = SimpleNamespace(
        id="ct_quote",
        content_goal="acquire",
        mode="pro",
        content_type_code=content_type_code,
        runtime_config_snapshot_json={},
    )
    template = SimpleNamespace(slug="decoration", quick_form_schema=[], pro_form_schema=[])
    request = _quote_request(price_format=price_format)

    compiled, missing = compile_content_brief(
        task=task,
        template=template,
        brief=ContentBriefPayload(user_request=request),
    )

    assert missing == []
    serialized = json.dumps(compiled, ensure_ascii=False)
    assert "拆除：1000元；水电：2400元" not in serialized
    assert "1.16w" not in serialized
    assert compiled["business_variables"]["product"] == "三室二厅"
    assert compiled["business_variables"]["quantity"] == "115平"
    assert compiled["business_variables"]["location"] == "长沙市"
    assert compiled["business_variables"]["scene"] == "旧房改造 大三房 三室二厅"
    assert compiled["business_variables"]["advantages"] == ["决策快效率高", "自有工人无转包"]
    assert compiled["business_variables"]["process"]
    assert compiled["form_values"] == {"user_request": compiled["user_request"]}

    parsed = _parse_content_studio_quote_case(request, content_type_code=content_type_code)
    assert parsed is not None
    assert parsed["trusted_snapshot"]["quote_type"] == quote_type
    assert parsed["trusted_snapshot"]["quote_block"]["original_content"] == "拆除：1000元；水电：2400元"


def test_standard_quote_case_rejects_task_type_mismatch():
    with pytest.raises(HTTPException) as exc_info:
        _parse_content_studio_quote_case(_quote_request(price_format="工种总价"), content_type_code="CT03")

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"]["code"] == "CONTENT_QUOTE_TYPE_MISMATCH"
    assert exc_info.value.detail["error"]["required_content_type_code"] == "CT04"


def test_standard_quote_case_requires_confirmed_title_price():
    request = json.loads(_quote_request())
    request["requirementType"].pop("titlePrice")

    with pytest.raises(HTTPException) as exc_info:
        _parse_content_studio_quote_case(json.dumps(request, ensure_ascii=False), content_type_code="CT03")

    assert exc_info.value.detail["error"]["code"] == "DANGJIA_TITLE_PRICE_REQUIRED"


@pytest.mark.asyncio
async def test_content_studio_quote_snapshot_survives_reload_and_clears_after_edit(monkeypatch):
    task = SimpleNamespace(
        id="ct_quote_draft",
        industry_template_version_id="industry-decoration-v3",
        content_goal="acquire",
        mode="pro",
        content_type_code="CT03",
        channel_profile_version_id=None,
        persona_profile_version_id=None,
        current_stage="brief",
        selected_image_item_id=None,
        selected_poster_template_id=None,
        runtime_config_snapshot_json={"schema_version": 3, "creation_mode": "viral_rewrite"},
        brief_json={},
        strategy_json={},
        to_dict=lambda: {
            "id": task.id,
            "brief": task.brief_json,
            "runtime_config_snapshot": task.runtime_config_snapshot_json,
        },
    )
    template = SimpleNamespace(slug="decoration", quick_form_schema=[], pro_form_schema=[])

    class FakeRepo:
        def __init__(self, db):
            del db

        async def get_task_for_user(self, task_id, user, for_update=False):
            del user, for_update
            return task if task_id == task.id else None

        async def get_template(self, template_id):
            return template if template_id == task.industry_template_version_id else None

        async def track(self, *args, **kwargs):
            del args, kwargs

    class FakeDB:
        async def commit(self):
            return None

    monkeypatch.setattr(content_service, "ContentRepository", FakeRepo)
    raw_request = _quote_request()
    await content_service.save_content_brief(
        FakeDB(),
        SimpleNamespace(uid="user-1"),
        task.id,
        ContentBriefPayload(user_request=raw_request),
        compile_now=False,
    )

    trusted = task.runtime_config_snapshot_json["trusted_external_material_snapshot"]
    sanitized = task.brief_json["user_request"]
    assert trusted["quote_block"]["original_content"] == "拆除：1000元；水电：2400元"
    assert raw_request not in json.dumps(task.brief_json, ensure_ascii=False)

    await content_service.save_content_brief(
        FakeDB(),
        SimpleNamespace(uid="user-1"),
        task.id,
        ContentBriefPayload(user_request=sanitized),
        compile_now=False,
    )

    assert task.runtime_config_snapshot_json["trusted_external_material_snapshot"] == trusted
    assert task.brief_json["business_variables"]["product"] == "三室二厅"

    await content_service.save_content_brief(
        FakeDB(),
        SimpleNamespace(uid="user-1"),
        task.id,
        ContentBriefPayload(user_request="改成普通装修避坑内容"),
        compile_now=False,
    )

    assert "trusted_external_material_snapshot" not in task.runtime_config_snapshot_json
    assert task.brief_json["user_request"] == "改成普通装修避坑内容"


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
