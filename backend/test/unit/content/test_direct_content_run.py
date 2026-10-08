from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from yuxi.content.schemas import ContentDirectGenerateCreate
from yuxi.services import content_service as service


@pytest.fixture
def direct_run(monkeypatch):
    task = SimpleNamespace(
        id="task-1",
        industry_template_version_id="template-1",
        content_type_code="CT02",
        brief_json={
            "user_request": '{"price":"40元/㎡"}',
            "form_values": {"creative_style": {"name": "反差价值型", "instruction": "突出真实优势"}},
            "visual_material": {"image_asset_id": "image-1", "cover_mode": "ai"},
        },
        runtime_config_snapshot_json={},
        selected_image_item_id=None,
    )
    asset = SimpleNamespace(
        id="selected-asset",
        industry_slug="renovation",
        status="ready",
        preparation_skill_hash="current-skill",
        prepared_json={
            "status": "prepared",
            "review": {"action": "approve"},
            "reference_card": {"schema_version": 2, "content_type_code": "CT02", "required_slots": []},
        },
        source_json={"title": "所选标题", "body": "所选完整原文"},
    )
    repo = SimpleNamespace(
        get_task_for_user=AsyncMock(return_value=task),
        get_template=AsyncMock(return_value=SimpleNamespace(slug="renovation")),
        track=AsyncMock(),
    )
    run = SimpleNamespace(id="run-1", uid="user-1")
    run_repo = SimpleNamespace(
        get_run_by_request_id=AsyncMock(return_value=None), create_run=AsyncMock(return_value=run)
    )
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    queue = SimpleNamespace(enqueue_job=AsyncMock())
    source_check = AsyncMock(return_value=True)
    asset_lookup = AsyncMock(return_value=asset)
    monkeypatch.setattr(service, "ContentRepository", lambda db: repo)
    monkeypatch.setattr(service, "AgentRunRepository", lambda db: run_repo)
    monkeypatch.setattr(service, "_require_v3_task", lambda task: None)
    monkeypatch.setattr(service, "_validate_model_spec", lambda spec: "doubao:test")
    monkeypatch.setattr(service, "_run_response", lambda run: {"run_id": run.id})
    monkeypatch.setattr(service, "require_asset", asset_lookup)
    monkeypatch.setattr(service, "preparation_skill_hash", lambda: "current-skill")
    monkeypatch.setattr(service, "published_variable_codes", AsyncMock(return_value=set()))
    monkeypatch.setattr(service, "check_asset_source", source_check)
    monkeypatch.setattr(service, "get_arq_pool", AsyncMock(return_value=queue))
    return SimpleNamespace(
        db=db,
        user=SimpleNamespace(uid="user-1"),
        task=task,
        asset=asset,
        run_repo=run_repo,
        queue=queue,
        source_check=source_check,
        asset_lookup=asset_lookup,
        payload=ContentDirectGenerateCreate(request_id="request-1", viral_asset_id=asset.id),
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_run_freezes_selected_original_before_enqueue(direct_run):
    ctx = direct_run

    async def enqueue(*args, **kwargs):
        ctx.db.commit.assert_awaited_once()

    ctx.queue.enqueue_job.side_effect = enqueue
    result = await service.create_direct_content_run(ctx.db, ctx.user, ctx.task.id, ctx.payload)

    assert result == {"run_id": "run-1"}
    ctx.asset_lookup.assert_awaited_once_with(ctx.db, ctx.user, "selected-asset")
    frozen = ctx.run_repo.create_run.call_args.kwargs["input_payload"]
    assert frozen["viral_asset_id"] == "selected-asset"
    assert frozen["viral_source"] == {"title": "所选标题", "body": "所选完整原文"}
    assert frozen["user_request"] == '{"price":"40元/㎡"}'
    assert frozen["model_spec"] == "doubao:test"
    assert ctx.task.status == "queued"
    ctx.queue.enqueue_job.assert_awaited_once_with(
        "process_direct_content_run", "run-1", _job_id="content-direct:run-1"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_run_allows_missing_creative_style(direct_run):
    ctx = direct_run
    ctx.task.brief_json["form_values"] = {}
    ctx.payload = ContentDirectGenerateCreate(
        request_id="request-1", viral_asset_id=ctx.asset.id, creative_style=None
    )

    result = await service.create_direct_content_run(ctx.db, ctx.user, ctx.task.id, ctx.payload)

    assert result == {"run_id": "run-1"}
    frozen = ctx.run_repo.create_run.call_args.kwargs["input_payload"]
    assert frozen["creative_style"] == {}
    ctx.queue.enqueue_job.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["type", "industry", "status", "skill", "review", "source", "body", "slots"])
async def test_direct_run_rejects_unavailable_selected_reference(direct_run, invalid):
    ctx = direct_run
    if invalid == "type":
        ctx.asset.prepared_json["reference_card"]["content_type_code"] = "CT01"
    elif invalid == "industry":
        ctx.asset.industry_slug = "other"
    elif invalid == "status":
        ctx.asset.status = "invalidated"
    elif invalid == "skill":
        ctx.asset.preparation_skill_hash = "old-skill"
    elif invalid == "review":
        ctx.asset.prepared_json["review"]["action"] = "reject"
    elif invalid == "source":
        ctx.source_check.return_value = False
    elif invalid == "body":
        ctx.asset.source_json["body"] = ""
    elif invalid == "slots":
        ctx.asset.prepared_json["reference_card"]["required_slots"] = [{"variable_codes": ["disabled"]}]

    with pytest.raises(HTTPException) as exc:
        await service.create_direct_content_run(ctx.db, ctx.user, ctx.task.id, ctx.payload)

    assert exc.value.status_code == 409
    ctx.run_repo.create_run.assert_not_awaited()
    ctx.queue.enqueue_job.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_run_preserves_selected_reference_access_error(direct_run):
    ctx = direct_run
    ctx.asset_lookup.side_effect = HTTPException(404, "爆款资产不存在或无权访问")

    with pytest.raises(HTTPException) as exc:
        await service.create_direct_content_run(ctx.db, ctx.user, ctx.task.id, ctx.payload)

    assert exc.value.status_code == 404
    ctx.run_repo.create_run.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_review_notes_direct_run_does_not_require_cover(direct_run):
    ctx = direct_run
    ctx.task.content_type_code = "CT07"
    ctx.task.brief_json = {
        "user_request": '{"persona":"业主评价"}',
        "form_values": {"mp_service_entry": "好评笔记", "creative_style": {"name": "理性设计师"}},
    }
    ctx.asset.prepared_json["reference_card"]["content_type_code"] = "CT07"

    result = await service.create_direct_content_run(ctx.db, ctx.user, ctx.task.id, ctx.payload)

    assert result == {"run_id": "run-1"}
    frozen = ctx.run_repo.create_run.call_args.kwargs["input_payload"]
    assert frozen["skip_cover"] is True
    assert frozen["visual_material"] == {}
    ctx.queue.enqueue_job.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_run_requires_explicit_reference_id(direct_run):
    ctx = direct_run
    ctx.payload = ContentDirectGenerateCreate(request_id="request-1")

    with pytest.raises(HTTPException) as exc:
        await service.create_direct_content_run(ctx.db, ctx.user, ctx.task.id, ctx.payload)

    assert exc.value.status_code == 409
    ctx.asset_lookup.assert_not_awaited()
    ctx.run_repo.create_run.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_review_notes_direct_run_does_not_require_viral_asset(direct_run):
    ctx = direct_run
    ctx.task.content_type_code = "CT07"
    ctx.task.brief_json = {
        "user_request": '{"persona":"业主评价"}',
        "form_values": {"mp_service_entry": "好评笔记"},
    }
    ctx.payload = ContentDirectGenerateCreate(request_id="request-2")

    result = await service.create_direct_content_run(ctx.db, ctx.user, ctx.task.id, ctx.payload)

    assert result == {"run_id": "run-1"}
    ctx.asset_lookup.assert_not_awaited()
    frozen = ctx.run_repo.create_run.call_args.kwargs["input_payload"]
    assert frozen["skip_cover"] is True
    assert frozen["viral_asset_id"] is None
    assert frozen["viral_source"] == {"title": "", "body": ""}


