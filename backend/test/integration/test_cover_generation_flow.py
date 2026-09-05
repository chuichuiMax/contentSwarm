from __future__ import annotations

import io
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image, ImageChops
from sqlalchemy import delete, select

from server.routers.content_cover_router import content_covers
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.repositories.content_repository import ContentRepository
import yuxi.services.content_cover_service as content_cover_service
import yuxi.services.content_cover_worker as content_cover_worker
from yuxi.content_cover.image2_client import Image2Config
from yuxi.content_cover.image2_settings import (
    get_image2_config_state,
    resolve_image2_config,
    save_image2_config,
)
from yuxi.content_cover.schemas import Image2Output, Image2Submission
from yuxi.content_cover.template_replication import analyze_template
from yuxi.content.rules import ensure_content_seed_data
from yuxi.storage.minio.client import get_minio_client
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import (
    ContentArtifact,
    ContentArtifactVersion,
    ContentCoverAsset,
    ContentCoverImage2Setting,
    ContentCoverJob,
    ContentMaterialLibraryItem,
    ContentRuleVersion,
    ContentTask,
    IndustryTemplateVersion,
)


def _png(color: str, size: tuple[int, int] = (420, 560)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


async def _reset_pg_manager() -> None:
    if pg_manager._initialized:
        await pg_manager.close()


async def _delete_owner_assets(db, owner_uid: str) -> None:
    asset_ids = select(ContentCoverAsset.id).where(ContentCoverAsset.owner_uid == owner_uid)
    await db.execute(delete(ContentMaterialLibraryItem).where(ContentMaterialLibraryItem.asset_id.in_(asset_ids)))
    await db.execute(delete(ContentCoverAsset).where(ContentCoverAsset.owner_uid == owner_uid))


@pytest.fixture(autouse=True)
async def isolate_postgres_event_loop():
    """Do not reuse asyncpg connections across pytest's per-test event loops."""
    await _reset_pg_manager()
    yield
    await _reset_pg_manager()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_image2_global_settings_persist_per_account_without_returning_key():
    pg_manager.initialize()
    await pg_manager.create_business_tables()
    owner_a = f"cover-config-a-{uuid.uuid4().hex}"
    owner_b = f"cover-config-b-{uuid.uuid4().hex}"

    try:
        async with pg_manager.get_async_session_context() as db:
            await save_image2_config(
                db,
                owner_uid=owner_a,
                base_url="https://relay-a.example.com/v1",
                api_key="secret-a",
            )
            await save_image2_config(
                db,
                owner_uid=owner_b,
                base_url="https://relay-b.example.com/v1",
                api_key="secret-b",
            )
            config_a = await resolve_image2_config(db, owner_uid=owner_a)
            config_b = await resolve_image2_config(db, owner_uid=owner_b)
            state_a = await get_image2_config_state(db, owner_uid=owner_a)

        assert (config_a.base_url, config_a.api_key) == (
            "https://relay-a.example.com/v1",
            "secret-a",
        )
        assert (config_b.base_url, config_b.api_key) == (
            "https://relay-b.example.com/v1",
            "secret-b",
        )
        assert state_a["source"] == "database"
        assert state_a["api_key_configured"] is True
        assert "api_key" not in state_a
    finally:
        async with pg_manager.get_async_session_context() as db:
            await db.execute(
                delete(ContentCoverImage2Setting).where(ContentCoverImage2Setting.owner_uid.in_([owner_a, owner_b]))
            )
            await db.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_compose_cover_runs_from_upload_to_stored_result(monkeypatch: pytest.MonkeyPatch):
    pg_manager.initialize()
    await pg_manager.create_business_tables()
    owner_uid = f"cover-e2e-{uuid.uuid4().hex}"
    user = SimpleNamespace(uid=owner_uid, department_id=None, role="user")
    asset_objects: list[tuple[str, str]] = []
    job_id = ""

    async def commit_without_external_queue(db, job):
        del job
        await db.commit()

    async def no_event(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(content_cover_service, "_enqueue", commit_without_external_queue)
    monkeypatch.setattr(content_cover_worker, "_emit", no_event)
    monkeypatch.setattr(content_cover_worker, "_check_cancelled", no_event)
    monkeypatch.setattr(content_cover_worker, "clear_cancel_signal", no_event)

    try:
        app = FastAPI()
        app.include_router(content_covers, prefix="/api")

        async def override_db():
            async with pg_manager.get_async_session_context() as db:
                yield db

        async def override_user():
            return user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_required_user] = override_user
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            bootstrap = await client.get("/api/content/covers/bootstrap")
            assert bootstrap.status_code == 200
            assert {item["id"] for item in bootstrap.json()["templates"]} >= {
                "grid_3x3",
                "split_vertical",
                "before_after",
                "card_stack",
            }

            uploaded_assets = []
            for index, color in enumerate(("#E5484D", "#3E63DD"), start=1):
                response = await client.post(
                    "/api/content/covers/assets",
                    data={"role": "source"},
                    files={"file": (f"source-{index}.png", _png(color), "image/png")},
                )
                assert response.status_code == 201, response.text
                uploaded_assets.append(response.json()["asset"])

            created = await client.post(
                "/api/content/covers/compose",
                json={
                    "asset_ids": [item["id"] for item in uploaded_assets],
                    "template_id": "split_vertical",
                    "theme_id": "editorial_ink",
                    "size": "1080x1440",
                    "layout": {"gap": 16, "margin": 24},
                    "idempotency_key": f"integration-{uuid.uuid4().hex}",
                },
            )
            assert created.status_code == 202, created.text
            job_id = created.json()["job"]["id"]

        await content_cover_worker.process_content_cover_job({}, job_id)
        async with pg_manager.get_async_session_context() as db:
            job = await ContentCoverRepository(db).get_job(job_id)

        assert job is not None
        assert job.status == "succeeded", job.error_message
        assert job.progress == 100
        assert len(job.result_json["asset_ids"]) == 1

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            restored = await client.get(f"/api/content/covers/jobs/{job_id}")
            history = await client.get("/api/content/covers/jobs")
            events = await client.get(f"/api/content/covers/jobs/{job_id}/events")
        assert restored.status_code == 200
        assert restored.json()["job"]["status"] == "succeeded"
        assert any(item["id"] == job_id for item in history.json()["items"])
        assert "event: end" in events.text

        async with pg_manager.get_async_session_context() as db:
            assets = (
                (await db.execute(ContentCoverAsset.__table__.select().where(ContentCoverAsset.owner_uid == owner_uid)))
                .mappings()
                .all()
            )
        output_asset = next(item for item in assets if item["role"] == "output")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            file_response = await client.get(f"/api/content/covers/assets/{output_asset['id']}/file")
        assert file_response.status_code == 200
        result_bytes = file_response.content
        with Image.open(io.BytesIO(result_bytes)) as image:
            assert image.size == (1080, 1440)
            assert image.format == "PNG"

        async def override_foreign_user():
            return SimpleNamespace(uid="foreign-user", department_id=None, role="user")

        app.dependency_overrides[get_required_user] = override_foreign_user
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            foreign_response = await client.get(f"/api/content/covers/assets/{output_asset['id']}/file")
        assert foreign_response.status_code == 404
    finally:
        async with pg_manager.get_async_session_context() as db:
            assets = (
                (await db.execute(ContentCoverAsset.__table__.select().where(ContentCoverAsset.owner_uid == owner_uid)))
                .mappings()
                .all()
            )
            asset_objects = [(item["bucket_name"], item["object_name"]) for item in assets]
            await db.execute(delete(ContentCoverJob).where(ContentCoverJob.owner_uid == owner_uid))
            await _delete_owner_assets(db, owner_uid)
            await db.commit()
        for bucket_name, object_name in asset_objects:
            await get_minio_client().adelete_file(bucket_name, object_name)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_compose_job_is_idempotent_cancellable_and_retryable(monkeypatch: pytest.MonkeyPatch):
    pg_manager.initialize()
    await pg_manager.create_business_tables()
    owner_uid = f"cover-e2e-{uuid.uuid4().hex}"
    user = SimpleNamespace(uid=owner_uid, department_id=None, role="user")
    asset_objects: list[tuple[str, str]] = []

    async def commit_without_external_queue(db, job):
        del job
        await db.commit()

    async def no_event(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(content_cover_service, "_enqueue", commit_without_external_queue)
    monkeypatch.setattr(content_cover_service, "publish_cancel_signal", no_event)
    monkeypatch.setattr(content_cover_worker, "_emit", no_event)
    monkeypatch.setattr(content_cover_worker, "clear_cancel_signal", no_event)

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
            asset_ids = []
            for index, color in enumerate(("#DE3C4B", "#2B59C3"), start=1):
                uploaded = await client.post(
                    "/api/content/covers/assets",
                    data={"role": "source"},
                    files={"file": (f"source-{index}.png", _png(color), "image/png")},
                )
                assert uploaded.status_code == 201, uploaded.text
                asset_ids.append(uploaded.json()["asset"]["id"])

            idempotency_key = f"integration-{uuid.uuid4().hex}"
            payload = {
                "asset_ids": asset_ids,
                "template_id": "split_horizontal",
                "theme_id": "swiss_accent",
                "size": "1080x1440",
                "layout": {},
                "idempotency_key": idempotency_key,
            }
            first = await client.post("/api/content/covers/compose", json=payload)
            duplicate = await client.post("/api/content/covers/compose", json=payload)
            assert first.status_code == duplicate.status_code == 202
            first_job_id = first.json()["job"]["id"]
            assert duplicate.json()["job"]["id"] == first_job_id
            assert duplicate.json()["deduplicated"] is True

            cancelled = await client.post(f"/api/content/covers/jobs/{first_job_id}/cancel")
            assert cancelled.status_code == 200
            assert cancelled.json()["job"]["status"] == "cancel_requested"

        await content_cover_worker.process_content_cover_job({}, first_job_id)
        async with pg_manager.get_async_session_context() as db:
            cancelled_job = await ContentCoverRepository(db).get_job(first_job_id)
        assert cancelled_job.status == "cancelled"

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            retried = await client.post(
                f"/api/content/covers/jobs/{first_job_id}/retry",
                json={"idempotency_key": f"retry-{uuid.uuid4().hex}"},
            )
        assert retried.status_code == 202, retried.text
        retry_job_id = retried.json()["job"]["id"]
        assert retried.json()["job"]["parent_job_id"] == first_job_id

        await content_cover_worker.process_content_cover_job({}, retry_job_id)
        async with pg_manager.get_async_session_context() as db:
            retry_job = await ContentCoverRepository(db).get_job(retry_job_id)
        assert retry_job.status == "succeeded", retry_job.error_message
    finally:
        async with pg_manager.get_async_session_context() as db:
            assets = (
                (await db.execute(ContentCoverAsset.__table__.select().where(ContentCoverAsset.owner_uid == owner_uid)))
                .mappings()
                .all()
            )
            asset_objects = [(item["bucket_name"], item["object_name"]) for item in assets]
            await db.execute(delete(ContentCoverJob).where(ContentCoverJob.owner_uid == owner_uid))
            await _delete_owner_assets(db, owner_uid)
            await db.commit()
        for bucket_name, object_name in asset_objects:
            await get_minio_client().adelete_file(bucket_name, object_name)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_async_image2_template_flow_stores_provider_task_and_result(
    monkeypatch: pytest.MonkeyPatch,
):
    pg_manager.initialize()
    await pg_manager.create_business_tables()
    owner_uid = f"cover-e2e-{uuid.uuid4().hex}"
    user = SimpleNamespace(uid=owner_uid, department_id=None, role="user")
    asset_objects: list[tuple[str, str]] = []
    captured_request = {}

    async def commit_without_external_queue(db, job):
        del job
        await db.commit()

    async def no_event(*args, **kwargs):
        del args, kwargs

    class FakeImage2Client:
        def __init__(self, config=None):
            captured_request["client_config"] = config

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback

        async def submit(self, request, *, idempotency_key=None):
            captured_request["request"] = request
            captured_request["idempotency_key"] = idempotency_key
            return Image2Submission(provider_task_id="provider-task-1", status="pending")

        async def poll(self, task_id):
            assert task_id == "provider-task-1"
            return Image2Submission(
                provider_task_id=task_id,
                status="completed",
                images=[Image2Output(b64_data="mock-result")],
            )

        async def read_output(self, output):
            assert output.b64_data == "mock-result"
            return _png("#815ACF", (1080, 1440)), "image/png"

    monkeypatch.setenv("IMAGE2_BASE_URL", "https://relay.example.com/v1")
    monkeypatch.setenv("IMAGE2_API_KEY", "test-key")
    monkeypatch.setenv("IMAGE2_MODEL", "image2-test")
    monkeypatch.setattr(content_cover_service, "_enqueue", commit_without_external_queue)
    monkeypatch.setattr(content_cover_worker, "_emit", no_event)
    monkeypatch.setattr(content_cover_worker, "_check_cancelled", no_event)
    monkeypatch.setattr(content_cover_worker, "clear_cancel_signal", no_event)

    async def load_image2_config(worker_owner_uid):
        assert worker_owner_uid == owner_uid
        return Image2Config.from_env()

    monkeypatch.setattr(content_cover_worker, "_load_image2_config", load_image2_config)
    monkeypatch.setattr(content_cover_worker, "Image2Client", FakeImage2Client)
    monkeypatch.setattr(content_cover_worker, "POLL_INTERVAL_SECONDS", 0)
    analysis = analyze_template(
        Image.new("RGB", (1080, 1440), "#247BA0"),
        target_size=(1080, 1440),
        ocr_blocks=[
            {"text": "OLD TITLE", "box": (120, 80, 900, 220)},
            {"text": "设计定制/环保定制", "box": (150, 245, 820, 310)},
        ],
    )
    monkeypatch.setattr(content_cover_worker, "analyze_template", lambda image, target_size: analysis)
    monkeypatch.setattr(
        content_cover_worker,
        "recognize_slot_texts",
        lambda image, current_analysis: "新品通勤指南设计定制/环保定制",
    )

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
            uploaded = {}
            for role, color in (("source", "#D64045"), ("template", "#247BA0")):
                response = await client.post(
                    "/api/content/covers/assets",
                    data={"role": role},
                    files={"file": (f"{role}.png", _png(color), "image/png")},
                )
                assert response.status_code == 201, response.text
                uploaded[role] = response.json()["asset"]["id"]

            created = await client.post(
                "/api/content/covers/generate",
                json={
                    "mode": "multi_reference",
                    "source_asset_ids": [uploaded["source"]],
                    "template_asset_id": uploaded["template"],
                    "title": "新品通勤指南",
                    "prompt": "",
                    "size": "1080x1440",
                    "n": 1,
                    "idempotency_key": f"integration-{uuid.uuid4().hex}",
                },
            )
            assert created.status_code == 202, created.text
            job_id = created.json()["job"]["id"]

        await content_cover_worker.process_content_cover_job({}, job_id)

        async with pg_manager.get_async_session_context() as db:
            job = await ContentCoverRepository(db).get_job(job_id)
            assets = (
                (await db.execute(ContentCoverAsset.__table__.select().where(ContentCoverAsset.owner_uid == owner_uid)))
                .mappings()
                .all()
            )
        assert job.status == "succeeded", job.error_message
        assert job.provider_task_id == "provider-task-1"
        assert captured_request["request"].mode == "multi_reference"
        assert len(captured_request["request"].source_images) == 1
        assert captured_request["request"].source_images[0].file_name.endswith("-reference.png")
        assert captured_request["request"].template_image.file_name.endswith("-reference.png")
        assert captured_request["request"].mask_image.file_name == "template-edit-mask.png"
        assert captured_request["idempotency_key"] == job_id
        assert "参考图1是用户原图" in captured_request["request"].prompt
        assert "参考图2是样式模板" in captured_request["request"].prompt
        assert "参考图1完整铺满画布" in captured_request["request"].prompt
        assert job.request_json["title"] == "新品通勤指南"
        assert "乱码文字" in captured_request["request"].negative_prompt
        assert "模板旧底图残留" in captured_request["request"].negative_prompt
        assert job.request_json["template_replicate"] is True
        assert job.request_json["parameters"]["template_replicate"] is True
        assert job.request_json["parameters"]["quality"] == "high"
        assert "input_fidelity" not in job.request_json["parameters"]
        assert job.request_json["parameters"]["output_format"] == "png"
        assert captured_request["client_config"].base_url == "https://relay.example.com/v1"
        assert captured_request["client_config"].api_key == "test-key"
        assert "template_settings" not in job.request_json

        output_asset = next(item for item in assets if item["role"] == "output")
        result_bytes = await get_minio_client().adownload_file(output_asset["bucket_name"], output_asset["object_name"])
        with Image.open(io.BytesIO(result_bytes)) as image:
            assert image.size == (1080, 1440)
            assert image.mode == "RGB"
            assert image.getpixel((20, 900)) == (214, 64, 69)
            assert image.getpixel((540, 900)) == (214, 64, 69)
            title_region = image.crop((120, 80, 900, 220))
            flat_background = Image.new("RGB", title_region.size, (214, 64, 69))
            difference = ImageChops.difference(title_region, flat_background).convert("L")
            assert difference.getbbox() is not None
    finally:
        async with pg_manager.get_async_session_context() as db:
            assets = (
                (await db.execute(ContentCoverAsset.__table__.select().where(ContentCoverAsset.owner_uid == owner_uid)))
                .mappings()
                .all()
            )
            asset_objects = [(item["bucket_name"], item["object_name"]) for item in assets]
            await db.execute(delete(ContentCoverJob).where(ContentCoverJob.owner_uid == owner_uid))
            await _delete_owner_assets(db, owner_uid)
            await db.commit()
        for bucket_name, object_name in asset_objects:
            await get_minio_client().adelete_file(bucket_name, object_name)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_selected_candidate_creates_new_artifact_version():
    pg_manager.initialize()
    await pg_manager.create_business_tables()
    owner_uid = f"cover-e2e-{uuid.uuid4().hex}"
    user = SimpleNamespace(uid=owner_uid, department_id=None, role="user")
    task_id = f"ct_{uuid.uuid4().hex}"
    artifact_id = f"car_{uuid.uuid4().hex}"
    asset_ids = [f"cca_{uuid.uuid4().hex}", f"cca_{uuid.uuid4().hex}"]
    job_id = f"ccj_{uuid.uuid4().hex}"

    async with pg_manager.get_async_session_context() as db:
        await ensure_content_seed_data(db)
        template = (
            await db.execute(select(IndustryTemplateVersion).order_by(IndustryTemplateVersion.created_at).limit(1))
        ).scalar_one()
        rule = (
            await db.execute(select(ContentRuleVersion).order_by(ContentRuleVersion.created_at).limit(1))
        ).scalar_one()
        await ContentRepository(db).create_task(
            task_id=task_id,
            user=user,
            name="封面版本测试",
            template=template,
            rule_version_id=rule.id,
            mode="quick",
            content_goal="acquire",
            project_id=None,
        )
        artifact = ContentArtifact(
            id=artifact_id,
            task_id=task_id,
            status="reviewed",
            current_version=1,
            title="内容标题",
            body="内容正文",
            topics=["封面"],
            review_snapshot={"status": "passed"},
            created_by=owner_uid,
        )
        db.add(artifact)
        db.add(
            ContentArtifactVersion(
                id=f"cav_{uuid.uuid4().hex}",
                artifact_id=artifact_id,
                version=1,
                title=artifact.title,
                body=artifact.body,
                topics=artifact.topics,
                source_type="generated",
                rule_version_id=rule.id,
                created_by=owner_uid,
            )
        )
        cover_repo = ContentCoverRepository(db)
        for index, asset_id in enumerate(asset_ids):
            await cover_repo.create_asset(
                id=asset_id,
                owner_uid=owner_uid,
                role="output",
                original_file_name=f"candidate-{index + 1}.png",
                content_type="image/png",
                file_size=100,
                image_width=1080,
                image_height=1440,
                sha256=f"{index + 1:064d}",
                bucket_name="content-covers",
                object_name=f"test/{asset_id}.png",
                metadata_json={"job_id": job_id},
            )
        await cover_repo.create_job(
            id=job_id,
            owner_uid=owner_uid,
            content_task_id=task_id,
            artifact_id=None,
            mode="text_to_image",
            status="succeeded",
            idempotency_key=f"integration-{uuid.uuid4().hex}",
            request_json={},
            result_json={"asset_ids": asset_ids},
            progress=100,
        )
        await db.commit()

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
            response = await client.post(
                f"/api/content/covers/jobs/{job_id}/set-current",
                json={"asset_id": asset_ids[1]},
            )
        assert response.status_code == 200, response.text
        assert response.json()["artifact"]["cover_asset_id"] == asset_ids[1]
        assert response.json()["artifact"]["current_version"] == 2

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            duplicate = await client.post(
                f"/api/content/covers/jobs/{job_id}/set-current",
                json={"asset_id": asset_ids[1]},
            )
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["artifact"]["current_version"] == 2

        async with pg_manager.get_async_session_context() as db:
            versions = (
                (
                    await db.execute(
                        select(ContentArtifactVersion)
                        .where(ContentArtifactVersion.artifact_id == artifact_id)
                        .order_by(ContentArtifactVersion.version)
                    )
                )
                .scalars()
                .all()
            )
            linked_job = await ContentCoverRepository(db).get_job(job_id)
        assert [item.cover_asset_id for item in versions] == [None, asset_ids[1]]
        assert versions[1].source_type == "cover_update"
        assert linked_job.artifact_id == artifact_id
    finally:
        async with pg_manager.get_async_session_context() as db:
            await db.execute(delete(ContentCoverJob).where(ContentCoverJob.owner_uid == owner_uid))
            await _delete_owner_assets(db, owner_uid)
            await db.execute(delete(ContentArtifactVersion).where(ContentArtifactVersion.artifact_id == artifact_id))
            await db.execute(delete(ContentArtifact).where(ContentArtifact.id == artifact_id))
            await db.execute(delete(ContentTask).where(ContentTask.id == task_id))
            await db.commit()
