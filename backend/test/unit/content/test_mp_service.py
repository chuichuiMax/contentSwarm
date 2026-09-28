from __future__ import annotations

import pytest
from fastapi import HTTPException

from yuxi.services.mp_service import (
    REGION_TREE,
    REGIONS,
    _compact_run,
    _content_approval_auto_resume_payload,
    _cover_asset_ids,
    _cover_auto_resume_payload,
    _lock_decoration_visual_material,
    _mp_gallery_item,
    _mp_hycanvas_template_item,
    _visible_mp_run,
    build_mp_brief_payload,
    expand_quote_range,
    has_mp_content_code,
    list_mp_galleries,
    list_mp_gallery_items,
    lookup_frame_area_pricing,
    map_nrlx_to_ct_code,
    mask_phone,
    next_mp_content_code,
    read_hycanvas_template_overlay,
    read_hycanvas_template_preview,
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
    assert resolve_content_goal("好评笔记", "CT05") == "brand"


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


def test_lookup_frame_area_pricing_matches_manual_area():
    assert lookup_frame_area_pricing("120")["value"] == "110-130㎡"
    assert lookup_frame_area_pricing("120㎡")["value"] == "110-130㎡"
    assert lookup_frame_area_pricing("110")["value"] == "110-130㎡"
    assert lookup_frame_area_pricing("70")["value"] == "50-70㎡"
    assert lookup_frame_area_pricing("300")["value"] == "300㎡以上"
    assert lookup_frame_area_pricing("360平")["value"] == "300㎡以上"


def test_lookup_frame_area_pricing_rejects_unknown():
    with pytest.raises(HTTPException) as exc:
        lookup_frame_area_pricing("10㎡")
    assert exc.value.detail["error"]["code"] == "MP_FRAME_AREA_INVALID"
    with pytest.raises(HTTPException) as missing:
        lookup_frame_area_pricing("80")
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
            "设计风格": "北欧之光",
            "所在区域": "长沙市 岳麓区",
        },
        content_type_name="工艺施工展示",
        content_type_id="ct-process",
        cover_asset_id="cca_demo",
        cover_template_id="tpl_1",
        content_code="NR20260825001",
    )
    values = brief.form_values
    assert values["brand_name"] == "鸿扬家装"
    assert values["project_type"] == "星河湾"
    assert values["mp_content_type_id"] == "ct-process"
    assert values.get("voice") != "业主第一人称"
    assert "好评知识库" not in str(values.get("writing_instruction") or "")
    locked = int(str(values["area"]).removesuffix("㎡"))
    assert 50 <= locked <= 70
    assert values["house_area"] == values["area"]
    assert values["外框面积"] == values["area"]
    assert values["location"] == "长沙市 岳麓区"
    assert values["renovation_scene"] == "北欧之光"
    assert values["scene"] == "北欧之光"
    assert values["mp_content_code"] == "NR20260825001"
    assert values["audience"] == ["长沙市 岳麓区"]
    assert "基础 4-5万" in values["craft_and_materials"]
    assert brief.attachments[0]["asset_id"] == "cca_demo"
    assert brief.visual_material is None


def test_build_mp_brief_payload_locks_hycanvas_visual_material():
    from yuxi.content.schemas import ContentVisualMaterialSelection

    brief = build_mp_brief_payload(
        service_entry="装修家居",
        form_values={
            "楼盘信息": "星河湾",
            "外框面积": "50-70㎡",
            "基础": "4-5万",
            "木制品": "2-3万",
            "主材": "2-3万",
            "设计风格": "北欧之光",
            "所在区域": "长沙市 岳麓区",
        },
        content_type_name="工艺施工展示",
        content_type_id="ct-process",
        cover_asset_id="cca_demo",
        cover_template_id=None,
        content_code="NR20260902001",
        visual_material=ContentVisualMaterialSelection(
            image_item_id="mli_demo",
            hycanvas_template_id="xiaohongshu-home-renovation",
        ),
    )
    assert brief.visual_material is not None
    assert brief.visual_material.image_item_id == "mli_demo"
    assert brief.visual_material.hycanvas_template_id == "xiaohongshu-home-renovation"
    assert brief.form_values["hycanvas_template_id"] == "xiaohongshu-home-renovation"


@pytest.mark.asyncio
async def test_lock_decoration_visual_material_requires_hycanvas_template():
    with pytest.raises(HTTPException) as exc:
        await _lock_decoration_visual_material(
            None,
            type("User", (), {"uid": "u1"})(),
            cover_asset_id="cca_1",
            hycanvas_template_id=None,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["error"]["code"] == "MP_HYCANVAS_TEMPLATE_REQUIRED"


@pytest.mark.asyncio
async def test_lock_decoration_visual_material_requires_image_or_upload():
    with pytest.raises(HTTPException) as exc:
        await _lock_decoration_visual_material(
            None,
            type("User", (), {"uid": "u1"})(),
            hycanvas_template_id="xiaohongshu-home-renovation",
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["error"]["code"] == "MP_COVER_REQUIRED"


@pytest.mark.asyncio
async def test_lock_decoration_visual_material_accepts_gallery_item(monkeypatch):
    class Item:
        id = "mli_1"
        asset_id = "cca_1"
        owner_uid = "u1"
        material_type = "image"
        status = "enabled"

    class Repo:
        def __init__(self, db, *, include_shared=False):
            self.include_shared = include_shared

        async def get_item_for_user(self, item_id, owner_uid, for_update=False):
            assert item_id == "mli_1"
            assert owner_uid == "u1"
            return Item()

        async def item_is_selected_by_task(self, item_id, owner_uid, exclude_task_id=None):
            return False

    monkeypatch.setattr("yuxi.services.mp_service.MaterialLibraryRepository", Repo)
    selection, asset_id = await _lock_decoration_visual_material(
        None,
        type("User", (), {"uid": "u1"})(),
        image_item_id="mli_1",
        hycanvas_template_id="xiaohongshu-home-renovation",
    )
    assert selection.image_item_id == "mli_1"
    assert selection.hycanvas_template_id == "xiaohongshu-home-renovation"
    assert asset_id == "cca_1"


@pytest.mark.asyncio
async def test_lock_decoration_visual_material_accepts_workspace_template_id(monkeypatch):
    class Item:
        id = "mli_1"
        asset_id = "cca_1"
        owner_uid = "u1"
        material_type = "image"
        status = "enabled"

    class Repo:
        def __init__(self, db, *, include_shared=False):
            self.include_shared = include_shared

        async def get_item_for_user(self, item_id, owner_uid, for_update=False):
            return Item()

        async def item_is_selected_by_task(self, item_id, owner_uid, exclude_task_id=None):
            return False

    monkeypatch.setattr("yuxi.services.mp_service.MaterialLibraryRepository", Repo)
    selection, asset_id = await _lock_decoration_visual_material(
        None,
        type("User", (), {"uid": "u1"})(),
        image_item_id="mli_1",
        hycanvas_template_id="8bc32ed9-f80a-47e2-8e2b-913e35c125c8",
    )
    assert selection.hycanvas_template_id == "8bc32ed9-f80a-47e2-8e2b-913e35c125c8"
    assert asset_id == "cca_1"


@pytest.mark.asyncio
async def test_lock_decoration_visual_material_accepts_enterprise_shared_image(monkeypatch):
    class Item:
        id = "mli_shared"
        asset_id = "cca_shared"
        owner_uid = "admin"
        material_type = "image"
        status = "enabled"

    class Repo:
        def __init__(self, db, *, include_shared=False):
            assert include_shared is True

        async def get_item_for_user(self, item_id, owner_uid, for_update=False):
            assert item_id == "mli_shared"
            assert owner_uid == "u1"
            return Item()

        async def item_is_selected_by_task(self, item_id, owner_uid, exclude_task_id=None):
            return False

    monkeypatch.setattr("yuxi.services.mp_service.MaterialLibraryRepository", Repo)
    selection, asset_id = await _lock_decoration_visual_material(
        None,
        type("User", (), {"uid": "u1"})(),
        image_item_id="mli_shared",
        hycanvas_template_id="xiaohongshu-home-renovation",
    )
    assert selection.image_item_id == "mli_shared"
    assert asset_id == "cca_shared"


@pytest.mark.asyncio
async def test_lock_decoration_visual_material_accepts_shared_cover_asset(monkeypatch):
    class Item:
        id = "mli_shared"
        asset_id = "cca_shared"
        owner_uid = "admin"
        material_type = "image"
        status = "enabled"

    class Repo:
        def __init__(self, db, *, include_shared=False):
            assert include_shared is True

        async def get_item_by_asset(self, asset_id):
            assert asset_id == "cca_shared"
            return Item()

        async def get_item_for_user(self, item_id, owner_uid, for_update=False):
            assert item_id == "mli_shared"
            return Item()

        async def item_is_selected_by_task(self, item_id, owner_uid, exclude_task_id=None):
            return False

    monkeypatch.setattr("yuxi.services.mp_service.MaterialLibraryRepository", Repo)
    selection, asset_id = await _lock_decoration_visual_material(
        None,
        type("User", (), {"uid": "u1"})(),
        cover_asset_id="cca_shared",
        hycanvas_template_id="xiaohongshu-home-renovation",
    )
    assert selection.image_item_id == "mli_shared"
    assert asset_id == "cca_shared"


@pytest.mark.asyncio
async def test_lock_decoration_visual_material_rejects_private_cover_of_others(monkeypatch):
    class Item:
        id = "mli_private"
        asset_id = "cca_private"
        owner_uid = "other"
        material_type = "image"
        status = "enabled"

    class Repo:
        def __init__(self, db, *, include_shared=False):
            pass

        async def get_item_by_asset(self, asset_id):
            return Item()

        async def get_item_for_user(self, item_id, owner_uid, for_update=False):
            return None

        async def item_is_selected_by_task(self, item_id, owner_uid, exclude_task_id=None):
            return False

    monkeypatch.setattr("yuxi.services.mp_service.MaterialLibraryRepository", Repo)
    with pytest.raises(HTTPException) as exc:
        await _lock_decoration_visual_material(
            None,
            type("User", (), {"uid": "u1"})(),
            cover_asset_id="cca_private",
            hycanvas_template_id="xiaohongshu-home-renovation",
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["error"]["code"] == "MP_COVER_LIBRARY_ITEM_MISSING"


@pytest.mark.asyncio
async def test_lock_decoration_visual_material_rejects_image_in_use(monkeypatch):
    class Item:
        id = "mli_1"
        asset_id = "cca_1"
        owner_uid = "u1"
        material_type = "image"
        status = "enabled"

    class Repo:
        def __init__(self, db, *, include_shared=False):
            self.include_shared = include_shared

        async def get_item_for_user(self, item_id, owner_uid, for_update=False):
            return Item()

        async def item_is_selected_by_task(self, item_id, owner_uid, exclude_task_id=None):
            return True

    monkeypatch.setattr("yuxi.services.mp_service.MaterialLibraryRepository", Repo)
    with pytest.raises(HTTPException) as exc:
        await _lock_decoration_visual_material(
            None,
            type("User", (), {"uid": "u1"})(),
            image_item_id="mli_1",
            hycanvas_template_id="xiaohongshu-home-renovation",
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "MP_COVER_IN_USE"


def test_mp_gallery_item_always_exposes_in_use():
    used = _mp_gallery_item({"id": "mli_1", "name": "客厅", "in_use": True})
    unused = _mp_gallery_item({"id": "mli_2", "name": "上传图片"})
    assert used["in_use"] is True
    assert used["file_url"] == "/api/mp/content/gallery-items/mli_1/file"
    assert used["thumbnail_file_url"] == "/api/mp/content/gallery-items/mli_1/thumbnail"
    assert unused["in_use"] is False
    assert unused["file_url"] == "/api/mp/content/gallery-items/mli_2/file"
    assert unused["thumbnail_file_url"] == "/api/mp/content/gallery-items/mli_2/thumbnail"


def test_mp_hycanvas_template_item_rewrites_preview_to_mp_proxy():
    template_id = "8bc32ed9-f80a-47e2-8e2b-913e35c125c8"
    item = _mp_hycanvas_template_item(
        {
            "id": template_id,
            "title": "小红书爆款封面",
            "preview_urls": [f"/api/content/covers/hycanvas/templates/{template_id}/render.png"],
        }
    )
    assert item["preview_urls"] == [f"/api/mp/content/hycanvas-templates/{template_id}/preview"]
    assert item["overlay_url"] == f"/api/mp/content/hycanvas-templates/{template_id}/overlay"
    assert item["title"] == "小红书爆款封面"


@pytest.mark.asyncio
async def test_read_hycanvas_template_preview_accepts_workspace_id(monkeypatch):
    template_id = "8bc32ed9-f80a-47e2-8e2b-913e35c125c8"

    class Client:
        async def fetch_template_preview(self, requested_id):
            assert requested_id == template_id
            return b"png", "image/png"

        @classmethod
        def from_env(cls):
            return Client()

    monkeypatch.setattr("yuxi.services.hycanvas_service.HyCanvasClient", Client)
    data, content_type = await read_hycanvas_template_preview(template_id)
    assert data == b"png"
    assert content_type == "image/png"


@pytest.mark.asyncio
async def test_read_hycanvas_template_preview_rejects_invalid_id():
    with pytest.raises(HTTPException) as exc:
        await read_hycanvas_template_preview("not-a-template-id")
    assert exc.value.status_code == 404
    assert exc.value.detail["error"]["code"] == "HYCANVAS_TEMPLATE_NOT_FOUND"


@pytest.mark.asyncio
async def test_read_hycanvas_template_overlay_accepts_workspace_id(monkeypatch):
    template_id = "8bc32ed9-f80a-47e2-8e2b-913e35c125c8"

    class Client:
        async def render_template_overlay_png(self, requested_id):
            assert requested_id == template_id
            return b"transparent-png", "image/png"

        @classmethod
        def from_env(cls):
            return Client()

    monkeypatch.setattr("yuxi.services.hycanvas_service.HyCanvasClient", Client)
    data, content_type = await read_hycanvas_template_overlay(template_id)
    assert data == b"transparent-png"
    assert content_type == "image/png"


@pytest.mark.asyncio
async def test_read_hycanvas_template_overlay_rejects_invalid_id():
    with pytest.raises(HTTPException) as exc:
        await read_hycanvas_template_overlay("not-a-template-id")
    assert exc.value.status_code == 404
    assert exc.value.detail["error"]["code"] == "HYCANVAS_TEMPLATE_NOT_FOUND"


@pytest.mark.asyncio
async def test_list_mp_gallery_items_forwards_in_use(monkeypatch):
    async def fake_list_material_items(*args, **kwargs):
        return {
            "items": [
                {"id": "mli_used", "name": "已占用", "in_use": True},
                {"id": "mli_free", "name": "可选用"},
            ],
            "total": 2,
        }

    monkeypatch.setattr("yuxi.services.mp_service.list_material_items", fake_list_material_items)
    result = await list_mp_gallery_items(None, type("Ctx", (), {"user": object()})(), "uncategorized")
    assert result["total"] == 2
    assert result["items"][0]["in_use"] is True
    assert result["items"][1]["in_use"] is False
    assert result["items"][0]["file_url"].endswith("/mli_used/file")
    assert result["items"][0]["thumbnail_file_url"].endswith("/mli_used/thumbnail")


@pytest.mark.asyncio
async def test_list_mp_gallery_items_forwards_descendant_pagination(monkeypatch):
    captured = {}

    async def fake_list_material_items(*args, **kwargs):
        captured.update(kwargs)
        return {"items": [], "total": 254, "page": 3, "page_size": 30}

    monkeypatch.setattr("yuxi.services.mp_service.list_material_items", fake_list_material_items)
    result = await list_mp_gallery_items(
        None,
        type("Ctx", (), {"user": object()})(),
        "case-root",
        scope="enterprise",
        include_descendants=True,
        page=3,
        page_size=30,
    )

    assert captured["include_descendants"] is True
    assert captured["page"] == 3
    assert captured["page_size"] == 30
    assert result == {"items": [], "total": 254, "page": 3, "page_size": 30}


@pytest.mark.asyncio
async def test_list_mp_galleries_includes_enterprise_scope(monkeypatch):
    async def fake_list_image_galleries(db, user):
        return {
            "galleries": [
                {"id": "mine", "name": "我的图库", "visibility": "private", "cover_item_id": "mli_1"},
                {
                    "id": "shared",
                    "name": "公共图库",
                    "visibility": "enterprise",
                    "cover_item_id": "mli_2",
                },
            ]
        }

    monkeypatch.setattr("yuxi.services.mp_service.list_image_galleries", fake_list_image_galleries)
    ctx = type("Ctx", (), {"user": object()})()
    all_galleries = await list_mp_galleries(None, ctx)
    assert [item["id"] for item in all_galleries["galleries"]] == ["mine", "shared"]
    assert all_galleries["galleries"][1]["cover_file_url"].endswith("/mli_2/file")
    assert all_galleries["galleries"][1]["cover_thumbnail_file_url"].endswith("/mli_2/thumbnail")
    shared = await list_mp_galleries(None, ctx, scope="enterprise")
    assert [item["id"] for item in shared["galleries"]] == ["shared"]
    private = await list_mp_galleries(None, ctx, scope="private")
    assert [item["id"] for item in private["galleries"]] == ["mine"]


def test_cover_asset_ids_keep_order_and_reject_more_than_three():
    assert _cover_asset_ids("a", ["a", "b", "c"]) == ["a", "b", "c"]
    with pytest.raises(HTTPException) as exc:
        _cover_asset_ids("a", ["b", "c", "d"])
    assert exc.value.detail["error"]["code"] == "MP_COVER_LIMIT"


def test_build_mp_brief_payload_review_notes_has_no_photos():
    brief = build_mp_brief_payload(
        service_entry="好评笔记",
        form_values={"设计师": "林工"},
        content_type_name="人设自荐",
        content_type_id=None,
        cover_asset_id=None,
        cover_asset_ids=["cover-2", "cover-3"],
        cover_template_id=None,
        content_code="NR20260828001",
    )
    assert brief.attachments == []
    assert not brief.form_values.get("cover_asset_id")
    assert not brief.form_values.get("cover_asset_ids")
    assert brief.audience == ["业主"]
    assert brief.form_values["project_type"] == "业主好评笔记"
    assert "style_excerpts" in brief.form_values["writing_instruction"]
    assert "不要写成获客" in brief.form_values["writing_instruction"]
    assert brief.form_values["voice"] == "业主第一人称"


def test_build_mp_brief_payload_review_notes_region_is_optional():
    with_region = build_mp_brief_payload(
        service_entry="好评笔记",
        form_values={"设计师": "林工", "所在区域": "株洲市 荷塘区"},
        content_type_name="人设自荐",
        content_type_id=None,
        cover_asset_id=None,
        cover_template_id=None,
        content_code="NR20260828002",
    )
    without_region = build_mp_brief_payload(
        service_entry="好评笔记",
        form_values={"设计师": "林工"},
        content_type_name="人设自荐",
        content_type_id=None,
        cover_asset_id=None,
        cover_template_id=None,
        content_code="NR20260828003",
    )
    assert with_region.audience == ["业主"]
    assert with_region.form_values["所在区域"] == "株洲市 荷塘区"
    assert with_region.form_values["location"] == "株洲市 荷塘区"
    assert without_region.audience == ["业主"]
    assert not str(without_region.form_values.get("所在区域") or "").strip()


def test_build_mp_brief_payload_accepts_city_only_region():
    brief = build_mp_brief_payload(
        service_entry="装修家居",
        form_values={
            "楼盘信息": "星河湾",
            "外框面积": "50-70㎡",
            "基础": "4万",
            "木制品": "2万",
            "主材": "2万",
            "设计风格": "北欧之光",
            "所在区域": "长沙市",
        },
        content_type_name="工艺施工展示",
        content_type_id="ct-process",
        cover_asset_id="cca_demo",
        cover_template_id=None,
        content_code="NR20260831006",
    )
    assert brief.form_values["所在区域"] == "长沙市"
    assert brief.audience == ["长沙市"]


def test_compact_run_exposes_error_message_for_failed_runs():
    result = _compact_run(
        {
            "run": {
                "id": "run-1",
                "thread_id": "task-1",
                "status": "failed",
                "request_id": "req-1",
                "error_message": "Agent 节点执行超时（120s）",
            }
        },
        None,
    )
    assert result["run_id"] == "run-1"
    assert result["status"] == "failed"
    assert result["error_message"] == "Agent 节点执行超时（120s）"
    assert result["interrupt"] is None


def test_cover_auto_resume_payload_uses_first_generated_asset():
    payload = _cover_auto_resume_payload(
        {
            "interrupt_type": "cover_selection",
            "run_id": "run-parent",
            "node_id": "select_cover",
            "expected_state_version": 4,
            "asset_ids": ["cca_1", "cca_2"],
        },
        "run-fallback",
    )
    assert payload == {
        "run_id": "run-parent",
        "node_id": "select_cover",
        "expected_state_version": 4,
        "asset_id": "cca_1",
    }


def test_cover_auto_resume_payload_ignores_non_cover_interrupts():
    assert _cover_auto_resume_payload({"interrupt_type": "content_approval"}, "run-1") is None
    assert _cover_auto_resume_payload({"interrupt_type": "cover_selection", "asset_ids": []}, "run-1") is None
    assert _cover_auto_resume_payload(None, "run-1") is None


def test_content_approval_auto_resume_payload_approves_current_interrupt():
    payload = _content_approval_auto_resume_payload(
        {
            "interrupt_type": "content_approval",
            "run_id": "run-parent",
            "node_id": "human_content_approval",
            "expected_state_version": 2,
        },
        "run-fallback",
    )
    assert payload == {
        "run_id": "run-parent",
        "node_id": "human_content_approval",
        "expected_state_version": 2,
        "decision": "approved",
        "note": "小程序自动审批",
    }
    assert _content_approval_auto_resume_payload({"interrupt_type": "cover_selection"}, "run-1") is None


def test_visible_mp_run_follows_cover_resume_and_completed_task():
    result = {
        "run": {
            "id": "run-parent",
            "thread_id": "task-1",
            "status": "interrupted",
            "request_id": "req-parent",
        },
        "continuations": [
            {
                "id": "run-parent",
                "thread_id": "task-1",
                "status": "interrupted",
                "request_id": "req-parent",
            },
            {
                "id": "run-child",
                "thread_id": "task-1",
                "status": "completed",
                "request_id": "req-child",
            },
        ],
    }
    visible = _visible_mp_run(result, task_status="reviewed")
    assert visible["id"] == "run-child"
    assert visible["status"] == "completed"
    following = _visible_mp_run(result, task_status="queued")
    assert following["id"] == "run-child"
    assert following["status"] == "completed"
