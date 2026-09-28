from types import SimpleNamespace
import json

import pytest
from fastapi import HTTPException

import yuxi.services.content_service as content_service
from yuxi.content.schemas import ContentBriefPayload
from yuxi.services.content_service import (
    _parse_content_studio_production_pack,
    _parse_content_studio_quote_case,
    compile_content_brief,
)


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
            "houseInfo": {
                "mySite": "湖南省长沙市岳麓区梅溪湖街道金茂府",
                "houseArea": "115平",
                "houseType": "三室二厅",
            },
            "prices": [
                {
                    "format": price_format,
                    "content": "拆除：1000元；水电：2400元",
                    "titlePrice": title_price or {"label": "整套人工合计", "displayText": "1.16w"},
                }
            ],
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


def test_compile_user_request_keeps_content_type_and_business_variables():
    task = SimpleNamespace(id="ct_latest", content_goal="acquire", mode="pro", content_type_code=None)
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
        business_variables={"目标人群": "刚需改善"},
        form_values={
            "user_request": "旧输入",
            "mp_service_entry": "装修家居",
            "mp_content_type_id": "NRLX0001",
            "mp_content_type_name": "工艺施工展示",
            "目标人群": "刚需改善",
        },
    )

    compiled, missing = compile_content_brief(task=task, template=template, brief=brief)

    assert missing == []
    assert compiled["user_request"] == "最新需求：只生成一篇杭州小户型收纳改造笔记"
    assert compiled["content_type_code"] == "CT05"
    assert compiled["form_values"]["user_request"] == "最新需求：只生成一篇杭州小户型收纳改造笔记"
    assert compiled["form_values"]["mp_content_type_id"] == "NRLX0001"
    assert compiled["form_values"]["mp_content_type_name"] == "工艺施工展示"
    assert compiled["business_variables"]["目标人群"] == "刚需改善"
    assert compiled["business_variables"]["mp_content_type_id"] == "NRLX0001"
    assert compiled["brand"] == {}
    assert compiled["audience"] == []


def test_compile_brief_locks_studio_content_type_without_user_request():
    task = SimpleNamespace(id="ct_studio_type", content_goal="educate", mode="pro", content_type_code=None)
    template = SimpleNamespace(slug="decoration", quick_form_schema=[], pro_form_schema=[])
    brief = ContentBriefPayload(
        content_type_code="CT06",
        form_values={"mp_service_entry": "装修家居", "mp_content_type_name": "装修知识科普"},
    )

    compiled, missing = compile_content_brief(task=task, template=template, brief=brief)

    assert missing == []
    assert compiled["content_type_code"] == "CT06"


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


def test_compile_production_pack_promotes_facts_to_factory_variables():
    task = SimpleNamespace(id="ct_pack", content_goal="acquire", mode="pro", content_type_code=None)
    template = SimpleNamespace(slug="decoration", quick_form_schema=[], pro_form_schema=[])
    request = {
        "serialNo": "H06380",
        "contentType": {
            "typeName": "工艺施工展示",
            "contentTypeId": "6c79d8ca-1774-4e79-a622-213104f1e7b8",
            "contentTypeCode": "CT05",
        },
        "persona": {"name": "朱穆", "employeeCode": "H06380"},
        "businessVariables": {"工艺名称": "HYB-吊顶与背景墙造型实现工艺", "项目阶段": "拆改阶段"},
        "facts": {
            "persona_fact": "朱穆，27岁，新媒体运营，工号H06380。",
            "process": ["个性定制系统", "HYB-吊顶与背景墙造型实现工艺"],
            "advantage": ["项目施工鸿扬家装"],
            "result": "拆改阶段按工艺规范落实HYB-吊顶与背景墙造型实现工艺",
            "audience": ["三口之家"],
        },
    }
    user_request = json.dumps(request, ensure_ascii=False)

    compiled, missing = compile_content_brief(
        task=task,
        template=template,
        brief=ContentBriefPayload(
            user_request=user_request,
            form_values={"mp_content_type_name": "工艺施工展示", "工艺名称": "HYB-吊顶与背景墙造型实现工艺"},
        ),
    )

    assert missing == []
    assert compiled["content_type_code"] == "CT05"
    assert compiled["business_variables"]["persona_fact"] == "朱穆，27岁，新媒体运营，工号H06380。"
    assert compiled["business_variables"]["process"] == ["个性定制系统", "HYB-吊顶与背景墙造型实现工艺"]
    assert compiled["business_variables"]["advantages"] == ["项目施工鸿扬家装"]
    assert compiled["persona"]["description"] == "朱穆，27岁，新媒体运营，工号H06380。"
    assert compiled["audience"] == ["三口之家"]
    assert compiled["brand"] == {"name": "鸿扬家装"}
    assert "quote_type" not in compiled["business_variables"]
    parsed = _parse_content_studio_production_pack(user_request)
    assert parsed is not None
    assert parsed["content_type_code"] == "CT05"


def test_compile_production_pack_promotes_community_to_case_background():
    request = {
        "contentType": {"typeName": "装修知识科普", "contentTypeCode": "CT06"},
        "businessVariables": {"楼盘信息": "新芙蓉之都", "所在区域": "长沙"},
        "facts": {
            "persona_fact": "张淑琪，新渠道。",
            "process": ["HYB-强弱电布管特色工艺"],
            "location": "长沙 · 新芙蓉之都",
            "audience": ["毛坯"],
        },
    }

    parsed = _parse_content_studio_production_pack(json.dumps(request, ensure_ascii=False))

    assert parsed is not None
    assert parsed["business_variables"]["case_background"] == "新芙蓉之都"
    assert parsed["business_variables"]["location"] == "长沙 · 新芙蓉之都"


def test_compile_quotation_list_pack_keeps_price_facts_without_locked_quote():
    task = SimpleNamespace(id="ct_quote_list", content_goal="acquire", mode="pro", content_type_code=None)
    template = SimpleNamespace(slug="decoration", quick_form_schema=[], pro_form_schema=[])
    request = {
        "serialNo": "H06380",
        "contentType": {
            "typeName": "装修报价清单",
            "contentTypeId": "5cbde95f-7cff-4ab3-8ba7-3d67d7326034",
            "contentTypeCode": "CT02",
        },
        "facts": {
            "persona_fact": "朱穆，27岁，管理员，工号H06380。",
            "process": ["定制化家装交付"],
            "advantage": ["项目施工鸿扬家装"],
            "product": "洋湖天旭定制化家装项目",
            "price": ["基础 12万", "木制品 6万"],
            "quote_type": "budget",
            "quantity": "137㎡",
            "audience": ["五口之家"],
        },
    }
    user_request = json.dumps(request, ensure_ascii=False)

    compiled, missing = compile_content_brief(
        task=task,
        template=template,
        brief=ContentBriefPayload(user_request=user_request),
    )

    assert missing == []
    assert compiled["content_type_code"] == "CT02"
    assert compiled["business_variables"]["price"] == ["基础 12万", "木制品 6万"]
    assert compiled["business_variables"]["quote_type"] == "budget"
    assert compiled["business_variables"]["quantity"] == "137㎡"
    assert compiled["business_variables"]["process"] == ["定制化家装交付"]
    assert "quote_block" not in compiled["business_variables"]
    assert _parse_content_studio_quote_case(user_request, content_type_code="CT02") is None


def test_compile_quotation_list_pack_defaults_budget_quote_type_from_type_name():
    task = SimpleNamespace(id="ct_quote_list_default", content_goal="acquire", mode="pro", content_type_code=None)
    template = SimpleNamespace(slug="decoration", quick_form_schema=[], pro_form_schema=[])
    request = {
        "contentType": {"typeName": "装修报价清单", "contentTypeCode": "CT02"},
        "facts": {
            "product": "洋湖天旭定制化家装项目",
            "persona_fact": "朱穆，管理员。",
        },
    }

    compiled, missing = compile_content_brief(
        task=task,
        template=template,
        brief=ContentBriefPayload(
            user_request=json.dumps(request, ensure_ascii=False),
            form_values={"基础": "12万", "木制品": "6万"},
        ),
    )

    assert missing == []
    assert compiled["business_variables"]["quote_type"] == "budget"
    assert compiled["business_variables"]["price"] == ["基础 12万", "木制品 6万"]
    assert "quote_block" not in compiled["business_variables"]


def test_compile_case_share_pack_keeps_price_facts_without_locked_quote():
    task = SimpleNamespace(id="ct_case_share", content_goal="acquire", mode="pro", content_type_code=None)
    template = SimpleNamespace(slug="decoration", quick_form_schema=[], pro_form_schema=[])
    request = {
        "serialNo": "H06380",
        "contentType": {
            "typeName": "装修案例分享",
            "contentTypeId": "2bbe1451-9fec-4cf0-9c59-8aa797900bbb",
            "contentTypeCode": "CT01",
        },
        "facts": {
            "persona_fact": "朱穆，27岁，管理员，工号H06380。",
            "process": ["定制化家装交付"],
            "advantage": ["项目施工鸿扬家装"],
            "result": "新芙蓉之都 122㎡ 复合写意 施工鸿扬家装",
            "product": "新芙蓉之都定制化家装项目",
            "location": "测试 · 新芙蓉之都",
            "scene": "复合写意",
            "audience": ["四口之家", "毛坯"],
            "quantity": "122㎡",
            "price": ["基础 9.5万", "木制品 4万", "主材 5.5万"],
            "quote_type": "budget",
            "pain": "四口之家关心新芙蓉之都122㎡复合写意怎么从方案落到完工",
        },
    }
    user_request = json.dumps(request, ensure_ascii=False)

    compiled, missing = compile_content_brief(
        task=task,
        template=template,
        brief=ContentBriefPayload(user_request=user_request),
    )

    assert missing == []
    assert compiled["content_type_code"] == "CT01"
    assert compiled["audience"] == ["四口之家", "毛坯"]
    assert compiled["business_variables"]["process"] == ["定制化家装交付"]
    assert compiled["business_variables"]["result"] == "新芙蓉之都 122㎡ 复合写意 施工鸿扬家装"
    assert compiled["business_variables"]["price"] == ["基础 9.5万", "木制品 4万", "主材 5.5万"]
    assert compiled["business_variables"]["quote_type"] == "budget"
    assert compiled["business_variables"]["quantity"] == "122㎡"
    assert compiled["business_variables"]["scene"] == "复合写意"
    assert "quote_block" not in compiled["business_variables"]
    assert _parse_content_studio_quote_case(user_request, content_type_code="CT01") is None


def test_compile_knowledge_pack_promotes_pain_and_process_without_quote():
    task = SimpleNamespace(id="ct_knowledge", content_goal="educate", mode="pro", content_type_code=None)
    template = SimpleNamespace(slug="decoration", quick_form_schema=[], pro_form_schema=[])
    request = {
        "serialNo": "H06380",
        "contentType": {
            "typeName": "装修知识科普",
            "contentTypeId": "95b91a82-ab50-4c78-b1dc-cdb469e50828",
            "contentTypeCode": "CT06",
        },
        "businessVariables": {"所在区域": "长沙", "工艺名称": "HYB-吊顶与背景墙造型实现工艺"},
        "facts": {
            "persona_fact": "朱穆，27岁，管理员，工号H06380。",
            "process": ["个性定制系统", "HYB-吊顶与背景墙造型实现工艺"],
            "advantage": ["鸿扬家装定制化家装与工艺标准说明"],
            "result": "看懂HYB-吊顶与背景墙造型实现工艺的判断标准与验收要点",
            "product": "HYB-吊顶与背景墙造型实现工艺",
            "scene": "复合写意",
            "audience": ["毛坯"],
            "pain": "毛坯不清楚HYB-吊顶与背景墙造型实现工艺该怎么判断、容易被话术带偏",
        },
    }
    user_request = json.dumps(request, ensure_ascii=False)

    compiled, missing = compile_content_brief(
        task=task,
        template=template,
        brief=ContentBriefPayload(
            user_request=user_request,
            form_values={"所在区域": "长沙"},
        ),
    )

    assert missing == []
    assert compiled["content_type_code"] == "CT06"
    assert compiled["audience"] == ["毛坯"]
    assert compiled["business_variables"]["craft_role"] == ["毛坯"]
    assert compiled["business_variables"]["process"] == ["个性定制系统", "HYB-吊顶与背景墙造型实现工艺"]
    assert compiled["business_variables"]["pain"] == "毛坯不清楚HYB-吊顶与背景墙造型实现工艺该怎么判断、容易被话术带偏"
    assert compiled["business_variables"]["pain_points"] == compiled["business_variables"]["pain"]
    assert compiled["business_variables"]["scene"] == "复合写意"
    assert compiled["business_variables"]["location"] == "长沙"
    assert "quote_type" not in compiled["business_variables"]
    assert "quote_block" not in compiled["business_variables"]
    assert _parse_content_studio_quote_case(user_request, content_type_code="CT06") is None


def test_compile_persona_pack_promotes_persona_fact_and_advantage():
    task = SimpleNamespace(id="ct_persona", content_goal="brand", mode="pro", content_type_code=None)
    template = SimpleNamespace(slug="decoration", quick_form_schema=[], pro_form_schema=[])
    request = {
        "serialNo": "H06380",
        "contentType": {
            "typeName": "人设自荐",
            "contentTypeId": "bbd42313-6031-4444-bc75-46d66836da83",
            "contentTypeCode": "CT07",
        },
        "facts": {
            "persona_fact": "朱穆，27岁，设计师，从业5年，服务长沙，工号H06380。",
            "process": ["设计师服务"],
            "advantage": ["设计师，5年", "鸿扬家装定制化家装交付"],
            "result": "长沙设计师，从业5年，可对接咨询",
            "product": "鸿扬家装设计师服务",
            "location": "长沙",
            "audience": ["毛坯"],
            "pain": "毛坯不知道该找谁、怕遇上不靠谱的设计师",
        },
    }
    user_request = json.dumps(request, ensure_ascii=False)

    compiled, missing = compile_content_brief(
        task=task,
        template=template,
        brief=ContentBriefPayload(user_request=user_request),
    )

    assert missing == []
    assert compiled["content_type_code"] == "CT07"
    assert compiled["audience"] == ["毛坯"]
    assert compiled["persona"]["description"] == "朱穆，27岁，设计师，从业5年，服务长沙，工号H06380。"
    assert compiled["business_variables"]["persona_fact"] == compiled["persona"]["description"]
    assert compiled["business_variables"]["advantage"] == ["设计师，5年", "鸿扬家装定制化家装交付"]
    assert compiled["business_variables"]["advantages"] == compiled["business_variables"]["advantage"]
    assert compiled["business_variables"]["location"] == "长沙"
    assert "quote_type" not in compiled["business_variables"]
    assert "quote_block" not in compiled["business_variables"]
    assert _parse_content_studio_quote_case(user_request, content_type_code="CT07") is None


def test_quote_case_accepts_configured_quotation_list_type_name():
    request = json.loads(_quote_request(price_format="项目单价"))
    request["requirementType"]["typeName"] = "装修报价清单"

    parsed = _parse_content_studio_quote_case(json.dumps(request, ensure_ascii=False), content_type_code="CT02")

    assert parsed is not None
    assert parsed["business_variables"]["type_name"] == "装修报价清单"
    assert parsed["trusted_snapshot"]["content_type_code"] == "CT02"


@pytest.mark.parametrize(
    "type_name",
    [
        "工艺施工展示",
        "装修避坑分享",
        "装修省钱攻略",
        "装修案例分享",
        "装修知识科普",
        "人设自荐",
    ],
)
def test_configured_non_quote_type_names_are_not_compiled_as_quote_cases(type_name):
    request = {
        "serialNo": "H06380",
        "persona": {"name": "朱穆", "employeeCode": "H06380"},
        "requirementType": {
            "typeName": type_name,
            "contentTypeId": "6c79d8ca-1774-4e79-a622-213104f1e7b8",
            "businessVariables": {"工艺名称": "HYB-吊顶与背景墙造型实现工艺"},
        },
    }

    assert _parse_content_studio_quote_case(json.dumps(request, ensure_ascii=False), content_type_code="CT05") is None


def test_quotation_list_without_quote_structure_is_not_compiled_as_quote_case():
    request = {
        "serialNo": "H06380",
        "requirementType": {
            "typeName": "装修报价清单",
            "businessVariables": {"楼盘信息": "洋湖天街"},
        },
    }

    assert _parse_content_studio_quote_case(json.dumps(request, ensure_ascii=False), content_type_code="CT02") is None


def test_standard_quote_case_rejects_task_type_mismatch():
    with pytest.raises(HTTPException) as exc_info:
        _parse_content_studio_quote_case(_quote_request(price_format="工种总价"), content_type_code="CT03")

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"]["code"] == "CONTENT_QUOTE_TYPE_MISMATCH"
    assert exc_info.value.detail["error"]["required_content_type_code"] == "CT04"


def test_standard_quote_case_requires_confirmed_title_price():
    request = json.loads(_quote_request())
    request["requirementType"]["prices"][0].pop("titlePrice")

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


@pytest.mark.parametrize(
    "payload",
    [
        {"user_request": ""},
        {"user_request": "   "},
        {"form_values": {"user_request": ""}},
        {"form_values": {"user_request": "  "}},
    ],
)
def test_empty_single_input_only_requests_visible_content_requirement(payload):
    task = SimpleNamespace(id="ct_empty", content_goal="acquire", mode="pro")
    template = SimpleNamespace(
        slug="decoration",
        quick_form_schema=[],
        pro_form_schema=[
            {"key": "brand_name", "label": "品牌", "required": True},
            {"key": "project_type", "label": "户型", "required": True},
        ],
    )
    _, missing = compile_content_brief(task=task, template=template, brief=ContentBriefPayload(**payload))
    assert missing == [{"field": "user_request", "label": "内容需求"}]


@pytest.mark.parametrize(
    ("content_type", "type_name", "tags"),
    [
        ("CT01", "自我介绍", ["自我推荐"]),
        ("CT07", "日常", ["拆除", "工地巡检"]),
        ("CT06", "自我介绍", ["工艺展示", "拆除"]),
    ],
)
def test_structured_persona_case_preserves_identity_without_inventing_pain(content_type, type_name, tags):
    request = json.dumps(
        {
            "persona": {
                "age": "30",
                "workYears": "5",
                "serviceCity": "长沙市",
                "introduction": "我从事装修行业五年了",
                "skills": ["工长", "水电", "泥瓦"],
                "serviceAdvantages": ["自有工人无转包"],
            },
            "requirementType": {"typeName": type_name, "houseInfo": {"mySite": "长沙金茂府"}},
            "tags": tags,
        },
        ensure_ascii=False,
    )
    task = SimpleNamespace(id="ct_persona", content_goal="acquire", mode="quick", content_type_code=content_type)
    compiled, missing = compile_content_brief(
        task=task, template=SimpleNamespace(slug="decoration"), brief=ContentBriefPayload(user_request=request)
    )
    values = compiled["business_variables"]
    assert missing == []
    assert "工长" in values["persona_fact"]
    assert "5年" in values["persona_fact"]
    assert values["location"] == "长沙市"
    assert values["advantages"] == ["自有工人无转包"]
    assert "pain" not in values and "result" not in values
    if content_type in {"CT06", "CT07"}:
        assert values["process"] == [tag for tag in tags if tag != "工艺展示"]
        assert values["craft_role"] == ["工长"]
        assert values["case_background"] == "长沙金茂府"
        assert "craft_count" not in values and "craft_duration" not in values
        assert ("inspection" in values) is (content_type == "CT07")
    else:
        assert "process" not in values
