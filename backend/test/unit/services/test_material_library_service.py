from __future__ import annotations

import io

import pytest
from fastapi import HTTPException
from PIL import Image

from yuxi.services.material_library_service import (
    MATERIAL_LIBRARY_BUCKET,
    _make_image_thumbnail,
    _normalize_image,
    serialize_item,
)
from yuxi.services.material_library_categories import (
    category_definition,
    normalize_material_category,
    validate_material_category,
)
from yuxi.storage.minio.client import MinIOClient
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentCoverPosterTemplate,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
)


def test_material_library_bucket_defaults_to_image():
    assert MATERIAL_LIBRARY_BUCKET == "image"
    assert MATERIAL_LIBRARY_BUCKET not in MinIOClient.PUBLIC_READ_BUCKETS


def test_normalize_image_returns_verified_png():
    source = io.BytesIO()
    Image.new("RGB", (32, 24), "red").save(source, format="JPEG")

    data, width, height, content_type = _normalize_image(source.getvalue())

    assert (width, height) == (32, 24)
    assert content_type == "image/png"
    assert data.startswith(b"\x89PNG\r\n\x1a\n")


def test_normalize_image_rejects_non_image():
    with pytest.raises(HTTPException) as exc_info:
        _normalize_image(b"not-an-image")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "MATERIAL_IMAGE_INVALID"


def test_make_image_thumbnail_limits_dimensions_and_returns_jpeg():
    source = io.BytesIO()
    Image.new("RGB", (1200, 800), "gray").save(source, format="PNG")

    data = _make_image_thumbnail(source.getvalue())

    assert data.startswith(b"\xff\xd8")
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (480, 320)


def test_material_categories_normalize_legacy_values_and_reject_free_form():
    assert normalize_material_category("image", "产品商品") == "product"
    assert normalize_material_category("cover_template", "unknown-old-value") == "uncategorized"
    assert category_definition("cover_template", "营销促销")["code"] == "marketing"
    with pytest.raises(ValueError):
        validate_material_category("image", "uncategorized")
    with pytest.raises(ValueError):
        validate_material_category("image", "custom")


def test_material_category_exposes_gallery_level():
    parent = ContentMaterialCategory(
        owner_uid="owner-1",
        material_type="image",
        id="gallery-1",
        industry_slug="decoration",
        name="案例",
    )
    child = ContentMaterialCategory(
        owner_uid="owner-1",
        material_type="image",
        id="gallery-2",
        parent_id=parent.id,
        name="客厅",
    )

    assert parent.to_dict()["level"] == 1
    assert parent.to_dict()["parent_id"] is None
    assert parent.to_dict()["industry_slug"] == "decoration"
    assert child.to_dict()["level"] == 2
    assert child.to_dict()["parent_id"] == parent.id


def test_cover_template_item_exposes_linked_generation_status():
    asset = ContentCoverAsset(
        id="asset-1",
        owner_uid="owner-1",
        role="template",
        original_file_name="poster.png",
        content_type="image/png",
        file_size=128,
        image_width=1080,
        image_height=1440,
        sha256="checksum",
        bucket_name="image",
        object_name="material-library/owner-1/cover-templates/asset-1/poster.png",
    )
    item = ContentMaterialLibraryItem(
        id="item-1",
        owner_uid="owner-1",
        asset_id=asset.id,
        material_type="cover_template",
        display_name="案例复盘",
        category="case-study",
        status="enabled",
    )
    category = ContentMaterialCategory(
        owner_uid="owner-1",
        material_type="cover_template",
        id="case-study",
        name="客户案例",
    )
    poster = ContentCoverPosterTemplate(
        id="poster-1",
        owner_uid="owner-1",
        asset_id=asset.id,
        name=item.display_name,
        category=item.category,
        canvas_width=1080,
        canvas_height=1440,
        product_box_json={"x": 0, "y": 0, "width": 1080, "height": 1440},
        checksum="poster-checksum",
        version=3,
        status="ready",
    )

    result = serialize_item(item, asset, category, poster)

    assert result["poster_template_id"] == poster.id
    assert result["template_status"] == "ready"
    assert result["template_version"] == 3
    assert result["selectable"] is True

    poster.status = "needs_annotation"
    assert serialize_item(item, asset, category, poster)["selectable"] is False
