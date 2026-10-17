from types import SimpleNamespace

import pytest

from yuxi.services.material_folder_permissions import (
    list_material_folder_permission_entries,
    LEGACY_ROUGH_UPLOAD_PERMISSION,
    grant_allows_folder_upload,
    grant_allows_folder_view,
    is_valid_material_grant_key,
    material_folder_upload_key,
    material_folder_view_key,
)


def test_material_folder_grant_keys_validate():
    folder_id = "enterprise-initial-rough"
    assert is_valid_material_grant_key(material_folder_view_key(folder_id))
    assert is_valid_material_grant_key(material_folder_upload_key(folder_id))
    assert is_valid_material_grant_key(LEGACY_ROUGH_UPLOAD_PERMISSION)
    assert not is_valid_material_grant_key("material_folder.bad")


def test_legacy_rough_upload_keeps_designer_rough_access():
    rough = SimpleNamespace(
        material_type="image",
        visibility="enterprise",
        parent_id=None,
        image_design_role="rough",
        name="毛坯房图库",
        id="enterprise-initial-rough",
    )
    grants = [LEGACY_ROUGH_UPLOAD_PERMISSION]
    assert grant_allows_folder_upload(grants, rough, legacy_may_upload=lambda _c: True)
    assert grant_allows_folder_view(
        grants,
        rough,
        legacy_may_view=lambda _c: True,
        legacy_may_browse_case=lambda _c, _p: False,
    )


def test_explicit_folder_grants_require_matching_keys():
    rough = SimpleNamespace(
        material_type="image",
        visibility="enterprise",
        parent_id=None,
        image_design_role="rough",
        name="毛坯房图库",
        id="enterprise-initial-rough",
    )
    case = SimpleNamespace(
        material_type="image",
        visibility="enterprise",
        parent_id=None,
        image_design_role="reference",
        name="案例图库",
        id="enterprise-initial-reference",
    )
    grants = [material_folder_view_key(case.id)]
    assert grant_allows_folder_view(
        grants,
        case,
        legacy_may_view=lambda _c: False,
        legacy_may_browse_case=lambda _c, _p: False,
    )
    assert not grant_allows_folder_upload(
        grants,
        case,
        legacy_may_upload=lambda _c: True,
    )
    assert not grant_allows_folder_view(
        grants,
        rough,
        legacy_may_view=lambda _c: True,
        legacy_may_browse_case=lambda _c, _p: False,
    )


def test_personal_materials_stay_available_without_folder_grants():
    uploads = SimpleNamespace(
        material_type="image",
        visibility="private",
        parent_id=None,
        id="mp-uploads-private",
        name="我的上传",
    )
    grants = [material_folder_view_key("enterprise-initial-reference")]
    assert grant_allows_folder_view(
        grants,
        uploads,
        legacy_may_view=lambda _c: False,
        legacy_may_browse_case=lambda _c, _p: False,
    )
    assert grant_allows_folder_upload(grants, uploads, legacy_may_upload=lambda _c: False)


@pytest.mark.asyncio
async def test_permission_catalog_skips_secondary_galleries(monkeypatch):
    parent = SimpleNamespace(
        id="case",
        parent_id=None,
        name="案例图库",
        visibility="enterprise",
        sort_order=1,
        material_type="image",
        owner_uid="enterprise",
        is_system=True,
    )
    child = SimpleNamespace(
        id="child",
        parent_id="case",
        name="桂语云峰",
        visibility="enterprise",
        sort_order=1,
        material_type="image",
        owner_uid="enterprise",
        is_system=False,
    )

    class _Repository:
        def __init__(self, db, include_shared=True):
            del db, include_shared

        async def list_categories(self, owner_uid, material_type):
            del owner_uid, material_type
            return [parent, child]

    async def _ensure(_db):
        return None

    monkeypatch.setattr(
        "yuxi.services.material_folder_permissions.MaterialLibraryRepository",
        _Repository,
    )
    monkeypatch.setattr(
        "yuxi.services.material_library_service.ensure_initial_enterprise_galleries",
        _ensure,
    )
    entries = await list_material_folder_permission_entries(None)
    labels = [entry["list"] for entry in entries]
    assert "素材库-企业共享-案例图库" in labels
    assert all("桂语云峰" not in label for label in labels)
    assert all("我的素材" not in label for label in labels)
