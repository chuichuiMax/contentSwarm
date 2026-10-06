from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import yuxi.image_design.save_targets as save_targets
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
async def test_fixed_folder_rename_and_delete_keep_stable_key_and_tombstone():
    db = FolderSettingsDB()
    admin = SimpleNamespace(uid="admin-1", role="admin")
    regular = SimpleNamespace(uid="regular-1", role="user")

    with pytest.raises(HTTPException) as forbidden:
        await personal_materials.rename_fixed_folder(db, regular, "rough", "新名称")
    assert forbidden.value.status_code == 403
    assert db.rows == []

    await personal_materials.rename_fixed_folder(db, admin, "rough", "装修毛坯")
    await personal_materials.delete_fixed_folder(db, admin, "rough")
    assert len(db.rows) == 1
    assert db.rows[0].folder_key == "rough"
    assert db.rows[0].name == "装修毛坯"
    assert db.rows[0].deleted_at is not None
    assert db.rows[0].updated_by == "admin-1"
    assert db.commits == 2

    with pytest.raises(HTTPException) as deleted:
        await personal_materials.rename_fixed_folder(db, admin, "rough", "恢复旧入口")
    assert deleted.value.status_code == 404
    assert len(db.rows) == 1


@pytest.mark.asyncio
async def test_deleted_fixed_folder_does_not_recreate_its_storage_category(monkeypatch):
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

    async def settings(_db):
        return {"rough": SimpleNamespace(deleted_at=datetime.now(UTC))}

    async def root(_db, _user, _scope):
        return SimpleNamespace(id="private-root")

    monkeypatch.setattr(personal_materials, "MaterialLibraryRepository", FakeRepo)
    monkeypatch.setattr(personal_materials, "fixed_folder_settings", settings)
    monkeypatch.setattr(save_targets, "ensure_scope_root", root)
    user = SimpleNamespace(uid="owner-1", department_id=None)

    folders = await personal_materials.folder_categories(FakeDB(), user)
    assert folders["rough"] == []
    assert folders["uploads"][0].id == "mp-uploads-private"
    assert [value["id"] for value in synced] == ["mp-uploads-private"]
