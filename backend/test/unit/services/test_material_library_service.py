from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image
from pydantic import ValidationError

import yuxi.services.material_library_service as material_library_service
from yuxi.services.material_library_service import (
    MATERIAL_LIBRARY_BUCKET,
    MaterialCategoryCreate,
    MaterialCategoryUpdate,
    MaterialShareCreate,
    _make_image_thumbnail,
    _make_share_card_cover,
    _make_share_display_webp,
    _normalize_image,
    create_material_share,
    create_material_category,
    render_public_material_share_page,
    serialize_public_material_share,
    serialize_item,
)
from yuxi.services.material_library_categories import (
    category_definition,
    normalize_material_category,
    validate_material_category,
)
from yuxi.repositories.material_library_repository import IMAGE_OCCUPANCY_RELEASED_STATUSES
from yuxi.storage.minio.client import MinIOClient
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentCoverPosterTemplate,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
    ContentMaterialShare,
    ContentMaterialShareItem,
)


def test_failed_and_cancelled_tasks_release_image_occupancy():
    assert IMAGE_OCCUPANCY_RELEASED_STATUSES == frozenset({"failed", "cancelled"})
    from sqlalchemy import func, select

    from yuxi.storage.postgres.models_content import ContentTask

    sql = str(
        select(func.count(ContentTask.id))
        .where(
            ContentTask.created_by == "owner",
            ContentTask.selected_image_item_id == "mli_1",
            ContentTask.deleted_at.is_(None),
            ContentTask.status.notin_(IMAGE_OCCUPANCY_RELEASED_STATUSES),
        )
        .compile(compile_kwargs={"literal_binds": True})
    )
    assert "content_tasks.status NOT IN ('cancelled', 'failed')" in sql or (
        "failed" in sql and "cancelled" in sql and "NOT IN" in sql
    )


def test_material_library_bucket_defaults_to_image():
    assert MATERIAL_LIBRARY_BUCKET == "image"
    assert MATERIAL_LIBRARY_BUCKET not in MinIOClient.PUBLIC_READ_BUCKETS


def test_normalize_image_returns_verified_webp():
    source = io.BytesIO()
    Image.new("RGB", (32, 24), "red").save(source, format="JPEG")

    data, width, height, content_type = _normalize_image(source.getvalue())

    assert (width, height) == (32, 24)
    assert content_type == "image/webp"
    assert data[8:12] == b"WEBP"


def test_normalize_image_rejects_non_image():
    with pytest.raises(HTTPException) as exc_info:
        _normalize_image(b"not-an-image")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "MATERIAL_IMAGE_INVALID"


def test_normalize_design_style_accepts_decoration_styles_only():
    from yuxi.services.material_library_service import _normalize_design_style

    assert _normalize_design_style("雅致现代") == "雅致现代"
    assert _normalize_design_style("  ") is None
    with pytest.raises(HTTPException) as exc_info:
        _normalize_design_style("不存在的风格")
    assert exc_info.value.detail["error"]["code"] == "MATERIAL_STYLE_INVALID"


def test_encode_material_thumbnail_limits_dimensions_and_returns_webp():
    from yuxi.services.material_upload_queue import encode_material_thumbnail

    source = io.BytesIO()
    Image.new("RGB", (1200, 800), "gray").save(source, format="PNG")

    data = encode_material_thumbnail(source.getvalue())

    assert data[8:12] == b"WEBP"
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "WEBP"
        assert image.size == (720, 480)


def test_share_card_cover_is_a_small_fixed_ratio_jpeg():
    source = io.BytesIO()
    Image.new("RGBA", (1200, 800), "royalblue").save(source, format="PNG")

    data = _make_share_card_cover(source.getvalue())

    assert data.startswith(b"\xff\xd8")
    assert len(data) < 128 * 1024
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG"
        assert image.size == (500, 400)


def test_share_display_webp_limits_wide_images_and_preserves_transparency():
    source = io.BytesIO()
    Image.new("RGBA", (2200, 1100), (65, 105, 225, 128)).save(source, format="PNG")

    data = _make_share_display_webp(source.getvalue())

    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "WEBP"
        assert image.size == (1440, 720)
        assert image.mode == "RGBA"


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


def test_material_category_payload_does_not_expose_image_design_role():
    assert "image_design_role" not in MaterialCategoryCreate.model_fields
    assert "image_design_role" not in MaterialCategoryUpdate.model_fields


def test_material_share_selection_requires_distinct_nonempty_image_ids():
    with pytest.raises(ValidationError):
        MaterialShareCreate(item_ids=[])
    with pytest.raises(ValidationError):
        MaterialShareCreate(item_ids=["mli_1", "mli_1"])
    with pytest.raises(ValidationError):
        MaterialShareCreate(item_ids=[" "])
    assert len(MaterialShareCreate(item_ids=[f"mli_{index}" for index in range(1000)]).item_ids) == 1000
    with pytest.raises(ValidationError):
        MaterialShareCreate(item_ids=[f"mli_{index}" for index in range(1001)])


@pytest.mark.parametrize(
    ("missing_field", "error_code"),
    [
        ("design_style", "MATERIAL_DESIGN_STYLE_REQUIRED"),
        ("building_name", "MATERIAL_BUILDING_NAME_REQUIRED"),
        ("area", "MATERIAL_AREA_REQUIRED"),
    ],
)
@pytest.mark.asyncio
async def test_material_share_rejects_legacy_decoration_gallery_without_project_details(
    monkeypatch, missing_field, error_code
):
    class FakeRepo:
        def __init__(self, _db, **_kwargs):
            pass

        async def list_image_items_with_assets_and_categories(self, _owner_uid, _item_ids):
            gallery = ContentMaterialCategory(
                owner_uid="owner-1",
                material_type="image",
                id="gallery-2",
                parent_id="gallery-1",
                industry_slug="decoration",
                name="桂语云峰",
                design_style="复古风潮",
                building_name="桂语云峰",
                area="120",
            )
            setattr(gallery, missing_field, None)
            return [(SimpleNamespace(id="item-1"), SimpleNamespace(), gallery)]

    monkeypatch.setattr(material_library_service, "MaterialLibraryRepository", FakeRepo)

    with pytest.raises(HTTPException) as error:
        await create_material_share(
            object(),
            SimpleNamespace(id="user-1", uid="owner-1", department_id=None),
            MaterialShareCreate(item_ids=["item-1"]),
        )

    assert error.value.status_code == 422
    assert error.value.detail["error"]["code"] == error_code


@pytest.mark.parametrize("raw_area", ["120㎡", "120m²", "120 m²", "120m2"])
def test_material_category_area_strips_supported_units_before_persisting(raw_area):
    create_payload = MaterialCategoryCreate(material_type="image", name="客厅案例", area=raw_area)
    update_payload = MaterialCategoryUpdate(area=raw_area)

    assert create_payload.area == "120"
    assert update_payload.area == "120"


def test_public_material_share_serializer_exposes_only_snapshot_data_in_display_order():
    share = ContentMaterialShare(
        id="mls_internal",
        token="not-enumerable-share-id",
        owner_uid="private-owner",
        category_id="private-category",
        title="客厅实景",
        building_name="万科金域华府",
        area="120",
        design_style="现代简约",
    )
    items = [
        ContentMaterialShareItem(
            share_id=share.id,
            display_order=1,
            original_file_name="first.png",
            content_type="image/png",
            file_size=128,
            image_width=48,
            image_height=36,
            bucket_name="image",
            object_name="immutable/1.png",
        ),
        ContentMaterialShareItem(
            share_id=share.id,
            display_order=2,
            original_file_name="second.png",
            content_type="image/png",
            file_size=128,
            image_width=48,
            image_height=36,
            bucket_name="image",
            object_name="immutable/2.png",
        ),
    ]

    payload = serialize_public_material_share(share, items)

    assert payload == {
        "share": {
            "id": "not-enumerable-share-id",
            "gallery_name": "客厅实景",
            "building_name": "万科金域华府",
            "area": "120",
            "design_style": "现代简约",
            "cover_url": "/api/material-library/shares/not-enumerable-share-id/images/1",
            "cover_webp_url": "/api/material-library/shares/not-enumerable-share-id/images/1.webp",
            "card_cover_url": "/api/material-library/shares/not-enumerable-share-id/cover.jpg",
            "images": [
                {
                    "order": 1,
                    "file_name": "first.png",
                    "url": "/api/material-library/shares/not-enumerable-share-id/images/1",
                    "webp_url": "/api/material-library/shares/not-enumerable-share-id/images/1.webp",
                },
                {
                    "order": 2,
                    "file_name": "second.png",
                    "url": "/api/material-library/shares/not-enumerable-share-id/images/2",
                    "webp_url": "/api/material-library/shares/not-enumerable-share-id/images/2.webp",
                },
            ],
        }
    }


def test_public_material_share_serializer_normalizes_legacy_area_units():
    share = ContentMaterialShare(
        id="mls_legacy",
        token="legacy-share-token",
        owner_uid="private-owner",
        category_id="private-category",
        title="旧分享",
        building_name="桂语云峰",
        area="120m²",
        design_style="复古风潮",
    )

    payload = serialize_public_material_share(share, [])

    assert payload["share"]["area"] == "120"


@pytest.mark.parametrize("raw_area", ["120", "120㎡", "120m²", "120 m²", "120m2"])
def test_public_material_share_page_renders_exactly_one_area_unit(raw_area):
    share = ContentMaterialShare(
        id="mls_area",
        token="area-share-token",
        owner_uid="owner-1",
        category_id="gallery-2",
        title="桂语云峰·120m²",
        building_name="桂语云峰",
        area=raw_area,
        design_style="复古风潮",
    )

    page = render_public_material_share_page(share, [], "https://share.example.test")

    assert "面积：120㎡" in page
    assert "桂语云峰｜120㎡｜复古风潮" in page


def test_public_material_share_page_uses_snapshot_order_and_renders_share_card_metadata(monkeypatch):
    monkeypatch.setenv("MATERIAL_LIBRARY_SHARE_PUBLIC_BASE_URL", "https://old-share.example.test")
    share = ContentMaterialShare(
        id="mls_1",
        token="share-token",
        owner_uid="owner-1",
        category_id="gallery-2",
        title="洋湖天序·三居式·复古写意",
        building_name="万科金域华府",
        area="120",
        design_style="现代简约",
    )
    items = [
        ContentMaterialShareItem(
            share_id=share.id,
            display_order=1,
            original_file_name="first.png",
            content_type="image/png",
            file_size=128,
            image_width=48,
            image_height=36,
            bucket_name="image",
            object_name="material-library-shares/owner-1/mls_1/1.png",
        ),
        ContentMaterialShareItem(
            share_id=share.id,
            display_order=2,
            original_file_name="second.png",
            content_type="image/png",
            file_size=256,
            image_width=64,
            image_height=48,
            bucket_name="image",
            object_name="material-library-shares/owner-1/mls_1/2.png",
        ),
    ]

    page = render_public_material_share_page(share, items, "https://share.example.test/boyun/")

    assert "<title>洋湖天序·三居式·复古写意</title>" in page
    assert '<meta name="description" content="万科金域华府｜120㎡｜现代简约">' in page
    assert '<meta property="og:type" content="website">' in page
    assert '<meta property="og:url" content="https://share.example.test/boyun/share/case/share-token">' in page
    assert '<meta property="og:site_name" content="Yuxi">' in page
    assert 'property="og:description" content="万科金域华府｜120㎡｜现代简约"' in page
    assert "楼盘：万科金域华府" in page
    assert "面积：120㎡" in page
    assert "风格：现代简约" in page
    assert (
        'property="og:image" '
        'content="https://share.example.test/boyun/api/material-library/shares/share-token/cover.jpg"' in page
    )
    assert (
        'property="og:image:secure_url" '
        'content="https://share.example.test/boyun/api/material-library/shares/share-token/cover.jpg"' in page
    )
    assert '<meta property="og:image:type" content="image/jpeg">' in page
    assert '<meta property="og:image:width" content="500">' in page
    assert '<meta property="og:image:height" content="400">' in page
    assert page.index("/images/1.webp") < page.index("/images/2.webp")


@pytest.mark.parametrize("design_style", ["工业再造", "优雅缤纷", "极简侘寂", "仿生未来"])
@pytest.mark.asyncio
async def test_decoration_gallery_child_requires_and_persists_an_allowed_design_style(monkeypatch, design_style):
    parent = ContentMaterialCategory(
        owner_uid="owner-1",
        material_type="image",
        id="gallery-decoration",
        industry_slug="decoration",
        name="222",
        sort_order=0,
    )

    class FakeDB:
        def add(self, _entry):
            pass

        async def commit(self):
            pass

        async def rollback(self):
            pass

    class FakeRepo:
        def __init__(self, _db, **_kwargs):
            pass

        async def get_category(self, *_args, **_kwargs):
            return parent

        async def create_category(self, **values):
            return ContentMaterialCategory(**values)

    async def ensure_categories(*_args, **_kwargs):
        return [parent]

    monkeypatch.setattr(material_library_service, "MaterialLibraryRepository", FakeRepo)
    monkeypatch.setattr(material_library_service, "ensure_material_categories", ensure_categories)

    with pytest.raises(HTTPException) as missing_style:
        await create_material_category(
            FakeDB(),
            type("User", (), {"id": "user-1", "uid": "owner-1", "department_id": None})(),
            MaterialCategoryCreate(material_type="image", name="客厅案例", parent_id=parent.id),
        )

    assert missing_style.value.status_code == 422
    assert missing_style.value.detail["error"]["code"] == "MATERIAL_DESIGN_STYLE_REQUIRED"

    with pytest.raises(HTTPException) as invalid_style:
        await create_material_category(
            FakeDB(),
            type("User", (), {"id": "user-1", "uid": "owner-1", "department_id": None})(),
            MaterialCategoryCreate(
                material_type="image",
                name="卧室案例",
                parent_id=parent.id,
                design_style="不在列表中",
            ),
        )

    assert invalid_style.value.status_code == 422
    assert invalid_style.value.detail["error"]["code"] == "MATERIAL_DESIGN_STYLE_INVALID"

    created = await create_material_category(
        FakeDB(),
        type("User", (), {"id": "user-1", "uid": "owner-1", "department_id": None})(),
        MaterialCategoryCreate(
            material_type="image",
            name="书房案例",
            parent_id=parent.id,
            design_style=design_style,
            building_name="洋湖天序",
            area="120",
        ),
    )

    assert created["category"]["design_style"] == design_style


@pytest.mark.asyncio
async def test_decoration_gallery_child_requires_and_persists_building_name_and_area(monkeypatch):
    parent = ContentMaterialCategory(
        owner_uid="owner-1",
        material_type="image",
        id="gallery-decoration",
        industry_slug="decoration",
        name="装修与家居",
        sort_order=0,
    )

    class FakeDB:
        def add(self, _entry):
            pass

        async def commit(self):
            pass

        async def rollback(self):
            pass

    class FakeRepo:
        def __init__(self, _db, **_kwargs):
            pass

        async def get_category(self, *_args, **_kwargs):
            return parent

        async def create_category(self, **values):
            return ContentMaterialCategory(**values)

    async def ensure_categories(*_args, **_kwargs):
        return [parent]

    monkeypatch.setattr(material_library_service, "MaterialLibraryRepository", FakeRepo)
    monkeypatch.setattr(material_library_service, "ensure_material_categories", ensure_categories)
    user = type("User", (), {"id": "user-1", "uid": "owner-1", "department_id": None})()

    with pytest.raises(HTTPException) as missing_building_name:
        await create_material_category(
            FakeDB(),
            user,
            MaterialCategoryCreate(
                material_type="image",
                name="客厅案例",
                parent_id=parent.id,
                design_style="江南印象",
            ),
        )

    assert missing_building_name.value.status_code == 422
    assert missing_building_name.value.detail["error"]["code"] == "MATERIAL_BUILDING_NAME_REQUIRED"

    with pytest.raises(HTTPException) as missing_area:
        await create_material_category(
            FakeDB(),
            user,
            MaterialCategoryCreate(
                material_type="image",
                name="卧室案例",
                parent_id=parent.id,
                design_style="江南印象",
                building_name="洋湖天序",
            ),
        )

    assert missing_area.value.status_code == 422
    assert missing_area.value.detail["error"]["code"] == "MATERIAL_AREA_REQUIRED"

    created = await create_material_category(
        FakeDB(),
        user,
        MaterialCategoryCreate(
            material_type="image",
            name="书房案例",
            parent_id=parent.id,
            design_style="江南印象",
            building_name="洋湖天序",
            area="120",
        ),
    )

    assert created["category"]["building_name"] == "洋湖天序"
    assert created["category"]["area"] == "120"


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
