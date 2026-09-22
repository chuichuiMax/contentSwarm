from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from PIL import Image

import yuxi.services.material_upload_queue as material_upload_queue
from yuxi.services.material_upload_queue import (
    INGEST_COMPLETED,
    INGEST_PENDING,
    encode_material_thumbnail,
    material_thumb_object_name,
    persist_material_thumbnail,
    process_material_upload,
    read_material_bytes,
    stage_material_bytes,
    stage_material_thumb,
)
from yuxi.services.run_worker import WorkerSettings
from yuxi.storage.minio import StorageError


class FakeRedis:
    def __init__(self):
        self.store: dict[str, bytes] = {}

    async def set(self, key: str, value: bytes, *, ex: int | None = None):
        del ex
        self.store[key] = value

    async def get(self, key: str):
        return self.store.get(key)

    async def delete(self, *keys: str):
        for key in keys:
            self.store.pop(key, None)


class FakeStorage:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.uploaded: list[tuple[str, str, bytes, str | None]] = []

    async def aupload_file(self, bucket_name, object_name, data, content_type=None):
        self.objects[(bucket_name, object_name)] = data
        self.uploaded.append((bucket_name, object_name, data, content_type))
        return SimpleNamespace(bucket_name=bucket_name, object_name=object_name)

    async def adownload_file(self, bucket_name, object_name):
        data = self.objects.get((bucket_name, object_name))
        if data is None:
            raise StorageError("missing")
        return data


class FakeCoverRepository:
    def __init__(self, asset):
        self.asset = asset

    async def get_asset(self, asset_id: str, *, for_update: bool = False):
        del for_update
        return self.asset if asset_id == self.asset.id else None

    async def update_asset_metadata(self, asset, metadata):
        asset.metadata_json = metadata


def _webp(size=(32, 24)) -> bytes:
    source = io.BytesIO()
    Image.new("RGB", size, "red").save(source, format="WEBP")
    return source.getvalue()


@pytest.mark.asyncio
async def test_pending_material_reads_the_staged_bytes_before_minio(monkeypatch):
    redis = FakeRedis()

    async def fake_client():
        return redis

    monkeypatch.setattr(material_upload_queue, "get_binary_redis_client", fake_client)

    await stage_material_bytes("cca_pending", b"original")
    await stage_material_thumb("cca_pending", b"thumbnail")
    asset = SimpleNamespace(
        id="cca_pending",
        bucket_name="image",
        object_name="material-library/u1/images/cca_pending/image.webp",
        metadata_json={"ingest_status": INGEST_PENDING},
    )

    assert await read_material_bytes(asset) == b"original"
    assert await material_upload_queue.read_staged_material_thumb("cca_pending") == b"thumbnail"
    assert material_upload_queue.material_upload_redis_key("cca_pending") in redis.store

    await material_upload_queue.delete_material_display_cache("cca_pending")
    assert await material_upload_queue.read_staged_material_thumb("cca_pending") is None


@pytest.mark.asyncio
async def test_process_material_upload_moves_redis_bytes_to_storage(monkeypatch):
    redis = FakeRedis()
    storage = FakeStorage()
    original = _webp()
    asset = SimpleNamespace(
        id="cca_queued",
        deleted_at=None,
        bucket_name="image",
        object_name="material-library/u1/images/cca_queued/image.webp",
        content_type="image/webp",
        metadata_json={"ingest_status": INGEST_PENDING},
    )
    repository = FakeCoverRepository(asset)

    class FakeSession:
        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class FakeManager:
        def get_async_session_context(self):
            return FakeSession()

    async def fake_client():
        return redis

    monkeypatch.setattr(material_upload_queue, "get_binary_redis_client", fake_client)
    monkeypatch.setattr(material_upload_queue, "pg_manager", FakeManager())
    monkeypatch.setattr(material_upload_queue, "get_minio_client", lambda: storage)
    monkeypatch.setattr(material_upload_queue, "ContentCoverRepository", lambda _db: repository)

    await stage_material_bytes(asset.id, original)
    await process_material_upload({}, asset.id)

    assert storage.uploaded[0] == ("image", asset.object_name, original, "image/webp")
    assert storage.uploaded[1][1] == material_thumb_object_name(asset.object_name)
    assert storage.uploaded[1][3] == "image/webp"
    assert storage.uploaded[1][2][8:12] == b"WEBP"
    assert asset.metadata_json["ingest_status"] == INGEST_COMPLETED
    assert await material_upload_queue.read_staged_material_thumb(asset.id) is None


@pytest.mark.asyncio
async def test_persist_thumbnail_reuses_redis_without_rereading_original(monkeypatch):
    redis = FakeRedis()
    storage = FakeStorage()
    original = _webp((900, 600))
    asset = SimpleNamespace(
        id="cca_ready",
        bucket_name="image",
        object_name="material-library/u1/images/cca_ready/image.webp",
        metadata_json={"ingest_status": INGEST_COMPLETED},
    )
    storage.objects[(asset.bucket_name, asset.object_name)] = original

    async def fake_client():
        return redis

    monkeypatch.setattr(material_upload_queue, "get_binary_redis_client", fake_client)
    monkeypatch.setattr(material_upload_queue, "get_minio_client", lambda: storage)

    first = await persist_material_thumbnail(asset)
    assert first[8:12] == b"WEBP"
    assert storage.uploaded[-1][1] == material_thumb_object_name(asset.object_name)
    uploads = len(storage.uploaded)
    second = await persist_material_thumbnail(asset)
    assert second == first
    assert len(storage.uploaded) == uploads


def test_material_thumb_object_name_sits_beside_original():
    assert (
        material_thumb_object_name("material-library/u1/images/cca_1/image.webp")
        == "material-library/u1/images/cca_1/image.webp.thumb.webp"
    )


def test_encode_material_thumbnail_is_webp():
    data = encode_material_thumbnail(_webp((64, 48)))
    assert data[8:12] == b"WEBP"


def test_worker_registers_material_upload_job():
    assert process_material_upload in WorkerSettings.functions
