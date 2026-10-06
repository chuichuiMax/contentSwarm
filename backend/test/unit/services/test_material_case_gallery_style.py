from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from yuxi.services import material_library_service
from yuxi.storage.postgres.models_content import ContentMaterialCategory


@pytest.fixture
def upload_scope(monkeypatch):
    def configure(
        *,
        visibility="enterprise",
        role="reference",
        industry="uncategorized",
        child=False,
        inherited_style=None,
        allowed=True,
    ):
        root = ContentMaterialCategory(
            id="root",
            owner_uid="owner",
            material_type="image",
            name="已改名图库",
            visibility=visibility,
            image_design_role=role,
            industry_slug=industry,
        )
        gallery = (
            ContentMaterialCategory(
                id="child",
                owner_uid="owner",
                material_type="image",
                name="历史下级图库",
                visibility=visibility,
                parent_id=root.id,
                industry_slug=industry,
                design_style=inherited_style,
            )
            if child
            else root
        )
        if not child:
            gallery.design_style = inherited_style
        monkeypatch.setattr(material_library_service, "resolve_material_category", AsyncMock(return_value=gallery))
        monkeypatch.setattr(
            material_library_service,
            "MaterialLibraryRepository",
            lambda *_args, **_kwargs: SimpleNamespace(get_category=AsyncMock(return_value=root)),
        )
        monkeypatch.setattr(material_library_service, "can_contribute_to_category", lambda *_args: allowed)
        return gallery

    return configure


@pytest.mark.asyncio
@pytest.mark.parametrize("child", [False, True])
async def test_enterprise_case_upload_requires_style_even_after_rename(upload_scope, child):
    gallery = upload_scope(child=child)
    with pytest.raises(HTTPException) as error:
        await material_library_service._resolve_upload_category(
            object(),
            SimpleNamespace(uid="owner", department_id=None),
            gallery.id,
            None,
        )
    assert error.value.status_code == 422
    assert error.value.detail["error"]["code"] == "MATERIAL_STYLE_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.parametrize("child", [False, True])
async def test_enterprise_case_upload_keeps_selected_gallery_without_style_routing(upload_scope, child):
    gallery = upload_scope(child=child)
    resolved, style = await material_library_service._resolve_upload_category(
        object(),
        SimpleNamespace(uid="owner", department_id=None),
        gallery.id,
        "雅致现代",
    )
    assert resolved is gallery
    assert style == "雅致现代"


@pytest.mark.asyncio
async def test_enterprise_case_child_can_inherit_its_style(upload_scope):
    gallery = upload_scope(child=True, inherited_style="江南印象")
    resolved, style = await material_library_service._resolve_upload_category(
        object(),
        SimpleNamespace(uid="owner", department_id=None),
        gallery.id,
        None,
    )
    assert resolved is gallery
    assert style == "江南印象"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "visibility,role,child",
    [
        ("enterprise", "rough", False),
        ("enterprise", "generated", False),
        ("enterprise", None, False),
        ("enterprise", None, True),
        ("private", None, False),
        ("private", None, True),
        ("private", "reference", False),
    ],
)
@pytest.mark.parametrize("submitted_style", [None, "雅致现代", "过期风格"])
async def test_other_decoration_galleries_ignore_style_and_upload_directly(
    upload_scope,
    visibility,
    role,
    child,
    submitted_style,
):
    gallery = upload_scope(
        visibility=visibility, role=role, child=child, industry="decoration", inherited_style="江南印象"
    )
    resolved, style = await material_library_service._resolve_upload_category(
        object(),
        SimpleNamespace(uid="owner", department_id=None),
        gallery.id,
        submitted_style,
    )
    assert resolved is gallery
    assert style is None


@pytest.mark.asyncio
async def test_enterprise_case_upload_rejects_invalid_style(upload_scope):
    gallery = upload_scope()
    with pytest.raises(HTTPException) as error:
        await material_library_service._resolve_upload_category(
            object(),
            SimpleNamespace(uid="owner", department_id=None),
            gallery.id,
            "不存在的风格",
        )
    assert error.value.detail["error"]["code"] == "MATERIAL_STYLE_INVALID"


@pytest.mark.asyncio
async def test_style_rule_does_not_bypass_upload_permission(upload_scope):
    gallery = upload_scope(role=None, industry="decoration", allowed=False)
    with pytest.raises(HTTPException) as error:
        await material_library_service._resolve_upload_category(
            object(),
            SimpleNamespace(uid="owner", department_id=None),
            gallery.id,
            None,
        )
    assert error.value.status_code == 403
    assert error.value.detail["error"]["code"] == "MATERIAL_UPLOAD_FORBIDDEN"
