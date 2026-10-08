from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.services import content_direct_worker as worker


@pytest.mark.unit
@pytest.mark.asyncio
async def test_review_notes_direct_run_saves_draft_without_cover(monkeypatch):
    run = SimpleNamespace(
        id="run-1",
        uid="user-1",
        thread_id="task-1",
        status="queued",
        input_payload={
            "skip_cover": True,
            "model_spec": "doubao:test",
            "creative_style": {"name": "理性设计师", "instruction": "专业设计"},
            "generation_prompt": "换一种表达",
            "user_request": "{}",
            "viral_asset_id": "asset-1",
            "viral_source": {"title": "原文标题", "body": "原文正文"},
            "visual_material": {},
            "content_brief": {"form_values": {"mp_service_entry": "好评笔记"}},
        },
    )
    task = SimpleNamespace(
        id="task-1",
        tenant_id=None,
        content_type_code="CT07",
        rule_version_id=None,
        status="queued",
        current_stage=None,
        review_json=None,
        selected_title_json=None,
        error_json=None,
    )
    output = SimpleNamespace(title="好评标题", body="好评正文", topics=["业主评价"])
    events = []
    repo = SimpleNamespace(
        get_task=AsyncMock(return_value=task),
        get_artifact_for_task=AsyncMock(return_value=None),
        save_artifact_version=AsyncMock(),
        track=AsyncMock(),
    )
    run_repo = SimpleNamespace(get_run=AsyncMock(return_value=run))

    class FakeDB:
        def add(self, _entry):
            return None

        async def flush(self):
            return None

        async def commit(self):
            return None

    @asynccontextmanager
    async def fake_session():
        yield FakeDB()

    generate_calls = []

    async def fake_generate(**kwargs):
        generate_calls.append(kwargs)
        await kwargs["on_delta"]({"field": "title", "value": output.title})
        await kwargs["on_delta"]({"field": "body", "value": output.body})
        await kwargs["on_delta"]({"field": "topics", "value": output.topics})
        return output

    async def record_event(run_id, event_type, data, **kwargs):
        del run_id, kwargs
        events.append((event_type, data))

    monkeypatch.setattr(worker.pg_manager, "get_async_session_context", fake_session)
    monkeypatch.setattr(worker, "AgentRunRepository", lambda _db: run_repo)
    monkeypatch.setattr(worker, "ContentRepository", lambda _db: repo)
    monkeypatch.setattr(worker, "_set_run_running", AsyncMock())
    monkeypatch.setattr(worker, "_set_run_terminal", AsyncMock())
    monkeypatch.setattr(worker, "append_run_stream_event", record_event)
    monkeypatch.setattr(worker, "has_cancel_signal", AsyncMock(return_value=False))
    monkeypatch.setattr(worker, "load_forbidden_words", AsyncMock(return_value={"alternatives": {}}))
    monkeypatch.setattr(worker, "load_knowledge_base_text", AsyncMock(return_value="业主看了未完工的工地"))
    monkeypatch.setattr(worker, "generate_direct_content", fake_generate)
    cover = AsyncMock(side_effect=AssertionError("好评笔记不应生成封面"))
    monkeypatch.setattr(worker, "_create_direct_cover_job", cover)

    await worker.process_direct_content_run(None, "run-1")

    cover.assert_not_awaited()
    assert generate_calls[0]["knowledge_context"] == "业主看了未完工的工地"
    worker.load_knowledge_base_text.assert_awaited_once_with("user-1", "好评笔记知识库")
    assert task.status == "generated"
    assert repo.track.await_args.args[0] == "content_direct_run_completed"
    assert "cover_asset_id" not in repo.track.await_args.kwargs["properties"]
    worker._set_run_terminal.assert_awaited_once_with("run-1", "completed")
    assert [item[0] for item in events if item[0] in {"content.generated", "end", "content.cover.started"}] == [
        "content.generated",
        "end",
    ]
