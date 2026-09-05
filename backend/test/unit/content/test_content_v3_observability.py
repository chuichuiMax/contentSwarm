from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import yuxi.services.content_service as content_service
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.storage.postgres.models_business import AgentRun


def _run(run_id: str, *, run_type: str, parent_run_id=None, parent_agent_run_id=None):
    return AgentRun(
        id=run_id,
        thread_id="task-1",
        agent_id="content-studio" if run_type != "content_node_agent" else "content-body-agent",
        uid="user-1",
        status="completed",
        request_id=f"request-{run_id}",
        input_payload={"node_id": "generate_body"} if run_type == "content_node_agent" else {},
        parent_run_id=parent_run_id,
        parent_agent_run_id=parent_agent_run_id,
        run_type=run_type,
    )


@pytest.mark.asyncio
async def test_run_repository_returns_content_continuations_and_delegated_agent_children():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(AgentRun.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        root = _run("root", run_type="content")
        resume = _run("resume", run_type="content_resume", parent_run_id="root")
        delegated = _run("agent-child", run_type="content_node_agent", parent_agent_run_id="resume")
        unrelated = _run("unrelated", run_type="content")
        db.add_all([root, resume, delegated, unrelated])
        await db.commit()

        resolved_root, content_runs, delegated_runs = await AgentRunRepository(db).list_content_run_family(resume)

    await engine.dispose()
    assert resolved_root.id == "root"
    assert [item.id for item in content_runs] == ["root", "resume"]
    assert [item.id for item in delegated_runs] == ["agent-child"]


@pytest.mark.asyncio
async def test_run_detail_proves_agent_skill_tool_and_zero_knowledge_calls(monkeypatch):
    parent = SimpleNamespace(
        id="run-1",
        thread_id="task-1",
        run_type="content",
        input_payload={},
        to_dict=lambda: {"id": "run-1", "status": "completed"},
    )
    child = SimpleNamespace(
        id="child-1",
        agent_id="content-body-agent",
        status="completed",
        parent_agent_run_id="run-1",
        input_payload={
            "node_id": "generate_body",
            "runtime_config_snapshot": {
                "skills": [{"slug": "content-body-generator", "version": "1.0.0", "content_hash": "hash"}],
                "model": "provider:model",
            },
        },
        error_type=None,
        error_message=None,
        started_at=None,
        finished_at=None,
    )

    class FakeRunRepo:
        def __init__(self, db):
            del db

        async def get_run_for_user(self, run_id, uid):
            assert (run_id, uid) == ("run-1", "user-1")
            return parent

        async def list_content_run_family(self, run):
            assert run is parent
            return parent, [parent], [child]

    class FakeContentRepo:
        def __init__(self, db):
            del db

        async def get_v3_run_projection(self, **kwargs):
            assert kwargs == {"task_id": "task-1", "run_ids": ["run-1"]}
            return {
                "nodes": [
                    {
                        "node_id": "generate_body",
                        "status": "completed",
                        "delegated_agent_run_id": "child-1",
                    }
                ],
                "match_decision": {"selected_group_id": "group-1"},
                "formula_selection": {
                    "selected_title_formula_code": "T01",
                    "selected_body_formula_code": "C01",
                },
                "external_wait": None,
                "evidence": {"source_counts": {"manual_input": 1}, "citation_count": 0},
            }

    async def fake_events(run_id, **kwargs):
        del kwargs
        assert run_id == "run-1"
        return [
            {
                "seq": "1-0",
                "event_type": "content.skill.activated",
                "payload": {
                    "created_at": "2026-08-21T00:00:00Z",
                    "payload": {
                        "node_id": "generate_body",
                        "delegated_agent_run_id": "child-1",
                        "skill_slug": "content-body-generator",
                        "skill_version": "1.0.0",
                    },
                },
            },
            {
                "seq": "2-0",
                "event_type": "content.tool.completed",
                "payload": {
                    "created_at": "2026-08-21T00:00:01Z",
                    "payload": {"node_id": "generate_body", "tool_name": "submit_content_node_result"},
                },
            },
        ]

    monkeypatch.setattr(content_service, "AgentRunRepository", FakeRunRepo)
    monkeypatch.setattr(content_service, "ContentRepository", FakeContentRepo)
    monkeypatch.setattr(content_service, "list_run_stream_events", fake_events)

    result = await content_service.get_content_run(
        object(),
        SimpleNamespace(uid="user-1"),
        "run-1",
    )

    assert result["match_decision"]["selected_group_id"] == "group-1"
    assert result["formula_selection"]["selected_body_formula_code"] == "C01"
    assert result["event_summary"] == {
        "agent_run_count": 1,
        "skill_activation_count": 1,
        "tool_event_count": 1,
        "knowledge_retrieval_count": 0,
        "knowledge_result_count": 0,
    }
