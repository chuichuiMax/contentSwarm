from pydantic import ValidationError

import pytest

from yuxi.services.resident_population_service import (
    DEFAULT_RESIDENT_POPULATIONS,
    ResidentPopulationCreate,
    ResidentPopulationUpdate,
)


def test_resident_population_create_requires_name():
    with pytest.raises(ValidationError):
        ResidentPopulationCreate(name="")


def test_resident_population_create_defaults_enabled():
    payload = ResidentPopulationCreate(name="三口之家")
    assert payload.enabled is True


def test_resident_population_update_allows_partial_fields():
    payload = ResidentPopulationUpdate(enabled=False)
    assert payload.model_dump(exclude_unset=True) == {"enabled": False}


def test_default_resident_populations_match_catalog():
    assert DEFAULT_RESIDENT_POPULATIONS == (
        ("三口之家", True),
        ("四口之家", True),
        ("五口之家", True),
        ("两代同堂", True),
        ("新婚婚房", True),
        ("养老房", True),
        ("人宠友好家", True),
    )


@pytest.mark.asyncio
async def test_ensure_default_resident_populations_skips_when_any_exist(monkeypatch):
    from yuxi.services import resident_population_service as service

    created: list[dict] = []

    class FakeRepo:
        async def has_any(self):
            return True

        async def create(self, data):
            created.append(data)
            return data

    monkeypatch.setattr(service, "ResidentPopulationRepository", lambda _db: FakeRepo())

    await service.ensure_default_resident_populations(object())
    assert created == []


@pytest.mark.asyncio
async def test_ensure_default_resident_populations_seeds_when_empty(monkeypatch):
    from yuxi.services import resident_population_service as service

    created: list[dict] = []

    class FakeRepo:
        async def has_any(self):
            return False

        async def create(self, data):
            created.append(data)
            return data

    monkeypatch.setattr(service, "ResidentPopulationRepository", lambda _db: FakeRepo())

    await service.ensure_default_resident_populations(object())
    assert len(created) == len(service.DEFAULT_RESIDENT_POPULATIONS)
    assert created[0]["name"] == "三口之家"
    assert created[0]["enabled"] is True
    assert created[-1]["name"] == "人宠友好家"
    assert created[-1]["enabled"] is True
    assert created[0]["created_by"] == "system"


@pytest.mark.asyncio
async def test_list_enabled_resident_population_names(monkeypatch):
    from yuxi.services import resident_population_service as service

    class FakeRepo:
        async def list_enabled_names(self):
            return ["三口之家", "四口之家", "五口之家"]

    async def fake_ensure(_db):
        return None

    monkeypatch.setattr(service, "ensure_default_resident_populations", fake_ensure)
    monkeypatch.setattr(service, "ResidentPopulationRepository", lambda _db: FakeRepo())

    result = await service.list_enabled_resident_population_names(object())
    assert result == ["三口之家", "四口之家", "五口之家"]
