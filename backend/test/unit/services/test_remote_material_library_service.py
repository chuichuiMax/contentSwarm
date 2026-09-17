from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import yuxi.content  # noqa: F401 - establish the package import order used by the app
import yuxi.services.remote_material_library_service as service


class FakeRepository:
    def __init__(self, setting=None):
        self.setting = setting
        self.saved = None

    async def get_remote_setting(self, *, for_update: bool = False):
        del for_update
        return self.setting

    async def upsert_remote_setting(self, **values):
        self.saved = values
        self.setting = SimpleNamespace(**values)
        return self.setting


class FakeDb:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class FakeJobDb(FakeDb):
    def __init__(self, scalar_result=None):
        super().__init__()
        self.scalar_result = scalar_result
        self.added = []

    async def scalar(self, query):
        del query
        return self.scalar_result

    def add(self, value):
        self.added.append(value)


@pytest.mark.asyncio
async def test_remote_config_state_is_redacted_and_superadmin_manageable(monkeypatch: pytest.MonkeyPatch):
    repository = FakeRepository(
        SimpleNamespace(
            base_url="http://remote.example",
            username="remote-admin",
            password="remote-secret",
            verification_status="verified",
            verified_at=None,
        )
    )
    monkeypatch.setattr(service, "MaterialLibraryRepository", lambda db: repository)

    state = await service.get_remote_material_config_state(
        FakeDb(),
        SimpleNamespace(role="superadmin"),
    )

    assert state == {
        "base_url": "http://remote.example",
        "configured": True,
        "source": "database",
        "can_manage": True,
        "verification_status": "verified",
        "verified_at": None,
    }
    assert "password" not in state
    assert "username" not in state


@pytest.mark.asyncio
async def test_resolve_remote_client_prefers_database_credentials(monkeypatch: pytest.MonkeyPatch):
    repository = FakeRepository(
        SimpleNamespace(
            base_url="http://database.example",
            username="database-user",
            password="database-password",
        )
    )
    monkeypatch.setattr(service, "MaterialLibraryRepository", lambda db: repository)
    monkeypatch.setenv("REMOTE_MATERIAL_BASE_URL", "http://environment.example")
    monkeypatch.setenv("REMOTE_MATERIAL_TOKEN", "environment-token")
    monkeypatch.setenv("REMOTE_MATERIAL_USERNAME", "environment-user")
    monkeypatch.setenv("REMOTE_MATERIAL_PASSWORD", "environment-password")

    client = await service.resolve_remote_material_client(FakeDb())

    assert client.base_url == "http://database.example"
    assert client.token == ""
    assert client.username == "database-user"
    assert client.password == "database-password"


@pytest.mark.asyncio
async def test_missing_remote_credentials_uses_configuration_error_not_platform_401(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("REMOTE_MATERIAL_TOKEN", raising=False)
    monkeypatch.delenv("REMOTE_MATERIAL_USERNAME", raising=False)
    monkeypatch.delenv("REMOTE_MATERIAL_PASSWORD", raising=False)
    client = service.VisioFlowMaterialClient()

    with pytest.raises(HTTPException) as exc_info:
        await client.authenticate(SimpleNamespace())

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "REMOTE_MATERIAL_CONFIG_REQUIRED"


@pytest.mark.asyncio
async def test_invalid_remote_credentials_are_not_saved(monkeypatch: pytest.MonkeyPatch):
    repository = FakeRepository()
    db = FakeDb()
    monkeypatch.setattr(service, "MaterialLibraryRepository", lambda db: repository)

    async def reject_credentials(self, client):
        del self, client
        raise service._remote_error(
            "远程素材库登录失败，请检查账号密码",
            code="REMOTE_MATERIAL_AUTH_FAILED",
        )

    monkeypatch.setattr(service.VisioFlowMaterialClient, "authenticate", reject_credentials)

    with pytest.raises(HTTPException) as exc_info:
        await service.verify_and_save_remote_material_config(
            db,
            SimpleNamespace(id=1, role="superadmin"),
            service.RemoteMaterialConfigUpdate(username="bad-user", password="bad-password"),
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["error"]["code"] == "REMOTE_MATERIAL_AUTH_FAILED"
    assert repository.saved is None
    assert db.commits == 0


@pytest.mark.asyncio
async def test_remote_sync_job_is_queued_and_returns_immediately(monkeypatch: pytest.MonkeyPatch):
    repository = FakeRepository(SimpleNamespace(username="user", password="password"))
    db = FakeJobDb()
    queued_jobs = []

    class FakeQueue:
        async def enqueue_job(self, function_name, job_id, **options):
            queued_jobs.append((function_name, job_id, options))
            return SimpleNamespace(job_id=job_id)

    monkeypatch.setattr(service, "MaterialLibraryRepository", lambda db: repository)
    monkeypatch.setattr(service, "get_arq_pool", lambda: _async_value(FakeQueue()))

    result = await service.create_remote_material_sync_job(
        db,
        SimpleNamespace(id=7, uid="admin", department_id=1),
    )

    assert result["reused"] is False
    assert result["job"]["status"] == "queued"
    assert result["job"]["progress"] == 0
    assert queued_jobs == [
        ("process_remote_material_sync_job", result["job"]["id"], {"_job_id": f"remote-sync:{result['job']['id']}"})
    ]
    assert db.commits == 1


@pytest.mark.asyncio
async def test_remote_sync_job_reuses_active_job(monkeypatch: pytest.MonkeyPatch):
    active_job = service.RemoteMaterialSyncJob(
        id="rmsj_active",
        owner_uid="admin",
        requested_by=7,
        status="running",
        phase="syncing",
        progress=42,
    )
    repository = FakeRepository(SimpleNamespace(username="user", password="password"))
    db = FakeJobDb(active_job)
    monkeypatch.setattr(service, "MaterialLibraryRepository", lambda db: repository)

    result = await service.create_remote_material_sync_job(
        db,
        SimpleNamespace(id=7, uid="admin", department_id=1),
    )

    assert result == {"job": active_job.to_dict(), "reused": True}
    assert db.added == []
    assert db.commits == 1


async def _async_value(value):
    return value
