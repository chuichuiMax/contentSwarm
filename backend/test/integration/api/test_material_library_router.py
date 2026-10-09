from __future__ import annotations

import asyncio
import io
import os
import re
import uuid

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.material_upload_queue import material_thumb_object_name
from yuxi.storage.minio.client import MinIOClient
from yuxi.storage.postgres.models_business import Department, OperationLog, User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentCoverPosterTemplate,
    ContentEmployee,
    ContentMaterialCategory,
    ContentMaterialFolderSetting,
    ContentMaterialLibraryItem,
    ContentMaterialShare,
    ContentMaterialShareItem,
    ContentMaterialUsage,
)
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
                role="superadmin",
                department_id=department.id,
            ),
            User(
                username=f"pytest_material_other_{suffix}",
                uid=f"pytest_material_other_{suffix}",
                password_hash=AuthUtils.hash_password(password),
                role="superadmin",
                department_id=department.id,
            ),
            User(
                username=f"pytest_material_member_{suffix}",
                uid=f"pytest_material_member_{suffix}",
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
    for uid in credentials[:2]:
        login = await test_client.post("/api/auth/token", data={"username": uid, "password": password})
        assert login.status_code == 200, login.text
        headers.append({"Authorization": f"Bearer {login.json()['access_token']}"})
    # This fixture tests gallery permissions, not employee credential provisioning.
    # Independent non-employee members cannot use the employee-only PC login path.
    member_token = AuthUtils.create_access_token({"sub": str(user_ids[2])})
    headers.append({"Authorization": f"Bearer {member_token}"})
    try:
        yield {
            "session_factory": session_factory,
            "owner": headers[0],
            "other": headers[1],
            "member": headers[2],
            "owner_uid": credentials[0],
            "department_id": department_id,
        }
    finally:
        async with session_factory() as db:
            assets = list(
                (
                    await db.execute(select(ContentCoverAsset).where(ContentCoverAsset.owner_uid.in_(credentials)))
                ).scalars()
            )
            share_objects = list(
                (
                    await db.execute(
                        select(ContentMaterialShareItem.bucket_name, ContentMaterialShareItem.object_name)
                        .join(ContentMaterialShare, ContentMaterialShare.id == ContentMaterialShareItem.share_id)
                        .where(ContentMaterialShare.owner_uid.in_(credentials))
                    )
                ).all()
            )
            share_tokens = list((await db.execute(
                select(ContentMaterialShare.token).where(ContentMaterialShare.owner_uid.in_(credentials))
            )).scalars())
            await db.execute(delete(ContentMaterialShare).where(ContentMaterialShare.owner_uid.in_(credentials)))
            await db.execute(
                delete(ContentCoverPosterTemplate).where(ContentCoverPosterTemplate.owner_uid.in_(credentials))
            )
            await db.execute(delete(ContentMaterialUsage).where(ContentMaterialUsage.user_uid.in_(credentials)))
            await db.execute(
                delete(ContentMaterialLibraryItem).where(ContentMaterialLibraryItem.owner_uid.in_(credentials))
            )
            await db.execute(delete(ContentCoverAsset).where(ContentCoverAsset.owner_uid.in_(credentials)))
            await db.execute(delete(ContentMaterialCategory).where(ContentMaterialCategory.owner_uid.in_(credentials)))
            await db.execute(delete(OperationLog).where(OperationLog.user_id.in_(user_ids)))
            await db.execute(delete(User).where(User.id.in_(user_ids)))
            await db.execute(delete(Department).where(Department.id == department_id))
            await db.commit()
        storage = MinIOClient()
        for bucket, object_name in share_objects:
            await storage.adelete_file(bucket, object_name)
            await storage.adelete_file(bucket, f"{object_name}.display.webp")
            await storage.adelete_file(bucket, f"{object_name}.card.jpg")
        for asset in assets:
            await storage.adelete_file(asset.bucket_name, asset.object_name)
            await storage.adelete_file(asset.bucket_name, material_thumb_object_name(asset.object_name))
            await storage.adelete_objects_by_prefix(asset.bucket_name, f"{asset.object_name}.share-v1.")
        from redis.asyncio import Redis

        redis = Redis.from_url(os.environ["REDIS_URL"])
        for token in share_tokens:
            keys = [key async for key in redis.scan_iter(match=f"material-share:v1:{token}:*")]
            if keys:
                await redis.delete(*keys)
        await redis.aclose()
        await engine.dispose()


@pytest_asyncio.fixture
async def material_mp_headers(material_users):
    """Bind a temporary app employee to this fixture's existing material owner."""
    employee_id = uuid.uuid4().hex
    phone = f"199{int(uuid.uuid4().hex[:10], 16) % 100000000:08d}"
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        owner = (await db.execute(select(User).where(User.uid == material_users["owner_uid"]))).scalar_one()
        owner.phone_number = phone
        db.add(
            ContentEmployee(
                id=employee_id,
                employee_code=f"pytest_share_{employee_id}",
                name="分享测试员工",
                login_account=phone,
                gender="女",
                login_port=["app"],
                role=owner.role,
                enabled=True,
                created_by=owner.uid,
            )
        )
        await db.commit()
    try:
        token = AuthUtils.create_access_token({"sub": employee_id, "typ": "mp", "uid": material_users["owner_uid"]})
        yield {"Authorization": f"Bearer {token}"}
    finally:
        async with session_factory() as db:
            await db.execute(delete(ContentEmployee).where(ContentEmployee.id == employee_id))
            await db.commit()
        await engine.dispose()


async def _insert_existing_child_gallery(material_users, parent: dict, **values) -> dict:
    """Seed a legacy/automatic child gallery without using the disabled manual API."""
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    category = ContentMaterialCategory(
        owner_uid=material_users["owner_uid"],
        material_type="image",
        id=f"legacy_child_{uuid.uuid4().hex}",
        tenant_id=str(material_users["department_id"]),
        visibility=parent.get("visibility") or "private",
        parent_id=parent["id"],
        industry_slug=parent.get("industry_slug") or "uncategorized",
        name=values.pop("name", "已有二级图库"),
        description=values.pop("description", "历史二级图库"),
        sort_order=10,
        is_system=False,
        **values,
    )
    async with session_factory() as db:
        db.add(category)
        await db.commit()
    await engine.dispose()
    return category.to_dict()


async def test_material_image_round_trip_uses_private_image_bucket(test_client, material_users):
    owner_headers = material_users["owner"]
    # 产品商品是已有分类；新账号不再自动创建旧的默认图片分类。
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with async_sessionmaker(engine)() as db:
        db.add(
            ContentMaterialCategory(
                owner_uid=material_users["owner_uid"],
                material_type="image",
                id="product",
                name="产品商品",
                visibility="private",
            )
        )
        await db.commit()
    await engine.dispose()
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
        assert asset.object_name == f"material-library/{asset.owner_uid}/images/{asset.id}/image.webp"
        assert asset.content_type == "image/webp"
        assert (asset.metadata_json or {}).get("ingest_status") in {"pending", "completed"}
    await engine.dispose()

    listed = await test_client.get(
        "/api/material-library/items?material_type=image&category=product&query=fixture",
        headers=owner_headers,
    )
    assert listed.status_code == 200, listed.text
    assert item["id"] in {entry["id"] for entry in listed.json()["items"]}

    categories = await test_client.get("/api/material-library/categories?material_type=image", headers=owner_headers)
    assert categories.status_code == 200, categories.text
    assert "product" in {entry["code"] for entry in categories.json()["categories"]}
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
    assert "product" in {entry["code"] for entry in galleries.json()["galleries"]}

    downloaded = await test_client.get(item["file_url"], headers=owner_headers)
    assert downloaded.status_code == 200, downloaded.text
    with Image.open(io.BytesIO(downloaded.content)) as image:
        assert image.size == (48, 36)

    thumbnail = await test_client.get(f"/api/material-library/items/{item['id']}/thumbnail", headers=owner_headers)
    assert thumbnail.status_code == 200, thumbnail.text
    assert thumbnail.headers["content-type"] == "image/webp"
    assert thumbnail.headers["cache-control"] == "private, max-age=86400"
    with Image.open(io.BytesIO(thumbnail.content)) as image:
        assert image.format == "WEBP"
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

    for viewer in ("other", "member"):
        visible_categories = await test_client.get(
            "/api/material-library/categories?material_type=image",
            headers=material_users[viewer],
        )
        assert visible_categories.status_code == 200, visible_categories.text
        visible = next(item for item in visible_categories.json()["categories"] if item["id"] == gallery["id"])
        assert visible["is_global_personal"] is True
        assert visible["can_manage"] is False

        visible_galleries = await test_client.get(
            "/api/material-library/galleries",
            headers=material_users[viewer],
        )
        shared_personal = next(item for item in visible_galleries.json()["galleries"] if item["id"] == gallery["id"])
        assert shared_personal["is_global_personal"] is True
        assert shared_personal["can_manage"] is False

    other_update = await test_client.patch(
        f"/api/material-library/categories/{gallery['id']}?material_type=image",
        headers=material_users["other"],
        json={"name": "越权修改"},
    )
    assert other_update.status_code == 403, other_update.text

    member_create = await test_client.post(
        "/api/material-library/categories",
        headers=material_users["member"],
        json={"material_type": "image", "name": "普通用户图库"},
    )
    assert member_create.status_code == 403, member_create.text

    member_upload = await test_client.post(
        "/api/material-library/images/import",
        headers=material_users["member"],
        data={"category": gallery["id"]},
        files=[("files", ("member.png", _png(), "image/png"))],
    )
    assert member_upload.status_code == 201, member_upload.text
    member_item = member_upload.json()["items"][0]
    sharing = await test_client.patch(
        f"/api/material-library/categories/{gallery['id']}?material_type=image",
        headers=headers,
        json={"visibility": "enterprise"},
    )
    assert sharing.status_code == 409, sharing.text
    for file_route in ("file", "thumbnail"):
        denied = await test_client.get(f"/api/material-library/items/{member_item['id']}/{file_route}", headers=headers)
        assert denied.status_code == 404, denied.text

    member_delete = await test_client.request(
        "DELETE",
        f"/api/material-library/categories/{gallery['id']}?material_type=image",
        headers=material_users["member"],
        json={"target_category_id": "uncategorized"},
    )
    assert member_delete.status_code == 403, member_delete.text

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
        assert removed.json()["moved"] == 2
        member_uploads = await test_client.get(
            "/api/material-library/my-materials/uploads", headers=material_users["member"]
        )
        assert member_item["id"] in {entry["id"] for entry in member_uploads.json()["items"]}
        assert (await test_client.get(member_item["file_url"], headers=material_users["member"])).status_code == 200
        assert (await test_client.get(member_item["file_url"], headers=headers)).status_code == 404
        listed = await test_client.get(
            "/api/material-library/items?material_type=image&category=uncategorized&query=spring",
            headers=headers,
        )
        assert [entry["id"] for entry in listed.json()["items"]] == [item["id"]]
    finally:
        await test_client.delete(f"/api/material-library/items/{item['id']}", headers=headers)
        await test_client.delete(f"/api/material-library/items/{member_item['id']}", headers=material_users["member"])


async def test_personal_presets_are_immutable_and_enterprise_deletion_does_not_restore(test_client, material_users):
    admin = material_users["owner"]
    member = material_users["member"]
    initial = await test_client.get("/api/material-library/my-materials/folders", headers=admin)
    assert initial.status_code == 200, initial.text
    assert {item["id"] for item in initial.json()["folders"]} == {"rough", "generated", "uploads", "works"}

    categories = await test_client.get("/api/material-library/categories?material_type=image", headers=admin)
    assert categories.status_code == 200, categories.text
    reference = next(
        item
        for item in categories.json()["categories"]
        if item["visibility"] == "enterprise" and item["image_design_role"] == "reference"
    )
    original_name = reference["name"]
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        assert await db.get(ContentMaterialFolderSetting, "rough") is None, "隔离测试库的固定入口已被修改"
    try:
        denied = await test_client.patch(
            "/api/material-library/my-materials/folders/rough", headers=member, json={"name": "越权改名"}
        )
        assert denied.status_code == 403, denied.text
        fixed_rename = await test_client.patch(
            "/api/material-library/my-materials/folders/rough", headers=admin, json={"name": "装修毛坯"}
        )
        assert fixed_rename.status_code == 409, fixed_rename.text

        enterprise_rename = await test_client.patch(
            f"/api/material-library/categories/{reference['id']}?material_type=image",
            headers=admin,
            json={"name": "客户案例"},
        )
        assert enterprise_rename.status_code == 200, enterprise_rename.text
        assert enterprise_rename.json()["category"]["image_design_role"] == "reference"

        fixed_delete = await test_client.delete("/api/material-library/my-materials/folders/rough", headers=admin)
        assert fixed_delete.status_code == 409, fixed_delete.text
        enterprise_delete = await test_client.request(
            "DELETE",
            f"/api/material-library/categories/{reference['id']}?material_type=image",
            headers=admin,
            json={},
        )
        assert enterprise_delete.status_code == 200, enterprise_delete.text

        for _ in range(2):
            folders = await test_client.get("/api/material-library/my-materials/folders", headers=admin)
            galleries = await test_client.get("/api/material-library/galleries", headers=admin)
            assert folders.status_code == galleries.status_code == 200
            assert "rough" in {item["id"] for item in folders.json()["folders"]}
            assert sum(item.get("personal_folder") == "rough" for item in galleries.json()["galleries"]) == 1
            assert reference["id"] not in {item["id"] for item in galleries.json()["galleries"]}

        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            setting = await db.get(ContentMaterialFolderSetting, "rough")
            category = await db.scalar(
                select(ContentMaterialCategory).where(
                    ContentMaterialCategory.owner_uid == reference["owner_uid"],
                    ContentMaterialCategory.material_type == "image",
                    ContentMaterialCategory.id == reference["id"],
                )
            )
            assert setting is None
            assert category is not None and category.deleted_at is not None
            assert category.image_design_role == "reference"
    finally:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            await db.execute(
                delete(ContentMaterialFolderSetting).where(ContentMaterialFolderSetting.folder_key == "rough")
            )
            category = await db.scalar(
                select(ContentMaterialCategory).where(
                    ContentMaterialCategory.owner_uid == reference["owner_uid"],
                    ContentMaterialCategory.material_type == "image",
                    ContentMaterialCategory.id == reference["id"],
                )
            )
            if category is not None:
                category.name = original_name
                category.deleted_at = None
            await db.commit()
        await engine.dispose()


async def test_global_personal_gallery_is_visible_across_departments_and_preserves_folder_permissions(
    test_client, material_users
):
    created = await test_client.post(
        "/api/material-library/categories",
        headers=material_users["owner"],
        json={"material_type": "image", "name": "跨端只读图库"},
    )
    assert created.status_code == 201, created.text
    gallery = created.json()["category"]
    assert gallery["is_global_personal"] is True

    engine = create_async_engine(os.environ["POSTGRES_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        outsider_department = Department(name=f"pytest-outsider-{uuid.uuid4().hex[:10]}")
        db.add(outsider_department)
        await db.flush()
        outsider = User(
            username=f"pytest_outsider_{uuid.uuid4().hex[:10]}",
            uid=f"pytest_outsider_{uuid.uuid4().hex[:10]}",
            password_hash=AuthUtils.hash_password(uuid.uuid4().hex),
            role="user",
            department_id=outsider_department.id,
        )
        unassigned = User(
            username=f"pytest_unassigned_{uuid.uuid4().hex[:10]}",
            uid=f"pytest_unassigned_{uuid.uuid4().hex[:10]}",
            password_hash=AuthUtils.hash_password(uuid.uuid4().hex),
            role="user",
            department_id=None,
        )
        db.add_all([outsider, unassigned])
        await db.flush()
        outsider_id = outsider.id
        outsider_uid = outsider.uid
        unassigned_id = unassigned.id
        department_id = outsider_department.id
        await db.commit()
    outsider_headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(outsider_id)})}"}
    try:
        same_tenant = await test_client.get("/api/material-library/galleries", headers=material_users["member"])
        other_tenant = await test_client.get("/api/material-library/galleries", headers=outsider_headers)
        assert same_tenant.status_code == other_tenant.status_code == 200
        assert gallery["id"] in {item["id"] for item in same_tenant.json()["galleries"]}
        assert gallery["id"] in {item["id"] for item in other_tenant.json()["galleries"]}
        async with factory() as db:
            unassigned_categories = await MaterialLibraryRepository(db, include_shared=True).list_categories(
                unassigned.uid, "image"
            )
        assert gallery["id"] in {item.id for item in unassigned_categories}

        async with factory() as db:
            owner = await db.scalar(select(User).where(User.uid == material_users["owner_uid"]))
            owner.role = "user"
            await db.commit()
        still_visible = await test_client.get("/api/material-library/galleries", headers=material_users["member"])
        assert gallery["id"] in {item["id"] for item in still_visible.json()["galleries"]}
        denied = await test_client.patch(
            f"/api/material-library/categories/{gallery['id']}?material_type=image",
            headers=material_users["owner"],
            json={"name": "降级后越权"},
        )
        assert denied.status_code == 403, denied.text
        denied_upload = await test_client.post(
            "/api/material-library/images/import",
            headers=material_users["owner"],
            data={"category": gallery["id"]},
            files=[("files", ("forbidden.png", _png(), "image/png"))],
        )
        assert denied_upload.status_code == 201, denied_upload.text
    finally:
        async with factory() as db:
            owner = await db.scalar(select(User).where(User.uid == material_users["owner_uid"]))
            if owner is not None:
                owner.role = "superadmin"
            await db.execute(delete(ContentMaterialCategory).where(ContentMaterialCategory.owner_uid == outsider_uid))
            await db.execute(delete(User).where(User.id.in_([outsider_id, unassigned_id])))
            await db.execute(delete(Department).where(Department.id == department_id))
            await db.commit()
        await engine.dispose()


async def test_target_employee_rough_gallery_revalidates_scope_on_every_operation(test_client, material_users):
    employee_id = f"user:{material_users['owner_uid']}"
    query = f"employee_id={employee_id}"
    admin = material_users["other"]
    member = material_users["member"]

    forbidden = await test_client.get(f"/api/material-library/categories?material_type=image&{query}", headers=member)
    assert forbidden.status_code == 403, forbidden.text
    categories = await test_client.get(f"/api/material-library/categories?material_type=image&{query}", headers=admin)
    assert categories.status_code == 200, categories.text
    assert categories.json()["target_employee"]["id"] == employee_id
    rough_id = categories.json()["personal_rough_category_id"]
    assert {item["id"] for item in categories.json()["categories"]} == {rough_id}

    galleries = await test_client.get(f"/api/material-library/galleries?{query}", headers=admin)
    assert galleries.status_code == 200, galleries.text
    assert {item["id"] for item in galleries.json()["galleries"]} == {rough_id}

    wrong_folder = await test_client.post(
        f"/api/material-library/images/import?{query}",
        headers=admin,
        data={"category": "mp-uploads-private"},
        files=[("files", ("wrong.png", _png(), "image/png"))],
    )
    assert wrong_folder.status_code == 403, wrong_folder.text

    uploaded = await test_client.post(
        f"/api/material-library/images/import?{query}",
        headers=admin,
        data={"category": rough_id},
        files=[("files", ("rough.png", _png(), "image/png"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    item_id = uploaded.json()["items"][0]["id"]
    try:
        items = await test_client.get(f"/api/material-library/items?material_type=image&{query}", headers=admin)
        assert items.status_code == 200, items.text
        assert item_id in {item["id"] for item in items.json()["items"]}

        denied_file = await test_client.get(f"/api/material-library/items/{item_id}/file?{query}", headers=member)
        assert denied_file.status_code == 403, denied_file.text
        allowed_file = await test_client.get(f"/api/material-library/items/{item_id}/file?{query}", headers=admin)
        assert allowed_file.status_code == 200, allowed_file.text

        updated = await test_client.patch(
            f"/api/material-library/items/{item_id}?{query}",
            headers=admin,
            json={"name": "毛坯实拍"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["item"]["name"] == "毛坯实拍"
    finally:
        deleted = await test_client.delete(f"/api/material-library/items/{item_id}?{query}", headers=admin)
        assert deleted.status_code == 200, deleted.text


async def test_admin_can_create_second_level_gallery(test_client, material_users):
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
            "design_style": "江南印象",
            "building_name": "测试楼盘",
            "area": "120",
        },
    )
    assert child_response.status_code == 201, child_response.text
    child = child_response.json()["category"]
    assert child["parent_id"] == parent["id"]
    assert child["design_style"] == "江南印象"
    assert child["building_name"] == "测试楼盘"
    assert child["area"] == "120"
    galleries = await test_client.get("/api/material-library/galleries", headers=headers)
    assert galleries.status_code == 200, galleries.text
    assert any(item["id"] == child["id"] and item["parent_id"] == parent["id"] for item in galleries.json()["galleries"])

    cover_child = await test_client.post(
        "/api/material-library/categories",
        headers=headers,
        json={"material_type": "cover_template", "name": "模板子分类", "parent_id": parent["id"]},
    )
    assert cover_child.status_code == 422, cover_child.text


async def test_decoration_gallery_child_update_preserves_share_details(test_client, material_users):
    headers = material_users["owner"]
    parent_response = await test_client.post(
        "/api/material-library/categories",
        headers=headers,
        json={"material_type": "image", "name": "装修与家居编辑", "industry_slug": "decoration"},
    )
    assert parent_response.status_code == 201, parent_response.text
    parent = parent_response.json()["category"]
    child = await _insert_existing_child_gallery(
        material_users,
        parent,
        name="客厅实景",
        design_style="江南印象",
        building_name="洋湖天序",
        area="120",
    )

    galleries = await test_client.get("/api/material-library/galleries", headers=headers)
    assert galleries.status_code == 200, galleries.text
    assert child["id"] in {item["id"] for item in galleries.json()["galleries"]}

    updated = await test_client.patch(
        f"/api/material-library/categories/{child['id']}?material_type=image",
        headers=headers,
        json={
            "name": "客厅实景",
            "design_style": "雅致现代",
            "building_name": "万科金域华府",
            "area": "120",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["category"]["design_style"] == "雅致现代"
    assert updated.json()["category"]["building_name"] == "万科金域华府"
    assert updated.json()["category"]["area"] == "120"


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
        deleted = await test_client.delete(f"/api/content/covers/poster-templates/{template['id']}", headers=headers)
        assert deleted.status_code == 200, deleted.text


@pytest.mark.parametrize(
    ("share_api", "public_prefix"),
    [("/api/material-library/shares", "/boyun"), ("/api/mp/share/cases", "")],
    ids=["pc-prefixed-share", "mini-program-root-share"],
)
async def test_second_level_decoration_gallery_share_keeps_ordered_snapshots_and_renders_project_details(
    test_client, material_users, material_mp_headers, share_api, public_prefix
):
    headers = material_users["owner"]
    share_headers = material_mp_headers if share_api == "/api/mp/share/cases" else headers
    public_base = f"https://share.example.test{public_prefix}"
    parent_response = await test_client.post(
        "/api/material-library/categories",
        headers=headers,
        json={"material_type": "image", "name": "装修与家居", "industry_slug": "decoration"},
    )
    assert parent_response.status_code == 201, parent_response.text
    parent = parent_response.json()["category"]
    child = await _insert_existing_child_gallery(
        material_users,
        parent,
        name="洋湖天序·三居式·复古写意",
        building_name="洋湖天序",
        area="120",
        design_style="复古风潮",
    )

    first_upload, second_upload = await asyncio.gather(
        test_client.post(
            "/api/material-library/images/import",
            headers=headers,
            data={"category": child["id"]},
            files=[("files", ("first.png", _png(), "image/png"))],
        ),
        test_client.post(
            "/api/material-library/images/import",
            headers=headers,
            data={"category": child["id"]},
            files=[("files", ("second.png", _poster_png(), "image/png"))],
        ),
    )
    assert first_upload.status_code == 201, first_upload.text
    assert second_upload.status_code == 201, second_upload.text
    first_item = first_upload.json()["items"][0]
    second_item = second_upload.json()["items"][0]

    missing = await test_client.post(share_api, headers=share_headers, json={"item_ids": []})
    assert missing.status_code == 422, missing.text

    created = await test_client.post(
        share_api,
        headers={
            **share_headers,
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "share.example.test",
            "X-Forwarded-Prefix": public_prefix,
        },
        json={
            "item_ids": [second_item["id"], first_item["id"]],
            "sharer_name": "客户端伪造姓名",
            "sharer_phone": "19900000000",
        },
    )
    assert created.status_code == 201, created.text
    share = created.json()["share"]
    assert share["title"] == "洋湖天序·三居式·复古写意"
    assert share["description"] == "洋湖天序｜120㎡｜复古风潮"
    assert share["image_count"] == 2
    assert share["page_path"].endswith(f"/shares/{share['token']}/page")
    assert share["url"] == f"{public_base}/share/case/{share['token']}"
    assert share["page_url"] == share["url"]
    assert share["image_url"] == f"{public_base}{share['image_path']}"
    assert share["card_cover_url"] == f"{public_base}/api/material-library/shares/{share['token']}/cover.jpg"

    # Creation must already have a durable small cover and a warm Redis entry.
    from redis.asyncio import Redis

    redis = Redis.from_url(os.environ["REDIS_URL"])
    cover_key = f"material-share:v1:{share['token']}:cover"
    try:
        prepared_cover = await redis.get(cover_key)
        assert prepared_cover and prepared_cover.startswith(b"\xff\xd8")
        assert 0 < await redis.ttl(cover_key) <= 86400
        async with material_users["session_factory"]() as snapshot_db:
            first_share_item = (await snapshot_db.execute(
                select(ContentMaterialShareItem).join(ContentMaterialShare).where(
                    ContentMaterialShare.token == share["token"], ContentMaterialShareItem.display_order == 1
                )
            )).scalar_one()
        assert await MinIOClient().astat_file(first_share_item.bucket_name, f"{first_share_item.object_name}.card.jpg")
        await redis.delete(cover_key)
        uncached_cover = await test_client.get(f"/api/material-library/shares/{share['token']}/cover.jpg")
        assert uncached_cover.status_code == 200, uncached_cover.text
        assert uncached_cover.content == prepared_cover
        assert await redis.get(cover_key) == prepared_cover
    finally:
        await redis.aclose()

    engine = create_async_engine(os.environ["POSTGRES_URL"])
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            employee = (
                await db.execute(
                    select(ContentEmployee).where(ContentEmployee.created_by == material_users["owner_uid"])
                )
            ).scalar_one()
            original_phone = employee.login_account
            employee.name = "修改后的员工姓名"
            employee.login_account = f"198{int(uuid.uuid4().hex[:10], 16) % 100000000:08d}"
            await db.commit()
    finally:
        await engine.dispose()
    public_data = await test_client.get(f"/api/material-library/shares/{share['token']}")
    assert public_data.status_code == 200, public_data.text
    assert public_data.json()["share"]["sharer_name"] == "分享测试员工"
    assert public_data.json()["share"]["sharer_phone"] == original_phone

    deleted = await test_client.delete(f"/api/material-library/items/{first_item['id']}", headers=headers)
    assert deleted.status_code == 200, deleted.text

    public_headers = {
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "share.example.test",
        "X-Forwarded-Prefix": public_prefix,
    }
    card_cover = await test_client.get(f"/api/material-library/shares/{share['token']}/cover.jpg")
    assert card_cover.status_code == 200, card_cover.text
    assert card_cover.headers["content-type"] == "image/jpeg"
    assert "public" in card_cover.headers["cache-control"]
    assert "immutable" in card_cover.headers["cache-control"]
    with Image.open(io.BytesIO(card_cover.content)) as image:
        assert image.size == (500, 400)

    public_page = await test_client.get(share["page_path"], headers=public_headers)
    assert public_page.status_code == 200, public_page.text
    assert "洋湖天序·三居式·复古写意" in public_page.text
    assert 'class="project-info-card"' in public_page.text
    assert "楼盘：洋湖天序" in public_page.text
    assert "面积：120㎡" in public_page.text
    assert "风格：复古风潮" in public_page.text
    assert f'href="tel:{original_phone}">电话：{original_phone} 分享测试员工</a>' in public_page.text
    assert 'property="og:description" content="洋湖天序｜120㎡｜复古风潮"' in public_page.text
    assert f'property="og:url" content="{share["url"]}"' in public_page.text
    assert f"{public_prefix}/api/material-library/shares/{share['token']}/images/1" in public_page.text
    assert "https://api:5050" not in public_page.text
    assert public_page.text.index("/images/1") < public_page.text.index("/images/2")

    canonical_page = await test_client.get(f"/share/case/{share['token']}", headers=public_headers)
    assert canonical_page.status_code == 200, canonical_page.text
    assert canonical_page.headers["content-type"].startswith("text/html")
    assert f'href="tel:{original_phone}"' in canonical_page.text
    assert "修改后的员工姓名" not in canonical_page.text
    assert '<meta property="og:url"' in canonical_page.text
    assert f'property="og:url" content="{share["url"]}"' in canonical_page.text
    assert f'property="og:image" content="{share["card_cover_url"]}"' in canonical_page.text
    image_urls = re.findall(r'<img[^>]+src="([^"]+)"', canonical_page.text)
    expected_images = [
        f"{public_prefix}/api/material-library/shares/{share['token']}/images/{order}.webp" for order in (1, 2)
    ]
    assert image_urls == [expected_images[0], *expected_images]

    second_snapshot = await test_client.get(f"/api/material-library/shares/{share['token']}/images/1")
    first_snapshot = await test_client.get(f"/api/material-library/shares/{share['token']}/images/2")
    assert second_snapshot.status_code == 200, second_snapshot.text
    assert first_snapshot.status_code == 200, first_snapshot.text
    assert second_snapshot.headers["content-type"] == "image/webp"
    assert "inline" in second_snapshot.headers["content-disposition"]
    assert "public" in second_snapshot.headers["cache-control"]
    assert "immutable" in second_snapshot.headers["cache-control"]
    with Image.open(io.BytesIO(second_snapshot.content)) as image:
        assert image.size == (1080, 1440)
    with Image.open(io.BytesIO(first_snapshot.content)) as image:
        assert image.size == (48, 36)

    display_image = await test_client.get(f"/api/material-library/shares/{share['token']}/images/1.webp")
    assert display_image.status_code == 200, display_image.text
    assert display_image.headers["content-type"] == "image/webp"
    assert "public" in display_image.headers["cache-control"]
    assert "immutable" in display_image.headers["cache-control"]
    with Image.open(io.BytesIO(display_image.content)) as image:
        assert image.format == "WEBP"
        assert image.size == (1080, 1440)
