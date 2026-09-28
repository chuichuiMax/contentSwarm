"""阿里云 OSS 分片上传适配层单元测试。"""

from types import SimpleNamespace

import pytest

from yuxi.storage.minio.client import StorageError, reset_storage_client
from yuxi.storage.oss.client import OssStorageClient


class _FakeOssSdkClient:
    def __init__(self) -> None:
        self.initiated = []
        self.parts = []
        self.completed = []
        self.aborted = []
        self.put_objects = []
        self.deleted = []
        self.head_keys = set()
        self.objects: dict[str, bytes] = {}

    def initiate_multipart_upload(self, request):
        self.initiated.append(request)
        return SimpleNamespace(upload_id="upload-1", status_code=200, request_id="req-init")

    def upload_part(self, request):
        self.parts.append(request)
        body = request.body
        if isinstance(body, (bytes, bytearray)):
            chunk = bytes(body)
        else:
            chunk = body.read()
        self.objects.setdefault(request.key, b"")
        # 不按偏移拼接，仅累计长度校验；真实数据在 complete 后由测试注入
        return SimpleNamespace(
            status_code=200,
            request_id=f"req-part-{request.part_number}",
            etag=f"etag-{request.part_number}",
            content_md5=None,
            hash_crc64=None,
        )

    def complete_multipart_upload(self, request):
        self.completed.append(request)
        return SimpleNamespace(
            status_code=200,
            request_id="req-complete",
            bucket=request.bucket,
            key=request.key,
            location=f"https://example/{request.key}",
            etag="etag-final",
        )

    def abort_multipart_upload(self, request):
        self.aborted.append(request)
        return SimpleNamespace(status_code=204)

    def put_object(self, request):
        body = request.body
        data = body.read() if hasattr(body, "read") else bytes(body)
        self.put_objects.append(request)
        self.objects[request.key] = data
        self.head_keys.add(request.key)
        return SimpleNamespace(status_code=200)

    def get_object(self, request):
        data = self.objects[request.key]
        return SimpleNamespace(body=SimpleNamespace(read=lambda: data, close=lambda: None))

    def delete_object(self, request):
        self.deleted.append(request.key)
        self.objects.pop(request.key, None)
        self.head_keys.discard(request.key)
        return SimpleNamespace(status_code=204)

    def head_object(self, request):
        if request.key not in self.objects and request.key not in self.head_keys:
            raise RuntimeError("NoSuchKey")
        data = self.objects.get(request.key, b"")
        return SimpleNamespace(content_length=len(data))

    def presign(self, request, **kwargs):
        return SimpleNamespace(url=f"https://signed.example/{request.key}")


@pytest.fixture
def oss_client(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "oss")
    monkeypatch.setenv("OSS_BUCKET", "hongyang02")
    monkeypatch.setenv("OSS_REGION", "cn-hangzhou")
    monkeypatch.setenv("OSS_ENDPOINT", "oss-cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "test-ak")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "test-sk")
    monkeypatch.setenv("OSS_PART_SIZE", str(100 * 1024))
    reset_storage_client()
    client = OssStorageClient()
    fake = _FakeOssSdkClient()
    client._client = fake
    yield client, fake
    reset_storage_client()


@pytest.mark.unit
def test_object_key_maps_logical_bucket_as_prefix(oss_client):
    client, _ = oss_client
    assert client._object_key("image", "a/b.png") == "image/a/b.png"
    assert client._object_key("hongyang02", "already.png") == "already.png"
    assert client._object_key("image", "image/a.png") == "image/a.png"


@pytest.mark.unit
def test_small_file_uses_put_object(oss_client):
    client, fake = oss_client
    data = b"hello-image"
    result = client.upload_file("image", "material-library/u1/x.png", data, content_type="image/png")
    assert result.bucket_name == "image"
    assert result.object_name == "material-library/u1/x.png"
    assert result.url.endswith("image/material-library/u1/x.png")
    assert len(fake.put_objects) == 1
    assert fake.put_objects[0].key == "image/material-library/u1/x.png"
    assert not fake.initiated


@pytest.mark.unit
def test_large_file_uses_multipart_upload(oss_client):
    client, fake = oss_client
    data = b"x" * (100 * 1024 + 10)
    result = client.upload_file("image", "big.bin", data)
    assert result.bucket_name == "image"
    assert fake.initiated
    assert len(fake.parts) == 2
    assert fake.completed
    assert fake.completed[0].complete_multipart_upload.parts[0].part_number == 1
    assert fake.completed[0].complete_multipart_upload.parts[1].part_number == 2
    assert not fake.aborted


@pytest.mark.unit
def test_multipart_failure_aborts_upload(oss_client, monkeypatch):
    client, fake = oss_client

    def boom(request):
        raise RuntimeError("upload part failed")

    fake.upload_part = boom
    with pytest.raises(StorageError, match="OSS"):
        client.upload_file("image", "fail.bin", b"y" * (100 * 1024 + 1))
    assert fake.aborted


@pytest.mark.unit
def test_get_minio_client_routes_image_buckets_to_oss(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "oss")
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "sk")
    reset_storage_client()
    from yuxi.storage.minio.client import get_minio_client
    from yuxi.storage.oss.client import RoutedStorageClient

    client = get_minio_client()
    assert isinstance(client, RoutedStorageClient)
    assert client.uses_oss("image")
    assert client.uses_oss("content-covers")
    assert not client.uses_oss("knowledgebases")
    assert not client.uses_oss("public")
    reset_storage_client()


@pytest.mark.unit
def test_routed_client_sends_only_image_buckets_to_oss(oss_client, monkeypatch):
    from yuxi.storage.oss.client import RoutedStorageClient

    oss, fake = oss_client

    class _FakeMinio:
        def __init__(self):
            self.uploads = []

        def upload_file(self, bucket_name, object_name, data, content_type=None):
            self.uploads.append((bucket_name, object_name, data, content_type))
            from yuxi.storage.minio.client import UploadResult

            return UploadResult(f"minio://{bucket_name}/{object_name}", bucket_name, object_name)

    minio = _FakeMinio()
    routed = RoutedStorageClient(minio, oss, frozenset({"image", "content-covers"}))
    routed.upload_file("image", "a.png", b"img")
    routed.upload_file("knowledgebases", "doc.pdf", b"pdf")
    assert fake.put_objects and fake.put_objects[0].key == "image/a.png"
    assert minio.uploads == [("knowledgebases", "doc.pdf", b"pdf", None)]
