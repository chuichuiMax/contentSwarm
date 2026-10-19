from __future__ import annotations

import asyncio
import io
import uuid

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import delete, select

from test.integration.api import test_material_library_router as material_fixtures
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentCoverAsset, ContentEmployee, ContentMaterialLibraryItem
from yuxi.utils.auth_utils import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]
material_users = material_fixtures.material_users


@pytest_asyncio.fixture
async def rough_accounts(material_users):
    sessions = material_users["session_factory"]
    employees = []
    accounts = []
    try:
        async with sessions() as db:
            for uid in material_users["owner_uids"]:
                user = await db.scalar(select(User).where(User.uid == uid))
                phone = f"199{uuid.uuid4().int % 10**8:08d}"
                user.phone_number = phone
                employee_id = uuid.uuid4().hex
                employees.append(employee_id)
                db.add(
                    ContentEmployee(
                        id=employee_id,
                        employee_code=f"pytest_rough_{employee_id}",
                        name="企业毛坯图库测试",
                        login_account=phone,
                        gender="女",
                        login_port=["app"],
                        role=user.role,
                        enabled=True,
                        created_by=uid,
                    )
                )
                token = AuthUtils.create_access_token({"sub": employee_id, "typ": "mp", "uid": uid})
                accounts.append({"uid": uid, "headers": {"Authorization": f"Bearer {token}"}})
            await db.commit()
        yield accounts
    finally:
        async with sessions() as db:
            await db.execute(delete(ContentEmployee).where(ContentEmployee.id.in_(employees)))
            await db.commit()


async def test_enterprise_rough_upload_browse_and_owner_delete_across_accounts(
    test_client, material_users, rough_accounts
):
    first, second, member = rough_accounts
    galleries = await test_client.get(
        "/api/mp/content/galleries",
        headers=first["headers"],
        params={"scope": "enterprise", "rough_only": "true"},
    )
    assert galleries.status_code == 200, galleries.text
    roots = galleries.json()["galleries"]
    assert len(roots) == 1 and roots[0]["image_design_role"] == "rough"
    gallery = roots[0]["id"]
    raw = io.BytesIO()
    Image.new("RGB", (32, 24), "#cc6633").save(raw, "PNG")
    ids = []
    for index, account in enumerate(rough_accounts):
        uploaded = await test_client.post(
            "/api/mp/content/uploads/cover",
            headers=account["headers"],
            files={"file": (f"rough-{index}.png", raw.getvalue(), "image/png")},
            data={"category": gallery, "scope": "enterprise"},
        )
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()["category"] == gallery
        ids.append(uploaded.json()["library_item_id"])

    # Exercise the actual worker/storage chain; a pending original is not successful browsing.
    listing = None
    for _ in range(60):
        response = await test_client.get(
            "/api/mp/content/gallery-items",
            headers=first["headers"],
            params={"category": gallery, "scope": "enterprise", "page_size": 100},
        )
        assert response.status_code == 200, response.text
        listing = response.json()
        if set(ids) <= {row["id"] for row in listing["items"]}:
            break
        assert not listing["upload_failures"], listing["upload_failures"]
        await asyncio.sleep(0.5)
    by_id = {row["id"]: row for row in listing["items"]}
    assert set(ids) <= by_id.keys(), listing
    assert by_id[ids[0]]["can_delete"] is True
    assert by_id[ids[1]]["can_delete"] is False  # Even a superadmin in the mini-program.

    other_listing = await test_client.get(
        "/api/mp/content/gallery-items",
        headers=member["headers"],
        params={"category": gallery, "scope": "enterprise", "page_size": 100},
    )
    assert set(ids) <= {row["id"] for row in other_listing.json()["items"]}
    pc_listing = await test_client.get(
        "/api/material-library/items",
        headers=material_users["owner"],
        params={"material_type": "image", "category": gallery, "scope": "enterprise", "page_size": 100},
    )
    assert pc_listing.status_code == 200, pc_listing.text
    assert set(ids) <= {row["id"] for row in pc_listing.json()["items"]}
    for suffix in ("file", "thumbnail"):
        media = await test_client.get(f"/api/mp/content/gallery-items/{ids[0]}/{suffix}", headers=member["headers"])
        assert media.status_code == 200, media.text
        with Image.open(io.BytesIO(media.content)) as decoded:
            assert decoded.width > 0

    future = await test_client.get(
        "/api/mp/content/gallery-items",
        headers=first["headers"],
        params={"category": gallery, "scope": "enterprise", "date_from": "2099-01-01", "date_to": "2099-01-02"},
    )
    assert future.status_code == 200 and not (set(ids) & {row["id"] for row in future.json()["items"]})
    forbidden = await test_client.delete(f"/api/mp/content/gallery-items/{ids[1]}", headers=first["headers"])
    assert forbidden.status_code == 403, forbidden.text
    async with material_users["session_factory"]() as db:
        designer = await db.scalar(select(User).where(User.uid == member["uid"]))
        designer.role = "设计师"
        await db.commit()
    removed = await test_client.delete(f"/api/mp/content/gallery-items/{ids[2]}", headers=member["headers"])
    assert removed.status_code == 200, removed.text
    removed = await test_client.delete(f"/api/mp/content/gallery-items/{ids[0]}", headers=first["headers"])
    assert removed.status_code == 200 and removed.json()["object_deleted"] is False, removed.text
    async with material_users["session_factory"]() as db:
        item = await db.scalar(select(ContentMaterialLibraryItem).where(ContentMaterialLibraryItem.id == ids[0]))
        asset = await db.scalar(select(ContentCoverAsset).where(ContentCoverAsset.id == item.asset_id))
        assert item.deleted_at is not None and asset.deleted_at is None
    pc_removed = await test_client.delete(f"/api/material-library/items/{ids[1]}", headers=material_users["owner"])
    assert pc_removed.status_code == 200, pc_removed.text


async def test_enterprise_upload_cannot_redirect_to_personal_or_other_shared_gallery(test_client, rough_accounts):
    headers = rough_accounts[0]["headers"]
    folders = await test_client.get("/api/mp/content/my-materials/folders", headers=headers)
    personal = next(row for row in folders.json()["folders"] if row["id"] == "uploads")
    galleries = await test_client.get("/api/mp/content/galleries?scope=enterprise", headers=headers)
    shared = galleries.json()["galleries"]
    targets = [personal["gallery_id"], *(row["id"] for row in shared if row["image_design_role"] != "rough")]
    for category in targets:
        response = await test_client.post(
            "/api/mp/content/uploads/cover",
            headers=headers,
            files={"file": ("forged.png", b"not-read-before-scope-check", "image/png")},
            data={"category": category, "scope": "enterprise"},
        )
        assert response.status_code == 403, response.text
