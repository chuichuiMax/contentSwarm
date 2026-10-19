"""Local API -> Redis/ARQ -> worker -> MinIO -> anonymous case page."""

import asyncio
import hashlib
import os
import secrets
import time
import uuid

import pytest
from sqlalchemy import delete, select

from test.integration.api import test_material_library_router as fixtures
from yuxi.storage.minio.client import MinIOClient
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
    ContentMaterialShare,
    ContentMaterialShareJob,
)

material_users = fixtures.material_users
material_mp_headers = fixtures.material_mp_headers
pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_channel_request_runs_through_live_worker_and_public_images(
    test_client,
    material_users,
    material_mp_headers,
    record_property,
):
    assert test_client.base_url.host in {"localhost", "127.0.0.1", "api-dev"}
    assert os.getenv("STORAGE_BACKEND", "minio") == "minio"
    uid = material_users["owner_uid"]
    token = secrets.token_hex(32)
    category_id, item_id, asset_id = [uuid.uuid4().hex for _ in range(3)]
    raw = fixtures._png()
    storage = MinIOClient()
    name = f"pytest-channel-share/{uid}/{asset_id}.png"
    await storage.aupload_file("image", name, raw, content_type="image/png")
    sessions = material_users["session_factory"]
    async with sessions() as db:
        db.add(
            ContentMaterialCategory(
                owner_uid=uid, material_type="image", id=category_id, name="渠道触发测试案例", parent_id="test-root"
            )
        )
        db.add(
            ContentCoverAsset(
                id=asset_id,
                owner_uid=uid,
                role="input",
                original_file_name="测试.png",
                content_type="image/png",
                file_size=len(raw),
                image_width=48,
                image_height=36,
                sha256=hashlib.sha256(raw).hexdigest(),
                bucket_name="image",
                object_name=name,
            )
        )
        await db.flush()
        db.add(
            ContentMaterialLibraryItem(
                id=item_id,
                owner_uid=uid,
                asset_id=asset_id,
                material_type="image",
                display_name="测试",
                category=category_id,
            )
        )
        await db.commit()
    try:
        unknown = await test_client.get(f"/api/material-library/shares/{token}")
        assert unknown.status_code == 200 and unknown.json()["share"]["status"] == "unknown"
        started = time.monotonic()
        payload = {"item_ids": [item_id], "request_token": token}
        accepted = await test_client.post("/api/mp/share/cases", json=payload, headers=material_mp_headers)
        accepted_seconds = time.monotonic() - started
        assert accepted.status_code == 201, accepted.text
        assert accepted.json()["share"]["status"] in {"pending", "completed"}
        duplicate = await test_client.post("/api/mp/share/cases", json=payload, headers=material_mp_headers)
        assert duplicate.status_code == 201 and duplicate.json()["share"]["id"] == token
        for _ in range(60):
            response = await test_client.get(f"/api/material-library/shares/{token}")
            assert response.status_code == 200, response.text
            state = response.json()["share"]
            assert state["status"] != "failed", state
            if state["status"] == "completed":
                break
            await asyncio.sleep(0.5)
        assert state["status"] == "completed", state
        record_property("accept_seconds", round(accepted_seconds, 3))
        record_property("complete_seconds", round(time.monotonic() - started, 3))
        print(f"local share accepted={accepted_seconds:.3f}s completed={time.monotonic() - started:.3f}s")
        assert len(state["images"]) == 1
        assert (await test_client.get(state["card_cover_url"])).headers["content-type"] == "image/jpeg"
        assert (await test_client.get(state["images"][0]["url"])).content == raw
        assert (await test_client.get(state["images"][0]["webp_url"])).headers["content-type"] == "image/webp"
        page = await test_client.get(f"/share/case/{token}")
        assert page.status_code == 200 and "渠道触发测试案例" in page.text
        async with sessions() as db:
            shares = (await db.scalars(select(ContentMaterialShare).where(ContentMaterialShare.token == token))).all()
            assert len(shares) == 1
            assert (await db.get(ContentMaterialShareJob, token)).status == "completed"
    finally:
        async with sessions() as db:
            await db.execute(delete(ContentMaterialShareJob).where(ContentMaterialShareJob.token == token))
            await db.commit()
        # The shared fixture removes only this test's users, source/snapshot objects and image cache.
