from types import SimpleNamespace

import pytest
import yuxi.content  # noqa: F401  先加载内容包，避免内容仓储的循环导入
from fastapi import HTTPException

from yuxi.services import content_cover_service


class _Rows:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self.values


class _Db:
    def __init__(self, batches):
        self.batches = list(batches)

    async def execute(self, _query):
        return _Rows(self.batches.pop(0))


@pytest.mark.asyncio
async def test_visible_content_cover_can_be_read_by_another_account(monkeypatch):
    asset = SimpleNamespace(
        bucket_name="content-covers",
        object_name="covers/result.webp",
        content_type="image/webp",
        original_file_name="cover.webp",
    )
    user = SimpleNamespace(uid="tiechui", role="superadmin", department_id=1)

    class CoverRepo:
        def __init__(self, _db):
            pass

        async def get_asset_for_user(self, *_args, **_kwargs):
            return None

        async def get_asset(self, asset_id):
            assert asset_id == "cca_visible"
            return asset

    class ContentRepo:
        def __init__(self, _db):
            pass

        async def get_task_for_user(self, task_id, current_user):
            assert task_id == "ct_visible"
            assert current_user is user
            return SimpleNamespace(id=task_id)

    monkeypatch.setattr(content_cover_service, "ContentCoverRepository", CoverRepo)
    monkeypatch.setattr(content_cover_service, "ContentRepository", ContentRepo)

    async def download(_bucket, _name):
        return b"image-bytes"

    monkeypatch.setattr(
        content_cover_service,
        "get_minio_client",
        lambda: SimpleNamespace(adownload_file=download),
    )

    data, content_type, file_name = await content_cover_service.get_cover_asset_file(
        _Db([["ct_visible"], []]),
        user,
        "cca_visible",
    )
    assert data == b"image-bytes"
    assert content_type == "image/webp"
    assert file_name == "cover.webp"


@pytest.mark.asyncio
async def test_unrelated_account_still_cannot_read_another_cover(monkeypatch):
    user = SimpleNamespace(uid="other", role="user", department_id=None)

    class CoverRepo:
        def __init__(self, _db):
            pass

        async def get_asset_for_user(self, *_args, **_kwargs):
            return None

        async def get_asset(self, _asset_id):
            return SimpleNamespace(id="cca_private")

    class ContentRepo:
        def __init__(self, _db):
            pass

        async def get_task_for_user(self, _task_id, _user):
            return None

    monkeypatch.setattr(content_cover_service, "ContentCoverRepository", CoverRepo)
    monkeypatch.setattr(content_cover_service, "ContentRepository", ContentRepo)

    with pytest.raises(HTTPException) as raised:
        await content_cover_service.get_cover_asset_file(_Db([["ct_private"], []]), user, "cca_private")
    assert raised.value.status_code == 404
    assert raised.value.detail["error"]["message"] == "封面素材不存在"
