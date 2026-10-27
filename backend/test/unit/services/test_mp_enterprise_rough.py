import io
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException, UploadFile

from yuxi.services import material_library_service as materials, mp_service
from yuxi.storage.postgres.models_content import ContentMaterialCategory, ContentMaterialLibraryItem


def rough_gallery(**changes):
    return ContentMaterialCategory(
        **{
            "id": "shared-rough",
            "owner_uid": "gallery-owner",
            "material_type": "image",
            "visibility": "enterprise",
            "parent_id": None,
            "name": "毛坯房图库",
            "image_design_role": "rough",
            "deleted_at": None,
            **changes,
        }
    )


@pytest.mark.asyncio
async def test_rough_entry_filters_other_enterprise_galleries(monkeypatch):
    monkeypatch.setattr(
        mp_service,
        "list_image_galleries",
        AsyncMock(
            return_value={
                "galleries": [
                    {"id": "rough", "visibility": "enterprise", "image_design_role": "rough", "parent_id": None},
                    {"id": "case", "visibility": "enterprise", "image_design_role": "reference", "parent_id": None},
                    {"id": "child", "visibility": "enterprise", "image_design_role": "rough", "parent_id": "rough"},
                    {"id": "personal", "visibility": "private", "image_design_role": "rough", "parent_id": None},
                ]
            }
        ),
    )
    result = await mp_service.list_mp_galleries(object(), SimpleNamespace(user=object()), "enterprise", True)
    assert [row["id"] for row in result["galleries"]] == ["rough"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        [],
        [
            {"id": "one", "visibility": "enterprise", "image_design_role": "rough"},
            {"id": "two", "visibility": "enterprise", "image_design_role": "rough"},
        ],
    ],
)
async def test_rough_entry_reports_missing_or_ambiguous_gallery(monkeypatch, rows):
    monkeypatch.setattr(mp_service, "list_image_galleries", AsyncMock(return_value={"galleries": rows}))
    with pytest.raises(HTTPException) as exc:
        await mp_service.list_mp_galleries(object(), SimpleNamespace(user=object()), "enterprise", True)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_shared_items_keep_all_uploaders_but_delete_flag_is_owner_only(monkeypatch):
    listing = AsyncMock(
        return_value={
            "items": [
                {"id": "mine", "uploaded_by": "alice", "can_manage": True},
                {"id": "other", "uploaded_by": "bob", "can_manage": True},
            ],
            "total": 2,
            "pending_upload_count": 1,
            "upload_failures": [{"id": "failed"}],
        }
    )
    monkeypatch.setattr(mp_service, "list_material_items", listing)
    result = await mp_service.list_mp_gallery_items(
        object(),
        SimpleNamespace(user=SimpleNamespace(uid="alice")),
        "shared-rough",
        "enterprise",
        page=2,
        page_size=30,
        date_from=date(2026, 10, 1),
        date_to=date(2026, 10, 15),
    )
    assert [row["can_delete"] for row in result["items"]] == [True, False]
    assert result["total"] == 2 and result["pending_upload_count"] == 1
    assert result["upload_failures"] == [{"id": "failed"}]
    assert listing.await_args.kwargs["date_from"] == date(2026, 10, 1)
    assert listing.await_args.kwargs["page"] == 2
    assert "enterprise_rough_owner_uid" not in listing.await_args.kwargs
    assert result["items"][1]["file_url"] == "/api/mp/content/gallery-items/other/file"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "visibility,role,parent",
    [
        ("private", "rough", None),
        ("enterprise", "reference", None),
        ("enterprise", "generated", None),
        ("enterprise", "rough", "parent"),
    ],
)
async def test_mp_enterprise_upload_rejects_other_destinations(monkeypatch, visibility, role, parent):
    gallery = rough_gallery(visibility=visibility, image_design_role=role, parent_id=parent, name="other")
    monkeypatch.setattr(materials, "material_access_mode", AsyncMock(return_value="full"))
    monkeypatch.setattr(materials, "resolve_material_category", AsyncMock(return_value=gallery))
    with pytest.raises(HTTPException) as exc:
        await materials.import_material_images(
            object(),
            SimpleNamespace(uid="alice", department_id=None),
            [SimpleNamespace(filename="new.png")],
            category=gallery.id,
            source_channel="mp",
            mp_scope="enterprise",
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_mp_enterprise_upload_rechecks_role_under_lock(monkeypatch):
    gallery = rough_gallery()
    # A misleading display name must not bypass the server's fixed gallery role.
    recheck = AsyncMock(return_value=rough_gallery(image_design_role="generated", name="毛坯房图库"))
    monkeypatch.setattr(materials, "material_access_mode", AsyncMock(return_value="full"))
    monkeypatch.setattr(materials, "resolve_material_category", AsyncMock(return_value=gallery))
    monkeypatch.setattr(
        materials, "MaterialLibraryRepository", lambda *a, **k: SimpleNamespace(get_category_exact=recheck)
    )
    with pytest.raises(HTTPException) as exc:
        await materials.import_material_images(
            object(),
            SimpleNamespace(uid="alice", department_id=None),
            [SimpleNamespace(filename="new.png")],
            category=gallery.id,
            source_channel="mp",
            mp_scope="enterprise",
        )
    assert exc.value.status_code == 403
    assert recheck.await_args.kwargs["for_update"] is True
    assert recheck.await_args.kwargs["visibility"] == "enterprise"


@pytest.mark.asyncio
async def test_legacy_explicit_mp_rough_upload_preserves_enterprise_scope(monkeypatch):
    gallery = rough_gallery()
    monkeypatch.setattr(materials, "material_access_mode", AsyncMock(return_value="full"))
    monkeypatch.setattr(materials, "_resolve_upload_category", AsyncMock(return_value=(gallery, None)))
    recheck = AsyncMock(return_value=gallery)
    monkeypatch.setattr(
        materials, "MaterialLibraryRepository", lambda *a, **k: SimpleNamespace(get_category_exact=recheck)
    )
    with pytest.raises(HTTPException) as exc:
        await materials.import_material_images(
            object(),
            SimpleNamespace(uid="alice"),
            [UploadFile(file=io.BytesIO(b"invalid image"), filename="new.png")],
            category="shared-rough",
            source_channel="mp",
        )
    assert exc.value.status_code == 400
    assert recheck.await_args.kwargs["visibility"] == "enterprise"
    assert recheck.await_args.kwargs["category_owner_uid"] == gallery.owner_uid


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,mode", [("user", "full"), ("设计师", "rough_upload"), ("admin", "full"), ("superadmin", "full")]
)
@pytest.mark.parametrize("own", [True, False])
async def test_mp_rough_deletion_checks_uploader_for_every_role(monkeypatch, role, mode, own):
    gallery = rough_gallery()
    item = ContentMaterialLibraryItem(
        id="photo",
        owner_uid="alice" if own else "bob",
        material_type="image",
        category=gallery.id,
        category_owner_uid=gallery.owner_uid,
        metadata_json={"ever_shared": True},
    )
    repo = SimpleNamespace(get_item_for_user=AsyncMock(return_value=item), get_category=AsyncMock(return_value=gallery))
    monkeypatch.setattr(materials, "MaterialLibraryRepository", lambda *a, **k: repo)
    monkeypatch.setattr(materials, "material_access_mode", AsyncMock(return_value=mode))
    db = SimpleNamespace(commit=AsyncMock(), add=Mock())
    ctx = SimpleNamespace(user=SimpleNamespace(uid="alice", role=role, id=1))
    if own:
        result = await mp_service.delete_mp_gallery_item(db, ctx, item.id)
        assert result["success"] is True and result["object_deleted"] is False
        assert item.deleted_at is not None
        db.commit.assert_awaited_once()
    else:
        with pytest.raises(HTTPException) as exc:
            await mp_service.delete_mp_gallery_item(db, ctx, item.id)
        assert exc.value.status_code == 403
        assert item.deleted_at is None
        db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_pc_admin_can_remove_foreign_rough_item(monkeypatch):
    gallery = rough_gallery()
    item = ContentMaterialLibraryItem(
        id="photo",
        owner_uid="bob",
        material_type="image",
        category=gallery.id,
        category_owner_uid=gallery.owner_uid,
        metadata_json={"ever_shared": True},
    )
    repo = SimpleNamespace(get_item_for_user=AsyncMock(return_value=item), get_category=AsyncMock(return_value=gallery))
    monkeypatch.setattr(materials, "MaterialLibraryRepository", lambda *a, **k: repo)
    monkeypatch.setattr(materials, "material_access_mode", AsyncMock(return_value="full"))
    db = SimpleNamespace(commit=AsyncMock(), add=Mock())
    result = await materials.delete_material_item(db, SimpleNamespace(uid="alice", role="admin", id=1), item.id)
    assert result["success"] is True and result["object_deleted"] is False
    assert item.deleted_at is not None
    db.commit.assert_awaited_once()
