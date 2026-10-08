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
    _can_manage_category,
    _can_manage_item,
    _is_target_rough_category,
    _make_share_card_cover,
    _make_share_display_webp,
    _normalize_image,
    create_material_share,
    create_material_category,
    ensure_initial_enterprise_galleries,
    render_public_material_share_page,
    serialize_public_material_share,
    serialize_item,
)
from yuxi.services.material_library_categories import (
    category_definition,
    normalize_material_category,
    validate_material_category,
)
from yuxi.repositories.material_library_repository import (
    IMAGE_OCCUPANCY_ACTIVE_STATUSES,
    IMAGE_OCCUPANCY_RELEASED_STATUSES,
)
from yuxi.storage.minio.client import MinIOClient
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentCoverPosterTemplate,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
    ContentMaterialShare,
    ContentMaterialShareItem,
)


def test_only_generating_or_succeeded_tasks_occupy_images():
    assert IMAGE_OCCUPANCY_ACTIVE_STATUSES == frozenset(
        {
            "queued",
            "running",
            "waiting_human",
            "waiting_external",
            "review_required",
            "reviewed",
            "generated",
            "completed",
        }
    )
    assert IMAGE_OCCUPANCY_RELEASED_STATUSES.isdisjoint(IMAGE_OCCUPANCY_ACTIVE_STATUSES)
    from sqlalchemy import func, select

    from yuxi.storage.postgres.models_content import ContentTask

    sql = str(
        select(func.count(ContentTask.id))
        .where(
            ContentTask.created_by == "owner",
            ContentTask.selected_image_item_id == "mli_1",
            ContentTask.deleted_at.is_(None),
            ContentTask.status.in_(IMAGE_OCCUPANCY_ACTIVE_STATUSES),
        )
        .compile(compile_kwargs={"literal_binds": True})
    )
    assert "IN (" in sql
    for status in IMAGE_OCCUPANCY_ACTIVE_STATUSES:
        assert f"'{status}'" in sql
    for status in ("draft", "brief_ready", "failed", "cancelled", "review_blocked", "deleted"):
        assert f"'{status}'" not in sql


def test_designer_can_browse_case_gallery_but_not_upload_it():
    from yuxi.services.material_library_service import (
        _designer_may_browse_case,
        _designer_may_see_category,
        is_enterprise_case_root,
    )

    case_root = SimpleNamespace(
        material_type="image",
        visibility="enterprise",
        parent_id=None,
        image_design_role="reference",
        name="案例图库",
    )
    style_gallery = SimpleNamespace(
        material_type="image",
        visibility="enterprise",
        parent_id="case-root",
        image_design_role=None,
        name="星河湾",
    )
    generated = SimpleNamespace(
        material_type="image",
        visibility="enterprise",
        parent_id=None,
        image_design_role="generated",
        name="生图图库",
    )

    assert is_enterprise_case_root(case_root)
    assert _designer_may_browse_case(case_root, None)
    assert _designer_may_browse_case(style_gallery, case_root)
    assert not _designer_may_see_category(case_root)
    assert not _designer_may_see_category(style_gallery)
    assert not _designer_may_browse_case(generated, None)
    assert not _designer_may_browse_case(style_gallery, generated)


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
            SimpleNamespace(id="user-1", uid="owner-1", department_id=None, role="user"),
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
            "sharer_name": None,
            "sharer_phone": None,
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


@pytest.mark.asyncio
async def test_only_admin_can_create_image_gallery_and_child_gallery(monkeypatch):
    fallback = ContentMaterialCategory(
        owner_uid="owner-1",
        material_type="image",
        id="uncategorized",
        industry_slug="uncategorized",
        name="未分类",
        sort_order=0,
        is_system=True,
    )

    class FakeDB:
        def add(self, _entry):
            pass

        async def commit(self):
            pass

        async def rollback(self):
            pass

    parent = ContentMaterialCategory(
        owner_uid="owner-1",
        id="gallery-parent",
        material_type="image",
        visibility="enterprise",
        name="案例图库",
        industry_slug="uncategorized",
        image_design_role="reference",
    )

    class FakeRepo:
        def __init__(self, _db, **_kwargs):
            pass

        async def get_category(self, _owner_uid, _material_type, category_id, **_kwargs):
            return parent if category_id == parent.id else None

        async def create_category(self, **values):
            return ContentMaterialCategory(**values)

    async def ensure_categories(*_args, **_kwargs):
        return [fallback]

    monkeypatch.setattr(material_library_service, "MaterialLibraryRepository", FakeRepo)
    monkeypatch.setattr(material_library_service, "ensure_material_categories", ensure_categories)
    regular_user = SimpleNamespace(id="user-1", uid="owner-1", department_id=None, role="user")
    admin_user = SimpleNamespace(id="admin-1", uid="owner-1", department_id=None, role="admin")

    with pytest.raises(HTTPException) as forbidden:
        await create_material_category(
            FakeDB(),
            regular_user,
            MaterialCategoryCreate(material_type="image", name="我的图库"),
        )
    assert forbidden.value.status_code == 403
    assert forbidden.value.detail["error"]["code"] == "MATERIAL_CATEGORY_FORBIDDEN"

    created_child = await create_material_category(
        FakeDB(),
        admin_user,
        MaterialCategoryCreate(
            material_type="image",
            name="桂语云峰",
            parent_id="gallery-parent",
            design_style="江南印象",
            building_name="桂语云峰",
            area="120",
        ),
    )
    assert created_child["category"]["parent_id"] == "gallery-parent"
    assert created_child["category"]["visibility"] == "enterprise"
    assert created_child["category"]["industry_slug"] == "uncategorized"
    assert created_child["category"]["design_style"] == "江南印象"
    assert created_child["category"]["building_name"] == "桂语云峰"
    assert created_child["category"]["area"] == "120"

    created = await create_material_category(
        FakeDB(),
        admin_user,
        MaterialCategoryCreate(material_type="image", name="全员图库", description="管理员发布"),
    )
    assert created["category"]["name"] == "全员图库"
    assert created["category"]["visibility"] == "private"
    assert created["category"]["parent_id"] is None
    assert created["category"]["is_global_personal"] is False

    with pytest.raises(HTTPException) as target_forbidden:
        await create_material_category(
            FakeDB(),
            regular_user,
            MaterialCategoryCreate(material_type="image", name="员工的其他图库"),
            actor_user=admin_user,
            target_private=True,
        )
    assert target_forbidden.value.status_code == 403


@pytest.mark.asyncio
async def test_initial_enterprise_roles_survive_rename_and_soft_delete_without_recreation():
    class Result:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self.rows

    class FakeDB:
        def __init__(self):
            self.rows = []
            self.added = 0

        async def execute(self, _query):
            return Result(self.rows)

        def add(self, category):
            self.rows.append(category)
            self.added += 1

        async def flush(self):
            pass

    db = FakeDB()
    await ensure_initial_enterprise_galleries(db)
    assert db.added == 3
    assert {row.image_design_role for row in db.rows} == {"reference", "rough", "generated"}

    reference = next(row for row in db.rows if row.image_design_role == "reference")
    reference.name = "客户案例"
    rough = next(row for row in db.rows if row.image_design_role == "rough")
    rough.deleted_at = material_library_service.utc_now_naive()
    await ensure_initial_enterprise_galleries(db)
    assert db.added == 3
    assert reference.name == "客户案例"
    assert rough.deleted_at is not None


@pytest.mark.asyncio
async def test_existing_enterprise_gallery_needs_reviewed_role_migration():
    class Result:
        def scalars(self):
            return [
                ContentMaterialCategory(
                    owner_uid="admin-1",
                    material_type="image",
                    id="old-generated",
                    visibility="enterprise",
                    name="生图图库",
                    is_system=True,
                )
            ]

    class FakeDB:
        added = 0

        async def execute(self, _query):
            return Result()

        def add(self, _category):
            self.added += 1

    db = FakeDB()
    with pytest.raises(HTTPException) as migration_required:
        await ensure_initial_enterprise_galleries(db)
    assert migration_required.value.status_code == 409
    assert migration_required.value.detail["error"]["code"] == "MATERIAL_GALLERY_MIGRATION_REQUIRED"
    assert db.added == 0


def test_target_employee_category_scope_only_matches_private_rough_gallery():
    rough = ContentMaterialCategory(
        owner_uid="employee-1",
        material_type="image",
        id="mp-rough-private",
        visibility="private",
        name="已改名的毛坯图库",
    )
    other = ContentMaterialCategory(
        owner_uid="employee-1",
        material_type="image",
        id="other",
        visibility="private",
        name="私人上传",
    )
    assert _is_target_rough_category(rough, "employee-1")
    assert not _is_target_rough_category(rough, "employee-2")
    assert not _is_target_rough_category(other, "employee-1")


def test_image_gallery_management_requires_admin_owner_but_cover_categories_keep_owner_rule():
    image_gallery = ContentMaterialCategory(
        owner_uid="owner-1",
        material_type="image",
        id="gallery-1",
        visibility="private",
        name="管理员图库",
        sort_order=0,
    )
    cover_category = ContentMaterialCategory(
        owner_uid="owner-1",
        material_type="cover_template",
        id="cover-1",
        visibility="private",
        name="封面分类",
        sort_order=0,
    )
    regular_owner = SimpleNamespace(uid="owner-1", role="user")
    admin_owner = SimpleNamespace(uid="owner-1", role="admin")
    other_admin = SimpleNamespace(uid="owner-2", role="superadmin")

    assert not _can_manage_category(regular_owner, image_gallery)
    assert _can_manage_category(admin_owner, image_gallery)
    assert not _can_manage_category(other_admin, image_gallery)
    assert _can_manage_category(regular_owner, cover_category)


def test_global_personal_gallery_contributions_stay_private_after_creator_demotion():
    from yuxi.image_design.save_targets import can_contribute_to_category

    gallery = ContentMaterialCategory(
        owner_uid="owner-1",
        material_type="image",
        id="mlc-global",
        visibility="private",
        is_global_personal=True,
        name="团队灵感",
    )
    item = ContentMaterialLibraryItem(owner_uid="owner-1")
    admin = SimpleNamespace(uid="owner-1", role="admin")
    demoted = SimpleNamespace(uid="owner-1", role="user")
    other_admin = SimpleNamespace(uid="owner-2", role="superadmin")

    assert can_contribute_to_category(admin, gallery)
    assert _can_manage_item(admin, item, gallery)
    assert gallery.is_global_personal is True
    assert can_contribute_to_category(demoted, gallery)
    assert _can_manage_item(demoted, item, gallery)
    assert not _can_manage_category(demoted, gallery)
    assert can_contribute_to_category(other_admin, gallery)
    assert not _can_manage_item(other_admin, item, gallery)


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
