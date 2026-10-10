import asyncio
import io
import os
import secrets
import uuid

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.storage.minio import MinIOClient
from yuxi.storage.postgres.models_business import Department, OperationLog, User
from yuxi.storage.postgres.models_content import ContentCoverAsset, ContentMaterialCategory, ContentMaterialLibraryItem
from yuxi.utils.auth_utils import AuthUtils


def _png():
    output = io.BytesIO()
    Image.new("RGB", (20, 20), "blue").save(output, format="PNG")
    return output.getvalue()


@pytest_asyncio.fixture
async def material_users():
    suffix = uuid.uuid4().hex[:12]
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        department = Department(name=f"pytest_employee_rough_{suffix}")
        db.add(department)
        await db.flush()
        users = [
            User(
                uid=f"pytest_employee_rough_{suffix}_{label}",
                username=f"pytest_rough_{label}_{suffix[:10]}",
                role=role,
                department_id=department.id,
                password_hash=AuthUtils.hash_password(secrets.token_urlsafe(24)),
            )
            for label, role in (("owner", "superadmin"), ("other", "superadmin"), ("member", "user"))
        ]
        db.add_all(users)
        await db.commit()
        user_ids = [user.id for user in users]
        owner_uids = [user.uid for user in users]
        department_id = department.id
    try:
        # Authenticate disposable test identities directly; this test does not exercise PC login.
        headers = [
            {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(user.id)})}"} for user in users
        ]
        yield {"owner": headers[0], "other": headers[1], "member": headers[2], "owner_uid": users[0].uid}
    finally:
        async with factory() as db:
            assets = list(
                await db.scalars(select(ContentCoverAsset).where(ContentCoverAsset.owner_uid.in_(owner_uids)))
            )
            await db.execute(
                delete(ContentMaterialLibraryItem).where(ContentMaterialLibraryItem.owner_uid.in_(owner_uids))
            )
            await db.execute(delete(ContentCoverAsset).where(ContentCoverAsset.owner_uid.in_(owner_uids)))
            await db.execute(delete(ContentMaterialCategory).where(ContentMaterialCategory.owner_uid.in_(owner_uids)))
            await db.execute(delete(OperationLog).where(OperationLog.user_id.in_(user_ids)))
            await db.execute(delete(User).where(User.id.in_(user_ids)))
            await db.execute(delete(Department).where(Department.id == department_id))
            await db.commit()
        from yuxi.services.material_upload_queue import material_thumb_object_name

        storage = MinIOClient()
        for asset in assets:
            await storage.adelete_file(asset.bucket_name, asset.object_name)
            await storage.adelete_file(asset.bucket_name, material_thumb_object_name(asset.object_name))
            await storage.adelete_objects_by_prefix(asset.bucket_name, f"{asset.object_name}.share-v1.")
        await engine.dispose()


pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_employee_counts_and_shared_detail_use_the_same_uploader_scope(test_client, material_users):
    owner = material_users["owner"]
    admin = material_users["other"]
    employee_id = f"user:{material_users['owner_uid']}"
    shared_params = {"material_type": "image", "uploader_employee_id": employee_id}
    categories = await test_client.get("/api/material-library/categories", params=shared_params, headers=admin)
    assert categories.status_code == 200, categories.text
    shared_id = categories.json()["enterprise_rough_category_id"]
    assert len(categories.json()["categories"]) == 1
    assert categories.json()["categories"][0]["visibility"] == "enterprise"
    assert categories.json()["target_employee"]["id"] == employee_id

    personal = await test_client.get(
        "/api/material-library/categories", headers=admin, params={"material_type": "image", "employee_id": employee_id}
    )
    assert personal.status_code == 200, personal.text
    personal_id = personal.json()["personal_rough_category_id"]
    own_shared_ids = []
    for headers, category, name in (
        (owner, shared_id, "own-shared-1"),
        (owner, shared_id, "own-shared-2"),
        (admin, shared_id, "other-shared"),
    ):
        upload = await test_client.post(
            "/api/material-library/images/import",
            headers=headers,
            data={"category": category},
            files=[("files", (f"{name}.png", _png(), "image/png"))],
        )
        assert upload.status_code == 201, upload.text
        if headers is owner:
            own_shared_ids.append(upload.json()["items"][0]["id"])
    upload = await test_client.post(
        f"/api/material-library/images/import?employee_id={employee_id}",
        headers=admin,
        data={"category": personal_id},
        files=[("files", ("personal.png", _png(), "image/png"))],
    )
    assert upload.status_code == 201, upload.text

    listing = await test_client.get("/api/employees", headers=admin)
    assert listing.status_code == 200, listing.text
    employee = next(row for row in listing.json()["employees"] if row["id"] == employee_id)
    assert employee["rough_image_count"] == 1
    assert employee["enterprise_rough_image_count"] == 2

    detail = await test_client.get("/api/material-library/items", headers=admin, params=shared_params)
    assert detail.status_code == 200, detail.text
    assert detail.json()["total"] == employee["enterprise_rough_image_count"]
    assert {row["id"] for row in detail.json()["items"]} == set(own_shared_ids)
    assert all(row["uploaded_by"] == material_users["owner_uid"] for row in detail.json()["items"])
    assert all(row["can_manage"] for row in detail.json()["items"])
    page = await test_client.get(
        "/api/material-library/items", headers=admin, params={**shared_params, "page": 2, "page_size": 1}
    )
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 2 and len(page.json()["items"]) == 1
    filtered = await test_client.get(
        "/api/material-library/items", headers=admin, params={**shared_params, "query": "own-shared-1"}
    )
    assert filtered.json()["total"] == 1
    excluded = await test_client.get(
        "/api/material-library/items", headers=admin, params={**shared_params, "date_to": "2000-01-01"}
    )
    assert excluded.json()["total"] == 0

    for resource in ("categories", "items"):
        denied = await test_client.get(
            f"/api/material-library/{resource}", headers=material_users["member"], params=shared_params
        )
        assert denied.status_code == 403, denied.text
        mixed = await test_client.get(
            f"/api/material-library/{resource}", headers=admin, params={**shared_params, "employee_id": employee_id}
        )
        assert mixed.status_code == 422, mixed.text

    # Files remain readable through the ordinary enterprise endpoint under the administrator's identity.
    for _ in range(30):
        image = await test_client.get(f"/api/material-library/items/{own_shared_ids[0]}/thumbnail", headers=admin)
        if image.status_code == 200:
            break
        await asyncio.sleep(1)
    assert image.status_code == 200, image.text
    assert image.headers["content-type"].startswith("image/") and len(image.content) > 0
