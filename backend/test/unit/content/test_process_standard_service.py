from pydantic import ValidationError

import pytest

from yuxi.services.process_standard_service import (
    DEFAULT_PROCESS_STANDARDS,
    ProcessStandardCreate,
    ProcessStandardUpdate,
)


def test_process_standard_create_requires_name():
    with pytest.raises(ValidationError):
        ProcessStandardCreate(name="", detail="HYB-示例工艺")


def test_process_standard_create_requires_detail():
    with pytest.raises(ValidationError):
        ProcessStandardCreate(name="暖通舒适系统", detail="")


def test_process_standard_create_defaults_enabled():
    payload = ProcessStandardCreate(name="暖通舒适系统", detail="HYB-中央空调管线孔封闭工艺")
    assert payload.enabled is True


def test_process_standard_update_allows_partial_fields():
    payload = ProcessStandardUpdate(enabled=False)
    assert payload.model_dump(exclude_unset=True) == {"enabled": False}


def test_default_process_standards_match_import_catalog():
    assert len(DEFAULT_PROCESS_STANDARDS) == 69
    names = {name for name, _, _ in DEFAULT_PROCESS_STANDARDS}
    assert len(names) == 15
    assert ("安全用电系统", "HYB-强电箱安全配置系统", True) in DEFAULT_PROCESS_STANDARDS
    assert ("个性定制系统", "HYB-油漆调色定制工艺", True) in DEFAULT_PROCESS_STANDARDS


@pytest.mark.asyncio
async def test_ensure_default_process_standards_skips_existing_keys(monkeypatch):
    from yuxi.services import process_standard_service as service

    existing = {("安全用电系统", "HYB-强电箱安全配置系统")}
    created: list[dict] = []

    class FakeRepo:
        async def get_by_name_detail(self, name, detail):
            return object() if (name, detail) in existing else None

        async def create(self, data):
            created.append(data)
            existing.add((data["name"], data["detail"]))
            return data

    monkeypatch.setattr(service, "ProcessStandardRepository", lambda _db: FakeRepo())

    await service.ensure_default_process_standards(object())
    assert len(created) == len(service.DEFAULT_PROCESS_STANDARDS) - 1
    assert created[0]["name"] == "安全用电系统"
    assert created[0]["detail"] == "HYB-强电箱内空开安装工艺"


@pytest.mark.asyncio
async def test_ensure_default_process_standards_seeds_all_when_empty(monkeypatch):
    from yuxi.services import process_standard_service as service

    created: list[dict] = []

    class FakeRepo:
        async def get_by_name_detail(self, name, detail):
            return None

        async def create(self, data):
            created.append(data)
            return data

    monkeypatch.setattr(service, "ProcessStandardRepository", lambda _db: FakeRepo())

    await service.ensure_default_process_standards(object())
    assert len(created) == len(service.DEFAULT_PROCESS_STANDARDS)
    assert created[0]["name"] == "安全用电系统"
    assert created[0]["detail"] == "HYB-强电箱安全配置系统"
    assert created[0]["created_by"] == "system"


@pytest.mark.asyncio
async def test_list_process_standards_filters_by_name(monkeypatch):
    from types import SimpleNamespace

    from yuxi.services import process_standard_service as service

    class FakeRepo:
        async def list_items(self, *, keyword=None, name=None):
            assert name == "暖通舒适系统"
            assert keyword is None
            return [
                SimpleNamespace(
                    to_dict=lambda: {
                        "id": "1",
                        "name": "暖通舒适系统",
                        "detail": "HYB-地暖高流地坪工艺",
                        "enabled": True,
                    }
                )
            ]

        async def list_names(self):
            return ["安全用电系统", "暖通舒适系统"]

    async def fake_ensure(_db):
        return None

    monkeypatch.setattr(service, "ensure_default_process_standards", fake_ensure)
    monkeypatch.setattr(service, "ProcessStandardRepository", lambda _db: FakeRepo())

    result = await service.list_process_standards(object(), name="暖通舒适系统")
    assert result["total"] == 1
    assert result["names"] == ["安全用电系统", "暖通舒适系统"]
    assert result["process_standards"][0]["name"] == "暖通舒适系统"


@pytest.mark.asyncio
async def test_list_enabled_process_type_names(monkeypatch):
    from yuxi.services import process_standard_service as service

    class FakeRepo:
        async def list_enabled_names(self):
            return ["安全用电系统", "暖通舒适系统"]

    async def fake_ensure(_db):
        return None

    monkeypatch.setattr(service, "ensure_default_process_standards", fake_ensure)
    monkeypatch.setattr(service, "ProcessStandardRepository", lambda _db: FakeRepo())

    result = await service.list_enabled_process_type_names(object())
    assert result == ["安全用电系统", "暖通舒适系统"]


@pytest.mark.asyncio
async def test_list_enabled_process_names_by_type(monkeypatch):
    from yuxi.services import process_standard_service as service

    class FakeRepo:
        async def list_enabled_names_by_type(self):
            return {"暖通舒适系统": ["HYB-地暖高流地坪工艺"]}

    async def fake_ensure(_db):
        return None

    monkeypatch.setattr(service, "ensure_default_process_standards", fake_ensure)
    monkeypatch.setattr(service, "ProcessStandardRepository", lambda _db: FakeRepo())

    result = await service.list_enabled_process_names_by_type(object())
    assert result == {"暖通舒适系统": ["HYB-地暖高流地坪工艺"]}


@pytest.mark.asyncio
async def test_create_process_standard_rejects_duplicate(monkeypatch):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from yuxi.services import process_standard_service as service

    class FakeRepo:
        async def get_by_name_detail(self, name, detail):
            return SimpleNamespace(id="existing")

        async def create(self, data):
            raise AssertionError("should not create duplicate")

    async def fake_ensure(_db):
        return None

    monkeypatch.setattr(service, "ensure_default_process_standards", fake_ensure)
    monkeypatch.setattr(service, "ProcessStandardRepository", lambda _db: FakeRepo())

    with pytest.raises(HTTPException) as exc_info:
        await service.create_process_standard(
            object(),
            SimpleNamespace(uid="u1"),
            ProcessStandardCreate(name="暖通舒适系统", detail="HYB-中央空调管线孔封闭工艺"),
        )
    assert exc_info.value.status_code == 409
