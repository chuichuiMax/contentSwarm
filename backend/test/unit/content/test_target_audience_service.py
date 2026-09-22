from pydantic import ValidationError

import pytest

from yuxi.services.target_audience_service import (
    DEFAULT_TARGET_AUDIENCES,
    TargetAudienceCreate,
    TargetAudienceUpdate,
)


def test_target_audience_create_requires_name():
    with pytest.raises(ValidationError):
        TargetAudienceCreate(name="")


def test_target_audience_create_defaults_enabled():
    payload = TargetAudienceCreate(name="毛坯")
    assert payload.enabled is True


def test_target_audience_update_allows_partial_fields():
    payload = TargetAudienceUpdate(enabled=False)
    assert payload.model_dump(exclude_unset=True) == {"enabled": False}


def test_default_target_audiences_match_catalog():
    assert DEFAULT_TARGET_AUDIENCES == (
        ("毛坯", True),
        ("精装房", True),
        ("旧房改造", True),
        ("别墅", True),
        ("乡墅", False),
    )


@pytest.mark.asyncio
async def test_ensure_default_target_audiences_skips_when_any_exist(monkeypatch):
    from yuxi.services import target_audience_service as service

    created: list[dict] = []

    class FakeRepo:
        async def has_any(self):
            return True

        async def create(self, data):
            created.append(data)
            return data

    monkeypatch.setattr(service, "TargetAudienceRepository", lambda _db: FakeRepo())

    await service.ensure_default_target_audiences(object())
    assert created == []


@pytest.mark.asyncio
async def test_ensure_default_target_audiences_seeds_when_empty(monkeypatch):
    from yuxi.services import target_audience_service as service

    created: list[dict] = []

    class FakeRepo:
        async def has_any(self):
            return False

        async def create(self, data):
            created.append(data)
            return data

    monkeypatch.setattr(service, "TargetAudienceRepository", lambda _db: FakeRepo())

    await service.ensure_default_target_audiences(object())
    assert len(created) == len(service.DEFAULT_TARGET_AUDIENCES)
    assert created[0]["name"] == "毛坯"
    assert created[0]["enabled"] is True
    assert created[-1]["name"] == "乡墅"
    assert created[-1]["enabled"] is False
    assert created[0]["created_by"] == "system"


@pytest.mark.asyncio
async def test_list_enabled_target_audience_names(monkeypatch):
    from yuxi.services import target_audience_service as service

    class FakeRepo:
        async def list_enabled_names(self):
            return ["毛坯", "精装房", "旧房改造", "别墅"]

    async def fake_ensure(_db):
        return None

    monkeypatch.setattr(service, "ensure_default_target_audiences", fake_ensure)
    monkeypatch.setattr(service, "TargetAudienceRepository", lambda _db: FakeRepo())

    result = await service.list_enabled_target_audience_names(object())
    assert result == ["毛坯", "精装房", "旧房改造", "别墅"]
