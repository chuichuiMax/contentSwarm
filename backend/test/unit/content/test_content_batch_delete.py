from types import SimpleNamespace

import pytest

from yuxi.services import content_service


class FakeDB:
    def __init__(self):
        self.commit_count = 0

    async def commit(self):
        self.commit_count += 1


@pytest.mark.asyncio
async def test_batch_delete_deduplicates_ids_and_soft_deletes_after_validation(monkeypatch):
    db = FakeDB()
    user = SimpleNamespace(uid="user-1")
    tasks = {
        task_id: SimpleNamespace(
            id=task_id,
            workflow_version_id="content-workflow-v3.7",
            runtime_config_snapshot_json={"schema_version": 1},
            deleted_at=None,
            status="completed",
            updated_by=None,
        )
        for task_id in ("task-1", "task-2")
    }

    class FakeRepository:
        def __init__(self, session):
            assert session is db

        async def get_task_for_user(self, task_id, current_user, *, for_update=False):
            assert current_user is user
            assert for_update is True
            return tasks.get(task_id)

    monkeypatch.setattr(content_service, "ContentRepository", FakeRepository)
    result = await content_service.delete_content_tasks(db, user, ["task-1", "task-1", "task-2"])

    assert result == {"deleted": True, "task_ids": ["task-1", "task-2"], "deleted_count": 2}
    assert db.commit_count == 1
    assert all(task.status == "deleted" and task.deleted_at is not None for task in tasks.values())


@pytest.mark.asyncio
async def test_batch_delete_does_not_mutate_or_commit_when_any_task_is_inaccessible(monkeypatch):
    db = FakeDB()
    user = SimpleNamespace(uid="user-1")
    first = SimpleNamespace(
        id="task-1",
        workflow_version_id="content-workflow-v3.7",
        deleted_at=None,
        status="completed",
        updated_by=None,
    )

    class FakeRepository:
        def __init__(self, session):
            assert session is db

        async def get_task_for_user(self, task_id, current_user, *, for_update=False):
            assert current_user is user
            assert for_update is True
            return first if task_id == first.id else None

    monkeypatch.setattr(content_service, "ContentRepository", FakeRepository)
    with pytest.raises(Exception):
        await content_service.delete_content_tasks(db, user, ["task-1", "other-user-task"])

    assert db.commit_count == 0
    assert first.status == "completed"
    assert first.deleted_at is None


@pytest.mark.asyncio
async def test_delete_allows_legacy_task(monkeypatch):
    db = FakeDB()
    user = SimpleNamespace(uid="user-1")
    task = SimpleNamespace(
        id="legacy-task",
        runtime_config_snapshot_json={"schema_version": 1},
        deleted_at=None,
        status="completed",
        updated_by=None,
    )

    class FakeRepository:
        def __init__(self, session):
            assert session is db

        async def get_task_for_user(self, task_id, current_user, *, for_update=False):
            assert task_id == task.id
            assert current_user is user
            assert for_update is True
            return task

    monkeypatch.setattr(content_service, "ContentRepository", FakeRepository)

    result = await content_service.delete_content_task(db, user, task.id)

    assert result == {"deleted": True, "task_id": task.id}
    assert db.commit_count == 1
    assert task.status == "deleted"
    assert task.deleted_at is not None
