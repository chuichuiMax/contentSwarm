from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from yuxi.services import content_cover_worker, material_library_service, personal_gallery_settings
from yuxi.storage.postgres.models_content import ContentTask


@pytest.mark.asyncio
@pytest.mark.parametrize("mini_program", [False, True])
@pytest.mark.parametrize("works_deleted", [False, True])
async def test_content_cover_outputs_belong_only_to_works(monkeypatch, mini_program, works_deleted):
    saved_items = []
    assets = []
    task = SimpleNamespace(brief_json={"form_values": {"mp_content_code": "MP1"} if mini_program else {}})

    class Session:
        async def scalar(self, query):
            model = query.column_descriptions[0]["entity"]
            assert model is ContentTask
            return task

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

    async def create_library_item(_db, **kwargs):
        saved_items.append(kwargs)

    monkeypatch.setattr(content_cover_worker.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(content_cover_worker, "ContentCoverRepository", Repository)
    monkeypatch.setattr(content_cover_worker, "get_minio_client", lambda: SimpleNamespace(aupload_file=upload_file))
    monkeypatch.setattr(content_cover_worker, "_normalize_output", lambda raw, **_kwargs: (raw, 32, 24))
    settings = {
        "generated": SimpleNamespace(deleted_at=datetime(2026, 10, 12)),
        "works": SimpleNamespace(deleted_at=datetime(2026, 10, 12) if works_deleted else None),
    }
    monkeypatch.setattr(personal_gallery_settings, "load_personal_gallery_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(material_library_service, "create_library_item_for_asset", create_library_item)

    job = SimpleNamespace(
        id="job-1", owner_uid="owner", tenant_id=None, content_task_id="task-1",
        request_json={}, result_json={}, mode="compose",
    )
    if works_deleted:
        with pytest.raises(HTTPException) as error:
            await content_cover_worker._store_outputs(job, [b"output"])
        assert error.value.status_code == 409
        assert assets == []
        assert saved_items == []
        return
    result = await content_cover_worker._store_outputs(job, [b"output"])

    assert result == [assets[0].id]
    assert saved_items == []
    assert assets[0].metadata_json["source"] == "content_production"
    assert assets[0].metadata_json["source_channel"] == ("mp" if mini_program else "pc")
    assert assets[0].metadata_json["source_folder"] == "works"
