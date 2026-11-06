"""AI cover availability, native prompt preservation and regeneration boundaries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.content.schemas import ContentDirectGenerateCreate, ContentRunCreate
from yuxi.content_cover.ai_cover_prompt import job_selection_for_variant
from yuxi.content_cover.schemas import CoverGenerateCreate, CoverRetryCreate
from yuxi.services import ai_cover_template_service as service
from yuxi.services import content_cover_service as covers
from yuxi.services import content_service as content
from yuxi.storage.postgres.models_business import AgentRun
from yuxi.storage.postgres.models_content import AiCoverCatalogOverride, AiCoverTemplate, ContentCoverJob, ContentType

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
CUSTOM_ID = "c0123456789abcdef"


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        for model in (AiCoverTemplate, AiCoverCatalogOverride, ContentType, ContentCoverJob, AgentRun):
            await connection.run_sync(model.__table__.create)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(
            AiCoverTemplate(
                id=CUSTOM_ID,
                name="自定义封面",
                prompt="绘制主标题示例",
                content_type_names=["装修案例分享"],
                file_name="custom.png",
                media_type="image/png",
                image=b"preview",
                created_by="test",
            )
        )
        await session.flush()
        yield session
    await engine.dispose()


async def test_disabled_covers_remain_manageable_and_previewable_but_disappear_from_choices(db):
    await service.update_ai_cover_status(db, CUSTOM_ID, enabled=False)
    await service.update_ai_cover_status(db, "p1", enabled=False)
    managed = {row["variant"]: row for row in (await service.list_managed_covers(db))["covers"]}
    assert managed[CUSTOM_ID]["enabled"] is False
    assert managed["p1"]["enabled"] is False
    assert managed["smart-random"]["enabled"] is True
    assert await service.custom_cover_preview_bytes(db, CUSTOM_ID) == (b"preview", "image/png")
    assert await service.list_custom_cover_prompts(db) == []
    visible = await service.apply_catalog_overrides(db, list(managed.values()))
    assert "p1" not in {row["variant"] for row in visible}
    await service.update_ai_cover_status(db, CUSTOM_ID, enabled=True)
    assert (await service.list_custom_cover_prompts(db))[0]["variant"] == CUSTOM_ID


async def test_status_only_override_preserves_native_prompt_and_copy_behavior(db):
    await service.update_ai_cover_status(db, "p17", enabled=False)
    await service.update_ai_cover_status(db, "p17", enabled=True)
    row = await db.get(AiCoverCatalogOverride, "p17")
    assert row.prompt == ""
    actual = await service.cover_generation_selection(
        db,
        title="实际标题",
        subtitle="实际副标题",
        form_values={},
        content_type_code="CT02",
        content_type_name="装修避坑分享",
        prompt_controls={"prompt_random": False, "prompt_variant": "p17"},
    )
    expected = job_selection_for_variant("p17", title="实际标题", subtitle="实际副标题")
    assert actual == expected
    await service.validate_ai_cover_choice(
        db,
        content_type_name="新增内容类型",
        prompt_controls={"prompt_random": False, "prompt_variant": "p17"},
    )
    managed = (await service.list_managed_covers(db))["covers"]
    assert next(item for item in managed if item["variant"] == "p17")["prompt_body"]


async def test_mp_preloaded_catalog_matches_active_list_for_each_content_type(db):
    await service.update_ai_cover_status(db, CUSTOM_ID, enabled=False)
    await service.update_ai_cover_status(db, "p1", enabled=False)
    content_types = [
        {"type_code": "CT01", "name": "装修案例分享"},
        {"type_code": "CT02", "name": "装修报价清单"},
    ]
    preloaded = await service.list_mp_ai_cover_prompts_by_type_code(db, content_types)
    for item in content_types:
        listed = await service.list_mp_ai_cover_prompts(
            db, content_type_code=item["type_code"], content_type_name=item["name"]
        )
        assert preloaded[item["type_code"]] == listed["prompts"]
        variants = {prompt["variant"] for prompt in listed["prompts"]}
        assert not {CUSTOM_ID, "p1"} & variants
        assert "smart-random" in variants
    assert any(item["variant"] == "p12" for item in preloaded["CT01"])


async def test_empty_content_type_scope_applies_to_lists_and_generation(db):
    custom = await db.get(AiCoverTemplate, CUSTOM_ID)
    custom.content_type_names = []
    db.add(
        AiCoverCatalogOverride(
            variant="p1",
            name="全类型封面",
            prompt="自定义提示词",
            content_type_names=[],
            enabled=True,
            hidden=False,
        )
    )
    await db.flush()
    custom_choices = await service.list_custom_cover_prompts(db, content_type_name="新增内容类型")
    assert [item["variant"] for item in custom_choices] == [CUSTOM_ID]
    catalog = [{"variant": "p1", "title": "原名称", "prompt_body": "原提示词"}]
    listed = await service.apply_catalog_overrides(db, catalog, content_type_name="新增内容类型")
    assert listed[0]["variant"] == "p1" and listed[0]["prompt_body"] == "自定义提示词"
    for variant in (CUSTOM_ID, "p1"):
        await service.validate_ai_cover_choice(
            db,
            content_type_name="新增内容类型",
            prompt_controls={"prompt_random": False, "prompt_variant": variant},
        )


@pytest.mark.parametrize("variant", [CUSTOM_ID, "p1"])
async def test_explicit_disabled_selection_is_rejected_before_generation(db, variant):
    await service.update_ai_cover_status(db, variant, enabled=False)
    with pytest.raises(HTTPException) as exc:
        await service.cover_generation_selection(
            db,
            title="实际标题",
            subtitle="实际副标题",
            form_values={},
            content_type_code="CT05",
            content_type_name="装修案例分享",
            prompt_controls={"prompt_random": False, "prompt_variant": variant},
        )
    assert exc.value.detail["error"]["code"] == "AI_COVER_DISABLED"


async def test_random_pool_excludes_disabled_covers_and_reports_empty_pool(db):
    for variant in service.catalog_cover_variants():
        if variant not in {"smart-random", "p17"}:
            await service.update_ai_cover_status(db, variant, enabled=False)
    for _ in range(3):
        actual = await service.cover_generation_selection(
            db,
            title="标题",
            subtitle="副标题",
            form_values={},
            content_type_code="CT02",
            content_type_name="装修避坑分享",
            prompt_controls={"prompt_random": True, "prompt_variant": CUSTOM_ID},
        )
        assert actual.variant == "p17"
    await service.update_ai_cover_status(db, "p17", enabled=False)
    with pytest.raises(HTTPException) as exc:
        await service.validate_ai_cover_choice(db, prompt_controls={"prompt_random": True})
    assert exc.value.detail["error"]["code"] == "AI_COVER_UNAVAILABLE"
    managed = (await service.list_managed_covers(db))["covers"]
    catalog = [item for item in managed if item["variant"] != CUSTOM_ID]
    visible = await service.apply_catalog_overrides(db, catalog, content_type_name="装修避坑分享")
    assert len(visible) == 1 and visible[0]["variant"] == "smart-random"
    assert visible[0]["enabled"] is True and visible[0]["selectable"] is False
    # Uploaded covers remain explicitly selectable even when the built-in random pool is empty.
    await service.validate_ai_cover_choice(db, prompt_controls={"prompt_random": False, "prompt_variant": CUSTOM_ID})


async def test_smart_random_status_is_rejected_and_deleted_cover_is_not_restored(db):
    with pytest.raises(HTTPException) as exc:
        await service.update_ai_cover_status(db, "smart-random", enabled=False)
    assert exc.value.detail["error"]["code"] == "AI_COVER_STATUS_UNSUPPORTED"
    await service.delete_ai_cover_template(db, "p1")
    with pytest.raises(HTTPException) as exc:
        await service.update_ai_cover_status(db, "p1", enabled=True)
    assert exc.value.status_code == 404
    assert (await db.get(AiCoverCatalogOverride, "p1")).hidden is True


async def test_edit_without_status_preserves_disabled_state_and_edited_fields(db):
    for variant in (CUSTOM_ID, "p1"):
        await service.update_ai_cover_status(db, variant, enabled=False)
        await service.update_ai_cover_template(
            db, variant, name="改名", prompt="新提示词", content_type_names=["装修案例分享"]
        )
        managed = (await service.list_managed_covers(db))["covers"]
        row = next(item for item in managed if item["variant"] == variant)
        assert row["enabled"] is False and row["title"] == "改名" and row["prompt_body"] == "新提示词"
        await service.update_ai_cover_status(db, variant, enabled=True)
        managed = (await service.list_managed_covers(db))["covers"]
        row = next(item for item in managed if item["variant"] == variant)
        assert row["prompt_body"] == "新提示词" and row["enabled"] is True


async def test_create_job_rejects_disabled_template_before_provider_work(db, monkeypatch):
    await service.update_ai_cover_status(db, "p1", enabled=False)
    provider = AsyncMock()
    monkeypatch.setattr(covers, "resolve_image2_config", provider)
    payload = CoverGenerateCreate(
        mode="image_to_image",
        source_asset_ids=["test-image"],
        prompt="请求快照",
        title="标题",
        idempotency_key="status-create-test",
        parameters={"ai_cover_prompt_variant": "p1"},
    )
    with pytest.raises(HTTPException) as exc:
        await covers.create_cover_generate_job(db, SimpleNamespace(uid="test"), payload)
    assert exc.value.detail["error"]["code"] == "AI_COVER_DISABLED"
    provider.assert_not_awaited()


@pytest.mark.parametrize("same_owner", [True, False])
async def test_idempotent_cover_request_reuses_own_job_after_template_disabled(db, monkeypatch, same_owner):
    await service.update_ai_cover_status(db, "p1", enabled=False)
    payload = CoverGenerateCreate(
        mode="image_to_image",
        source_asset_ids=["test-image"],
        prompt="原提示词",
        idempotency_key="existing-cover-request",
        parameters={"ai_cover_prompt_variant": "p1"},
    )
    job = ContentCoverJob(
        id="existing-cover",
        owner_uid="test" if same_owner else "another-user",
        mode="image_to_image",
        status="queued",
        idempotency_key=payload.idempotency_key,
        request_json=payload.model_dump(),
    )
    db.add(job)
    await db.flush()
    provider = AsyncMock()
    enqueue = AsyncMock()
    monkeypatch.setattr(covers, "resolve_image2_config", provider)
    monkeypatch.setattr(covers, "_enqueue", enqueue)
    if same_owner:
        result = await covers.create_cover_generate_job(db, SimpleNamespace(uid="test"), payload)
        assert result["job"]["id"] == job.id and result["deduplicated"] is True
        assert result["job"]["request"] == payload.model_dump()
        assert job.status == "queued"
    else:
        with pytest.raises(HTTPException) as exc:
            await covers.create_cover_generate_job(db, SimpleNamespace(uid="test"), payload)
        assert exc.value.detail["error"]["code"] == "AI_COVER_DISABLED"
    provider.assert_not_awaited()
    enqueue.assert_not_awaited()


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("same_owner", [True, False])
async def test_idempotent_content_request_checks_owner_before_reusing_disabled_choice(
    db, monkeypatch, direct, same_owner
):
    await service.update_ai_cover_status(db, "p1", enabled=False)
    run = AgentRun(
        id="existing-content-run",
        thread_id="test-task",
        agent_id="content-studio",
        uid="test" if same_owner else "another-user",
        status="running",
        request_id="existing-content-request",
    )
    db.add(run)
    await db.flush()
    task = SimpleNamespace(
        id="test-task",
        content_type_code="CT02",
        selected_image_item_id=None,
        runtime_config_snapshot_json={"schema_version": 3, "creation_mode": "viral_rewrite"},
        brief_json={
            "typeName": "装修避坑分享",
            "user_request": "装修建议",
            "visual_material": {
                "cover_mode": "ai",
                "image_asset_id": "test-image",
                "ai_cover_prompt_random": False,
                "ai_cover_prompt_variant": "p1",
            },
        },
    )
    monkeypatch.setattr(content.ContentRepository, "get_task_for_user", AsyncMock(return_value=task))
    enqueue = AsyncMock()
    monkeypatch.setattr(content, "get_arq_pool", enqueue)
    if direct:
        submit = content.create_direct_content_run
        payload = ContentDirectGenerateCreate(request_id=run.request_id)
    else:
        submit = content.create_content_run
        payload = ContentRunCreate(request_id=run.request_id)
    if same_owner:
        result = await submit(db, SimpleNamespace(uid="test"), task.id, payload)
        assert result["run_id"] == run.id and result["status"] == "running"
        assert run.status == "running"
    else:
        with pytest.raises(HTTPException) as exc:
            await submit(db, SimpleNamespace(uid="test"), task.id, payload)
        assert exc.value.detail["error"]["code"] == "CONTENT_REQUEST_ID_CONFLICT"
    enqueue.assert_not_awaited()


@pytest.mark.parametrize("direct", [False, True])
async def test_content_submission_rejects_disabled_choice_before_queueing(db, monkeypatch, direct):
    await service.update_ai_cover_status(db, "p1", enabled=False)
    task = SimpleNamespace(
        id="test-task",
        content_type_code="CT02",
        brief_json={
            "typeName": "装修避坑分享",
            "user_request": "装修建议",
            "visual_material": {"cover_mode": "ai", "ai_cover_prompt_random": False, "ai_cover_prompt_variant": "p1"},
        },
    )
    monkeypatch.setattr(content.ContentRepository, "get_task_for_user", AsyncMock(return_value=task))
    monkeypatch.setattr(content, "_require_v3_task", lambda row: None)
    monkeypatch.setattr(content, "_require_runnable_v3_task", lambda row: None)
    enqueue = AsyncMock()
    monkeypatch.setattr(content, "_enqueue_content_run", enqueue)
    with pytest.raises(HTTPException) as exc:
        if direct:
            await content.create_direct_content_run(
                db, SimpleNamespace(uid="test"), task.id, ContentDirectGenerateCreate(request_id="status-direct")
            )
        else:
            await content.create_content_run(
                db, SimpleNamespace(uid="test"), task.id, ContentRunCreate(request_id="status-workflow")
            )
    assert exc.value.detail["error"]["code"] == "AI_COVER_DISABLED"
    enqueue.assert_not_awaited()


@pytest.mark.parametrize("recover_existing", [False, True])
async def test_manual_regeneration_checks_status_but_existing_provider_recovery_uses_snapshot(
    db, monkeypatch, recover_existing
):
    await service.update_ai_cover_status(db, "p1", enabled=False)
    request = {"prompt": "已保存提示词", "parameters": {"ai_cover_prompt_variant": "p1"}}
    old = SimpleNamespace(
        id="old-job",
        status="failed",
        mode="image_to_image",
        request_json=request,
        provider_task_id="provider-existing" if recover_existing else None,
        error_code="IMAGE2_POLL_TIMEOUT",
        result_json={},
        content_task_id=None,
        artifact_id=None,
        model="image2",
    )
    monkeypatch.setattr(covers.ContentCoverRepository, "get_job_for_user", AsyncMock(return_value=old))
    monkeypatch.setattr(covers, "resolve_image2_config", AsyncMock(return_value=SimpleNamespace(model="image2")))
    create_job = AsyncMock(return_value=(SimpleNamespace(id="new-job"), False))
    monkeypatch.setattr(covers, "_create_job", create_job)
    monkeypatch.setattr(covers, "serialize_job", lambda row: {"id": row.id})
    payload = CoverRetryCreate(idempotency_key="status-retry-test")
    if recover_existing:
        await covers.retry_cover_job(db, SimpleNamespace(uid="test"), old.id, payload)
        assert create_job.await_args.kwargs["provider_task_id"] == "provider-existing"
        assert create_job.await_args.kwargs["request"] == request
    else:
        with pytest.raises(HTTPException) as exc:
            await covers.retry_cover_job(db, SimpleNamespace(uid="test"), old.id, payload)
        assert exc.value.detail["error"]["code"] == "AI_COVER_DISABLED"
        create_job.assert_not_awaited()
