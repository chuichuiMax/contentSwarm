from fastapi import HTTPException
from pydantic import ValidationError

import pytest

from yuxi.services.variable_service import (
    SERVICE_ENTRIES,
    VariableCreate,
    VariableUpdate,
    _normalize_editions,
    _normalize_ports,
    next_variable_code,
)


def test_variable_create_schema_requires_name_and_service_entry():
    with pytest.raises(ValidationError):
        VariableCreate(name="", service_entry="好评笔记")
    with pytest.raises(ValidationError):
        VariableCreate(name="设计师", service_entry="")


def test_variable_create_defaults_ports_and_editions():
    payload = VariableCreate(name="楼盘信息", service_entry="装修家居")
    assert payload.ports == ["pc", "app"]
    assert payload.editions == ["quick", "pro"]


def test_variable_create_rejects_empty_ports_and_editions():
    with pytest.raises(ValidationError):
        VariableCreate(name="楼盘信息", service_entry="装修家居", ports=[])
    with pytest.raises(ValidationError):
        VariableCreate(name="楼盘信息", service_entry="装修家居", editions=[])


def test_normalize_ports_and_editions_keep_canonical_order():
    assert _normalize_ports(["app", "pc"]) == ["pc", "app"]
    assert _normalize_editions(["pro", "quick"]) == ["quick", "pro"]
    with pytest.raises(HTTPException) as ports_exc:
        _normalize_ports([])
    assert ports_exc.value.detail["error"]["code"] == "VARIABLE_INVALID_FIELD"
    with pytest.raises(HTTPException) as editions_exc:
        _normalize_editions(["unknown"])
    assert editions_exc.value.detail["error"]["code"] == "VARIABLE_INVALID_FIELD"


def test_variable_update_schema_allows_partial_enabled():
    payload = VariableUpdate(enabled=False)
    assert payload.model_dump(exclude_unset=True) == {"enabled": False}


def test_next_variable_code_increments_fwtd_sequence():
    assert next_variable_code([]) == "FWTD0001"
    assert next_variable_code(["FWTD0001", "FWTD0005", "OTHER01"]) == "FWTD0006"
    assert next_variable_code(["FWTD0009"]) == "FWTD0010"


def test_service_entries_are_home_and_review_notes():
    assert SERVICE_ENTRIES == ("装修家居", "好评笔记")


@pytest.mark.asyncio
async def test_ensure_default_variables_adds_missing_names(monkeypatch):
    from types import SimpleNamespace

    from yuxi.services import variable_service as service

    created = []

    class FakeRepo:
        async def list_variables(self):
            return [SimpleNamespace(service_entry="装修家居", name="楼盘信息")]

        async def list_codes(self):
            return ["FWTD0006"]

        async def create(self, data):
            created.append(data)
            return data

    monkeypatch.setattr(service, "VariableRepository", lambda _db: FakeRepo())
    await service.ensure_default_variables(object())
    names = {item["name"] for item in created}
    assert "楼盘信息" not in names
    assert {"目标人群", "外框面积", "设计风格", "所在区域", "岗位", "从业年限"} <= names


@pytest.mark.asyncio
async def test_create_variable_allows_same_name_on_different_service_entry(monkeypatch):
    from types import SimpleNamespace

    from yuxi.services import variable_service as service

    existing = SimpleNamespace(id="v1", name="外框面积", service_entry="装修家居")

    class FakeRepo:
        async def list_codes(self):
            return ["FWTD0018"]

        async def get_by_service_entry_and_name(self, service_entry, name):
            if service_entry == existing.service_entry and name == existing.name:
                return existing
            return None

        async def get_by_code(self, code):
            return None

        async def create(self, data):
            return SimpleNamespace(to_dict=lambda: data, **data)

    async def fake_ensure(_db):
        return None

    monkeypatch.setattr(service, "ensure_default_variables", fake_ensure)
    monkeypatch.setattr(service, "VariableRepository", lambda _db: FakeRepo())

    result = await service.create_variable(
        object(),
        SimpleNamespace(uid="u1"),
        VariableCreate(name="外框面积", service_entry="好评笔记"),
    )
    assert result["variable"]["name"] == "外框面积"
    assert result["variable"]["service_entry"] == "好评笔记"


@pytest.mark.asyncio
async def test_create_variable_rejects_same_name_on_same_service_entry(monkeypatch):
    from types import SimpleNamespace

    from yuxi.services import variable_service as service

    class FakeRepo:
        async def list_codes(self):
            return []

        async def get_by_service_entry_and_name(self, service_entry, name):
            return SimpleNamespace(id="v1")

        async def get_by_code(self, code):
            return None

        async def create(self, data):
            raise AssertionError("should not create")

    async def fake_ensure(_db):
        return None

    monkeypatch.setattr(service, "ensure_default_variables", fake_ensure)
    monkeypatch.setattr(service, "VariableRepository", lambda _db: FakeRepo())

    with pytest.raises(HTTPException) as exc_info:
        await service.create_variable(
            object(),
            SimpleNamespace(uid="u1"),
            VariableCreate(name="外框面积", service_entry="好评笔记"),
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "VARIABLE_NAME_EXISTS"