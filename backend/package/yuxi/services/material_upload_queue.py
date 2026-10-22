"""Stage material uploads in Redis until the ARQ worker persists them to MinIO."""

from __future__ import annotations

import asyncio
import ctypes
import gc
import io
import os
from typing import Any

from PIL import Image, ImageOps

from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.services.run_queue_service import get_arq_pool, get_binary_redis_client
from yuxi.services.material_share_images import ensure_material_share_images
from yuxi.storage.minio import StorageError, get_minio_client
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.logging_config import logger

INGEST_PENDING = "pending"
INGEST_COMPLETED = "completed"
INGEST_EXPIRED = "expired"
INGEST_FAILED = "failed"
# 新上传带上这个标记后，原图写入成功前不出现在文件夹。旧素材没有该标记。
SHOW_AFTER_ORIGINAL = "show_after_original"
MATERIAL_UPLOAD_STAGING_TTL_SECONDS = int(os.getenv("MATERIAL_UPLOAD_STAGING_TTL_SECONDS", str(7 * 24 * 3600)))
MATERIAL_THUMBNAIL_SIZE = (720, 480)
# 大图同时解码会把唯一的入库进程内存打满。同一时刻只处理一张。
_material_upload_lock = asyncio.Lock()


def material_upload_redis_key(asset_id: str) -> str:
    return f"material-upload:{asset_id}"


def _material_thumb_redis_key(asset_id: str) -> str:
    return f"{material_upload_redis_key(asset_id)}:thumb"


def material_thumb_object_name(object_name: str) -> str:
    return f"{object_name}.thumb.webp"


def ingest_status_of(asset: Any) -> str:
    return str((getattr(asset, "metadata_json", None) or {}).get("ingest_status") or INGEST_COMPLETED)


def encode_material_thumbnail(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail(MATERIAL_THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="WEBP", quality=78, method=4)
        return output.getvalue()


async def stage_material_bytes(asset_id: str, data: bytes) -> None:
    redis = await get_binary_redis_client()
    await redis.set(material_upload_redis_key(asset_id), data, ex=MATERIAL_UPLOAD_STAGING_TTL_SECONDS)


async def stage_material_thumb(asset_id: str, data: bytes) -> None:
    redis = await get_binary_redis_client()
    await redis.set(_material_thumb_redis_key(asset_id), data, ex=MATERIAL_UPLOAD_STAGING_TTL_SECONDS)


async def read_staged_material_thumb(asset_id: str) -> bytes | None:
    redis = await get_binary_redis_client()
    return await redis.get(_material_thumb_redis_key(asset_id))


async def delete_material_display_cache(asset_id: str) -> None:
    redis = await get_binary_redis_client()
    await redis.delete(material_upload_redis_key(asset_id), _material_thumb_redis_key(asset_id))


async def enqueue_material_oss_upload(asset_id: str) -> None:
    queue = await get_arq_pool()
    queued = await queue.enqueue_job(
        "process_material_upload",
        asset_id,
        _job_id=f"material-upload:{asset_id}",
    )
    if queued is None:
        raise RuntimeError(f"material upload job was not accepted: {asset_id}")


async def read_material_bytes(asset: Any) -> bytes:
    metadata = getattr(asset, "metadata_json", None) or {}
    if ingest_status_of(asset) == INGEST_PENDING:
        redis = await get_binary_redis_client()
        staged = await redis.get(str(metadata.get("redis_key") or material_upload_redis_key(asset.id)))
        if staged is not None:
            return staged
    return await get_minio_client().adownload_file(asset.bucket_name, asset.object_name)


async def read_material_bytes_or_thumbnail(asset: Any) -> tuple[bytes, str]:
    """原图缺失时返回 OSS 上已有的缩略图，避免把预览请求报成原图过期。"""
    try:
        return await read_material_bytes(asset), str(getattr(asset, "content_type", None) or "image/webp")
    except StorageError as original_error:
        try:
            thumbnail = await get_minio_client().adownload_file(
                asset.bucket_name, material_thumb_object_name(asset.object_name)
            )
        except StorageError:
            raise original_error from None
        return thumbnail, "image/webp"


async def material_thumbnail_presigned_url(asset: Any, *, days: int = 1) -> str | None:
    """OSS 上已有缩略图时返回预签名 URL，供小程序直连下载，减轻 API 回源。"""
    storage = get_minio_client()
    thumbnail_name = material_thumb_object_name(asset.object_name)
    if await storage.astat_file(asset.bucket_name, thumbnail_name) is None:
        return None
    return await asyncio.to_thread(storage.get_presigned_url, asset.bucket_name, thumbnail_name, days)


async def material_file_presigned_url(asset: Any, *, days: int = 1) -> str | None:
    """原图已入库 MinIO 时返回预签名 URL，供外部应用直连下载。"""
    if ingest_status_of(asset) != INGEST_COMPLETED:
        return None
    storage = get_minio_client()
    if await storage.astat_file(asset.bucket_name, asset.object_name) is None:
        return None
    return await asyncio.to_thread(storage.get_presigned_url, asset.bucket_name, asset.object_name, days)


async def persist_material_thumbnail(asset: Any) -> bytes:
    storage = get_minio_client()
    thumbnail_name = material_thumb_object_name(asset.object_name)
    try:
        return await storage.adownload_file(asset.bucket_name, thumbnail_name)
    except StorageError:
        thumbnail = await read_staged_material_thumb(asset.id)
        if thumbnail is None:
            thumbnail = encode_material_thumbnail(await read_material_bytes(asset))
        await storage.aupload_file(asset.bucket_name, thumbnail_name, thumbnail, content_type="image/webp")
        return thumbnail


def _is_last_material_upload_try(ctx: Any) -> bool:
    from yuxi.services.run_worker import WorkerSettings

    try:
        job_try = int(ctx.get("job_try") or 1) if isinstance(ctx, dict) else 1
    except (TypeError, ValueError):
        job_try = 1
    return job_try >= max(1, int(getattr(WorkerSettings, "max_tries", 1)))


async def _mark_material_upload_result(asset_id: str, status: str) -> None:
    async with pg_manager.get_async_session_context() as db:
        repository = ContentCoverRepository(db)
        asset = await repository.get_asset(asset_id, for_update=True)
        if asset is None or ingest_status_of(asset) == INGEST_COMPLETED:
            return
        metadata = dict(asset.metadata_json or {})
        metadata["ingest_status"] = status
        metadata.pop("redis_key", None)
        await repository.update_asset_metadata(asset, metadata)
        await db.commit()
    await delete_material_display_cache(asset_id)


def _release_material_upload_memory() -> None:
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:
        return


async def process_material_upload(ctx: Any, asset_id: str) -> None:
    """Persist a staged material and thumbnail, then mark the asset as readable from MinIO."""
    async with _material_upload_lock:
        try:
            await _persist_staged_material(asset_id)
        except Exception:
            logger.exception("material upload persist failed: asset=%s", asset_id)
            if not _is_last_material_upload_try(ctx):
                raise
            await _mark_material_upload_result(asset_id, INGEST_FAILED)
        finally:
            _release_material_upload_memory()


async def _persist_staged_material(asset_id: str) -> None:
    async with pg_manager.get_async_session_context() as db:
        repository = ContentCoverRepository(db)
        asset = await repository.get_asset(asset_id, for_update=True)
        if asset is None or ingest_status_of(asset) == INGEST_COMPLETED:
            return

        metadata = dict(asset.metadata_json or {})
        redis_key = str(metadata.get("redis_key") or material_upload_redis_key(asset.id))
        redis = await get_binary_redis_client()
        data = await redis.get(redis_key)
        if data is None:
            storage = get_minio_client()
            existing = await storage.astat_file(asset.bucket_name, asset.object_name)
            metadata["ingest_status"] = INGEST_COMPLETED if existing else INGEST_EXPIRED
            metadata.pop("redis_key", None)
            await repository.update_asset_metadata(asset, metadata)
            await db.commit()
            if existing:
                logger.info("material upload already stored: asset=%s", asset_id)
            else:
                logger.warning("material upload expired before persist: asset=%s", asset_id)
            return
        thumbnail = await redis.get(_material_thumb_redis_key(asset.id))
        if thumbnail is None:
            thumbnail = encode_material_thumbnail(data)

        storage = get_minio_client()
        await storage.aupload_file(asset.bucket_name, asset.object_name, data, content_type=asset.content_type)
        await storage.aupload_file(
            asset.bucket_name,
            material_thumb_object_name(asset.object_name),
            thumbnail,
            content_type="image/webp",
        )
        await ensure_material_share_images(asset, data)

        metadata["ingest_status"] = INGEST_COMPLETED
        metadata.pop("redis_key", None)
        await repository.update_asset_metadata(asset, metadata)
        await db.commit()

    await delete_material_display_cache(asset_id)
    logger.info("material upload persisted: asset=%s", asset_id)
