from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import yuxi.services.personal_materials as personal_materials
from yuxi.storage.postgres.models_content import ContentMaterialCategory


class FolderSettingsDB:
    def __init__(self):
        self.rows = []
        self.commits = 0

    async def execute(self, _query):
        rows = self.rows

        class Result:
            def scalars(self):
                return rows

        return Result()

    def add(self, setting):
        self.rows.append(setting)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_only_superadmin_can_modify_or_delete_global_personal_galleries():
    db = FolderSettingsDB()
    admin = SimpleNamespace(uid="admin-1", role="admin")
    regular = SimpleNamespace(uid="regular-1", role="user")

    with pytest.raises(HTTPException) as forbidden:
        await personal_materials.rename_fixed_folder(db, regular, "rough", "新名称")
    assert forbidden.value.status_code == 403
    for operation, args in (
        (personal_materials.rename_fixed_folder, ("rough", "装修毛坯")),
        (personal_materials.delete_fixed_folder, ("rough",)),
    ):
        with pytest.raises(HTTPException) as immutable:
            await operation(db, admin, *args)
        assert immutable.value.status_code == 403
    assert db.rows == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_missing_personal_folders_provision_four_actual_categories(monkeypatch):
    rows = []
    synced = []

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def list_categories(self, _owner, _type):
            return rows

        async def sync_system_categories(self, values):
            synced.extend(values)
            rows.extend(ContentMaterialCategory(**value) for value in values)

    class FakeDB:
        async def flush(self):
            pass

    monkeypatch.setattr(personal_materials, "MaterialLibraryRepository", FakeRepo)
    from unittest.mock import AsyncMock
    monkeypatch.setattr(personal_materials, "load_personal_gallery_settings", AsyncMock(return_value={}))
    user = SimpleNamespace(uid="owner-1", department_id=None)

    folders = await personal_materials.folder_categories(FakeDB(), user)
    assert folders["rough"][0].id == "mp-rough-private"
    assert folders["uploads"][0].id == "mp-uploads-private"
    assert {value["id"] for value in synced} == {
        "mp-rough-private",
        "mp-uploads-private",
        "mp-generated-private",
        "mp-works-private",
    }
