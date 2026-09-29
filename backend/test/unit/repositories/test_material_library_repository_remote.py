from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.repositories.material_library_repository import MaterialLibraryRepository


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeDb:
    def __init__(self, setting=None):
        self.setting = setting
        self.added = []
        self.flushes = 0

    async def execute(self, query):
        del query
        return FakeResult(self.setting)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushes += 1


@pytest.mark.asyncio
async def test_remote_setting_repository_reads_and_creates_global_setting():
    db = FakeDb()
    repository = MaterialLibraryRepository(db)

    assert await repository.get_remote_setting() is None

    setting = await repository.upsert_remote_setting(
        base_url="http://remote.example",
        username="remote-user",
        password="remote-password",
        verification_status="verified",
        verified_at=None,
        updated_by=7,
    )

    assert setting.id == "global"
    assert setting.base_url == "http://remote.example"
    assert setting.username == "remote-user"
    assert setting.password == "remote-password"
    assert setting.verification_status == "verified"
    assert setting.updated_by == 7
    assert db.added == [setting]
    assert db.flushes == 1


@pytest.mark.asyncio
async def test_remote_setting_repository_updates_existing_setting():
    setting = SimpleNamespace(
        id="global",
        base_url="http://old.example",
        username="old-user",
        password="old-password",
        verification_status="failed",
        verified_at=None,
        updated_by=1,
        updated_at=None,
    )
    db = FakeDb(setting)
    repository = MaterialLibraryRepository(db)

    result = await repository.upsert_remote_setting(
        base_url="http://remote.example",
        username="remote-user",
        password="remote-password",
        verification_status="verified",
        verified_at=None,
        updated_by=7,
    )

    assert result is setting
    assert setting.base_url == "http://remote.example"
    assert setting.username == "remote-user"
    assert setting.password == "remote-password"
    assert setting.verification_status == "verified"
    assert setting.updated_by == 7
    assert setting.updated_at is not None
    assert db.added == []
    assert db.flushes == 1
