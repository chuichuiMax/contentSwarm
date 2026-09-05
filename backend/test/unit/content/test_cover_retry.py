from types import SimpleNamespace

import pytest

import yuxi.services.content_cover_service as content_cover_service
from yuxi.content_cover.schemas import CoverRetryCreate


@pytest.mark.asyncio
async def test_hycanvas_retry_skips_image2_config_and_preserves_provider(monkeypatch):
    old_job = SimpleNamespace(
        id="cover-hycanvas-failed",
        status="failed",
        mode="hycanvas",
        model="hycanvas-deterministic",
        request_json={
            "template_id": "01c7f0bc-3ce5-431b-82e5-7390e9bc246e",
            "fields": {},
            "parameters": {"visual_plan_hash": "a" * 64},
        },
        content_task_id="task-1",
        artifact_id=None,
        provider_task_id=None,
        error_code="COVER_WORKER_FAILED",
        result_json={},
    )
    captured = {}

    class FakeRepo:
        def __init__(self, db):
            del db

        async def get_job_for_user(self, job_id, owner_uid):
            assert job_id == old_job.id
            assert owner_uid == "user-1"
            return old_job

    async def reject_image2_lookup(*args, **kwargs):
        del args, kwargs
        raise AssertionError("HyCanvas retry must not resolve image2 configuration")

    async def create_job(db, user, **kwargs):
        del db, user
        captured.update(kwargs)
        return SimpleNamespace(id="cover-hycanvas-retried"), False

    monkeypatch.setattr(content_cover_service, "ContentCoverRepository", FakeRepo)
    monkeypatch.setattr(content_cover_service, "resolve_image2_config", reject_image2_lookup)
    monkeypatch.setattr(content_cover_service, "_create_job", create_job)
    monkeypatch.setattr(
        content_cover_service,
        "serialize_job",
        lambda job: {"id": job.id, "status": "queued"},
    )

    result = await content_cover_service.retry_cover_job(
        object(),
        SimpleNamespace(uid="user-1"),
        old_job.id,
        CoverRetryCreate(idempotency_key="content-workflow-cover:retry-1"),
        workflow_resume={
            "parent_run_id": "run-retry-1",
            "node_id": "wait_cover_job",
            "expected_state_version": 6,
        },
    )

    assert result == {
        "job": {"id": "cover-hycanvas-retried", "status": "queued"},
        "deduplicated": False,
    }
    assert captured["mode"] == "hycanvas"
    assert captured["model"] == "hycanvas-deterministic"
    assert captured["request"]["parameters"]["workflow_resume"] == {
        "parent_run_id": "run-retry-1",
        "node_id": "wait_cover_job",
        "expected_state_version": 6,
    }
