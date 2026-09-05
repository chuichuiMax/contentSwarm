from __future__ import annotations

import io
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import delete, select

import yuxi.services.content_cover_service as content_cover_service
import yuxi.services.content_cover_worker as content_cover_worker
from server.routers.content_cover_router import content_covers
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.storage.minio.client import get_minio_client
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentCoverEditProject,
    ContentCoverJob,
    ContentMaterialLibraryItem,
)


def _png(color: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (420, 560), color).save(output, format="PNG")
    return output.getvalue()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cover_editor_saves_scene_and_renders_new_output(monkeypatch: pytest.MonkeyPatch):
    if pg_manager._initialized:
        await pg_manager.close()
    pg_manager.initialize()
    await pg_manager.create_business_tables()
    owner_uid = f"cover-editor-{uuid.uuid4().hex}"
    user = SimpleNamespace(uid=owner_uid, department_id=None, role="user")
    stored_objects: list[tuple[str, str]] = []

    async def commit_without_external_queue(db, job):
        del job
        await db.commit()

    async def no_event(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(content_cover_service, "_enqueue", commit_without_external_queue)
    monkeypatch.setattr(content_cover_worker, "_emit", no_event)
    monkeypatch.setattr(content_cover_worker, "_check_cancelled", no_event)
    monkeypatch.setattr(content_cover_worker, "clear_cancel_signal", no_event)
    monkeypatch.setattr(content_cover_worker, "_notify_content_workflow", no_event)

    app = FastAPI()
    app.include_router(content_covers, prefix="/api")

    async def override_db():
        async with pg_manager.get_async_session_context() as db:
            yield db

    async def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_required_user] = override_user

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            source_ids = []
            for index, color in enumerate(("#B42318", "#175CD3"), start=1):
                response = await client.post(
                    "/api/content/covers/assets",
                    data={"role": "source"},
                    files={"file": (f"source-{index}.png", _png(color), "image/png")},
                )
                assert response.status_code == 201, response.text
                source_ids.append(response.json()["asset"]["id"])

            composed = await client.post(
                "/api/content/covers/compose",
                json={
                    "asset_ids": source_ids,
                    "template_id": "split_vertical",
                    "theme_id": "editorial_ink",
                    "size": "1080x1440",
                    "layout": {"gap": 16, "margin": 24},
                    "idempotency_key": f"compose-{uuid.uuid4().hex}",
                },
            )
            assert composed.status_code == 202, composed.text
            compose_job_id = composed.json()["job"]["id"]

        await content_cover_worker.process_content_cover_job({}, compose_job_id)
        async with pg_manager.get_async_session_context() as db:
            compose_job = await ContentCoverRepository(db).get_job(compose_job_id)
            source_output_id = compose_job.result_json["asset_ids"][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/content/covers/editor-projects",
                json={"asset_id": source_output_id},
            )
            assert created.status_code == 201, created.text
            project = created.json()["project"]
            assert project["editability"] == "flattened"

            scene = project["scene"]
            scene["layers"].append(
                {
                    "id": "title",
                    "layer_type": "text",
                    "name": "主标题",
                    "text": "编辑后的封面",
                    "x": 120,
                    "y": 160,
                    "width": 840,
                    "height": 180,
                    "font_size": 92,
                    "font_weight": 700,
                    "fill": "#FFFFFF",
                    "stroke": True,
                    "stroke_color": "#111111",
                    "stroke_width": 3,
                }
            )
            saved = await client.patch(
                f"/api/content/covers/editor-projects/{project['id']}",
                json={"expected_revision": 1, "scene": scene},
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["project"]["revision"] == 2

            rendered_job = await client.post(
                f"/api/content/covers/editor-projects/{project['id']}/render",
                json={
                    "expected_revision": 2,
                    "idempotency_key": f"editor-{uuid.uuid4().hex}",
                },
            )
            assert rendered_job.status_code == 202, rendered_job.text
            assert rendered_job.json()["job"]["model"] == "deterministic-canvas-v1"
            render_job_id = rendered_job.json()["job"]["id"]

        await content_cover_worker.process_content_cover_job({}, render_job_id)
        async with pg_manager.get_async_session_context() as db:
            render_job = await ContentCoverRepository(db).get_job(render_job_id)
            assert render_job.status == "succeeded", render_job.error_message
            edited_asset = await ContentCoverRepository(db).get_asset(render_job.result_json["asset_ids"][0])
            assert edited_asset.metadata_json["editor_render"] is True
            assert edited_asset.metadata_json["derived_from_asset_id"] == source_output_id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            file_response = await client.get(f"/api/content/covers/assets/{edited_asset.id}/file")
            assert file_response.status_code == 200
            restored = await client.post(
                "/api/content/covers/editor-projects",
                json={"asset_id": edited_asset.id},
            )
            assert restored.status_code == 201, restored.text
            restored_project = restored.json()["project"]
            assert len(restored_project["scene"]["layers"]) == 1
            assert restored_project["scene"]["layers"][0]["text"] == "编辑后的封面"
        with Image.open(io.BytesIO(file_response.content)) as image:
            assert image.size == (1080, 1440)
            assert image.format == "PNG"
    finally:
        async with pg_manager.get_async_session_context() as db:
            assets = list(
                (await db.execute(select(ContentCoverAsset).where(ContentCoverAsset.owner_uid == owner_uid))).scalars()
            )
            stored_objects = [(item.bucket_name, item.object_name) for item in assets]
            await db.execute(delete(ContentCoverEditProject).where(ContentCoverEditProject.owner_uid == owner_uid))
            await db.execute(delete(ContentCoverJob).where(ContentCoverJob.owner_uid == owner_uid))
            asset_ids = [item.id for item in assets]
            if asset_ids:
                await db.execute(
                    delete(ContentMaterialLibraryItem).where(ContentMaterialLibraryItem.asset_id.in_(asset_ids))
                )
            await db.execute(delete(ContentCoverAsset).where(ContentCoverAsset.owner_uid == owner_uid))
            await db.commit()
        for bucket_name, object_name in stored_objects:
            await get_minio_client().adelete_file(bucket_name, object_name)
        await pg_manager.close()
