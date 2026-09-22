"""Stage material uploads in Redis until the ARQ worker persists them to MinIO."""

from __future__ import annotations

import io
import os
from typing import Any

from PIL import Image, ImageOps

from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.services.run_queue_service import get_arq_pool, get_binary_redis_client
from yuxi.storage.minio import StorageError, get_minio_client
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.logging_config import logger

INGEST_PENDING = "pending"
INGEST_COMPLETED = "completed"
MATERIAL_UPLOAD_STAGING_TTL_SECONDS = int(os.getenv("MATERIAL_UPLOAD_STAGING_TTL_SECONDS", "3600"))
MATERIAL_THUMBNAIL_SIZE = (720, 480)


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


async def process_material_upload(_ctx: Any, asset_id: str) -> None:
    """Persist a staged material and thumbnail, then mark the asset as readable from MinIO."""
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
            raise RuntimeError(f"staged material bytes expired before upload: {asset.id}")
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

        metadata["ingest_status"] = INGEST_COMPLETED
        metadata.pop("redis_key", None)
        await repository.update_asset_metadata(asset, metadata)
        await db.commit()

    await delete_material_display_cache(asset_id)
    logger.info("material upload persisted: asset=%s", asset_id)
