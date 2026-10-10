from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.services import content_cover_worker, material_library_service, personal_materials, personal_gallery_settings
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentTask


@pytest.mark.asyncio
@pytest.mark.parametrize("mini_program", [False, True])
async def test_cover_output_enters_pc_library_only_for_pc_content(monkeypatch, mini_program):
    saved_items = []
    assets = []
    task = SimpleNamespace(brief_json={"form_values": {"mp_content_code": "MP1"} if mini_program else {}})
    user = SimpleNamespace(uid="owner")

    class Session:
        async def scalar(self, query):
            model = query.column_descriptions[0]["entity"]
            return task if model is ContentTask else user if model is User else None

        async def commit(self):
            pass

    @asynccontextmanager
    async def session_context():
        yield Session()

    class Repository:
        def __init__(self, _db):
            pass

        async def create_asset(self, **values):
            asset = SimpleNamespace(**values)
            assets.append(asset)
            return asset

    async def upload_file(**kwargs):
        return SimpleNamespace(bucket_name=kwargs["bucket_name"], object_name=kwargs["object_name"])

    async def folder_categories(_db, _user):
        return {"generated": [SimpleNamespace(id="generated-gallery", owner_uid="system:material-library")]}

    async def create_library_item(_db, **kwargs):
        saved_items.append(kwargs)

    monkeypatch.setattr(content_cover_worker.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(content_cover_worker, "ContentCoverRepository", Repository)
    monkeypatch.setattr(content_cover_worker, "get_minio_client", lambda: SimpleNamespace(aupload_file=upload_file))
    monkeypatch.setattr(content_cover_worker, "_normalize_output", lambda raw, **_kwargs: (raw, 32, 24))
    monkeypatch.setattr(personal_materials, "folder_categories", folder_categories)
    monkeypatch.setattr(personal_gallery_settings, "load_personal_gallery_settings", AsyncMock(return_value={}))
    monkeypatch.setattr(material_library_service, "create_library_item_for_asset", create_library_item)

    job = SimpleNamespace(
        id="job-1", owner_uid="owner", tenant_id=None, content_task_id="task-1",
        request_json={}, result_json={}, mode="compose",
    )
    result = await content_cover_worker._store_outputs(job, [b"output"])

    assert result == [assets[0].id]
    assert len(saved_items) == (0 if mini_program else 1)
    if saved_items:
        assert saved_items[0]["category"] == "generated-gallery"
        assert saved_items[0]["category_owner_uid"] == "system:material-library"
