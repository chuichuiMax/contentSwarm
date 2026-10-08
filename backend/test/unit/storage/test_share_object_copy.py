from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from yuxi.storage.minio.client import MinIOClient, StorageError
from yuxi.storage.oss.client import OssStorageClient, RoutedStorageClient


@pytest.mark.asyncio
async def test_minio_copies_between_buckets_without_download(monkeypatch):
    client = MinIOClient()
    client._client = SimpleNamespace(copy_object=Mock())
    result = await client.acopy_file("image", "source.webp", "share.webp", source_bucket_name="content-covers")
    bucket, target, source = client._client.copy_object.call_args.args
    assert (bucket, target) == ("image", "share.webp")
    assert (source.bucket_name, source.object_name) == ("content-covers", "source.webp")
    assert (result.bucket_name, result.object_name) == ("image", "share.webp")


@pytest.mark.asyncio
async def test_oss_copies_logical_buckets_with_correct_physical_prefixes(monkeypatch):
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "test-secret")
    client = OssStorageClient()
    client._client = SimpleNamespace(copy_object=Mock())
    result = await client.acopy_file("image", "source.webp", "share.webp", source_bucket_name="content-covers")
    request = client._client.copy_object.call_args.args[0]
    assert request.bucket == request.source_bucket == client.bucket
    assert request.key == "image/share.webp"
    assert request.source_key == "content-covers/source.webp"
    assert result.object_name == "share.webp"


@pytest.mark.asyncio
async def test_routed_copy_uses_server_copy_for_oss_and_transfers_only_across_backends():
    minio = SimpleNamespace(adownload_file=AsyncMock(return_value=b"original"), acopy_file=AsyncMock())
    oss = SimpleNamespace(acopy_file=AsyncMock(), aupload_file=AsyncMock())
    client = RoutedStorageClient(minio, oss, frozenset({"image", "content-covers"}))
    await client.acopy_file("image", "source.jpg", "share.jpg", source_bucket_name="content-covers")
    oss.acopy_file.assert_awaited_once()
    minio.adownload_file.assert_not_awaited()
    await client.acopy_file("image", "source.jpg", "share.jpg", source_bucket_name="public", content_type="image/jpeg")
    minio.adownload_file.assert_awaited_once_with("public", "source.jpg")
    oss.aupload_file.assert_awaited_once_with("image", "share.jpg", b"original", "image/jpeg")


@pytest.mark.asyncio
async def test_oss_copy_failure_is_reported_without_downloading(monkeypatch):
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "test-secret")
    client = OssStorageClient()
    client._client = SimpleNamespace(copy_object=Mock(side_effect=ValueError("copy failed")))
    with pytest.raises(StorageError, match="复制文件失败"):
        await client.acopy_file("image", "source.jpg", "share.jpg")
