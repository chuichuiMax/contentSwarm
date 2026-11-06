"""Real HTTP and PostgreSQL cover status checks in a disposable schema."""

import io
import asyncio

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import text

from server.routers.content_cover_router import content_covers
from server.routers.mp_router import mp
from server.utils.auth_middleware import get_db
from test.integration.api.test_case_visit_router import visit_api as visit_api
from yuxi.services.ai_cover_template_service import cover_generation_selection
from yuxi.storage.postgres.models_content import AiCoverCatalogOverride, AiCoverTemplate, ContentCoverJob, ContentType
from yuxi.storage.postgres.models_business import Base
from yuxi.storage.postgres.manager import PostgresManager

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
BASE = "/api/content/covers/ai-cover-prompts"


@pytest_asyncio.fixture
async def cover_api(visit_api):
    async with visit_api.sessions() as db:
        async with db.bind.begin() as connection:
            for model in (AiCoverTemplate, AiCoverCatalogOverride, ContentType):
                await connection.run_sync(model.__table__.create)

    async def committed_db():
        async with visit_api.sessions() as db:
            yield db
            await db.commit()

    visit_api.app.dependency_overrides[get_db] = committed_db
    visit_api.app.include_router(content_covers, prefix="/api")
    visit_api.app.include_router(mp, prefix="/api")
    return visit_api


def cover_upload(name):
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(image, "PNG")
    return {"file": (name, image.getvalue(), "image/png")}


async def test_http_cover_status_create_edit_filter_and_reenable(cover_api):
    client = cover_api.client
    admin = cover_api.tokens[3]
    form = {"name": "测试封面", "prompt": "测试提示词", "content_type_names": ["装修案例分享"]}
    created = await client.post(BASE + "/manage", headers=admin, data=form, files=cover_upload("default.png"))
    assert created.status_code == 201, created.text
    enabled = created.json()["cover"]
    assert enabled["enabled"] is True
    created = await client.post(
        BASE + "/manage", headers=admin, data={**form, "enabled": "false"}, files=cover_upload("disabled.png")
    )
    assert created.status_code == 201, created.text
    disabled = created.json()["cover"]
    assert disabled["enabled"] is False

    for variant in (enabled["variant"], "p17"):
        changed = await client.patch(BASE + f"/manage/{variant}/status", headers=admin, json={"enabled": False})
        assert changed.status_code == 200 and changed.json()["cover"]["enabled"] is False
    managed = (await client.get(BASE + "/manage", headers=admin)).json()["covers"]
    by_id = {row["variant"]: row for row in managed}
    assert by_id[enabled["variant"]]["enabled"] is False
    assert by_id[disabled["variant"]]["enabled"] is False
    assert by_id["p17"]["prompt_body"]
    assert by_id["smart-random"]["enabled"] is True

    for endpoint, headers in [
        (BASE, cover_api.tokens[1]),
        ("/api/mp/content/ai-cover-prompts", cover_api.mp_tokens[1]),
    ]:
        response = await client.get(endpoint, headers=headers, params={"content_type_name": "装修案例分享"})
        assert response.status_code == 200, response.text
        visible = {row["variant"] for row in response.json()["prompts"]}
        assert not {enabled["variant"], disabled["variant"], "p17"} & visible
        assert "smart-random" in visible

    preview = await client.get(BASE + f"/{disabled['variant']}/preview.png")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
    edited = await client.put(BASE + f"/manage/{disabled['variant']}", headers=admin, data={**form, "name": "已改名"})
    assert edited.status_code == 200 and edited.json()["cover"]["enabled"] is False
    response = await client.patch(BASE + f"/manage/{disabled['variant']}/status", headers=admin, json={"enabled": True})
    assert response.status_code == 200
    response = await client.get(BASE, headers=admin, params={"content_type_name": "装修案例分享"})
    selected = next(row for row in response.json()["prompts"] if row["variant"] == disabled["variant"])
    assert selected["title"] == "已改名" and selected["prompt_body"] == form["prompt"]
    async with cover_api.sessions() as db:
        selection = await cover_generation_selection(
            db,
            title="真实标题",
            subtitle="真实副标题",
            form_values={},
            content_type_code="CT05",
            content_type_name="装修案例分享",
            prompt_controls={"prompt_random": False, "prompt_variant": disabled["variant"]},
        )
        assert selection.variant == disabled["variant"] and selection.prompt == form["prompt"]


async def test_http_status_requires_admin_validates_input_and_keeps_deleted_hidden(cover_api):
    client = cover_api.client
    url = BASE + "/manage/p1/status"
    assert (await client.patch(url, json={"enabled": False})).status_code == 401
    assert (await client.patch(url, headers=cover_api.tokens[1], json={"enabled": False})).status_code == 403
    admin = cover_api.tokens[3]
    for body in ({}, {"enabled": None}, {"enabled": "false"}, {"enabled": 0}):
        assert (await client.patch(url, headers=admin, json=body)).status_code == 422
    assert (
        await client.patch(BASE + "/manage/missing/status", headers=admin, json={"enabled": True})
    ).status_code == 404
    smart = await client.patch(BASE + "/manage/smart-random/status", headers=admin, json={"enabled": False})
    assert smart.status_code == 422 and smart.json()["detail"]["error"]["code"] == "AI_COVER_STATUS_UNSUPPORTED"
    assert (await client.delete(BASE + "/manage/p1", headers=admin)).status_code == 200
    assert (await client.patch(url, headers=admin, json={"enabled": True})).status_code == 404
    managed = (await client.get(BASE + "/manage", headers=admin)).json()["covers"]
    assert "p1" not in {row["variant"] for row in managed}


async def test_concurrent_first_status_updates_preserve_native_template_fields(cover_api):
    responses = await asyncio.gather(
        *(
            cover_api.client.patch(BASE + "/manage/p17/status", headers=cover_api.tokens[3], json={"enabled": False})
            for _ in range(4)
        )
    )
    assert [response.status_code for response in responses] == [200] * 4
    async with cover_api.sessions() as db:
        row = await db.get(AiCoverCatalogOverride, "p17")
        assert row.enabled is False and row.prompt == "" and row.name == "无文案和谐画面"


async def test_mp_form_schema_preload_and_list_both_exclude_disabled_templates(cover_api):
    async with cover_api.sessions() as db:
        async with db.bind.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        db.add(ContentType(id="case-type", type_code="CT01", name="装修案例分享", enabled=True, created_by="test"))
        db.add(
            AiCoverTemplate(
                id="caaaaaaaaaaaaaaaa",
                name="禁用的全类型封面",
                prompt="测试提示词",
                content_type_names=[],
                file_name="disabled-preload.png",
                media_type="image/png",
                image=b"preview",
                enabled=False,
                created_by="test",
            )
        )
        await db.commit()
    changed = await cover_api.client.patch(
        BASE + "/manage/p1/status", headers=cover_api.tokens[3], json={"enabled": False}
    )
    assert changed.status_code == 200
    response = await cover_api.client.get(
        "/api/mp/content/form-schema",
        headers=cover_api.mp_tokens[1],
        params={"service_entry": "装修家居", "include_hycanvas_templates": "false"},
    )
    assert response.status_code == 200, response.text
    preloaded = response.json()["ai_cover_prompts_by_type_code"]["CT01"]
    listed = await cover_api.client.get(
        "/api/mp/content/ai-cover-prompts",
        headers=cover_api.mp_tokens[1],
        params={"content_type_code": "CT01", "content_type_name": "装修案例分享"},
    )
    assert listed.status_code == 200 and listed.json()["prompts"] == preloaded
    variants = {item["variant"] for item in preloaded}
    assert not {"p1", "caaaaaaaaaaaaaaaa"} & variants
    assert {"smart-random", "p12", "p17"} <= variants


async def test_http_idempotent_generation_returns_existing_job_after_disabling_template(cover_api):
    async with cover_api.sessions() as db:
        async with db.bind.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        db.add(
            ContentCoverJob(
                id="existing-cover-http",
                owner_uid="visitor-test-user-3",
                mode="image_to_image",
                status="queued",
                idempotency_key="existing-cover-http-request",
                request_json={"prompt": "原提示词快照"},
            )
        )
        await db.commit()
    disabled = await cover_api.client.patch(
        BASE + "/manage/p17/status", headers=cover_api.tokens[3], json={"enabled": False}
    )
    assert disabled.status_code == 200
    payload = {
        "mode": "image_to_image",
        "source_asset_ids": ["original-source"],
        "prompt": "原提示词快照",
        "idempotency_key": "existing-cover-http-request",
        "parameters": {"ai_cover_prompt_variant": "p17"},
    }
    for _ in range(2):
        result = await cover_api.client.post("/api/content/covers/generate", headers=cover_api.tokens[3], json=payload)
        assert result.status_code == 202, result.text
        assert result.json()["job"]["id"] == "existing-cover-http"
        assert result.json()["job"]["request"] == {"prompt": "原提示词快照"}
        assert result.json()["deduplicated"] is True
    new = await cover_api.client.post(
        "/api/content/covers/generate",
        headers=cover_api.tokens[3],
        json={**payload, "idempotency_key": "different-cover-http-request"},
    )
    assert new.status_code == 422 and new.json()["detail"]["error"]["code"] == "AI_COVER_DISABLED"
    other = await cover_api.client.post("/api/content/covers/generate", headers=cover_api.tokens[1], json=payload)
    assert other.status_code == 422 and other.json()["detail"]["error"]["code"] == "AI_COVER_DISABLED"


async def test_existing_database_migration_defaults_to_enabled_and_preserves_subsequent_disabling(cover_api):
    async with cover_api.sessions() as db:
        engine = db.bind
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        for table in ("ai_cover_templates", "ai_cover_catalog_overrides"):
            await connection.execute(text(f"ALTER TABLE {table} DROP COLUMN enabled"))
        await connection.execute(
            text(
                "INSERT INTO ai_cover_catalog_overrides (variant, name, prompt, content_type_names, hidden) "
                "VALUES ('p17', 'legacy', '', '[]', false)"
            )
        )
    manager = object.__new__(PostgresManager)
    manager.async_engine = engine
    manager._initialized = True
    await manager.ensure_content_schema()
    async with engine.begin() as connection:
        assert (
            await connection.execute(text("SELECT enabled FROM ai_cover_catalog_overrides WHERE variant = 'p17'"))
        ).scalar_one() is True
        await connection.execute(text("UPDATE ai_cover_catalog_overrides SET enabled = false WHERE variant = 'p17'"))
    await manager.ensure_content_schema()
    async with engine.connect() as connection:
        assert (
            await connection.execute(text("SELECT enabled FROM ai_cover_catalog_overrides WHERE variant = 'p17'"))
        ).scalar_one() is False
