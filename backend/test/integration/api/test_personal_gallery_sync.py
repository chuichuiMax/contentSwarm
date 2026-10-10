"""Real HTTP regression against an isolated DB; never modify business gallery definitions."""

import asyncio
import socket

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.material_library_router import material_library
from server.routers.mp_router import mp
from server.utils.auth_middleware import get_db
from yuxi.storage.postgres.models_business import Base, Department, User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentEmployee,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
)
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.asyncio
async def test_pc_global_management_syncs_authenticated_mp_accounts(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'galleries.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    headers = {}
    mp_headers = {}
    async with sessions() as db:
        department = Department(name="isolated-gallery-test")
        db.add(department)
        await db.flush()
        for index, (uid, role) in enumerate((("super", "superadmin"), ("alice", "admin"), ("bob", "user"))):
            phone = f"1380000000{index}"
            account = User(uid=uid, username=uid, password_hash="unused", role=role,
                           phone_number=phone, department_id=department.id)
            db.add(account)
            db.add(ContentEmployee(id=uid, employee_code=uid, name=uid, login_account=phone,
                                   gender="unknown", role="运营", login_port=["app"], created_by="super"))
            await db.flush()
            headers[uid] = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(account.id)})}"}
            mp_headers[uid] = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': uid, 'typ': 'mp'})}"}
            db.add(ContentMaterialCategory(owner_uid=uid, material_type="image", id="historical-rough",
                                           name="毛坯房图库", visibility="private", industry_slug="decoration"))
            db.add(ContentCoverAsset(id=f"asset-{uid}", owner_uid=uid, role="library_image",
                                    original_file_name="photo.jpg", content_type="image/jpeg", file_size=1,
                                    image_width=1, image_height=1, sha256=uid, bucket_name="image", object_name=uid))
            db.add(ContentMaterialLibraryItem(id=f"item-{uid}", owner_uid=uid, asset_id=f"asset-{uid}",
                                             material_type="image", display_name="photo", category="historical-rough",
                                             category_owner_uid=uid))
        await db.commit()

    async def isolated_db():
        async with sessions() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    app = FastAPI()
    app.include_router(material_library, prefix="/api")
    app.include_router(mp, prefix="/api")
    app.dependency_overrides[get_db] = isolated_db
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if serving.done():
                    await serving
                    pytest.fail("Isolated API exited before startup")
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
            path = "/api/material-library/categories/historical-rough?material_type=image"
            response = await client.patch(path, headers=headers["alice"], json={"name": "越权修改"})
            assert response.status_code == 403, response.text
            response = await client.patch(path, headers=headers["super"], json={
                "name": "施工照片", "description": "全员图库说明", "industry_slug": "uncategorized"})
            assert response.status_code == 200, response.text
            assert response.json()["category"]["name"] == "施工照片"

            for uid in ("alice", "bob"):
                folders = await client.get("/api/mp/content/my-materials/folders", headers=mp_headers[uid])
                assert folders.status_code == 200, folders.text
                rough = next(row for row in folders.json()["folders"] if row["id"] == "rough")
                assert (rough["name"], rough["gallery_id"], rough["count"]) == ("施工照片", "historical-rough", 1)
                galleries = await client.get("/api/material-library/galleries?industry_slug=uncategorized",
                                             headers=headers[uid])
                assert galleries.status_code == 200, galleries.text
                gallery = next(row for row in galleries.json()["galleries"] if row.get("personal_folder") == "rough")
                assert gallery["name"] == "施工照片"
                assert not gallery["can_edit"] and not gallery["can_delete"]

            # Rejecting an invalid migration target must leave every account intact.
            failed = await client.request("DELETE", path, headers=headers["super"],
                                          json={"target_category_id": "missing"})
            assert failed.status_code == 422, failed.text
            still_there = await client.get("/api/mp/content/my-materials/rough", headers=mp_headers["alice"])
            assert [row["id"] for row in still_there.json()["items"]] == ["item-alice"]
            response = await client.request("DELETE", path, headers=headers["super"],
                                            json={"target_category_id": "mp-uploads-private"})
            assert response.status_code == 200, response.text
            for uid in ("super", "alice", "bob"):
                for _ in range(2):
                    folders = await client.get("/api/mp/content/my-materials/folders", headers=mp_headers[uid])
                    assert "rough" not in {row["id"] for row in folders.json()["folders"]}
                uploads = await client.get("/api/mp/content/my-materials/uploads", headers=mp_headers[uid])
                assert [row["id"] for row in uploads.json()["items"]] == [f"item-{uid}"]
                deleted = await client.get("/api/mp/content/my-materials/rough", headers=mp_headers[uid])
                assert deleted.status_code == 409
        async with sessions() as db:
            for uid in ("super", "alice", "bob"):
                assert (await db.get(ContentCoverAsset, f"asset-{uid}")).deleted_at is None
                item = await db.get(ContentMaterialLibraryItem, f"item-{uid}")
                assert (item.owner_uid, item.category_owner_uid) == (uid, uid)
    finally:
        server.should_exit = True
        await serving
        listener.close()
        await engine.dispose()
