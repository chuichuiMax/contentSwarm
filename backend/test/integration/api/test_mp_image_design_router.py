from __future__ import annotations

import io
import os
import uuid

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test.integration.api import test_material_library_router as material_fixtures
from yuxi.services.employee_service import EmployeeCreate, create_employee, delete_employee
from yuxi.storage.postgres.models_business import User
from yuxi.utils.auth_utils import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]
material_users = material_fixtures.material_users


@pytest_asyncio.fixture
async def admin_headers(material_users):
    # Provision an isolated administrator instead of requiring a developer's credentials.
    return material_users["owner"]


@pytest_asyncio.fixture
async def mp_accounts(test_client, material_users):
    accounts = []
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        for _ in range(2):
            suffix = uuid.uuid4().hex[:10]
            phone = f"139{uuid.uuid4().int % 10**8:08d}"
            # Commit fixture setup before the next HTTP request opens its transaction.
            async with sessions() as db:
                created = await create_employee(
                    db,
                    User(uid=material_users["owner_uid"]),
                    EmployeeCreate(
                        employee_code=f"ID{suffix}",
                        name=f"图片设计测试_{suffix}",
                        login_account=phone,
                        gender="male",
                        login_port=["app"],
                        role="运营",
                        enabled=True,
                    ),
                )
                await db.commit()
            account = {"id": created["employee"]["id"]}
            accounts.append(account)
            sent = await test_client.post("/api/mp/auth/sms/send", json={"phone": phone})
            assert sent.status_code == 200, sent.text
            login = await test_client.post(
                "/api/mp/auth/sms/login",
                json={
                    "phone": phone,
                    "code": sent.json()["debug_code"],
                },
            )
            assert login.status_code == 200, login.text
            account["headers"] = {"Authorization": f"Bearer {login.json()['access_token']}"}
        yield accounts
    finally:
        try:
            async with sessions() as db:
                for account in accounts:
                    await delete_employee(db, account["id"])
                # Employee deletion is soft for platform users; release the temporary department FK.
                await db.execute(
                    update(User)
                    .where(
                        User.department_id == int(material_users["department_id"]),
                        User.is_deleted == 1,
                    )
                    .values(department_id=None)
                )
                await db.commit()
        finally:
            await engine.dispose()


async def test_image_design_routes_require_mp_authentication(test_client):
    for route in (
        "drafts",
        "library",
        "save-targets",
        "library/missing/file",
        "library/missing/thumbnail",
        "tasks/missing",
        "results",
        "results/missing/file",
        "tasks/missing/inputs/source/file",
    ):
        response = await test_client.get(f"/api/mp/image-design/{route}")
        assert response.status_code == 401, response.text
    for route in ("polish", "tasks", "tasks/missing/retry"):
        response = await test_client.post(f"/api/mp/image-design/{route}", json={})
        assert response.status_code == 401, response.text


async def test_mp_task_rejects_missing_refinement_and_legacy_target(test_client, mp_accounts):
    response = await test_client.post(
        "/api/mp/image-design/tasks",
        headers=mp_accounts[0]["headers"],
        json={
            "workflow": "redesign",
            "images": [{"role": "source", "library_item_id": "missing"}],
            "save_target_id": "folder",
            "polished_prompt": "forged",
        },
    )
    assert response.status_code == 422, response.text


async def test_mp_task_rejects_unowned_refinement(test_client, mp_accounts):
    response = await test_client.post(
        "/api/mp/image-design/tasks",
        headers=mp_accounts[0]["headers"],
        json={
            "workflow": "redesign",
            "images": [{"role": "source", "library_item_id": "missing"}],
            "refinement_id": "idr_unknown",
            "save_target": {"scope": "enterprise"},
            "count": 1,
        },
    )
    assert response.status_code == 404, response.text


async def test_mp_transfer_array_passes_validation_before_refinement_lookup(test_client, mp_accounts):
    payload = {
        "workflow": "transfer",
        "images": [{"role": "reference", "library_item_id": "missing"}],
        "target_space": "客厅",
        "layout_type": "一字型沙发墙",
        "extra_element": ["落地窗旁休闲躺椅", "壁炉居中"],
        "refinement_id": "idr_unknown",
        "save_target": {"scope": "private"},
    }
    response = await test_client.post("/api/mp/image-design/tasks", headers=mp_accounts[0]["headers"], json=payload)
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["error"]["code"] == "IMAGE_DESIGN_REFINEMENT_INVALID"
    payload["extra_element"].append("开放式层板展示架")
    rejected = await test_client.post("/api/mp/image-design/tasks", headers=mp_accounts[0]["headers"], json=payload)
    assert rejected.status_code == 422, rejected.text


async def test_mp_results_and_task_files_remain_authenticated(test_client, mp_accounts):
    headers = mp_accounts[0]["headers"]
    result = await test_client.get("/api/mp/image-design/results", headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["items"] == []
    for path in ("tasks/missing", "results/missing/file", "tasks/missing/inputs/source/file"):
        response = await test_client.get(f"/api/mp/image-design/{path}", headers=headers)
        assert response.status_code == 404, response.text
    retry = await test_client.post("/api/mp/image-design/tasks/missing/retry", headers=headers)
    assert retry.status_code == 404, retry.text


async def test_save_targets_offer_fixed_destinations_and_reject_pc_token(test_client, admin_headers, mp_accounts):
    pc = await test_client.get("/api/mp/image-design/save-targets", headers=admin_headers)
    assert pc.status_code == 401, pc.text
    response = await test_client.get("/api/mp/image-design/save-targets", headers=mp_accounts[0]["headers"])
    assert response.status_code == 200, response.text
    assert response.json()["scopes"] == [
        {"scope": "private", "label": "我的素材", "can_write_root": True, "folders": []}
    ]


async def test_drafts_are_normalized_and_account_isolated(test_client, mp_accounts):
    headers = mp_accounts[0]["headers"]
    saved = await test_client.put(
        "/api/mp/image-design/drafts",
        headers=headers,
        json={
            "drafts": {"adapt": {"reference": {"id": "draft-image"}, "save_target": {"scope": "private"}}},
        },
    )
    assert saved.status_code == 200, saved.text
    assert set(saved.json()["drafts"]) == {"redesign", "adapt", "transfer"}
    loaded = await test_client.get("/api/mp/image-design/drafts", headers=headers)
    assert loaded.json()["drafts"]["adapt"]["reference"]["id"] == "draft-image"
    other = await test_client.get("/api/mp/image-design/drafts", headers=mp_accounts[1]["headers"])
    assert other.json()["drafts"] == {"redesign": {}, "adapt": {}, "transfer": {}}
    forged = await test_client.put(
        "/api/mp/image-design/drafts",
        headers=headers,
        json={
            "owner_uid": "another-owner",
            "drafts": {},
        },
    )
    assert forged.status_code == 422, forged.text


async def test_upload_is_private_dedicated_input_with_authenticated_files(test_client, mp_accounts):
    raw = io.BytesIO()
    Image.new("RGB", (32, 24), "red").save(raw, format="PNG")
    headers = mp_accounts[0]["headers"]
    uploaded = await test_client.post(
        "/api/mp/image-design/uploads",
        headers=headers,
        files={"file": ("room.png", raw.getvalue(), "image/png")},
        data={"role": "source"},
    )
    assert uploaded.status_code == 200, uploaded.text
    item = uploaded.json()["item"]
    assert item["source_role"] == "upload"
    assert item["source_item_id"] is None
    listed = await test_client.get("/api/mp/image-design/library", headers=headers)
    assert item["id"] in {row["id"] for row in listed.json()["items"]}
    ordinary = await test_client.get(
        "/api/mp/content/gallery-items", headers=headers, params={"category": "uncategorized", "scope": "private"}
    )
    assert ordinary.status_code == 200, ordinary.text
    assert item["asset_id"] not in {row["asset_id"] for row in ordinary.json()["items"]}
    own_uploads = await test_client.get("/api/mp/content/my-materials/uploads", headers=headers)
    assert own_uploads.status_code == 200, own_uploads.text
    assert item["asset_id"] in {row["asset_id"] for row in own_uploads.json()["items"]}
    foreign_uploads = await test_client.get("/api/mp/content/my-materials/uploads", headers=mp_accounts[1]["headers"])
    assert foreign_uploads.status_code == 200, foreign_uploads.text
    assert item["asset_id"] not in {row["asset_id"] for row in foreign_uploads.json()["items"]}
    for url in (item["file_url"], item["thumbnail_file_url"]):
        own = await test_client.get(url, headers=headers)
        assert own.status_code == 200, own.text
        assert own.headers["content-type"].startswith("image/")
        foreign = await test_client.get(url, headers=mp_accounts[1]["headers"])
        assert foreign.status_code == 404, foreign.text


async def test_mp_cover_upload_stays_private_in_fixed_upload_folder(test_client, mp_accounts):
    raw = io.BytesIO()
    Image.new("RGB", (32, 24), "green").save(raw, format="PNG")
    owner = mp_accounts[0]["headers"]
    other = mp_accounts[1]["headers"]
    uploaded = await test_client.post(
        "/api/mp/content/uploads/cover",
        headers=owner,
        files={"file": ("room.png", raw.getvalue(), "image/png")},
        data={"folder": "uploads"},
    )
    assert uploaded.status_code == 200, uploaded.text
    item_id = uploaded.json()["library_item_id"]
    try:
        assert uploaded.json()["category"] == "mp-uploads-private"
        folders = await test_client.get("/api/mp/content/my-materials/folders", headers=owner)
        assert folders.status_code == 200, folders.text
        assert next(folder["count"] for folder in folders.json()["folders"] if folder["id"] == "uploads") >= 1
        own = await test_client.get("/api/mp/content/my-materials/uploads", headers=owner)
        assert own.status_code == 200, own.text
        assert item_id in {item["id"] for item in own.json()["items"]}
        foreign = await test_client.get("/api/mp/content/my-materials/uploads", headers=other)
        assert foreign.status_code == 200, foreign.text
        assert item_id not in {item["id"] for item in foreign.json()["items"]}
        forbidden = await test_client.get(f"/api/mp/content/gallery-items/{item_id}/file", headers=other)
        assert forbidden.status_code == 404, forbidden.text
    finally:
        await test_client.delete(f"/api/mp/content/gallery-items/{item_id}", headers=owner)


async def test_pc_personal_materials_follow_mp_folder_rules(test_client, mp_accounts):
    mp_headers = mp_accounts[0]["headers"]
    token = mp_headers["Authorization"].split(" ", 1)[1]
    uid = AuthUtils.verify_access_token(token)["uid"]
    other_token = mp_accounts[1]["headers"]["Authorization"].split(" ", 1)[1]
    other_uid = AuthUtils.verify_access_token(other_token)["uid"]
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one()
        other_user = (await db.execute(select(User).where(User.uid == other_uid))).scalar_one()
        pc_headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(user.id)})}"}
        other_pc_headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(other_user.id)})}"}
    await engine.dispose()

    raw = io.BytesIO()
    Image.new("RGB", (32, 24), "green").save(raw, format="PNG")
    uploaded = await test_client.post(
        "/api/mp/content/uploads/cover",
        headers=mp_headers,
        files={"file": ("shared-rules.png", raw.getvalue(), "image/png")},
        data={"folder": "uploads"},
    )
    assert uploaded.status_code == 200, uploaded.text
    item_id = uploaded.json()["library_item_id"]
    pc_item_id = None
    try:
        mp_folders = await test_client.get("/api/mp/content/my-materials/folders", headers=mp_headers)
        pc_folders = await test_client.get("/api/material-library/my-materials/folders", headers=pc_headers)
        assert pc_folders.status_code == 200, pc_folders.text
        mp_by_id = {folder["id"]: folder for folder in mp_folders.json()["folders"]}
        pc_by_id = {folder["id"]: folder for folder in pc_folders.json()["folders"]}
        assert {
            folder_id: (folder["name"], folder["count"], folder["can_upload"]) for folder_id, folder in pc_by_id.items()
        } == {
            folder_id: (folder["name"], folder["count"], folder["can_upload"]) for folder_id, folder in mp_by_id.items()
        }
        assert pc_by_id["uploads"]["cover_thumbnail_file_url"].startswith("/api/material-library/items/")
        assert mp_by_id["uploads"]["cover_thumbnail_file_url"].startswith("/api/mp/content/gallery-items/")

        mp_items = await test_client.get("/api/mp/content/my-materials/uploads", headers=mp_headers)
        pc_items = await test_client.get("/api/material-library/my-materials/uploads", headers=pc_headers)
        assert pc_items.status_code == 200, pc_items.text
        assert pc_items.json()["total"] == mp_items.json()["total"]
        assert [item["id"] for item in pc_items.json()["items"]] == [item["id"] for item in mp_items.json()["items"]]
        assert item_id in {item["id"] for item in pc_items.json()["items"]}
        other_items = await test_client.get("/api/material-library/my-materials/uploads", headers=other_pc_headers)
        assert item_id not in {item["id"] for item in other_items.json()["items"]}
        file = await test_client.get(f"/api/material-library/items/{item_id}/thumbnail", headers=pc_headers)
        assert file.status_code == 200, file.text
        assert file.headers["content-type"] == "image/webp"
        forbidden = await test_client.get(f"/api/material-library/items/{item_id}/thumbnail", headers=other_pc_headers)
        assert forbidden.status_code == 404, forbidden.text

        pc_upload = await test_client.post(
            "/api/material-library/images/import",
            headers=pc_headers,
            files=[("files", ("pc-rough.png", raw.getvalue(), "image/png"))],
            data={"category": "mp-rough-private"},
        )
        assert pc_upload.status_code == 201, pc_upload.text
        pc_item_id = pc_upload.json()["items"][0]["id"]
        mp_rough = await test_client.get("/api/mp/content/my-materials/rough", headers=mp_headers)
        pc_rough = await test_client.get("/api/material-library/my-materials/rough", headers=pc_headers)
        other_mp_rough = await test_client.get("/api/mp/content/my-materials/rough", headers=mp_accounts[1]["headers"])
        other_pc_rough = await test_client.get("/api/material-library/my-materials/rough", headers=other_pc_headers)
        assert mp_rough.status_code == 200, mp_rough.text
        assert pc_rough.status_code == 200, pc_rough.text
        assert pc_item_id in {item["id"] for item in mp_rough.json()["items"]}
        assert pc_item_id in {item["id"] for item in pc_rough.json()["items"]}
        assert pc_item_id in {item["id"] for item in other_mp_rough.json()["items"]}
        assert pc_item_id in {item["id"] for item in other_pc_rough.json()["items"]}
        shared_file = await test_client.get(
            f"/api/mp/content/gallery-items/{pc_item_id}/thumbnail", headers=mp_accounts[1]["headers"]
        )
        assert shared_file.status_code == 200, shared_file.text
        forbidden_delete = await test_client.delete(
            f"/api/mp/content/gallery-items/{pc_item_id}", headers=mp_accounts[1]["headers"]
        )
        assert forbidden_delete.status_code in {403, 404}, forbidden_delete.text

        mp_works = await test_client.get("/api/mp/image/works", headers=mp_headers)
        pc_works = await test_client.get("/api/material-library/my-materials/works", headers=pc_headers)
        assert pc_works.status_code == 200, pc_works.text
        assert pc_works.json()["total"] == mp_works.json()["total"]
    finally:
        if pc_item_id:
            await test_client.delete(f"/api/material-library/items/{pc_item_id}", headers=pc_headers)
        await test_client.delete(f"/api/mp/content/gallery-items/{item_id}", headers=mp_headers)


async def test_add_library_deduplicates_and_revokes_deleted_source(test_client, admin_headers, mp_accounts):
    category = await test_client.post(
        "/api/material-library/categories",
        headers=admin_headers,
        json={
            "material_type": "image",
            "name": f"MP design test {uuid.uuid4().hex[:8]}",
            "visibility": "enterprise",
        },
    )
    assert category.status_code == 201, category.text
    gallery_id = category.json()["category"]["id"]
    item_id = None
    try:
        raw = io.BytesIO()
        Image.new("RGB", (32, 24), "blue").save(raw, format="PNG")
        source = await test_client.post(
            "/api/material-library/images/import",
            headers=admin_headers,
            data={"category": gallery_id},
            files=[("files", ("shared.png", raw.getvalue(), "image/png"))],
        )
        assert source.status_code == 201, source.text
        item_id = source.json()["items"][0]["id"]
        payload = {"source_library_item_id": item_id, "source_gallery_id": gallery_id, "source_role": "source"}
        first = await test_client.post("/api/mp/image-design/library", headers=mp_accounts[0]["headers"], json=payload)
        again = await test_client.post("/api/mp/image-design/library", headers=mp_accounts[0]["headers"], json=payload)
        assert first.status_code == again.status_code == 200, (first.text, again.text)
        assert first.json()["item"]["id"] == again.json()["item"]["id"]
        assert first.json()["item"]["source_role"] == "source"
        changed = await test_client.patch(
            f"/api/material-library/categories/{gallery_id}",
            headers=admin_headers,
            params={"material_type": "image"},
            json={"visibility": "private"},
        )
        assert changed.status_code == 200, changed.text
        # Administrator-created personal galleries remain department-visible.
        visible = await test_client.get(first.json()["item"]["file_url"], headers=mp_accounts[0]["headers"])
        assert visible.status_code == 200, visible.text
        deleted = await test_client.delete(f"/api/material-library/items/{item_id}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text
        item_id = None
        revoked = await test_client.get(first.json()["item"]["file_url"], headers=mp_accounts[0]["headers"])
        assert revoked.status_code == 404, revoked.text
        rejected = await test_client.post(
            "/api/mp/image-design/library", headers=mp_accounts[1]["headers"], json=payload
        )
        assert rejected.status_code == 404, rejected.text
    finally:
        if item_id:
            await test_client.delete(f"/api/material-library/items/{item_id}", headers=admin_headers)
        await test_client.request(
            "DELETE",
            f"/api/material-library/categories/{gallery_id}",
            headers=admin_headers,
            params={"material_type": "image"},
            json={},
        )


async def test_generated_asset_attach_is_idempotent_and_hiding_keeps_pc_item(material_users):
    from yuxi.image_design.schemas import ImageDesignSaveTarget
    from yuxi.image_design.worker import attach_generated_asset
    from yuxi.services.personal_materials import folder_categories
    from yuxi.storage.postgres.models_content import (
        ContentCoverAsset,
        ContentMaterialLibraryItem,
        ImageDesignLibraryItem,
    )
    from yuxi.utils.datetime_utils import utc_now_naive

    asset_id = f"cca_{uuid.uuid4().hex}"
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            user = await db.scalar(select(User).where(User.uid == material_users["owner_uid"]))
            gallery = (await folder_categories(db, user))["generated"][0]
            asset = ContentCoverAsset(
                id=asset_id,
                owner_uid=user.uid,
                role="output",
                original_file_name="generated.png",
                content_type="image/png",
                file_size=8,
                image_width=32,
                image_height=24,
                sha256="a" * 64,
                bucket_name="image",
                object_name=f"test/{asset_id}.png",
            )
            db.add(asset)
            await db.flush()
            kwargs = {
                "user": user,
                "asset": asset,
                "requested": ImageDesignSaveTarget(scope="private", gallery_id=None),
                "job_id": f"idj_{uuid.uuid4().hex}",
                "workflow": "room_adapt",
                "mp_fixed_target": True,
            }
            await attach_generated_asset(db, **kwargs)
            await db.commit()
            await attach_generated_asset(db, **kwargs)
            items = (
                (
                    await db.execute(
                        select(ContentMaterialLibraryItem).where(ContentMaterialLibraryItem.asset_id == asset_id)
                    )
                )
                .scalars()
                .all()
            )
            refs = (
                (await db.execute(select(ImageDesignLibraryItem).where(ImageDesignLibraryItem.asset_id == asset_id)))
                .scalars()
                .all()
            )
            assert len(items) == len(refs) == 1
            assert items[0].category == gallery.id
            assert items[0].category_owner_uid == gallery.owner_uid
            refs[0].hidden_at = utc_now_naive()
            await db.commit()
            await attach_generated_asset(db, **kwargs)
            assert refs[0].hidden_at is not None
            assert items[0].deleted_at is None
    finally:
        async with sessions() as db:
            await db.execute(delete(ImageDesignLibraryItem).where(ImageDesignLibraryItem.asset_id == asset_id))
            await db.execute(delete(ContentMaterialLibraryItem).where(ContentMaterialLibraryItem.asset_id == asset_id))
            await db.execute(delete(ContentCoverAsset).where(ContentCoverAsset.id == asset_id))
            await db.commit()
        await engine.dispose()
