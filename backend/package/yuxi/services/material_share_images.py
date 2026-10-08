"""Reusable, content-versioned share images and bounded Redis image caching."""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

from PIL import Image, ImageOps
from redis.exceptions import RedisError

from yuxi.services.run_queue_service import get_binary_redis_client
from yuxi.storage.minio import get_minio_client

logger = logging.getLogger(__name__)
SHARE_CARD_COVER_SIZE = (500, 400)
SHARE_CARD_COVER_MAX_BYTES = 128 * 1024
SHARE_DISPLAY_WEBP_MAX_WIDTH = 1440
SHARE_DISPLAY_WEBP_QUALITY = 80
SHARE_IMAGE_CACHE_TTL = 86400
SHARE_IMAGE_CACHE_MAX_BYTES = 512 * 1024


def make_share_card_cover(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image = ImageOps.fit(image, SHARE_CARD_COVER_SIZE, Image.Resampling.LANCZOS)
        candidate = b""
        for quality in (82, 75, 68, 60, 50):
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            candidate = output.getvalue()
            if len(candidate) <= SHARE_CARD_COVER_MAX_BYTES:
                return candidate
        return candidate


def make_share_display_webp(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source)
        if image.width > SHARE_DISPLAY_WEBP_MAX_WIDTH:
            height = round(image.height * SHARE_DISPLAY_WEBP_MAX_WIDTH / image.width)
            image = image.resize((SHARE_DISPLAY_WEBP_MAX_WIDTH, height), Image.Resampling.LANCZOS)
        has_alpha = image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info)
        output = io.BytesIO()
        image.convert("RGBA" if has_alpha else "RGB").save(
            output, format="WEBP", quality=SHARE_DISPLAY_WEBP_QUALITY, method=6
        )
        return output.getvalue()


async def ensure_material_share_images(asset: Any, data: bytes | None = None) -> tuple[str, str]:
    """Persist derivatives once per source hash; never reuse a previous source version."""
    storage = get_minio_client()
    base = f"{asset.object_name}.share-v1.{asset.sha256}"
    names = (f"{base}.display.webp", f"{base}.card.jpg")
    sizes = await asyncio.gather(*(storage.astat_file(asset.bucket_name, name) for name in names))
    if all(size is not None for size in sizes):
        return names
    if data is None:
        # The upload worker calls this helper too, so keep this import out of module initialization.
        from yuxi.services.material_upload_queue import read_material_bytes

        data = await read_material_bytes(asset)
    for name, size, encoder, content_type in zip(
        names, sizes, (make_share_display_webp, make_share_card_cover), ("image/webp", "image/jpeg"), strict=True
    ):
        if size is None:
            encoded = await asyncio.to_thread(encoder, data)
            await storage.aupload_file(asset.bucket_name, name, encoded, content_type=content_type)
    return names


async def read_share_image_cache(token: str, variant: str) -> bytes | None:
    try:
        redis = await get_binary_redis_client()
        return await redis.get(f"material-share:v1:{token}:{variant}")
    except (RedisError, RuntimeError):
        # Redis is an acceleration layer; the immutable object remains authoritative.
        logger.warning("Share image cache unavailable", exc_info=True)
        return None


async def write_share_image_cache(token: str, variant: str, data: bytes) -> None:
    if len(data) > SHARE_IMAGE_CACHE_MAX_BYTES:
        return
    try:
        redis = await get_binary_redis_client()
        await redis.set(f"material-share:v1:{token}:{variant}", data, ex=SHARE_IMAGE_CACHE_TTL)
    except (RedisError, RuntimeError):
        logger.warning("Share image cache write failed", exc_info=True)
