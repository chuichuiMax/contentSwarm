from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.content.generation import DEFAULT_DIRECT_GENERATION_PROMPT
from yuxi.content.mp_studio_direct import (
    build_content_request_payload,
    creative_style_options,
    pick_random_creative_style,
)


def test_creative_style_options_follow_the_style_sheet():
    assert [item["label"] for item in creative_style_options("CT06", "工艺施工展示")] == [
        "项目经理掏心窝",
        "本地信任型",
        "专业干货型",
        "极简美学设计型",
    ]
    assert [item["label"] for item in creative_style_options("CT02", "装修报价清单")] == [
        "项目经理掏心窝",
        "本地信任型",
        "案例证明型",
        "痛点共鸣型",
    ]
    assert [item["label"] for item in creative_style_options("CT01", "装修案例分享")] == [
        "项目经理掏心窝",
        "本地信任型",
        "案例证明型",
        "痛点共鸣型",
        "实景案例拆解型",
    ]
    assert [item["label"] for item in creative_style_options("CT06", "装修知识科普")] == [
        "实景案例拆解型",
        "极简美学设计型",
    ]
    assert [item["label"] for item in creative_style_options("CT07", "人设自荐")] == ["理性设计师"]
    craft = creative_style_options("CT06", "工艺施工展示")[0]
    quote = creative_style_options("CT02", "装修报价清单")[0]
    assert craft["description"] == "用真诚的语气表达价格秀明、团队优势"
    assert quote["description"] == "用真诚的语气表达价格透明、团队优势"


def test_pick_random_creative_style_returns_name_and_instruction():
    style = pick_random_creative_style("CT02", "装修报价清单")
    assert style["name"] in {item["label"] for item in creative_style_options("CT02", "装修报价清单")}
    assert style["instruction"]


def test_build_content_request_payload_matches_studio_shape():
    employee = SimpleNamespace(
        name="张三",
        employee_code="E001",
        login_account="13900000000",
        gender="male",
        age=32,
        role="项目经理",
        current_branch="长沙店",
        current_department="设计部",
    )
    user = SimpleNamespace(uid="u1", username="张三", phone_number="13900000000", role="项目经理", department_id=None)
    payload = build_content_request_payload(
        employee=employee,
        user=user,
        content_type_name="工艺施工展示",
        content_type_id="ct-id",
        content_type_code="CT06",
        business_variables={
            "楼盘信息": "星河湾",
            "外框面积": "50-70㎡",
            "项目阶段": "水电阶段",
            "工艺名称": "强弱电布管",
        },
    )
    assert payload["contentType"]["contentTypeCode"] == "CT06"
    assert payload["facts"]["case_background"] == "星河湾"
    assert payload["facts"]["quantity"]
    assert "persona" in payload


@pytest.mark.asyncio
async def test_build_mp_decoration_direct_defaults(monkeypatch):
    from yuxi.content import mp_studio_direct as module

    monkeypatch.setattr(module, "pick_random_creative_style", lambda *_args, **_kwargs: {"name": "本地信任型", "instruction": "desc"})
    monkeypatch.setattr(
        module,
        "pick_random_ready_viral_asset_id",
        AsyncMock(return_value="vav_test"),
    )
    employee = SimpleNamespace(
        name="李四",
        employee_code="E002",
        login_account="13900000001",
        gender="female",
        age=28,
        role="设计师",
        current_branch="",
        current_department="",
    )
    user = SimpleNamespace(uid="u2", username="李四", phone_number="13900000001", role="设计师", department_id=None)
    result = await module.build_mp_decoration_direct_defaults(
        None,
        employee,
        user,
        content_type_name="装修报价清单",
        content_type_id="ct2",
        content_type_code="CT02",
        business_variables={"外框面积": "142㎡"},
    )
    assert result["viral_asset_id"] == "vav_test"
    assert result["generation_prompt"] == DEFAULT_DIRECT_GENERATION_PROMPT
    assert result["creative_style"]["name"] == "本地信任型"
    assert result["user_request"].startswith("{")


def test_build_mp_review_notes_direct_defaults_uses_owner_knowledge():
    from yuxi.content import mp_studio_direct as module

    employee = SimpleNamespace(
        name="李四",
        employee_code="E002",
        login_account="13900000001",
        gender="female",
        age=28,
        role="设计师",
        current_branch="",
        current_department="",
    )
    user = SimpleNamespace(uid="u2", username="李四", phone_number="13900000001", role="设计师", department_id=None)
    result = module.build_mp_review_notes_direct_defaults(
        employee,
        user,
        content_type_name="人设自荐",
        content_type_id="ct7",
        content_type_code="CT07",
        business_variables={"设计师": "张三", "项目经理": "王五"},
    )
    assert "viral_asset_id" not in result
    assert result["creative_style"] == {}
    assert "好评笔记知识库" in result["generation_prompt"]
    assert "业主角度" in result["generation_prompt"]
    assert '"设计师": "张三"' in result["user_request"] or "张三" in result["user_request"]
