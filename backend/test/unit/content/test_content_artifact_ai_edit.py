from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage

import yuxi.content.generation as generation
import yuxi.services.content_service as content_service
from yuxi.content.schemas import ContentArtifactAIEdit


def _task() -> SimpleNamespace:
    return SimpleNamespace(
        id="task-1",
        latest_run_id="run-1",
        runtime_config_snapshot_json={"schema_version": 3},
        brief_json={"required_terms": [], "forbidden_terms": []},
        strategy_json={},
        evidence_json={"items": []},
        rule_version_id="rule-1",
        status="reviewed",
        current_stage="review",
        review_json={"status": "passed", "checks": []},
    )


def _artifact() -> SimpleNamespace:
    artifact = SimpleNamespace(
        id="artifact-1",
        task_id="task-1",
        current_version=2,
        title="原始标题",
        body="原始正文",
        topics=["原话题"],
        strategy_snapshot={
            "creation_methods": ["M01", "S01", "M03"],
            "title_formula": {"code": "T01"},
            "body_formula": {"code": "C02"},
        },
        evidence_snapshot={"items": [{"id": "evidence-1", "value": "已确认事实"}]},
        status="reviewed",
        review_snapshot={"status": "passed", "checks": []},
        edit_diff_snapshot=[],
        updated_at=None,
    )
    artifact.to_dict = lambda: {
        "id": artifact.id,
        "task_id": artifact.task_id,
        "current_version": artifact.current_version,
        "title": artifact.title,
        "body": artifact.body,
        "topics": artifact.topics,
        "status": artifact.status,
        "review_snapshot": artifact.review_snapshot,
        "edit_diff_snapshot": artifact.edit_diff_snapshot,
    }
    return artifact


@pytest.mark.asyncio
async def test_refine_generated_content_only_accepts_artifact_fields(monkeypatch):
    captured = {}

    class FakeModel:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return AIMessage(
                content=json.dumps(
                    {"title": "新标题", "body": "新正文", "topics": ["话题一"]},
                    ensure_ascii=False,
                )
            )

    monkeypatch.setattr(generation, "resolve_chat_model_spec", lambda _spec: "provider:model")
    monkeypatch.setattr(generation, "load_chat_model", lambda **_kwargs: FakeModel())

    result = await generation.refine_generated_content(
        model_spec=None,
        instruction="把语气改得更简洁",
        title="原始标题",
        body="原始正文",
        topics=["原话题"],
        brief={},
        strategy={},
        evidence_bundle={},
    )

    assert result == {"title": "新标题", "body": "新正文", "topics": ["话题一"]}
    assert "工作流、节点、规则、策略、证据和封面都是只读上下文" in captured["messages"][0].content


@pytest.mark.asyncio
async def test_ai_edit_saves_new_artifact_version_without_changing_workflow(monkeypatch):
    artifact = _artifact()
    task = _task()
    repo = SimpleNamespace(
        get_artifact_for_user=AsyncMock(return_value=artifact),
        get_task_for_user=AsyncMock(return_value=task),
        save_artifact_version=AsyncMock(),
        track=AsyncMock(),
    )
    run_repo = SimpleNamespace(get_run=AsyncMock(return_value=SimpleNamespace(status="completed")))
    db = SimpleNamespace(refresh=AsyncMock(), commit=AsyncMock())
    monkeypatch.setattr(content_service, "ContentRepository", lambda _db: repo)
    monkeypatch.setattr(content_service, "AgentRunRepository", lambda _db: run_repo)
    refine = AsyncMock(return_value={"title": "原始标题", "body": "精简后的正文", "topics": ["新话题"]})
    validation_context = {}

    def fake_validate(**kwargs):
        validation_context.update(kwargs)
        return {"status": "passed", "checks": []}

    monkeypatch.setattr(content_service, "refine_generated_content", refine)
    monkeypatch.setattr(content_service, "validate_content", fake_validate)

    response = await content_service.ai_edit_content_artifact(
        db,
        SimpleNamespace(uid="user-1"),
        artifact.id,
        ContentArtifactAIEdit(instruction="精简正文并调整话题", expected_version=2),
    )

    assert artifact.current_version == 3
    assert artifact.body == "精简后的正文"
    assert artifact.topics == ["新话题"]
    assert task.latest_run_id == "run-1"
    assert task.current_stage == "review"
    assert response["changed_fields"] == ["body", "topics"]
    assert refine.await_args.kwargs["strategy"] == artifact.strategy_snapshot
    assert refine.await_args.kwargs["evidence_bundle"] == artifact.evidence_snapshot
    assert validation_context["strategy"] == {
        "methods": ["M01", "S01", "M03"],
        "title_formula_code": "T01",
        "body_formula_code": "C02",
    }
    assert validation_context["evidence_bundle"] == artifact.evidence_snapshot
    assert repo.save_artifact_version.await_args.kwargs["source_type"] == "ai_edit"
    assert repo.save_artifact_version.await_args.kwargs["skill_versions"] == {}
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_ai_edit_rejects_incomplete_workflow_before_model_call(monkeypatch):
    artifact = _artifact()
    task = _task()
    repo = SimpleNamespace(
        get_artifact_for_user=AsyncMock(return_value=artifact),
        get_task_for_user=AsyncMock(return_value=task),
    )
    run_repo = SimpleNamespace(get_run=AsyncMock(return_value=SimpleNamespace(status="failed")))
    refine = AsyncMock()
    monkeypatch.setattr(content_service, "ContentRepository", lambda _db: repo)
    monkeypatch.setattr(content_service, "AgentRunRepository", lambda _db: run_repo)
    monkeypatch.setattr(content_service, "refine_generated_content", refine)

    with pytest.raises(HTTPException) as exc_info:
        await content_service.ai_edit_content_artifact(
            SimpleNamespace(),
            SimpleNamespace(uid="user-1"),
            artifact.id,
            ContentArtifactAIEdit(instruction="修改正文", expected_version=2),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "CONTENT_AI_EDIT_WORKFLOW_INCOMPLETE"
    refine.assert_not_awaited()


@pytest.mark.asyncio
async def test_ai_edit_rejects_stale_artifact_version_before_model_call(monkeypatch):
    artifact = _artifact()
    task = _task()
    repo = SimpleNamespace(
        get_artifact_for_user=AsyncMock(return_value=artifact),
        get_task_for_user=AsyncMock(return_value=task),
    )
    run_repo = SimpleNamespace(get_run=AsyncMock(return_value=SimpleNamespace(status="completed")))
    refine = AsyncMock()
    monkeypatch.setattr(content_service, "ContentRepository", lambda _db: repo)
    monkeypatch.setattr(content_service, "AgentRunRepository", lambda _db: run_repo)
    monkeypatch.setattr(content_service, "refine_generated_content", refine)

    with pytest.raises(HTTPException) as exc_info:
        await content_service.ai_edit_content_artifact(
            SimpleNamespace(),
            SimpleNamespace(uid="user-1"),
            artifact.id,
            ContentArtifactAIEdit(instruction="修改正文", expected_version=1),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "CONTENT_ARTIFACT_VERSION_CONFLICT"
    refine.assert_not_awaited()


@pytest.mark.asyncio
async def test_ai_edit_validation_failure_preserves_current_artifact(monkeypatch):
    artifact = _artifact()
    task = _task()
    repo = SimpleNamespace(
        get_artifact_for_user=AsyncMock(return_value=artifact),
        get_task_for_user=AsyncMock(return_value=task),
    )
    run_repo = SimpleNamespace(get_run=AsyncMock(return_value=SimpleNamespace(status="completed")))
    monkeypatch.setattr(content_service, "ContentRepository", lambda _db: repo)
    monkeypatch.setattr(content_service, "AgentRunRepository", lambda _db: run_repo)
    monkeypatch.setattr(
        content_service,
        "refine_generated_content",
        AsyncMock(return_value={"title": "原始标题", "body": "包含无证据数字 999", "topics": ["原话题"]}),
    )
    monkeypatch.setattr(
        content_service,
        "validate_content",
        lambda **_kwargs: {
            "status": "blocked",
            "checks": [{"code": "FACT_NUMBER_WITHOUT_SOURCE"}],
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        await content_service.ai_edit_content_artifact(
            SimpleNamespace(),
            SimpleNamespace(uid="user-1"),
            artifact.id,
            ContentArtifactAIEdit(instruction="增加数字", expected_version=2),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"]["code"] == "CONTENT_AI_EDIT_VALIDATION_FAILED"
    assert artifact.current_version == 2
    assert artifact.body == "原始正文"
