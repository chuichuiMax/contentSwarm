from __future__ import annotations

import pytest
from unittest.mock import AsyncMock
from fastapi import HTTPException
from pydantic import ValidationError

from yuxi.image_design.mp_schemas import SaveTarget
from yuxi.image_design.save_targets import (
    ENTERPRISE_ROOT_CATEGORY_ID,
    ENTERPRISE_ROOT_OWNER_UID,
    PRIVATE_ROOT_CATEGORY_ID,
    list_writable_save_targets,
    resolve_mp_save_target,
    resolve_writable_save_target,
    list_mp_save_targets,
    validate_mp_save_target,
)
from yuxi.storage.postgres.models_content import ContentMaterialCategory


def _category(
    owner_uid: str,
    category_id: str,
    *,
    visibility: str,
    name: str,
    parent_id: str | None = None,
    is_system: bool = False,
) -> ContentMaterialCategory:
    return ContentMaterialCategory(
        owner_uid=owner_uid,
        material_type="image",
        id=category_id,
        visibility=visibility,
        name=name,
        parent_id=parent_id,
        is_system=is_system,
    )


class FakeMaterialLibraryRepository:
    categories: list[ContentMaterialCategory] = []

    def __init__(self, _db, *, include_shared: bool = False):
        self.include_shared = include_shared

    async def migrate_generated_private_root(self, owner_uid, root_id):
        pass

    async def ensure_default_categories(self, values):
        existing = {(item.owner_uid, item.material_type, item.id) for item in self.categories}
        for value in values:
            key = (value["owner_uid"], value["material_type"], value["id"])
            if key not in existing:
                self.categories.append(ContentMaterialCategory(**value))
                existing.add(key)

    async def sync_system_categories(self, values):
        existing = {(item.owner_uid, item.material_type, item.id): item for item in self.categories}
        for value in values:
            key = (value["owner_uid"], value["material_type"], value["id"])
            if key not in existing:
                self.categories.append(ContentMaterialCategory(**value))
                continue
            category = existing[key]
            for field, field_value in value.items():
                setattr(category, field, field_value)
            category.deleted_at = None

    async def get_category_exact(
        self,
        *,
        requester_uid: str,
        material_type: str,
        category_id: str,
        category_owner_uid: str | None = None,
        visibility: str | None = None,
        for_update: bool = False,
    ):
        for category in self.categories:
            if category.material_type != material_type or category.id != category_id:
                continue
            if category_owner_uid is not None and category.owner_uid != category_owner_uid:
                continue
            if visibility is not None and category.visibility != visibility:
                continue
            if category.owner_uid == requester_uid or category.visibility == "enterprise":
                return category
        return None

    async def list_categories(self, owner_uid: str, material_type: str):
        return sorted(
            (
                category
                for category in self.categories
                if category.material_type == material_type
                and (category.owner_uid == owner_uid or category.visibility == "enterprise")
            ),
            key=lambda category: (category.sort_order is None, category.sort_order or 0),
        )


@pytest.fixture
def ordinary_user():
    return type("User", (), {"uid": "ordinary-user", "department_id": "department-1", "role": "employee"})()


@pytest.fixture(autouse=True)
def fake_repository(monkeypatch):
    from yuxi.image_design import save_targets
    from yuxi.services import personal_materials

    FakeMaterialLibraryRepository.categories = []
    monkeypatch.setattr(save_targets, "MaterialLibraryRepository", FakeMaterialLibraryRepository)
    monkeypatch.setattr(personal_materials, "MaterialLibraryRepository", FakeMaterialLibraryRepository)
    monkeypatch.setattr(personal_materials, "load_personal_gallery_settings", AsyncMock(return_value={}))
    monkeypatch.setattr(save_targets, "load_personal_gallery_settings", AsyncMock(return_value={}))


def test_root_target_normalizes_missing_gallery_id():
    target = SaveTarget.model_validate({"scope": "enterprise"})

    assert target.gallery_id is None
    assert target.model_dump() == {"scope": "enterprise", "gallery_id": None}


def test_save_target_rejects_blank_gallery_id_and_internal_fields():
    with pytest.raises(ValidationError):
        SaveTarget.model_validate({"scope": "private", "gallery_id": " "})
    with pytest.raises(ValidationError):
        SaveTarget.model_validate(
            {"scope": "enterprise", "gallery_id": None, "category_owner_uid": "system:material-library"}
        )


@pytest.mark.asyncio
async def test_employee_can_resolve_enterprise_root(ordinary_user):
    resolved = await resolve_writable_save_target(
        object(), ordinary_user, SaveTarget(scope="enterprise", gallery_id=None)
    )

    assert resolved.category_owner_uid == ENTERPRISE_ROOT_OWNER_UID
    assert resolved.category_id == ENTERPRISE_ROOT_CATEGORY_ID
    assert resolved.public_target == {"scope": "enterprise", "gallery_id": None}


@pytest.mark.asyncio
async def test_private_scope_rejects_another_users_folder(ordinary_user):
    FakeMaterialLibraryRepository.categories = [
        _category("another-user", "another-private", visibility="private", name="他人的素材")
    ]

    with pytest.raises(HTTPException) as error:
        await resolve_writable_save_target(
            object(), ordinary_user, SaveTarget(scope="private", gallery_id="another-private")
        )

    assert error.value.detail["error"]["code"] == "IMAGE_DESIGN_SAVE_TARGET_INVALID"


@pytest.mark.asyncio
async def test_visible_folder_in_another_scope_returns_scope_mismatch(ordinary_user):
    FakeMaterialLibraryRepository.categories = [
        _category(
            ENTERPRISE_ROOT_OWNER_UID,
            "enterprise-folder",
            visibility="enterprise",
            name="企业图库",
        )
    ]

    with pytest.raises(HTTPException) as error:
        await resolve_writable_save_target(
            object(), ordinary_user, SaveTarget(scope="private", gallery_id="enterprise-folder")
        )

    assert error.value.detail["error"]["code"] == "IMAGE_DESIGN_SAVE_TARGET_SCOPE_MISMATCH"


@pytest.mark.asyncio
async def test_corrupt_system_root_is_not_accepted_as_a_save_target(ordinary_user):
    FakeMaterialLibraryRepository.categories = [
        _category(
            ENTERPRISE_ROOT_OWNER_UID,
            ENTERPRISE_ROOT_CATEGORY_ID,
            visibility="enterprise",
            name="伪造企业根目录",
            is_system=False,
        )
    ]

    with pytest.raises(HTTPException) as error:
        await resolve_writable_save_target(object(), ordinary_user, SaveTarget(scope="enterprise", gallery_id=None))

    assert error.value.detail["error"]["code"] == "IMAGE_DESIGN_SAVE_TARGET_INVALID"


@pytest.mark.asyncio
async def test_writable_target_list_hides_storage_roots_and_keeps_scope_paths(ordinary_user):
    FakeMaterialLibraryRepository.categories = [
        _category("ordinary-user", PRIVATE_ROOT_CATEGORY_ID, visibility="private", name="未分类", is_system=True),
        _category(
            ENTERPRISE_ROOT_OWNER_UID,
            ENTERPRISE_ROOT_CATEGORY_ID,
            visibility="enterprise",
            name="企业根目录",
            is_system=True,
        ),
        _category("ordinary-user", "private-parent", visibility="private", name="我的一级图库"),
        _category(
            "ordinary-user",
            "private-child",
            visibility="private",
            name="我的二级图库",
            parent_id="private-parent",
        ),
        _category("another-user", "other-private", visibility="private", name="别人的图库"),
        _category(ENTERPRISE_ROOT_OWNER_UID, "enterprise-parent", visibility="enterprise", name="企业一级图库"),
        _category(
            ENTERPRISE_ROOT_OWNER_UID,
            "enterprise-child",
            visibility="enterprise",
            name="企业二级图库",
            parent_id="enterprise-parent",
        ),
    ]

    payload = await list_writable_save_targets(object(), ordinary_user)

    assert [scope["scope"] for scope in payload["scopes"]] == ["private", "enterprise"]
    assert all(scope["can_write_root"] is True for scope in payload["scopes"])
    private, enterprise = payload["scopes"]
    assert private["label"] == "我的素材"
    assert enterprise["label"] == "企业图库"
    private_by_id = {folder["id"]: folder for folder in private["folders"]}
    assert "mp-generated-private" in private_by_id
    assert "uncategorized" not in private_by_id
    assert private_by_id["private-parent"]["path"] == "我的一级图库"
    assert private_by_id["private-child"]["path"] == "我的一级图库 / 我的二级图库"
    assert "other-private" not in private_by_id
    assert enterprise["folders"] == [
        {"id": "enterprise-parent", "name": "企业一级图库", "parent_id": None, "path": "企业一级图库"},
        {
            "id": "enterprise-child",
            "name": "企业二级图库",
            "parent_id": "enterprise-parent",
            "path": "企业一级图库 / 企业二级图库",
        },
    ]
    folder_ids = {folder["id"] for scope in payload["scopes"] for folder in scope["folders"]}
    assert ENTERPRISE_ROOT_CATEGORY_ID not in folder_ids


@pytest.mark.asyncio
async def test_mp_targets_use_both_real_destinations(ordinary_user):
    db = object()
    FakeMaterialLibraryRepository.categories = [
        _category("ordinary-user", "product", visibility="private", name="AI生图图库"),
        _category("admin", "generated-real-id", visibility="enterprise", name="生图图库"),
        _category("admin", "case", visibility="enterprise", name="案例图库"),
    ]
    scopes = (await list_mp_save_targets(db, ordinary_user))["scopes"]
    assert [s["scope"] for s in scopes] == ["private", "enterprise"]
    assert scopes[0]["folders"][0]["id"] == "product"
    assert scopes[1]["folders"][0]["id"] == "generated-real-id"
    for gallery_id in (None, "private-root", "product"):
        saved = await resolve_mp_save_target(db, ordinary_user, SaveTarget(scope="private", gallery_id=gallery_id))
        assert saved.public_target == {"scope": "private", "gallery_id": "product"}
        assert saved.category_owner_uid == "ordinary-user"
    saved = await resolve_mp_save_target(
        db, ordinary_user, SaveTarget(scope="enterprise", gallery_id="generated-real-id")
    )
    assert saved.public_target == {"scope": "enterprise", "gallery_id": "generated-real-id"}
    assert saved.category_owner_uid == "admin"
    for target in [
        SaveTarget(scope="enterprise"),
        SaveTarget(scope="enterprise", gallery_id="case"),
        SaveTarget(scope="private", gallery_id="uncategorized"),
    ]:
        with pytest.raises(HTTPException):
            await validate_mp_save_target(db, ordinary_user, target)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 2])
async def test_missing_or_ambiguous_enterprise_is_unavailable_and_never_redirected(ordinary_user, count):
    FakeMaterialLibraryRepository.categories = [
        _category(f"admin-{i}", f"generated-{i}", visibility="enterprise", name="生图图库") for i in range(count)
    ]
    scopes = (await list_mp_save_targets(object(), ordinary_user))["scopes"]
    assert scopes[0]["folders"][0]["id"] == "mp-generated-private"
    assert scopes[1]["folders"] == [] and scopes[1]["error"]
    assert scopes[1]["can_write_root"] is False
    with pytest.raises(HTTPException):
        await resolve_mp_save_target(object(), ordinary_user, SaveTarget(scope="enterprise", gallery_id="generated-0"))


@pytest.mark.asyncio
async def test_old_product_gallery_is_preserved_and_independent_ai_gallery_is_provisioned(ordinary_user):
    old = _category("ordinary-user", "product", visibility="private", name="产品商品")
    other = _category("other-user", "product", visibility="private", name="AI生图图库")
    FakeMaterialLibraryRepository.categories = [old, other]
    saved = await resolve_mp_save_target(object(), ordinary_user, SaveTarget(scope="private"))
    assert saved.category_id == "mp-generated-private"
    assert saved.category_owner_uid == "ordinary-user"
    assert old.name == "产品商品" and old.id == "product"
    assert other.owner_uid == "other-user" and other.name == "AI生图图库"
    await resolve_mp_save_target(object(), ordinary_user, SaveTarget(scope="private"))
    assert len([c for c in FakeMaterialLibraryRepository.categories if c.id == "mp-generated-private"]) == 1
    with pytest.raises(HTTPException):
        await resolve_mp_save_target(object(), ordinary_user, SaveTarget(scope="private", gallery_id="product"))
