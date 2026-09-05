from __future__ import annotations

import pytest
from fastapi import HTTPException

from yuxi.services.mp_service import (
    REGION_TREE,
    REGIONS,
    _cover_asset_ids,
    build_mp_brief_payload,
    expand_quote_range,
    has_mp_content_code,
    lookup_frame_area_pricing,
    map_nrlx_to_ct_code,
    mask_phone,
    next_mp_content_code,
    resolve_content_goal,
)


def test_map_nrlx_to_ct_code_uses_default_mapping():
    assert map_nrlx_to_ct_code("NRLX0001", "工艺施工展示") == "CT05"
    assert map_nrlx_to_ct_code("NRLX0005", "装修案例分享") == "CT01"
    assert map_nrlx_to_ct_code("NRLX0007", "人设自荐") == "CT07"


def test_map_nrlx_to_ct_code_falls_back_to_name():
    assert map_nrlx_to_ct_code("NRLX0099", "报价清单") == "CT02"


def test_map_nrlx_to_ct_code_rejects_unknown_type():
    with pytest.raises(HTTPException) as exc:
        map_nrlx_to_ct_code("NRLX0099", "未知类型")
    assert exc.value.status_code == 422
    assert exc.value.detail["error"]["code"] == "MP_CONTENT_TYPE_UNMAPPED"


def test_resolve_content_goal_prefers_acquire_except_incompatible_types():
    assert resolve_content_goal("装修家居", "CT05") == "acquire"
    assert resolve_content_goal("装修家居", "CT04") == "educate"
    assert resolve_content_goal("好评笔记", "CT07") == "brand"


def test_expand_quote_range_uses_half_step_then_integers():
    assert expand_quote_range("15-18万") == ("15万", "15.5万", "16万", "17万", "18万")
    assert expand_quote_range("2-3万") == ("2万", "2.5万", "3万")
    assert expand_quote_range("4-5万") == ("4万", "4.5万", "5万")
    assert expand_quote_range("6-8万") == ("6万", "6.5万", "7万", "8万")
    values = expand_quote_range("20-30万")
    assert values[0] == "20万"
    assert values[1] == "20.5万"
    assert values[-1] == "30万"
    assert "21.5万" not in values


def test_expand_quote_range_opens_from_floor():
    values = expand_quote_range("30万以上")
    assert values[0] == "30万"
    assert values[1] == "30.5万"
    assert values[-1] == "40万"
    assert "31.5万" not in values


def test_lookup_frame_area_pricing_returns_quotes():
    item = lookup_frame_area_pricing("50-70㎡")
    assert item["quotes"]["基础"] == "4-5万"
    assert item["quotes"]["木制品"] == "2-3万"
    assert item["quotes"]["主材"] == "2-3万"
    large = lookup_frame_area_pricing("300㎡以上")
    assert large["quotes"]["基础"] == "30万以上"
    assert large["quotes"]["木制品"] == "11万以上"
    assert large["quotes"]["主材"] == "16万以上"


def test_lookup_frame_area_pricing_rejects_unknown():
    with pytest.raises(HTTPException) as exc:
        lookup_frame_area_pricing("10㎡")
    assert exc.value.detail["error"]["code"] == "MP_FRAME_AREA_INVALID"
    with pytest.raises(HTTPException) as missing:
        lookup_frame_area_pricing("70-90㎡")
    assert missing.value.detail["error"]["code"] == "MP_FRAME_AREA_INVALID"


def test_mask_phone_hides_middle_digits():
    assert mask_phone("13912345678") == "139****5678"


def test_has_mp_content_code_rejects_empty_drafts():
    assert has_mp_content_code({"form_values": {"mp_content_code": "NR20260825001"}}) is True
    assert has_mp_content_code({"form_values": {"mp_content_code": "  "}}) is False
    assert has_mp_content_code({"form_values": {}}) is False
    assert has_mp_content_code(None) is False


def test_next_mp_content_code_increments_same_day_sequence():
    code = next_mp_content_code(["NR20260825001", "NR20260824009", "NR20260825002"], day="20260825")
    assert code == "NR20260825003"


def test_region_tree_covers_hunan_prefectures_and_districts():
    assert len(REGION_TREE) == 14
    assert REGIONS == tuple(city for city, _ in REGION_TREE)
    districts = dict(REGION_TREE)
    assert "荷塘区" in districts["株洲市"]
    assert "云龙示范区" in districts["株洲市"]
    assert "岳麓区" in districts["长沙市"]
    assert all(len(items) >= 4 for _, items in REGION_TREE)


def test_build_mp_brief_payload_maps_decoration_fields_to_v3_variables():
    brief = build_mp_brief_payload(
        service_entry="装修家居",
        form_values={
            "楼盘信息": "星河湾",
            "外框面积": "50-70㎡",
            "基础": "4-5万",
            "木制品": "2-3万",
            "主材": "2-3万",
            "设计风格": "北欧",
            "所在区域": "长沙市 岳麓区",
        },
        content_type_name="工艺施工展示",
        cover_asset_id="cca_demo",
        cover_template_id="tpl_1",
        content_code="NR20260825001",
    )
    values = brief.form_values
    assert values["brand_name"] == "鸿扬家居"
    assert values["project_type"] == "星河湾"
    assert values["area"] == "50-70㎡"
    assert values["mp_content_code"] == "NR20260825001"
    assert values["audience"] == ["长沙市 岳麓区"]
    assert "基础 4-5万" in values["craft_and_materials"]
    assert brief.attachments[0]["asset_id"] == "cca_demo"


def test_cover_asset_ids_keep_order_and_reject_more_than_three():
    assert _cover_asset_ids("a", ["a", "b", "c"]) == ["a", "b", "c"]
    with pytest.raises(HTTPException) as exc:
        _cover_asset_ids("a", ["b", "c", "d"])
    assert exc.value.detail["error"]["code"] == "MP_COVER_LIMIT"


def test_build_mp_brief_payload_attaches_up_to_three_photos():
    brief = build_mp_brief_payload(
        service_entry="好评笔记",
        form_values={"设计师": "林工"},
        content_type_name="人设自荐",
        cover_asset_id="cover-1",
        cover_asset_ids=["cover-2", "cover-3"],
        cover_template_id=None,
        content_code="NR20260828001",
    )
    assert [item["asset_id"] for item in brief.attachments] == ["cover-1", "cover-2", "cover-3"]
    assert brief.form_values["cover_asset_ids"] == ["cover-1", "cover-2", "cover-3"]
    assert brief.audience == ["装修业主"]


def test_build_mp_brief_payload_review_notes_region_is_optional():
    with_region = build_mp_brief_payload(
        service_entry="好评笔记",
        form_values={"设计师": "林工", "所在区域": "株洲市 荷塘区"},
        content_type_name="人设自荐",
        cover_asset_id="cover-1",
        cover_template_id=None,
        content_code="NR20260828002",
    )
    without_region = build_mp_brief_payload(
        service_entry="好评笔记",
        form_values={"设计师": "林工"},
        content_type_name="人设自荐",
        cover_asset_id="cover-1",
        cover_template_id=None,
        content_code="NR20260828003",
    )
    assert with_region.audience == ["株洲市 荷塘区"]
    assert with_region.form_values["所在区域"] == "株洲市 荷塘区"
    assert without_region.audience == ["装修业主"]
    assert not str(without_region.form_values.get("所在区域") or "").strip()
