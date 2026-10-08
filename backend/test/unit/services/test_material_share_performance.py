from __future__ import annotations

import asyncio
import hashlib
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from PIL import Image

from yuxi.services import material_library_service as service
from yuxi.storage.minio import StorageError


class MemoryStorage:
    def __init__(self):
        self.objects = {}
        self.reads = []
        self.copies = []
        self.deleted = []
        self.active = self.peak = 0
        self.fail_copy = None

    async def astat_file(self, bucket_name, object_name):
        data = self.objects.get((bucket_name, object_name))
        return len(data) if data is not None else None

    async def adownload_file(self, bucket_name, object_name):
        self.reads.append(object_name)
        return self.objects[(bucket_name, object_name)]

    async def aupload_file(self, bucket_name, object_name, data, content_type=None):
        self.objects[bucket_name, object_name] = data
        return SimpleNamespace(bucket_name=bucket_name, object_name=object_name)

    async def acopy_file(self, bucket_name, source_object_name, object_name, **kwargs):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.001)
            if object_name == self.fail_copy or (self.fail_copy and object_name.endswith(self.fail_copy)):
                raise StorageError("copy failed")
            self.copies.append((source_object_name, object_name))
            source_bucket = kwargs.get("source_bucket_name") or bucket_name
            self.objects[bucket_name, object_name] = self.objects[source_bucket, source_object_name]
            return SimpleNamespace(bucket_name=bucket_name, object_name=object_name)
        finally:
            self.active -= 1

    async def adelete_file(self, bucket_name, object_name):
        self.deleted.append(object_name)
        self.objects.pop((bucket_name, object_name), None)


class MemoryRedis:
    def __init__(self):
        self.objects = {}
        self.expiry = {}

    async def get(self, key):
        return self.objects.get(key)

    async def set(self, key, data, ex):
        self.objects[key] = data
        self.expiry[key] = ex


def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (1600, 1000), "royalblue").save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def share_env(monkeypatch):
    # Imported here so the initial regression run can exercise the old service first.
    from yuxi.services import material_share_images as images

    storage = MemoryStorage()
    redis = MemoryRedis()
    monkeypatch.setattr(service, "get_minio_client", lambda: storage)
    monkeypatch.setattr(images, "get_minio_client", lambda: storage)
    monkeypatch.setattr(images, "get_binary_redis_client", AsyncMock(return_value=redis))
    return storage, redis, images


@pytest.mark.asyncio
async def test_derivatives_reuse_content_version_and_regenerate_after_source_changes(share_env):
    storage, _, images = share_env
    data = image_bytes()
    asset = SimpleNamespace(sha256=hashlib.sha256(data).hexdigest(), bucket_name="image", object_name="case.png")
    first = await images.ensure_material_share_images(asset, data)
    storage.reads.clear()
    assert await images.ensure_material_share_images(asset) == first
    assert storage.reads == []
    asset.sha256 = "new-content-version"
    second = await images.ensure_material_share_images(asset, data)
    assert second != first
    assert all(("image", name) in storage.objects for name in first + second)
    with Image.open(io.BytesIO(storage.objects["image", second[0]])) as display:
        assert display.format == "WEBP"
        assert display.width <= 1440
    with Image.open(io.BytesIO(storage.objects["image", second[1]])) as cover:
        assert cover.format == "JPEG"
        assert cover.size == (500, 400)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_share_copies_ready_images_in_order_and_cleans_all_completed_copies(share_env, monkeypatch, failure):
    storage, redis, images = share_env
    data = image_bytes()
    category = SimpleNamespace(
        id="case",
        parent_id="root",
        industry_slug="decoration",
        name="案例",
        building_name="测试楼盘",
        area="120",
        design_style="雅致现代",
    )
    rows = []
    for index in range(6):
        asset = SimpleNamespace(
            id=f"asset-{index}",
            sha256=hashlib.sha256(data).hexdigest(),
            bucket_name="image",
            object_name=f"source-{index}.png",
            metadata_json={},
            content_type="image/png",
            original_file_name=f"{index}.png",
            file_size=len(data),
            image_width=1600,
            image_height=1000,
        )
        storage.objects["image", asset.object_name] = data
        await images.ensure_material_share_images(asset, data)
        rows.append((SimpleNamespace(id=f"item-{index}"), asset, category))
    repo = SimpleNamespace(
        list_image_items_with_assets_and_categories=AsyncMock(return_value=list(reversed(rows))),
        create_share=AsyncMock(),
    )
    monkeypatch.setattr(service, "MaterialLibraryRepository", lambda *a, **kw: repo)
    monkeypatch.setattr(service, "resolve_employee_for_user", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_audit_material", lambda *a, **kw: None)
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    storage.reads.clear()
    if failure:
        storage.fail_copy = "3.png.display.webp"
        with pytest.raises(HTTPException) as error:
            await service.create_material_share(
                db,
                SimpleNamespace(uid="owner", id=1, role="superadmin"),
                service.MaterialShareCreate(item_ids=[row[0].id for row in rows]),
            )
        assert error.value.detail["error"]["code"] == "MATERIAL_SHARE_STORAGE_FAILED"
        db.commit.assert_not_awaited()
        assert not any("material-library-shares/" in name for _, name in storage.objects)
        assert storage.deleted
    else:
        result = await service.create_material_share(
            db,
            SimpleNamespace(uid="owner", id=1, role="superadmin"),
            service.MaterialShareCreate(item_ids=[row[0].id for row in rows]),
        )
        _, snapshots = repo.create_share.await_args.args
        assert [item.original_file_name for item in snapshots] == [f"{i}.png" for i in range(6)]
        assert [item.display_order for item in snapshots] == list(range(1, 7))
        assert storage.reads and all(name.endswith(".card.jpg") for name in storage.reads)
        assert len(storage.copies) == 13  # six originals, six WebP images, first card cover
        assert redis.objects
        # Changing/deleting a gallery object cannot change the share snapshots.
        storage.objects["image", "source-0.png"] = b"changed"
        assert storage.objects["image", snapshots[0].object_name] == data
        assert result["share"]["image_count"] == 6
    assert 1 < storage.peak <= 4


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["cover", "webp"])
async def test_public_images_reuse_redis_and_persisted_files_after_cache_expiry(share_env, monkeypatch, variant):
    storage, redis, _ = share_env
    snapshot = SimpleNamespace(display_order=1, bucket_name="image", object_name="snapshot.png")
    monkeypatch.setattr(service, "get_public_material_share", AsyncMock(return_value=(object(), [snapshot])))
    storage.objects["image", snapshot.object_name] = image_bytes()
    fetch = (
        service.get_public_material_share_card_cover
        if variant == "cover"
        else (lambda db, token: service.get_public_material_share_display_webp(db, token, 1))
    )
    first = await fetch(None, "existing-token")
    assert first and redis.objects
    assert all(0 < ttl <= 86400 for ttl in redis.expiry.values())
    # A hot request needs neither the original nor the persisted derivative.
    storage.reads.clear()
    assert await fetch(None, "existing-token") == first
    assert storage.reads == []
    redis.objects.clear()
    del storage.objects["image", snapshot.object_name]
    assert await fetch(None, "existing-token") == first
    assert snapshot.object_name not in storage.reads


@pytest.mark.asyncio
async def test_cached_cover_does_not_make_unknown_share_public(share_env, monkeypatch):
    _, redis, _ = share_env
    redis.objects["material-share:v1:unknown:cover"] = b"cached"
    monkeypatch.setattr(service, "get_public_material_share", AsyncMock(side_effect=HTTPException(404)))
    with pytest.raises(HTTPException) as error:
        await service.get_public_material_share_card_cover(None, "unknown")
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_image_cache_bounds_and_outage_leave_persistent_images_readable(share_env, monkeypatch):
    storage, redis, images = share_env
    await images.write_share_image_cache("large", "display:1", b"x" * (images.SHARE_IMAGE_CACHE_MAX_BYTES + 1))
    assert redis.objects == {}
    monkeypatch.setattr(images, "get_binary_redis_client", AsyncMock(side_effect=RuntimeError("unavailable")))
    snapshot = SimpleNamespace(display_order=1, bucket_name="image", object_name="snapshot.png")
    monkeypatch.setattr(service, "get_public_material_share", AsyncMock(return_value=(object(), [snapshot])))
    storage.objects["image", "snapshot.png.card.jpg"] = b"persisted-jpeg"
    assert await service.get_public_material_share_card_cover(None, "existing") == b"persisted-jpeg"


@pytest.mark.asyncio
async def test_pending_upload_share_preserves_staged_original_without_waiting_for_worker(share_env, monkeypatch):
    storage, _, _ = share_env
    data = image_bytes()
    asset = SimpleNamespace(
        id="pending",
        sha256=hashlib.sha256(data).hexdigest(),
        bucket_name="image",
        object_name="pending.png",
        metadata_json={"ingest_status": "pending"},
        content_type="image/png",
        original_file_name="pending.png",
        file_size=len(data),
        image_width=1600,
        image_height=1000,
    )
    category = SimpleNamespace(
        id="case",
        parent_id="root",
        industry_slug="other",
        name="案例",
        building_name=None,
        area=None,
        design_style=None,
    )
    repo = SimpleNamespace(
        list_image_items_with_assets_and_categories=AsyncMock(
            return_value=[(SimpleNamespace(id="item"), asset, category)]
        ),
        create_share=AsyncMock(),
    )
    monkeypatch.setattr(service, "MaterialLibraryRepository", lambda *a, **kw: repo)
    monkeypatch.setattr(service, "read_material_bytes", AsyncMock(return_value=data))
    monkeypatch.setattr(service, "resolve_employee_for_user", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_audit_material", lambda *a, **kw: None)
    await service.create_material_share(
        SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()),
        SimpleNamespace(uid="owner", id=1, role="superadmin"),
        service.MaterialShareCreate(item_ids=["item"]),
    )
    _, snapshots = repo.create_share.await_args.args
    assert storage.objects["image", snapshots[0].object_name] == data
    assert all(source != "pending.png" for source, _ in storage.copies)


@pytest.mark.asyncio
async def test_remote_sync_prepares_images_and_uses_immutable_content_paths(share_env, monkeypatch):
    from yuxi.services import remote_material_library_service as remote

    storage, _, _ = share_env
    monkeypatch.setattr(remote, "get_minio_client", lambda: storage)
    downloader = SimpleNamespace(download=AsyncMock(return_value=image_bytes()))
    first = await remote._download_remote_asset(
        asyncio.Semaphore(1), downloader, None, "owner", {"id": "remote", "original_object_key": "source"}
    )
    second_image = io.BytesIO()
    Image.new("RGB", (32, 24), "red").save(second_image, format="PNG")
    downloader.download.return_value = second_image.getvalue()
    second = await remote._download_remote_asset(
        asyncio.Semaphore(1), downloader, None, "owner", {"id": "remote", "original_object_key": "source"}
    )
    assert first["uploaded"].object_name != second["uploaded"].object_name
    assert first["sha256"] in first["uploaded"].object_name
    assert len(storage.objects) == 6  # each content version has an original, WebP and card cover
