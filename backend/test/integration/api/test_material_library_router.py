from __future__ import annotations

import asyncio
import io
import os
import uuid

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.storage.postgres.models_business import Department, OperationLog, User
from yuxi.utils.auth_utils import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (48, 36), "#4A7BF7").save(output, format="PNG")
    return output.getvalue()


def _poster_png() -> bytes:
    from PIL import ImageDraw

    output = io.BytesIO()
    image = Image.new("RGBA", (1080, 1440), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1079, 1439), outline="#F0522D", width=28)
    draw.rectangle((0, 0, 1079, 180), fill="#252525")
    image.save(output, format="PNG")
    return output.getvalue()


@pytest_asyncio.fixture
async def material_users(test_client):
    suffix = uuid.uuid4().hex[:10]
    password = f"Pw!{uuid.uuid4().hex}"
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        department = Department(name=f"pytest-material-{suffix}", description="material library integration")
        db.add(department)
        await db.flush()
        users = [
            User(
                username=f"pytest_material_owner_{suffix}",
                uid=f"pytest_material_owner_{suffix}",
                password_hash=AuthUtils.hash_password(password),
                role="user",
                department_id=department.id,
            ),
            User(
                username=f"pytest_material_other_{suffix}",
                uid=f"pytest_material_other_{suffix}",
                password_hash=AuthUtils.hash_password(password),
                role="user",
                department_id=department.id,
            ),
        ]
        db.add_all(users)
        await db.flush()
        user_ids = [user.id for user in users]
        department_id = department.id
        credentials = [user.uid for user in users]
        await db.commit()

    headers = []
    for uid in credentials:
        login = await test_client.post("/api/auth/token", data={"username": uid, "password": password})
        assert login.status_code == 200, login.text
        headers.append({"Authorization": f"Bearer {login.json()['access_token']}"})
    try:
        yield {"owner": headers[0], "other": headers[1]}
    finally:
        async with session_factory() as db:
            from yuxi.storage.postgres.models_content import ContentMaterialCategory

            await db.execute(delete(ContentMaterialCategory).where(ContentMaterialCategory.owner_uid.in_(credentials)))
            await db.execute(delete(OperationLog).where(OperationLog.user_id.in_(user_ids)))
            await db.execute(delete(User).where(User.id.in_(user_ids)))
            await db.execute(delete(Department).where(Department.id == department_id))
            await db.commit()
        await engine.dispose()


async def test_material_image_round_trip_uses_private_image_bucket(test_client, material_users):
    owner_headers = material_users["owner"]
    uploaded = await test_client.post(
        "/api/material-library/images/import",
        headers=owner_headers,
        data={"category": "product"},
        files=[("files", ("fixture.png", _png(), "image/png"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    item = uploaded.json()["items"][0]
    assert item["material_type"] == "image"
    assert item["category"] == "product"
    assert item["category_name"] == "产品商品"
    assert "tags" not in item

    from yuxi.storage.postgres.models_content import ContentCoverAsset

    engine = create_async_engine(os.environ["POSTGRES_URL"])
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        asset = await db.get(ContentCoverAsset, item["asset_id"])
        assert asset is not None
        assert asset.bucket_name == "image"
        assert asset.object_name == f"material-library/{asset.owner_uid}/images/{asset.id}/image.png"
    await engine.dispose()

    listed = await test_client.get(
        "/api/material-library/items?material_type=image&category=product&query=fixture",
        headers=owner_headers,
    )
    assert listed.status_code == 200, listed.text
    assert item["id"] in {entry["id"] for entry in listed.json()["items"]}

    categories = await test_client.get(
        "/api/material-library/categories?material_type=image", headers=owner_headers
    )
    assert categories.status_code == 200, categories.text
    assert categories.json()["categories"][0]["code"] == "product"
    cover_categories, cover_categories_again = await asyncio.gather(
        test_client.get(
            "/api/material-library/categories?material_type=cover_template",
            headers=owner_headers,
        ),
        test_client.get(
            "/api/material-library/categories?material_type=cover_template",
            headers=owner_headers,
        ),
    )
    assert cover_categories.status_code == 200, cover_categories.text
    assert cover_categories_again.status_code == 200, cover_categories_again.text
    assert {entry["code"] for entry in cover_categories.json()["categories"]} >= {
        "brand",
        "uncategorized",
    }
    galleries = await test_client.get("/api/material-library/galleries", headers=owner_headers)
    assert galleries.status_code == 200, galleries.text
    product_gallery = next(entry for entry in galleries.json()["galleries"] if entry["code"] == "product")
    assert product_gallery["count"] >= 1
    assert product_gallery["cover_item_id"] == item["id"]

    downloaded = await test_client.get(item["file_url"], headers=owner_headers)
    assert downloaded.status_code == 200, downloaded.text
    with Image.open(io.BytesIO(downloaded.content)) as image:
        assert image.size == (48, 36)

    thumbnail = await test_client.get(
        f"/api/material-library/items/{item['id']}/thumbnail", headers=owner_headers
    )
    assert thumbnail.status_code == 200, thumbnail.text
    assert thumbnail.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(thumbnail.content)) as image:
        assert image.size == (48, 36)

    private = await test_client.get(item["file_url"], headers=material_users["other"])
    assert private.status_code == 404

    deleted = await test_client.delete(f"/api/material-library/items/{item['id']}", headers=owner_headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["object_deleted"] is True
    missing = await test_client.get(item["file_url"], headers=owner_headers)
    assert missing.status_code == 404


async def test_material_import_rejects_free_form_or_missing_category(test_client, material_users):
    for data in ({}, {"category": "不存在的图库"}):
        response = await test_client.post(
            "/api/material-library/images/import",
            headers=material_users["owner"],
            data=data,
            files=[("files", ("fixture.png", _png(), "image/png"))],
        )
        assert response.status_code == 422, response.text


async def test_image_gallery_crud_and_safe_item_reassignment(test_client, material_users):
    headers = material_users["owner"]
    blank = await test_client.post(
        "/api/material-library/categories",
        headers=headers,
        json={"material_type": "image", "name": "   "},
    )
    assert blank.status_code == 422, blank.text
    created = await test_client.post(
        "/api/material-library/categories",
        headers=headers,
        json={"material_type": "image", "name": "春季新品", "description": "三月新品图片"},
    )
    assert created.status_code == 201, created.text
    gallery = created.json()["category"]
    assert gallery["count"] == 0

    other_categories = await test_client.get(
        "/api/material-library/categories?material_type=image",
        headers=material_users["other"],
    )
    assert other_categories.status_code == 200, other_categories.text
    assert gallery["id"] not in {item["id"] for item in other_categories.json()["categories"]}
    other_update = await test_client.patch(
        f"/api/material-library/categories/{gallery['id']}?material_type=image",
        headers=material_users["other"],
        json={"name": "越权修改"},
    )
    assert other_update.status_code == 404, other_update.text

    protected = await test_client.request(
        "DELETE",
        "/api/material-library/categories/uncategorized?material_type=image",
        headers=headers,
        json={"target_category_id": "product"},
    )
    assert protected.status_code == 409, protected.text

    duplicate = await test_client.post(
        "/api/material-library/categories",
        headers=headers,
        json={"material_type": "image", "name": "春季新品"},
    )
    assert duplicate.status_code == 409, duplicate.text

    renamed = await test_client.patch(
        f"/api/material-library/categories/{gallery['id']}?material_type=image",
        headers=headers,
        json={"name": "春季上新", "description": "春季新品与商品细节"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["category"]["name"] == "春季上新"

    uploaded = await test_client.post(
        "/api/material-library/images/import",
        headers=headers,
        data={"category": gallery["id"]},
        files=[("files", ("spring.png", _png(), "image/png"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    item = uploaded.json()["items"][0]
    try:
        removed = await test_client.request(
            "DELETE",
            f"/api/material-library/categories/{gallery['id']}?material_type=image",
            headers=headers,
            json={"target_category_id": "uncategorized"},
        )
        assert removed.status_code == 200, removed.text
        assert removed.json()["moved"] == 1
        listed = await test_client.get(
            "/api/material-library/items?material_type=image&category=uncategorized&query=spring",
            headers=headers,
        )
        assert [entry["id"] for entry in listed.json()["items"]] == [item["id"]]
    finally:
        await test_client.delete(f"/api/material-library/items/{item['id']}", headers=headers)


async def test_image_gallery_supports_exactly_one_nested_level(test_client, material_users):
    headers = material_users["owner"]
    parent_response = await test_client.post(
        "/api/material-library/categories",
        headers=headers,
        json={"material_type": "image", "name": "项目案例", "industry_slug": "decoration"},
    )
    assert parent_response.status_code == 201, parent_response.text
    parent = parent_response.json()["category"]
    child_response = await test_client.post(
        "/api/material-library/categories",
        headers=headers,
        json={
            "material_type": "image",
            "name": "客厅案例",
            "description": "项目案例中的客厅图片",
            "parent_id": parent["id"],
        },
    )
    assert child_response.status_code == 201, child_response.text
    child = child_response.json()["category"]
    assert child["parent_id"] == parent["id"]
    assert child["level"] == 2
    assert child["industry_slug"] == "decoration"

    mismatched_child = await test_client.post(
        "/api/material-library/categories",
        headers=headers,
        json={
            "material_type": "image",
            "name": "跨行业子图库",
            "parent_id": parent["id"],
            "industry_slug": "education",
        },
    )
    assert mismatched_child.status_code == 422, mismatched_child.text
    assert mismatched_child.json()["detail"]["error"]["code"] == "MATERIAL_INDUSTRY_INHERITED"

    grandchild = await test_client.post(
        "/api/material-library/categories",
        headers=headers,
        json={"material_type": "image", "name": "第三级", "parent_id": child["id"]},
    )
    assert grandchild.status_code == 422, grandchild.text
    assert grandchild.json()["detail"]["error"]["code"] == "MATERIAL_CATEGORY_DEPTH_INVALID"

    system_child = await test_client.post(
        "/api/material-library/categories",
        headers=headers,
        json={"material_type": "image", "name": "兜底子图库", "parent_id": "uncategorized"},
    )
    assert system_child.status_code == 422, system_child.text

    cover_child = await test_client.post(
        "/api/material-library/categories",
        headers=headers,
        json={"material_type": "cover_template", "name": "模板子分类", "parent_id": parent["id"]},
    )
    assert cover_child.status_code == 422, cover_child.text

    uploaded = await test_client.post(
        "/api/material-library/images/import",
        headers=headers,
        data={"category": child["id"]},
        files=[("files", ("living-room.png", _png(), "image/png"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    item = uploaded.json()["items"][0]
    try:
        galleries_response = await test_client.get("/api/material-library/galleries", headers=headers)
        assert galleries_response.status_code == 200, galleries_response.text
        galleries = {entry["id"]: entry for entry in galleries_response.json()["galleries"]}
        assert galleries[parent["id"]]["count"] == 1
        assert galleries[parent["id"]]["direct_count"] == 0
        assert galleries[parent["id"]]["child_count"] == 1
        assert galleries[child["id"]]["count"] == 1
        assert galleries[parent["id"]]["industry_name"] == "装修与家居"
        assert galleries[child["id"]]["industry_slug"] == "decoration"

        decoration_response = await test_client.get(
            "/api/material-library/galleries?industry_slug=decoration", headers=headers
        )
        assert decoration_response.status_code == 200, decoration_response.text
        decoration_ids = {entry["id"] for entry in decoration_response.json()["galleries"]}
        assert {parent["id"], child["id"], "uncategorized"} <= decoration_ids

        changed = await test_client.patch(
            f"/api/material-library/categories/{parent['id']}?material_type=image",
            headers=headers,
            json={"industry_slug": "professional-services"},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["category"]["industry_slug"] == "professional-services"
        categories_response = await test_client.get(
            "/api/material-library/categories?material_type=image", headers=headers
        )
        categories = {entry["id"]: entry for entry in categories_response.json()["categories"]}
        assert categories[child["id"]]["industry_slug"] == "professional-services"

        blocked = await test_client.request(
            "DELETE",
            f"/api/material-library/categories/{parent['id']}?material_type=image",
            headers=headers,
            json={"target_category_id": "uncategorized"},
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["error"]["code"] == "MATERIAL_CATEGORY_HAS_CHILDREN"

        removed_child = await test_client.request(
            "DELETE",
            f"/api/material-library/categories/{child['id']}?material_type=image",
            headers=headers,
            json={"target_category_id": parent["id"]},
        )
        assert removed_child.status_code == 200, removed_child.text
        assert removed_child.json()["moved"] == 1
        listed = await test_client.get(
            f"/api/material-library/items?material_type=image&category={parent['id']}",
            headers=headers,
        )
        assert item["id"] in {entry["id"] for entry in listed.json()["items"]}

        removed_parent = await test_client.request(
            "DELETE",
            f"/api/material-library/categories/{parent['id']}?material_type=image",
            headers=headers,
            json={"target_category_id": "uncategorized"},
        )
        assert removed_parent.status_code == 200, removed_parent.text
    finally:
        await test_client.delete(f"/api/material-library/items/{item['id']}", headers=headers)


async def test_cover_mask_is_stored_but_not_listed_as_library_template(test_client, material_users):
    headers = material_users["owner"]
    uploaded = await test_client.post(
        "/api/content/covers/assets",
        headers=headers,
        data={"role": "mask"},
        files={"file": ("mask.png", _png(), "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    asset = uploaded.json()["asset"]
    try:
        listed = await test_client.get(
            "/api/material-library/items?material_type=cover_template",
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        assert asset["id"] not in {item["asset_id"] for item in listed.json()["items"]}
    finally:
        deleted = await test_client.delete(f"/api/content/covers/assets/{asset['id']}", headers=headers)
        assert deleted.status_code == 200, deleted.text


async def test_poster_template_uses_controlled_category_without_tags(test_client, material_users):
    headers = material_users["owner"]
    category_response = await test_client.post(
        "/api/material-library/categories",
        headers=headers,
        json={"material_type": "cover_template", "name": "客户案例", "description": "案例复盘封面"},
    )
    assert category_response.status_code == 201, category_response.text
    category = category_response.json()["category"]
    imported = await test_client.post(
        "/api/content/covers/poster-templates/import",
        headers=headers,
        data={"category": category["id"]},
        files=[("files", ("poster.png", _poster_png(), "image/png"))],
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["summary"]["created"] == 1
    template = imported.json()["items"][0]["template"]
    assert template["category"] == category["id"]
    assert template["category_name"] == "客户案例"
    assert "tags" not in template
    try:
        updated = await test_client.patch(
            f"/api/content/covers/poster-templates/{template['id']}",
            headers=headers,
            json={"name": "自定义分类模板", "category": category["id"]},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["template"]["category_name"] == "客户案例"

        removed_category = await test_client.request(
            "DELETE",
            f"/api/material-library/categories/{category['id']}?material_type=cover_template",
            headers=headers,
            json={"target_category_id": "uncategorized"},
        )
        assert removed_category.status_code == 200, removed_category.text
        assert removed_category.json()["moved"] == 1

        listed = await test_client.get(
            "/api/material-library/items?material_type=cover_template&category=uncategorized&query=自定义分类",
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        assert [item["asset_id"] for item in listed.json()["items"]] == [template["asset_id"]]
    finally:
        deleted = await test_client.delete(
            f"/api/content/covers/poster-templates/{template['id']}", headers=headers
        )
        assert deleted.status_code == 200, deleted.text
