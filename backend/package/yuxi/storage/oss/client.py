"""
阿里云 OSS 存储客户端（SDK V2）

将逻辑 bucket（如 image / public / content-covers）映射为物理 bucket 下的前缀，
大文件走分片上传（initiate → upload_part → complete）。
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
from datetime import timedelta
from io import BytesIO

import alibabacloud_oss_v2 as oss
from yuxi.storage.minio.client import StorageError, UploadResult
from yuxi.utils import logger

DEFAULT_OSS_BUCKET = "hongyang02"
DEFAULT_OSS_REGION = "cn-hangzhou"
DEFAULT_OSS_ENDPOINT = "oss-cn-hangzhou.aliyuncs.com"
# 非末片最小约 100KB；默认 5MB 更适合大图/视频
DEFAULT_PART_SIZE = 5 * 1024 * 1024
# 仅这些逻辑桶走 OSS：素材库 image、封面 content-covers（小程序上传复用素材库）
DEFAULT_OSS_LOGICAL_BUCKETS = ("image", "content-covers")


def is_oss_storage_enabled() -> bool:
    """是否启用 OSS（仅对素材库/封面等图片逻辑桶生效）。"""
    return os.getenv("STORAGE_BACKEND", "minio").strip().lower() == "oss"


def oss_logical_buckets() -> frozenset[str]:
    raw = (os.getenv("OSS_LOGICAL_BUCKETS") or "").strip()
    if raw:
        return frozenset(item.strip() for item in raw.split(",") if item.strip())
    cover = (os.getenv("CONTENT_COVER_BUCKET") or "content-covers").strip()
    return frozenset({*DEFAULT_OSS_LOGICAL_BUCKETS, cover})


class RoutedStorageClient:
    """按逻辑桶分流：素材库/封面 → OSS，其余 → MinIO。"""

    def __init__(self, minio_client, oss_client: OssStorageClient, buckets: frozenset[str] | None = None):
        self._minio = minio_client
        self._oss = oss_client
        self._oss_buckets = buckets if buckets is not None else oss_logical_buckets()
        self.PUBLIC_READ_BUCKETS = getattr(minio_client, "PUBLIC_READ_BUCKETS", {"public"})
        self.KB_BUCKETS = getattr(
            minio_client,
            "KB_BUCKETS",
            {"documents": "knowledgebases", "parsed": "knowledgebases", "images": "public"},
        )

    def uses_oss(self, bucket_name: str) -> bool:
        return (bucket_name or "").strip() in self._oss_buckets

    def _backend(self, bucket_name: str):
        return self._oss if self.uses_oss(bucket_name) else self._minio

    def ensure_bucket_exists(self, bucket_name: str) -> bool:
        return self._backend(bucket_name).ensure_bucket_exists(bucket_name)

    def upload_file(
        self, bucket_name: str, object_name: str, data: bytes, content_type: str | None = None
    ) -> UploadResult:
        return self._backend(bucket_name).upload_file(bucket_name, object_name, data, content_type)

    async def aupload_file(
        self,
        bucket_name: str,
        object_name: str,
        data: bytes,
        content_type: str | None = None,
    ) -> UploadResult:
        return await self._backend(bucket_name).aupload_file(bucket_name, object_name, data, content_type)

    def upload_file_from_path(self, bucket_name: str, object_name: str, file_path: str) -> UploadResult:
        return self._backend(bucket_name).upload_file_from_path(bucket_name, object_name, file_path)

    def download_file(self, bucket_name: str, object_name: str) -> bytes:
        return self._backend(bucket_name).download_file(bucket_name, object_name)

    async def adownload_file(self, bucket_name: str, object_name: str) -> bytes:
        return await self._backend(bucket_name).adownload_file(bucket_name, object_name)

    async def adownload_response(self, bucket_name: str, object_name: str):
        return await self._backend(bucket_name).adownload_response(bucket_name, object_name)

    def delete_file(self, bucket_name: str, object_name: str) -> bool:
        return self._backend(bucket_name).delete_file(bucket_name, object_name)

    async def adelete_file(self, bucket_name: str, object_name: str) -> bool:
        return await self._backend(bucket_name).adelete_file(bucket_name, object_name)

    async def adelete_objects_by_prefix(self, bucket_name: str, prefix: str) -> int:
        return await self._backend(bucket_name).adelete_objects_by_prefix(bucket_name, prefix)

    async def adelete_bucket(self, bucket_name: str) -> bool:
        return await self._backend(bucket_name).adelete_bucket(bucket_name)

    def file_exists(self, bucket_name: str, object_name: str) -> bool:
        return self._backend(bucket_name).file_exists(bucket_name, object_name)

    def stat_file(self, bucket_name: str, object_name: str) -> int | None:
        return self._backend(bucket_name).stat_file(bucket_name, object_name)

    async def astat_file(self, bucket_name: str, object_name: str) -> int | None:
        return await self._backend(bucket_name).astat_file(bucket_name, object_name)

    def get_presigned_url(self, bucket_name: str, object_name: str, days: int = 7) -> str:
        return self._backend(bucket_name).get_presigned_url(bucket_name, object_name, days)


class OssStorageClient:
    """与 MinIOClient 关键方法签名兼容的 OSS 适配层。"""

    PUBLIC_READ_BUCKETS = {"public"}
    KB_BUCKETS = {
        "documents": "knowledgebases",
        "parsed": "knowledgebases",
        "images": "public",
    }

    def __init__(self) -> None:
        self.bucket = (os.getenv("OSS_BUCKET") or DEFAULT_OSS_BUCKET).strip()
        self.region = (os.getenv("OSS_REGION") or DEFAULT_OSS_REGION).strip()
        self.endpoint = (os.getenv("OSS_ENDPOINT") or DEFAULT_OSS_ENDPOINT).strip()
        self.part_size = max(100 * 1024, int(os.getenv("OSS_PART_SIZE") or DEFAULT_PART_SIZE))
        self.public_base_url = (os.getenv("OSS_PUBLIC_BASE_URL") or "").strip().rstrip("/")
        self._client: oss.Client | None = None

        access_key_id = (os.getenv("OSS_ACCESS_KEY_ID") or "").strip()
        access_key_secret = (os.getenv("OSS_ACCESS_KEY_SECRET") or "").strip()
        if not access_key_id or not access_key_secret:
            raise StorageError("STORAGE_BACKEND=oss 时必须配置 OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET")

        # 同步写入 SDK 约定环境变量，供 EnvironmentVariableCredentialsProvider 使用
        os.environ["OSS_ACCESS_KEY_ID"] = access_key_id
        os.environ["OSS_ACCESS_KEY_SECRET"] = access_key_secret

    @property
    def client(self) -> oss.Client:
        if self._client is None:
            credentials_provider = oss.credentials.EnvironmentVariableCredentialsProvider()
            config = oss.config.load_default()
            config.credentials_provider = credentials_provider
            config.region = self.region
            config.endpoint = self.endpoint
            self._client = oss.Client(config)
        return self._client

    def _object_key(self, bucket_name: str, object_name: str) -> str:
        """逻辑 bucket → 物理 key：{logical_bucket}/{object_name}。"""
        object_name = object_name.lstrip("/")
        logical = (bucket_name or "").strip()
        if not logical or logical == self.bucket:
            return object_name
        if object_name.startswith(f"{logical}/"):
            return object_name
        return f"{logical}/{object_name}"

    def _public_url(self, key: str) -> str:
        if self.public_base_url:
            return f"{self.public_base_url}/{key}"
        return f"https://{self.bucket}.{self.endpoint}/{key}"

    def _guess_content_type(self, object_name: str) -> str:
        guessed_type, _ = mimetypes.guess_type(object_name)
        if guessed_type:
            return guessed_type
        ext = object_name.split(".")[-1].lower()
        content_types = {
            "md": "text/markdown",
            "webp": "image/webp",
            "bmp": "image/bmp",
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
        }
        return content_types.get(ext, "application/octet-stream")

    def ensure_bucket_exists(self, bucket_name: str) -> bool:
        """OSS 物理桶由运维预创建；逻辑桶仅作前缀，不做 make_bucket。"""
        _ = bucket_name
        return True

    def _multipart_upload(self, key: str, data: bytes, content_type: str) -> None:
        initiate_result = self.client.initiate_multipart_upload(
            oss.InitiateMultipartUploadRequest(
                bucket=self.bucket,
                key=key,
                content_type=content_type,
            )
        )
        upload_id = initiate_result.upload_id
        if not upload_id:
            raise StorageError(f"OSS 初始化分片上传失败: {key}")

        upload_parts: list = []
        try:
            offset = 0
            part_number = 1
            total = len(data)
            while offset < total:
                current_size = min(self.part_size, total - offset)
                part_data = data[offset : offset + current_size]
                part_result = self.client.upload_part(
                    oss.UploadPartRequest(
                        bucket=self.bucket,
                        key=key,
                        upload_id=upload_id,
                        part_number=part_number,
                        body=part_data,
                    )
                )
                upload_parts.append(oss.UploadPart(part_number=part_number, etag=part_result.etag))
                offset += current_size
                part_number += 1

            upload_parts.sort(key=lambda part: part.part_number)
            self.client.complete_multipart_upload(
                oss.CompleteMultipartUploadRequest(
                    bucket=self.bucket,
                    key=key,
                    upload_id=upload_id,
                    complete_multipart_upload=oss.CompleteMultipartUpload(parts=upload_parts),
                )
            )
        except Exception:
            try:
                self.client.abort_multipart_upload(
                    oss.AbortMultipartUploadRequest(
                        bucket=self.bucket,
                        key=key,
                        upload_id=upload_id,
                    )
                )
            except Exception as abort_exc:  # noqa: BLE001 — 尽力中止分片
                logger.warning(f"OSS abort multipart failed for {key}: {abort_exc}")
            raise

    def upload_file(
        self, bucket_name: str, object_name: str, data: bytes, content_type: str | None = None
    ) -> UploadResult:
        try:
            key = self._object_key(bucket_name, object_name)
            resolved_content_type = content_type or self._guess_content_type(object_name)
            # 小文件直接 Put；达到分片阈值后走分片（与官方示例一致）
            if len(data) < self.part_size:
                self.client.put_object(
                    oss.PutObjectRequest(
                        bucket=self.bucket,
                        key=key,
                        body=BytesIO(data),
                        content_type=resolved_content_type,
                    )
                )
            else:
                self._multipart_upload(key, data, resolved_content_type)

            url = self._public_url(key)
            logger.info(f"OSS uploaded {bucket_name}/{object_name} -> {key} ({len(data)} bytes)")
            return UploadResult(url, bucket_name, object_name)
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001 — SDK 异常统一包装
            error_msg = f"上传文件 '{object_name}' 到 OSS 失败: {exc}"
            logger.error(error_msg)
            raise StorageError(error_msg) from exc

    async def aupload_file(
        self,
        bucket_name: str,
        object_name: str,
        data: bytes,
        content_type: str | None = None,
    ) -> UploadResult:
        return await asyncio.to_thread(self.upload_file, bucket_name, object_name, data, content_type)

    def upload_file_from_path(self, bucket_name: str, object_name: str, file_path: str) -> UploadResult:
        try:
            with open(file_path, "rb") as file_data:
                data = file_data.read()
            return self.upload_file(bucket_name, object_name, data)
        except FileNotFoundError as exc:
            raise StorageError(f"文件 '{file_path}' 不存在") from exc
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"从路径上传文件失败: {exc}") from exc

    def download_file(self, bucket_name: str, object_name: str) -> bytes:
        try:
            key = self._object_key(bucket_name, object_name)
            result = self.client.get_object(oss.GetObjectRequest(bucket=self.bucket, key=key))
            body = result.body
            if body is None:
                raise StorageError(f"对象 '{object_name}' 在 OSS 中为空")
            data = body.read()
            if hasattr(body, "close"):
                body.close()
            logger.info(f"成功从 OSS 下载 '{key}' ({bucket_name})")
            return data
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if "NoSuchKey" in message or "404" in message:
                raise StorageError(f"对象 '{object_name}' 在存储桶 '{bucket_name}' 中不存在") from exc
            raise StorageError(f"下载文件失败: {exc}") from exc

    async def adownload_file(self, bucket_name: str, object_name: str) -> bytes:
        return await asyncio.to_thread(self.download_file, bucket_name, object_name)

    async def adownload_response(self, bucket_name: str, object_name: str):
        data = await self.adownload_file(bucket_name, object_name)
        return BytesIO(data)

    def delete_file(self, bucket_name: str, object_name: str) -> bool:
        try:
            key = self._object_key(bucket_name, object_name)
            self.client.delete_object(oss.DeleteObjectRequest(bucket=self.bucket, key=key))
            logger.info(f"成功从 OSS 删除 '{key}'")
            return True
        except Exception as exc:  # noqa: BLE001
            if "NoSuchKey" in str(exc):
                logger.warning(f"要删除的对象 '{object_name}' 不存在")
                return False
            raise StorageError(f"删除文件失败: {exc}") from exc

    async def adelete_file(self, bucket_name: str, object_name: str) -> bool:
        return await asyncio.to_thread(self.delete_file, bucket_name, object_name)

    async def adelete_objects_by_prefix(self, bucket_name: str, prefix: str) -> int:
        key_prefix = self._object_key(bucket_name, prefix)
        deleted_count = 0

        def _delete_objects() -> None:
            nonlocal deleted_count
            paginator = self.client.list_objects_v2_paginator()
            for page in paginator.iter_page(oss.ListObjectsV2Request(bucket=self.bucket, prefix=key_prefix)):
                for obj in page.contents or []:
                    try:
                        self.client.delete_object(oss.DeleteObjectRequest(bucket=self.bucket, key=obj.key))
                        deleted_count += 1
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"Failed to delete OSS object {obj.key}: {exc}")

        await asyncio.to_thread(_delete_objects)
        return deleted_count

    async def adelete_bucket(self, bucket_name: str) -> bool:
        """不删除物理 OSS 桶，仅清空对应逻辑前缀。"""
        await self.adelete_objects_by_prefix(bucket_name, "")
        return True

    def file_exists(self, bucket_name: str, object_name: str) -> bool:
        try:
            key = self._object_key(bucket_name, object_name)
            self.client.head_object(oss.HeadObjectRequest(bucket=self.bucket, key=key))
            return True
        except Exception as exc:  # noqa: BLE001
            if "NoSuchKey" in str(exc) or "404" in str(exc) or "Not Found" in str(exc):
                return False
            raise StorageError(f"检查文件存在性失败: {exc}") from exc

    def stat_file(self, bucket_name: str, object_name: str) -> int | None:
        try:
            key = self._object_key(bucket_name, object_name)
            result = self.client.head_object(oss.HeadObjectRequest(bucket=self.bucket, key=key))
            return int(result.content_length) if result.content_length is not None else None
        except Exception as exc:  # noqa: BLE001
            if "NoSuchKey" in str(exc) or "404" in str(exc) or "Not Found" in str(exc):
                return None
            raise StorageError(f"获取文件信息失败: {exc}") from exc

    async def astat_file(self, bucket_name: str, object_name: str) -> int | None:
        return await asyncio.to_thread(self.stat_file, bucket_name, object_name)

    def get_presigned_url(self, bucket_name: str, object_name: str, days: int = 7) -> str:
        key = self._object_key(bucket_name, object_name)
        # SDK V2 presign：优先使用客户端预签名；失败则回退公网直链
        try:
            result = self.client.presign(
                oss.GetObjectRequest(bucket=self.bucket, key=key),
                expires=timedelta(days=max(1, int(days))),
            )
            url = getattr(result, "url", None) or getattr(result, "signed_url", None)
            if url:
                return url
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"OSS presign failed for {key}: {exc}")
        return self._public_url(key)
