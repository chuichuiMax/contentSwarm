from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from yuxi.services import material_library_service
from yuxi.storage.postgres.models_content import ContentMaterialCategory


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["毛坯房图库", "我的上传"])
async def test_personal_presets_upload_directly_without_project_style_children(monkeypatch, name):
    gallery = ContentMaterialCategory(
        owner_uid="owner",
        id="existing",
        material_type="image",
        visibility="private",
        name=name,
        industry_slug="decoration",
    )
    monkeypatch.setattr(material_library_service, "resolve_material_category", AsyncMock(return_value=gallery))
    resolved, style = await material_library_service._resolve_upload_category(
        object(), SimpleNamespace(uid="owner", department_id=None), "existing", None
    )
    assert resolved is gallery
    assert style is None


@pytest.mark.asyncio
async def test_pc_uncategorized_upload_keeps_private_category(monkeypatch):
    category = SimpleNamespace(
        id="uncategorized",
        owner_uid="owner",
        visibility="private",
        deleted_at=None,
        image_design_role=None,
        name="未分类",
    )
    resolve = AsyncMock(return_value=(category, None))
    recheck = AsyncMock(return_value=category)
    monkeypatch.setattr(material_library_service, "_resolve_upload_category", resolve)
    monkeypatch.setattr(
        material_library_service,
        "MaterialLibraryRepository",
        lambda *_args, **_kwargs: SimpleNamespace(get_category_exact=recheck),
    )

    with pytest.raises(HTTPException) as error:
        await material_library_service.import_material_images(
            object(),
            SimpleNamespace(uid="owner", role="user"),
            [SimpleNamespace(filename="")],
            category="uncategorized",
        )

    assert error.value.status_code == 400
    assert recheck.await_args.kwargs["category_owner_uid"] == "owner"
    assert recheck.await_args.kwargs["visibility"] == "private"


@pytest.mark.asyncio
async def test_pc_fallback_created_after_mini_program_gallery(monkeypatch):
    categories = [SimpleNamespace(owner_uid="owner", id="mp-uploads-private", is_system=True)]
    normalized = []

    class Repository:
        async def list_categories(self, _owner_uid, _material_type):
            return categories

        async def ensure_default_categories(self, values):
            categories.extend(SimpleNamespace(**value) for value in values)

        async def normalize_orphan_categories(self, *_args):
            normalized.append(_args[-1])

    monkeypatch.setattr(material_library_service, "MaterialLibraryRepository", lambda _db: Repository())
    monkeypatch.setattr("yuxi.services.personal_materials.folder_categories", AsyncMock(return_value={}))
    result = await material_library_service.ensure_material_categories(
        object(), owner_uid="owner", tenant_id=None, material_type="image"
    )

    assert {category.id for category in result} == {"mp-uploads-private", "uncategorized"}
    assert normalized == ["uncategorized"]
