"""Public state reads stay consistent across a worker commit."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from yuxi.services import material_share_jobs as jobs


@pytest.mark.asyncio
async def test_completion_between_reads_never_returns_completed_without_images(monkeypatch):
    snapshot_read = False

    async def read_job(*args, **kwargs):
        # Simulate the worker committing just after a query that missed its snapshot.
        return SimpleNamespace(status="completed" if snapshot_read else "pending", share_json={"title": "案例"})

    async def read_snapshot(token):
        nonlocal snapshot_read
        snapshot_read = True
        return None, []

    monkeypatch.setattr(
        jobs, "MaterialLibraryRepository", lambda db: SimpleNamespace(get_share_with_items=read_snapshot)
    )
    result = await jobs.read_material_share_state(SimpleNamespace(get=read_job), "a" * 64)
    assert result["share"]["status"] == "pending"


@pytest.mark.asyncio
async def test_inconsistent_completed_job_is_an_error_not_an_empty_case(monkeypatch):
    monkeypatch.setattr(jobs, "MaterialLibraryRepository", lambda db: SimpleNamespace(
        get_share_with_items=AsyncMock(return_value=(None, [])),
    ))
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(status="completed")))
    with pytest.raises(RuntimeError, match="no committed snapshot"):
        await jobs.read_material_share_state(db, "a" * 64)


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent_delete", [False, True])
async def test_deleted_share_is_gone_including_deletion_between_reads(monkeypatch, concurrent_delete):
    monkeypatch.setattr(
        jobs,
        "MaterialLibraryRepository",
        lambda db: SimpleNamespace(
            get_share_with_items=AsyncMock(return_value=(None, [])),
        ),
    )
    states = (
        [SimpleNamespace(status="completed"), SimpleNamespace(status="deleted")]
        if concurrent_delete
        else [
            SimpleNamespace(status="deleted"),
        ]
    )
    db = SimpleNamespace(get=AsyncMock(side_effect=states))
    with pytest.raises(HTTPException) as error:
        await jobs.read_material_share_state(db, "a" * 64)
    assert error.value.status_code == 410
    assert error.value.detail["error"]["code"] == "MATERIAL_SHARE_DELETED"
