"""真实 API 校验企业案例图库的上传风格规则。"""

import pytest
from test.integration.api import test_material_library_router as material_fixtures

from test.integration.api.test_material_library_router import (
    _insert_existing_child_gallery,
    _png,
)

material_users = material_fixtures.material_users

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.mark.parametrize(
    "visibility,role,child",
    [
        ("enterprise", "reference", False),
        ("enterprise", "reference", True),
        ("enterprise", "rough", False),
        ("enterprise", "generated", False),
        ("enterprise", None, False),
        ("private", None, False),
        ("private", None, True),
    ],
)
async def test_pc_upload_requires_style_only_for_enterprise_case_gallery(
    test_client,
    material_users,  # noqa: F811
    visibility,
    role,
    child,
):
    headers = material_users["owner"]
    if role:
        response = await test_client.get(
            "/api/material-library/categories?material_type=image",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        gallery = next(
            item
            for item in response.json()["categories"]
            if item["visibility"] == "enterprise" and item["image_design_role"] == role and not item["parent_id"]
        )
    else:
        response = await test_client.post(
            "/api/material-library/categories",
            headers=headers,
            json={
                "material_type": "image",
                "name": "风格范围测试",
                "visibility": visibility,
                "industry_slug": "decoration",
            },
        )
        assert response.status_code == 201, response.text
        gallery = response.json()["category"]
    if child:
        gallery = await _insert_existing_child_gallery(material_users, gallery)

    payload = {"category": gallery["id"]}
    upload = await test_client.post(
        "/api/material-library/images/import",
        headers=headers,
        data=payload,
        files=[("files", ("style.png", _png(), "image/png"))],
    )
    if role == "reference":
        assert upload.status_code == 422, upload.text
        assert upload.json()["detail"]["error"]["code"] == "MATERIAL_STYLE_REQUIRED"
        payload["design_style"] = "雅致现代"
        upload = await test_client.post(
            "/api/material-library/images/import",
            headers=headers,
            data=payload,
            files=[("files", ("style.png", _png(), "image/png"))],
        )
    assert upload.status_code == 201, upload.text
    item = upload.json()["items"][0]
    assert item["category"] == gallery["id"]
    assert item["design_style"] == ("雅致现代" if role == "reference" else None)
