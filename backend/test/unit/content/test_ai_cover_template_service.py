import pytest
from fastapi import HTTPException

from types import SimpleNamespace

from yuxi.services.ai_cover_template_service import (
    _content_type_applies,
    apply_catalog_overrides,
    cover_upload_name,
    is_custom_cover_id,
    merge_catalog_item,
)


def test_cover_upload_name_keeps_local_photo_name():
    assert cover_upload_name(r"C:\Users\demo\客厅封面.png") == "客厅封面.png"
    assert cover_upload_name("folder/sample.JPEG") == "sample.JPEG"


def test_cover_upload_name_rejects_unsupported_file():
    with pytest.raises(HTTPException) as exc:
        cover_upload_name("notes.txt")
    assert exc.value.status_code == 422
    assert exc.value.detail["error"]["code"] == "AI_COVER_FILE_TYPE"


def test_merge_catalog_item_applies_edit_and_hides_deleted():
    item = {"variant": "p1", "title": "手写风双行标题", "label": "提示词一", "prompt_body": "旧提示词"}
    edited = merge_catalog_item(
        item,
        SimpleNamespace(
            hidden=False, enabled=True, name="手写风双标题", prompt="新提示词", content_type_names=["装修案例分享"]
        ),
    )
    assert edited["title"] == "手写风双标题"
    assert edited["prompt_body"] == "新提示词"
    assert edited["content_type_names"] == ["装修案例分享"]
    assert merge_catalog_item(item, SimpleNamespace(hidden=True, name="", prompt="", content_type_names=[])) is None


def test_custom_cover_id_shape():
    assert is_custom_cover_id("c0123456789abcdef")
    assert not is_custom_cover_id("p1")
    assert not is_custom_cover_id("smart-random")


def test_content_type_applies_treats_empty_names_as_all_types():
    assert _content_type_applies([], "装修案例分享")
    assert _content_type_applies(["装修案例分享"], "装修案例分享")
    assert not _content_type_applies(["工艺施工展示"], "装修案例分享")


@pytest.mark.asyncio
async def test_apply_catalog_overrides_keeps_items_with_empty_override_scope():
    item = {"variant": "p1", "title": "手写风双行标题", "label": "提示词一", "prompt_body": "旧提示词"}
    overrides = {
        "p1": SimpleNamespace(
            hidden=False,
            enabled=True,
            name="手写风双标题",
            prompt="新提示词",
            content_type_names=[],
        )
    }
    visible = await apply_catalog_overrides(
        None,
        [item],
        content_type_name="装修案例分享",
        overrides=overrides,
    )
    assert len(visible) == 1
    assert visible[0]["title"] == "手写风双标题"
