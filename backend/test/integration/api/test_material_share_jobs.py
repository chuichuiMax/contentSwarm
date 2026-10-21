"""Real PostgreSQL and HTTP boundaries in a disposable schema; storage is deterministic."""

import asyncio
import os
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.material_library_router import material_library, public_share_router
from server.routers.mp_router import mp
from server.utils.auth_middleware import get_db, get_mp_context
from test.unit.services.test_material_share_performance import MemoryStorage, image_bytes
from yuxi.services import material_library_service as library
from yuxi.services import material_share_images as images
from yuxi.services import material_share_jobs as jobs
from yuxi.storage.postgres.models_business import Base, OperationLog, User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
    ContentMaterialShare,
    ContentMaterialShareItem,
    ContentMaterialShareJob,
    ContentEmployee,
    ContentCaseVisitLead,
    ContentCaseVisitSubmission,
)
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def env(monkeypatch):
    schema = f"test_share_jobs_{uuid.uuid4().hex}"
    url = os.environ["POSTGRES_URL"].replace("postgresql://", "postgresql+asyncpg://", 1)
    setup = create_async_engine(url)
    async with setup.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tables = {
        model.__table__
        for model in (
            User,
            OperationLog,
            ContentCoverAsset,
            ContentMaterialCategory,
            ContentMaterialLibraryItem,
            ContentMaterialShare,
            ContentMaterialShareItem,
            ContentMaterialShareJob,
            ContentEmployee,
            ContentCaseVisitLead,
            ContentCaseVisitSubmission,
        )
    }
    while True:
        dependencies = {key.column.table for table in tables for key in table.foreign_keys}
        if dependencies <= tables:
            break
        tables |= dependencies
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=list(tables)))

    storage = MemoryStorage()
    monkeypatch.setattr(library, "get_minio_client", lambda: storage)
    monkeypatch.setattr(images, "get_minio_client", lambda: storage)
    monkeypatch.setattr(library, "write_share_image_cache", AsyncMock())
    enqueue = AsyncMock()
    monkeypatch.setattr(jobs, "enqueue_share", enqueue)
    monkeypatch.setattr(jobs.pg_manager, "get_async_session_context", sessions)
    user = User(id=1, uid="sender", username="sender", password_hash="unused", role="superadmin")
    employee = SimpleNamespace(id="employee", name="分享员工", login_account="19900000001")
    raw = image_bytes()
    async with sessions() as db:
        db.add(user)
        db.add(
            ContentMaterialCategory(
                owner_uid="sender",
                material_type="image",
                id="case",
                name="图库",
                parent_id="root",
                visibility="private",
            )
        )
        for index in (1, 2):
            asset = ContentCoverAsset(
                id=f"a{index}",
                owner_uid="sender",
                role="input",
                sha256=f"hash{index}",
                bucket_name="image",
                object_name=f"version-{index}.png",
                file_size=len(raw),
                image_width=1600,
                image_height=1000,
                content_type="image/png",
                original_file_name=f"{index}.png",
                metadata_json={},
            )
            db.add(asset)
            await db.flush()
            db.add(
                ContentMaterialLibraryItem(
                    id=f"i{index}",
                    owner_uid="sender",
                    asset_id=asset.id,
                    material_type="image",
                    display_name=f"{index}",
                    category="case",
                )
            )
            storage.objects["image", asset.object_name] = raw
            await images.ensure_material_share_images(asset, raw)
        await db.commit()

    app = FastAPI()
    app.include_router(mp, prefix="/api")
    app.include_router(material_library, prefix="/api")
    app.include_router(public_share_router)

    async def db_dependency():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_mp_context] = lambda: SimpleNamespace(user=user, employee=employee)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://share.test") as client:
        yield SimpleNamespace(
            sessions=sessions, client=client, app=app, user=user, employee=employee,
            storage=storage, enqueue=enqueue, raw=raw
        )
    await engine.dispose()
    async with setup.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await setup.dispose()


async def accept(env, token, ids=None):
    async with env.sessions() as db:
        return await jobs.accept_material_share(
            db, env.user, jobs.MaterialShareRequest(item_ids=ids or ["i2", "i1"], request_token=token), env.employee
        )


async def test_http_unknown_pending_completed_and_legacy_contract(env):
    token = "a" * 64
    response = await env.client.get(f"/api/material-library/shares/{token}")
    assert response.status_code == 200 and response.json()["share"]["status"] == "unknown"
    assert response.headers["cache-control"] == "no-store"
    env.enqueue.assert_not_awaited()  # Public reads never submit work.
    response = await env.client.post("/api/mp/share/cases", json={"item_ids": ["i2", "i1"], "request_token": token})
    assert response.status_code == 201, response.text
    assert response.json()["share"]["status"] == "pending"
    page = await env.client.get(f"/share/case/{token}")
    assert "案例正在准备中" in page.text and page.headers["cache-control"] == "no-store"
    assert "version-" not in page.text and "sender" not in page.text
    await jobs.process_material_share({}, token)
    response = await env.client.get(f"/api/material-library/shares/{token}")
    share = response.json()["share"]
    assert share["status"] == "completed"
    assert [row["file_name"] for row in share["images"]] == ["2.png", "1.png"]
    assert share["sharer_name"] == "分享员工"
    async with env.sessions() as db:
        job = await db.get(ContentMaterialShareJob, token)
        stored = await db.scalar(select(ContentMaterialShare).where(ContentMaterialShare.token == token))
        assert stored.source_platform == "mp"
        assert stored.created_at == job.created_at
    assert (await env.client.get(f"/share/case/{token}")).status_code == 200
    response = await env.client.post("/api/mp/share/cases", json={"item_ids": ["i1"]})
    assert response.status_code == 201, response.text
    assert len(response.json()["share"]["id"]) == 32
    async with env.sessions() as db:
        stored = await db.scalar(
            select(ContentMaterialShare).where(ContentMaterialShare.token == response.json()["share"]["id"])
        )
        assert stored.source_platform == "mp"


async def test_concurrent_duplicate_and_conflicting_order_or_owner(env):
    token = "b" * 64
    results = await asyncio.gather(*(accept(env, token) for _ in range(6)))
    assert all(result["share"]["id"] == token for result in results)
    async with env.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(ContentMaterialShareJob)) == 1
        with pytest.raises(HTTPException) as failure:
            await jobs.accept_material_share(
                db, env.user, jobs.MaterialShareRequest(item_ids=["i1", "i2"], request_token=token), env.employee
            )
        assert failure.value.status_code == 409
        with pytest.raises(HTTPException) as failure:
            await jobs.accept_material_share(
                db,
                SimpleNamespace(uid="other"),
                jobs.MaterialShareRequest(item_ids=["i2", "i1"], request_token=token),
                env.employee,
            )
        assert failure.value.status_code == 409
    await asyncio.gather(jobs.process_material_share({}, token), jobs.process_material_share({}, token))
    async with env.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(ContentMaterialShare)) == 1


async def test_custom_contact_is_frozen_before_worker_and_conflicting_retry_rejected(env):
    token = "f" * 64
    payload = {
        "item_ids": ["i1"], "request_token": token,
        "contact_name": "对外联系人", "contact_phone": "13800000003",
        "sharer_uid": "forged-user",
    }
    for _ in range(2):
        response = await env.client.post("/api/mp/share/cases", json=payload)
        assert response.status_code == 201, response.text
    for changed in [{"contact_phone": "13900000004"}, {"contact_name": "另一联系人"}]:
        response = await env.client.post("/api/mp/share/cases", json={**payload, **changed})
        assert response.status_code == 409, response.text
    env.employee.name = "员工资料后来修改"
    await jobs.process_material_share({}, token)
    public = (await env.client.get(f"/api/material-library/shares/{token}")).json()["share"]
    assert (public["sharer_name"], public["sharer_phone"]) == ("对外联系人", "13800000003")
    assert env.employee.login_account == "19900000001"
    async with env.sessions() as db:
        stored = await db.scalar(select(ContentMaterialShare).where(ContentMaterialShare.token == token))
        assert stored.sharer_uid == "sender" and stored.sharer_employee_id == "employee"
        assert await db.scalar(select(func.count()).select_from(ContentMaterialShareJob)) == 1
        assert await db.scalar(select(func.count()).select_from(ContentMaterialShare)) == 1


async def test_versions_are_frozen_and_pending_source_cannot_be_deleted(env, monkeypatch):
    token = "c" * 64
    await accept(env, token)
    # Keep unrelated task/template usage queries out of this source-deletion regression.
    from yuxi.repositories.content_cover_repository import ContentCoverRepository

    monkeypatch.setattr(ContentCoverRepository, "asset_is_in_active_job", AsyncMock(return_value=False))
    monkeypatch.setattr(library.MaterialLibraryRepository, "get_poster_template_by_asset", AsyncMock(return_value=None))
    monkeypatch.setattr(library.MaterialLibraryRepository, "item_is_selected_by_task", AsyncMock(return_value=False))
    async with env.sessions() as db:
        with pytest.raises(HTTPException) as failure:
            await library.delete_material_item(db, env.user, "i2")
        assert failure.value.detail["error"]["code"] == "MATERIAL_SHARE_IN_PROGRESS"
        await db.rollback()
        asset = await db.get(ContentCoverAsset, "a2")
        asset.role, asset.metadata_json = "output", {"domain": "image_design"}
        await db.commit()
        from yuxi.image_design.service import delete_result

        with pytest.raises(HTTPException) as failure:
            await delete_result(db, env.user, "a2")
        assert failure.value.status_code == 409
        await db.rollback()
        asset = await db.get(ContentCoverAsset, "a2")
        asset.object_name, asset.sha256 = "later-version.png", "later-hash"
        category = await db.get(ContentMaterialCategory, ("sender", "image", "case"))
        category.name = "修改后的图库"
        await db.commit()
    await jobs.process_material_share({}, token)
    async with env.sessions() as db:
        state = await jobs.read_material_share_state(db, token)
        assert state["share"]["gallery_name"] == "图库"
        job = await db.get(ContentMaterialShareJob, token)
        assert job.sources_json[0]["object_name"] == "version-2.png"
        assert not await jobs.asset_has_pending_share(db, "a2")
        snapshot = await db.get(ContentMaterialShareItem, (job.share_json["id"], 1))
        assert env.storage.objects[snapshot.bucket_name, snapshot.object_name] == env.raw


async def test_pending_share_blocks_cover_asset_deletion_before_storage(env, monkeypatch):
    from yuxi.repositories.content_cover_repository import ContentCoverRepository
    from yuxi.services import content_cover_service as covers

    token = "9" * 64
    await accept(env, token, ["i1"])
    monkeypatch.setattr(covers, "get_minio_client", lambda: env.storage)
    monkeypatch.setattr(ContentCoverRepository, "asset_is_in_active_job", AsyncMock(return_value=False))
    async with env.sessions() as db:
        with pytest.raises(HTTPException) as failure:
            await covers.delete_cover_asset(db, env.user, "a1")
        assert failure.value.status_code == 409
        assert failure.value.detail["error"]["code"] == "MATERIAL_SHARE_IN_PROGRESS"
        await db.rollback()
        asset = await db.get(ContentCoverAsset, "a1")
        assert asset.deleted_at is None
        assert env.storage.objects[asset.bucket_name, asset.object_name] == env.raw
    await jobs.process_material_share({}, token)
    async with env.sessions() as db:
        assert (await jobs.read_material_share_state(db, token))["share"]["status"] == "completed"


async def test_worker_failure_public_state_and_rejected_selection(env):
    token = "d" * 64
    await accept(env, token)
    env.storage.fail_copy = "1.png.display.webp"
    await jobs.process_material_share({}, token)
    response = await env.client.get(f"/api/material-library/shares/{token}")
    assert response.json()["share"]["status"] == "failed"
    assert set(response.json()["share"]) == {"id", "status", "title"}
    async with env.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(ContentMaterialShare)) == 0
    assert not any("material-library-shares/" in name for _, name in env.storage.objects)
    rejected = "e" * 64
    with pytest.raises(HTTPException):
        await accept(env, rejected, ["not-owned-or-missing"])
    response = await env.client.get(f"/api/material-library/shares/{rejected}")
    assert response.json()["share"]["status"] == "failed"
    assert "not-owned" not in response.text


async def test_cancelled_worker_recovery_and_queue_outage(env, monkeypatch):
    token = "f" * 64
    env.enqueue.side_effect = ConnectionError("redis down")
    assert (await accept(env, token))["share"]["status"] == "pending"
    original = jobs.store_material_share_snapshot
    monkeypatch.setattr(jobs, "store_material_share_snapshot", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await jobs.process_material_share({}, token)
    async with env.sessions() as db:
        assert (await db.get(ContentMaterialShareJob, token)).status == "pending"
        assert await jobs.asset_has_pending_share(db, "a1")
    monkeypatch.setattr(jobs, "store_material_share_snapshot", original)
    # Dispatch can immediately execute the worker: no dispatcher lock is held at enqueue time.
    env.enqueue.side_effect = lambda token: None

    async def run(token):
        await jobs.process_material_share({}, token)

    env.enqueue.side_effect = run
    await jobs.dispatch_material_shares({})
    async with env.sessions() as db:
        assert (await db.get(ContentMaterialShareJob, token)).status == "completed"


async def test_expired_queue_and_token_validation(env):
    token = "1" * 64
    await accept(env, token)
    async with env.sessions() as db:
        job = await db.get(ContentMaterialShareJob, token)
        job.created_at = utc_now_naive() - timedelta(minutes=31)
        await db.commit()
    await jobs.dispatch_material_shares({})
    async with env.sessions() as db:
        assert (await db.get(ContentMaterialShareJob, token)).status == "failed"
        assert not await jobs.asset_has_pending_share(db, "a1")
    for invalid in ("guessable", "a" * 63, "g" * 64):
        response = await env.client.post("/api/mp/share/cases", json={"item_ids": ["i1"], "request_token": invalid})
        assert response.status_code == 422
    page = jobs.render_share_status_page(
        token, {"share": {"status": "pending", "title": "<script>alert(1)</script>"}}, "https://share.test"
    )
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page and "attempts>15" in page


async def test_source_deleted_while_acceptance_waits_for_asset_lock_is_rejected(env, monkeypatch):
    prepared = asyncio.Event()
    original = jobs.prepare_material_share_snapshot

    async def prepare(*args, **kwargs):
        result = await original(*args, **kwargs)
        prepared.set()
        return result

    monkeypatch.setattr(jobs, "prepare_material_share_snapshot", prepare)
    async with env.sessions() as deleting:
        asset = await deleting.scalar(select(ContentCoverAsset).where(ContentCoverAsset.id == "a1").with_for_update())
        asset.deleted_at = utc_now_naive()
        request = asyncio.create_task(accept(env, "2" * 64))
        await asyncio.wait_for(prepared.wait(), 3)
        await deleting.commit()
        with pytest.raises(HTTPException) as failure:
            await request
        assert failure.value.status_code == 404
    async with env.sessions() as db:
        assert (await db.get(ContentMaterialShareJob, "2" * 64)).status == "failed"
    env.enqueue.assert_not_awaited()
