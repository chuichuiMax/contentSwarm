import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from langgraph.errors import InvalidUpdateError

from yuxi.content.schemas import ReviewReport
from yuxi.services import content_run_worker
from yuxi.services.run_worker import RetryableRunError


class FakeGraph:
    def __init__(self):
        self.updated_state = None
        self.invoked_with = "not-called"
        self.completed = False
        self.pending_node = "generate_body"
        self.values = {}

    async def aget_state(self, config):
        return SimpleNamespace(
            next=() if self.completed else (self.pending_node,),
            interrupts=(),
            values=self.values,
        )

    async def aupdate_state(self, config, values, as_node=None):
        self.updated_state = values
        self.updated_as_node = as_node

    async def ainvoke(self, graph_input, **kwargs):
        self.invoked_with = graph_input
        self.completed = True


@pytest.mark.asyncio
async def test_worker_shutdown_marks_content_run_retryable_instead_of_cancelled(monkeypatch):
    class InterruptedGraph:
        async def ainvoke(self, graph_input, **kwargs):
            del graph_input, kwargs
            raise asyncio.CancelledError

    statuses = []
    events = []
    run = SimpleNamespace(
        id="run-worker-interrupted",
        status="pending",
        uid="user-1",
        request_id="request-worker-interrupted",
        checkpoint_thread_id="content:task-worker-interrupted",
        input_payload={"action": "start", "model_spec": None},
    )
    task = SimpleNamespace(
        id="task-worker-interrupted",
        workflow_version_id="workflow-v3",
        rule_version_id="rules-v3",
        industry_template_version_id="industry-v3",
        runtime_config_snapshot_json={"schema_version": 3},
        brief_json={},
        evidence_json={"items": []},
        mode="quick",
        status="queued",
        error_json=None,
    )
    workflow = SimpleNamespace(definition_json={"schema_version": 3})

    async def load_run(run_id):
        return run, task, workflow, {"version": {"id": "rules-v3"}}

    async def set_status(run_id, *, status, **kwargs):
        statuses.append((status, kwargs))

    async def append_event(run_id, event_type, payload, **kwargs):
        events.append((event_type, payload))

    async def no_event(*args, **kwargs):
        return None

    async def no_cancel(*args, **kwargs):
        return False

    async def get_graph(*args, **kwargs):
        return InterruptedGraph()

    class FakeRepo:
        def __init__(self, db):
            del db

        async def get_task(self, task_id, for_update=False):
            del for_update
            return task if task_id == task.id else None

        async def track(self, *args, **kwargs):
            del args, kwargs

    @asynccontextmanager
    async def session_context():
        yield object()

    monkeypatch.setattr(content_run_worker, "_load_content_run", load_run)
    monkeypatch.setattr(content_run_worker, "_set_content_run_status", set_status)
    monkeypatch.setattr(content_run_worker, "append_run_stream_event", append_event)
    monkeypatch.setattr(content_run_worker, "clear_cancel_signal", no_event)
    monkeypatch.setattr(content_run_worker, "has_cancel_signal", no_cancel)
    monkeypatch.setattr(content_run_worker, "ContentRepository", FakeRepo)
    monkeypatch.setattr(content_run_worker.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(
        content_run_worker.agent_manager,
        "get_agent",
        lambda agent_id: SimpleNamespace(get_graph=get_graph),
    )

    await content_run_worker.process_content_run({"job_try": 1}, run.id)

    assert [status for status, _ in statuses] == ["running", "failed"]
    assert statuses[-1][1]["error_type"] == "worker_interrupted"
    assert task.status == "failed"
    assert task.error_json == {
        "code": "CONTENT_RUN_WORKER_INTERRUPTED",
        "message": "执行进程发生重载或重启，请从当前节点重试",
        "retryable": True,
    }
    assert [event_type for event_type, _ in events] == ["metadata", "error", "end"]


@pytest.mark.asyncio
async def test_graph_initialization_failure_marks_run_and_task_failed(monkeypatch):
    statuses = []
    events = []
    run = SimpleNamespace(
        id="run-bootstrap-failure",
        status="pending",
        uid="user-1",
        request_id="request-bootstrap-failure",
        checkpoint_thread_id="content:task-bootstrap-failure",
        input_payload={"action": "start", "model_spec": None},
    )
    task = SimpleNamespace(
        id="task-bootstrap-failure",
        workflow_version_id="workflow-v3",
        rule_version_id="rules-v3",
        industry_template_version_id="industry-v3",
        runtime_config_snapshot_json={"schema_version": 3},
        status="queued",
        error_json=None,
    )
    workflow = SimpleNamespace(definition_json={"schema_version": 3})

    async def load_run(run_id):
        return run, task, workflow, {"version": {"id": "rules-v3"}}

    async def set_status(run_id, *, status, **kwargs):
        statuses.append((status, kwargs))

    async def append_event(run_id, event_type, payload, **kwargs):
        events.append((event_type, payload))

    async def no_event(*args, **kwargs):
        return None

    async def get_graph(*args, **kwargs):
        raise ValueError("V3 工作流必须声明 runtime_limits")

    class FakeRepo:
        def __init__(self, db):
            del db

        async def get_task(self, task_id, for_update=False):
            del for_update
            return task if task_id == task.id else None

        async def track(self, *args, **kwargs):
            del args, kwargs

    @asynccontextmanager
    async def session_context():
        yield object()

    monkeypatch.setattr(content_run_worker, "_load_content_run", load_run)
    monkeypatch.setattr(content_run_worker, "_set_content_run_status", set_status)
    monkeypatch.setattr(content_run_worker, "append_run_stream_event", append_event)
    monkeypatch.setattr(content_run_worker, "clear_cancel_signal", no_event)
    monkeypatch.setattr(content_run_worker, "ContentRepository", FakeRepo)
    monkeypatch.setattr(content_run_worker.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(
        content_run_worker.agent_manager,
        "get_agent",
        lambda agent_id: SimpleNamespace(get_graph=get_graph),
    )

    await content_run_worker.process_content_run({"job_try": 1}, run.id)

    assert [status for status, _ in statuses] == ["running", "failed"]
    assert task.status == "failed"
    assert task.error_json["code"] == "CONTENT_WORKFLOW_FAILED"
    assert [event_type for event_type, _ in events] == ["metadata", "error", "end"]


@pytest.mark.asyncio
async def test_failed_node_retry_continues_from_checkpoint(monkeypatch):
    graph = FakeGraph()
    statuses = []

    run = SimpleNamespace(
        id="run-retry",
        status="pending",
        uid="user-1",
        request_id="request-1",
        checkpoint_thread_id="content:task-1",
        input_payload={"action": "retry", "node_id": "generate_body", "model_spec": None},
    )
    task = SimpleNamespace(
        id="task-1",
        workflow_version_id="workflow-v3",
        rule_version_id="rules-v3",
        industry_template_version_id="industry-v3",
        runtime_config_snapshot_json={"schema_version": 3},
        brief_json={},
        strategy_json={},
        evidence_json={"items": []},
    )
    workflow = SimpleNamespace(definition_json={"schema_version": 3, "nodes": [], "edges": []})

    async def load_run(run_id):
        return run, task, workflow, {"version": {"id": "rules-v3"}}

    async def set_status(run_id, *, status, **kwargs):
        statuses.append(status)

    async def no_event(*args, **kwargs):
        return None

    async def no_cancel(*args, **kwargs):
        return False

    async def get_graph(*args, **kwargs):
        return graph

    monkeypatch.setattr(content_run_worker, "_load_content_run", load_run)
    monkeypatch.setattr(content_run_worker, "_set_content_run_status", set_status)
    monkeypatch.setattr(content_run_worker, "append_run_stream_event", no_event)
    monkeypatch.setattr(content_run_worker, "clear_cancel_signal", no_event)
    monkeypatch.setattr(content_run_worker, "has_cancel_signal", no_cancel)
    monkeypatch.setattr(
        content_run_worker.agent_manager,
        "get_agent",
        lambda agent_id: SimpleNamespace(get_graph=get_graph),
    )

    await content_run_worker.process_content_run({"job_try": 1}, run.id)

    assert graph.invoked_with is None
    assert graph.updated_state == {
        "run_id": run.id,
        "uid": run.uid,
        "model_spec": None,
        "resume_parent_run_id": None,
    }
    assert statuses == ["running", "completed"]


@pytest.mark.asyncio
async def test_retry_at_parallel_join_specifies_completed_predecessor(monkeypatch):
    class AmbiguousGraph(FakeGraph):
        def __init__(self):
            super().__init__()
            self.pending_node = "select_viral_reference"
            self.update_calls = []

        async def aupdate_state(self, config, values, as_node=None):
            self.update_calls.append(as_node)
            if as_node is None:
                raise InvalidUpdateError("Ambiguous update, specify as_node")
            await super().aupdate_state(config, values, as_node=as_node)

    graph = AmbiguousGraph()
    run = SimpleNamespace(
        id="run-parallel-join-retry",
        status="pending",
        uid="user-1",
        thread_id="task-1",
        request_id="request-parallel-join-retry",
        checkpoint_thread_id="content:task-1",
        input_payload={"action": "retry", "node_id": None, "model_spec": None},
    )
    task = SimpleNamespace(
        id="task-1",
        workflow_version_id="workflow-v3",
        rule_version_id="rules-v3",
        industry_template_version_id="industry-v3",
        runtime_config_snapshot_json={"schema_version": 3},
    )
    workflow = SimpleNamespace(
        definition_json={
            "schema_version": 3,
            "edges": [
                ["collect_business_rule_evidence", "select_viral_reference"],
                ["collect_viral_candidates", "select_viral_reference"],
            ],
        }
    )

    async def load_run(run_id):
        return run, task, workflow, {"version": {"id": "rules-v3"}}

    async def no_event(*args, **kwargs):
        return None

    async def no_cancel(*args, **kwargs):
        return False

    async def get_graph(*args, **kwargs):
        return graph

    monkeypatch.setattr(content_run_worker, "_load_content_run", load_run)
    monkeypatch.setattr(content_run_worker, "_set_content_run_status", no_event)
    monkeypatch.setattr(content_run_worker, "append_run_stream_event", no_event)
    monkeypatch.setattr(content_run_worker, "clear_cancel_signal", no_event)
    monkeypatch.setattr(content_run_worker, "has_cancel_signal", no_cancel)
    monkeypatch.setattr(
        content_run_worker.agent_manager,
        "get_agent",
        lambda agent_id: SimpleNamespace(get_graph=get_graph),
    )

    await content_run_worker.process_content_run({"job_try": 1}, run.id)

    assert graph.update_calls == [None, "collect_viral_candidates"]
    assert graph.invoked_with is None


@pytest.mark.asyncio
async def test_cover_submission_retry_replans_when_template_text_is_too_long(monkeypatch):
    graph = FakeGraph()
    graph.pending_node = "submit_cover_job"
    graph.values = {
        "visual_plan": {"text": ["合规标题", "增加12㎡收纳空间", "品牌标签"]},
        "runtime_config_snapshot": {
            "visual_material": {
                "hycanvas_fillable_fields": [
                    {
                        "semanticRole": "subtitle",
                        "constraints": {"maxChars": 7},
                    }
                ]
            }
        },
    }
    run = SimpleNamespace(
        id="run-cover-submit-retry",
        status="pending",
        uid="user-1",
        thread_id="task-1",
        request_id="request-cover-submit-retry",
        checkpoint_thread_id="content:task-1",
        input_payload={"action": "retry", "node_id": "submit_cover_job", "model_spec": None},
    )
    task = SimpleNamespace(
        id="task-1",
        workflow_version_id="workflow-v3",
        rule_version_id="rules-v3",
        industry_template_version_id="industry-v3",
        runtime_config_snapshot_json={"schema_version": 3},
    )
    workflow = SimpleNamespace(definition_json={"schema_version": 3, "nodes": [], "edges": []})

    async def load_run(run_id):
        return run, task, workflow, {"version": {"id": "rules-v3"}}

    async def no_event(*args, **kwargs):
        return None

    async def no_cancel(*args, **kwargs):
        return False

    async def get_graph(*args, **kwargs):
        return graph

    monkeypatch.setattr(content_run_worker, "_load_content_run", load_run)
    monkeypatch.setattr(content_run_worker, "_set_content_run_status", no_event)
    monkeypatch.setattr(content_run_worker, "append_run_stream_event", no_event)
    monkeypatch.setattr(content_run_worker, "clear_cancel_signal", no_event)
    monkeypatch.setattr(content_run_worker, "has_cancel_signal", no_cancel)
    monkeypatch.setattr(
        content_run_worker.agent_manager,
        "get_agent",
        lambda agent_id: SimpleNamespace(get_graph=get_graph),
    )

    await content_run_worker.process_content_run({"job_try": 1}, run.id)

    assert graph.updated_state["visual_plan"] is None
    assert graph.updated_state["cover_job"] is None
    assert graph.updated_as_node == "human_content_approval"
    assert graph.invoked_with is None


@pytest.mark.asyncio
async def test_failed_cover_wait_retry_requeues_cover_and_updates_checkpoint(monkeypatch):
    graph = FakeGraph()
    graph.pending_node = "wait_cover_job"
    graph.values = {
        "cover_job": {"cover_job_id": "cover-failed", "plan_hash": "a" * 64},
        "state_version": 6,
    }
    captured = {}
    run = SimpleNamespace(
        id="run-cover-retry",
        status="pending",
        uid="user-1",
        thread_id="task-1",
        request_id="request-cover-retry",
        checkpoint_thread_id="content:task-1",
        input_payload={"action": "retry", "node_id": "wait_cover_job", "model_spec": None},
    )
    task = SimpleNamespace(
        id="task-1",
        workflow_version_id="workflow-v3",
        rule_version_id="rules-v3",
        industry_template_version_id="industry-v3",
        runtime_config_snapshot_json={"schema_version": 3},
    )
    workflow = SimpleNamespace(definition_json={"schema_version": 3, "nodes": [], "edges": []})

    async def load_run(run_id):
        return run, task, workflow, {"version": {"id": "rules-v3"}}

    async def retry_cover(run_arg, state):
        captured["run"] = run_arg
        captured["state"] = state
        return {"id": "cover-retried", "status": "queued"}

    async def no_event(*args, **kwargs):
        return None

    async def no_cancel(*args, **kwargs):
        return False

    async def get_graph(*args, **kwargs):
        return graph

    monkeypatch.setattr(content_run_worker, "_load_content_run", load_run)
    monkeypatch.setattr(content_run_worker, "_retry_failed_cover_job", retry_cover)
    monkeypatch.setattr(content_run_worker, "_set_content_run_status", no_event)
    monkeypatch.setattr(content_run_worker, "append_run_stream_event", no_event)
    monkeypatch.setattr(content_run_worker, "clear_cancel_signal", no_event)
    monkeypatch.setattr(content_run_worker, "has_cancel_signal", no_cancel)
    monkeypatch.setattr(
        content_run_worker.agent_manager,
        "get_agent",
        lambda agent_id: SimpleNamespace(get_graph=get_graph),
    )

    await content_run_worker.process_content_run({"job_try": 1}, run.id)

    assert captured == {"run": run, "state": graph.values}
    assert graph.updated_state == {
        "run_id": run.id,
        "uid": run.uid,
        "model_spec": None,
        "resume_parent_run_id": None,
        "cover_job": {
            "cover_job_id": "cover-retried",
            "plan_hash": "a" * 64,
            "status": "queued",
        },
    }
    assert graph.invoked_with is None


@pytest.mark.asyncio
async def test_missing_cover_wait_retry_rewinds_to_cover_submission(monkeypatch):
    graph = FakeGraph()
    graph.pending_node = "wait_cover_job"
    graph.values = {
        "cover_job": {"cover_job_id": "cover-never-created", "plan_hash": "a" * 64},
        "state_version": 6,
    }
    run = SimpleNamespace(
        id="run-cover-rewind",
        status="pending",
        uid="user-1",
        thread_id="task-1",
        request_id="request-cover-rewind",
        checkpoint_thread_id="content:task-1",
        input_payload={"action": "retry", "node_id": "wait_cover_job", "model_spec": None},
    )
    task = SimpleNamespace(
        id="task-1",
        workflow_version_id="workflow-v3",
        rule_version_id="rules-v3",
        industry_template_version_id="industry-v3",
        runtime_config_snapshot_json={"schema_version": 3},
    )
    workflow = SimpleNamespace(definition_json={"schema_version": 3, "nodes": [], "edges": []})

    async def load_run(run_id):
        return run, task, workflow, {"version": {"id": "rules-v3"}}

    async def missing_cover(run_arg, state):
        del run_arg, state
        return None

    async def no_event(*args, **kwargs):
        return None

    async def no_cancel(*args, **kwargs):
        return False

    async def get_graph(*args, **kwargs):
        return graph

    monkeypatch.setattr(content_run_worker, "_load_content_run", load_run)
    monkeypatch.setattr(content_run_worker, "_retry_failed_cover_job", missing_cover)
    monkeypatch.setattr(content_run_worker, "_set_content_run_status", no_event)
    monkeypatch.setattr(content_run_worker, "append_run_stream_event", no_event)
    monkeypatch.setattr(content_run_worker, "clear_cancel_signal", no_event)
    monkeypatch.setattr(content_run_worker, "has_cancel_signal", no_cancel)
    monkeypatch.setattr(
        content_run_worker.agent_manager,
        "get_agent",
        lambda agent_id: SimpleNamespace(get_graph=get_graph),
    )

    await content_run_worker.process_content_run({"job_try": 1}, run.id)

    assert graph.updated_state["cover_job"] is None
    assert graph.updated_as_node == "plan_visuals"
    assert graph.invoked_with is None


@pytest.mark.asyncio
async def test_retryable_model_validation_error_is_wrapped_for_arq_retry(monkeypatch):
    class InvalidModelGraph:
        async def ainvoke(self, graph_input, **kwargs):
            del graph_input, kwargs
            ReviewReport.model_validate(
                {"status": "passed", "checks": [{"code": "x", "level": "passed", "message": "ok"}]}
            )

    run = SimpleNamespace(
        id="run-model-retry",
        status="pending",
        uid="user-1",
        request_id="request-model-retry",
        checkpoint_thread_id="content:task-model-retry",
        input_payload={"action": "start", "model_spec": None},
    )
    task = SimpleNamespace(
        id="task-model-retry",
        workflow_version_id="workflow-v3",
        rule_version_id="rules-v3",
        industry_template_version_id="industry-v3",
        runtime_config_snapshot_json={"schema_version": 3},
        brief_json={"task_id": "task-model-retry"},
        strategy_json={"compatibility": "compatible"},
        evidence_json={"items": []},
        selected_angle_json={},
    )
    workflow = SimpleNamespace(definition_json={"schema_version": 3, "nodes": [], "edges": []})
    statuses = []

    async def load_run(run_id):
        return run, task, workflow, {"version": {"id": "rules-v3"}}

    async def set_status(run_id, *, status, **kwargs):
        statuses.append(status)

    async def no_event(*args, **kwargs):
        return None

    async def no_cancel(*args, **kwargs):
        return False

    async def get_graph(*args, **kwargs):
        return InvalidModelGraph()

    monkeypatch.setattr(content_run_worker, "_load_content_run", load_run)
    monkeypatch.setattr(content_run_worker, "_set_content_run_status", set_status)
    monkeypatch.setattr(content_run_worker, "append_run_stream_event", no_event)
    monkeypatch.setattr(content_run_worker, "clear_cancel_signal", no_event)
    monkeypatch.setattr(content_run_worker, "has_cancel_signal", no_cancel)
    monkeypatch.setattr(
        content_run_worker.agent_manager,
        "get_agent",
        lambda agent_id: SimpleNamespace(get_graph=get_graph),
    )

    with pytest.raises(RetryableRunError):
        await content_run_worker.process_content_run({"job_try": 1}, run.id)

    assert statuses == ["running"]
