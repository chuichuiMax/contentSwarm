from types import SimpleNamespace

import pytest

from yuxi.services import content_service


class FakeAgentRunRepository:
    def __init__(self, db):
        self.db = db

    async def get_run_by_request_id(self, request_id):
        assert request_id == "request-1"
        return SimpleNamespace(
            id="run-existing",
            thread_id="ct_1",
            status="interrupted",
            request_id=request_id,
            uid="user-1",
        )


@pytest.mark.asyncio
async def test_enqueue_content_run_returns_existing_run_for_same_request(monkeypatch):
    queue_called = False

    async def fail_if_queue_requested():
        nonlocal queue_called
        queue_called = True

    monkeypatch.setattr(content_service, "AgentRunRepository", FakeAgentRunRepository)
    monkeypatch.setattr(content_service, "get_arq_pool", fail_if_queue_requested)

    result = await content_service._enqueue_content_run(
        SimpleNamespace(),
        user=SimpleNamespace(uid="user-1"),
        task=SimpleNamespace(id="ct_1"),
        request_id="request-1",
        action="resume",
        model_spec=None,
    )

    assert result["run_id"] == "run-existing"
    assert result["status"] == "interrupted"
    assert queue_called is False
