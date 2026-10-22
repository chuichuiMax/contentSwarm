from __future__ import annotations

import asyncio
import mimetypes
import os
from typing import Any

import httpx

from yuxi.content_cover.ai_cover_prompt_catalog import (
    AI_COVER_OSS_LOGICAL_BUCKET,
    AI_COVER_OSS_PREFIX,
    AiCoverPromptSpec,
    api_preview_url_for_variant,
    spec_for_variant,
    spec_to_dict,
    specs_for_content_type,
)
from yuxi.utils import logger

_DEFAULT_AI_COVER_BUCKET = "hongyang02"


def _ai_cover_public_base() -> str:
    return (os.getenv("AI_COVER_OSS_PUBLIC_BASE_URL") or os.getenv("OSS_PUBLIC_BASE_URL") or "").strip().rstrip("/")


def _oss_object_prefixes() -> tuple[str, ...]:
    base = AI_COVER_OSS_PREFIX
    sub = (os.getenv("AI_COVER_OSS_SUBDIR") or "AI封面").strip().strip("/")
    prefixes = [base]
    if sub:
        prefixes.append(f"{base.rstrip('/')}/{sub}/")
    return tuple(dict.fromkeys(prefixes))


def _oss_object_name_candidates(spec: AiCoverPromptSpec) -> tuple[str, ...]:
    names: list[str] = []
    for prefix in _oss_object_prefixes():
        for leaf in spec.oss_preview_basenames():
            names.append(f"{prefix}{leaf}")
    return tuple(dict.fromkeys(names))


def _physical_object_key(object_name: str) -> str:
    object_name = object_name.lstrip("/")
    prefix = f"{AI_COVER_OSS_LOGICAL_BUCKET}/"
    if object_name.startswith(prefix):
        return object_name
    return f"{prefix}{object_name}"


def _oss_presigned_get_url(object_name: str, *, expires_sec: int = 3600) -> str | None:
    try:
        client = _build_ai_cover_oss_client()
    except ImportError as exc:
        logger.debug(f"AI 封面 OSS SDK 不可用: {exc}")
        return None
    if client is None:
        return None
    try:
        days = max(1, int(expires_sec // 86400) or 1)
        return client.get_presigned_url(AI_COVER_OSS_LOGICAL_BUCKET, object_name.lstrip("/"), days=days)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"AI 封面 presign 失败 {object_name}: {exc}")
        return None


def _signed_urls_for_object_name(object_name: str) -> tuple[str, ...]:
    url = _oss_presigned_get_url(object_name)
    return (url,) if url else ()


def _public_urls_for_object_name(object_name: str) -> tuple[str, ...]:
    base = _ai_cover_public_base()
    if not base:
        return ()
    object_name = object_name.lstrip("/")
    logical_prefix = f"{AI_COVER_OSS_LOGICAL_BUCKET}/"
    keys = [f"{logical_prefix}{object_name}"]
    if object_name.startswith(logical_prefix):
        keys.append(object_name)
    return tuple(f"{base}/{key.lstrip('/')}" for key in dict.fromkeys(keys))


async def _fetch_http(url: str) -> tuple[bytes, str] | None:
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url)
            if response.status_code != 200:
                logger.debug(f"AI 封面 HTTP {response.status_code}: {url[:160]}")
                return None
            guessed, _ = mimetypes.guess_type(url)
            content_type = response.headers.get("content-type") or guessed or "image/png"
            return response.content, content_type.split(";")[0].strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"AI 封面 HTTP 预览失败 {url}: {exc}")
        return None


def _build_ai_cover_oss_client():
    try:
        from yuxi.storage.minio.client import StorageError
        from yuxi.storage.oss.client import OssStorageClient
    except ImportError:
        return None

    try:
        client = OssStorageClient()
    except StorageError:
        return None
    client.bucket = (os.getenv("AI_COVER_OSS_BUCKET") or _DEFAULT_AI_COVER_BUCKET).strip()
    client.region = (os.getenv("AI_COVER_OSS_REGION") or "cn-hangzhou").strip()
    client.endpoint = (os.getenv("AI_COVER_OSS_ENDPOINT") or "oss-cn-hangzhou.aliyuncs.com").strip()
    public_base = (os.getenv("AI_COVER_OSS_PUBLIC_BASE_URL") or "").strip()
    if public_base:
        client.public_base_url = public_base.rstrip("/")
    client._client = None
    return client


async def _fetch_via_ai_cover_oss(object_name: str) -> tuple[bytes, str] | None:
    try:
        client = await asyncio.to_thread(_build_ai_cover_oss_client)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"AI 封面 OSS 客户端不可用: {exc}")
        return None
    if client is None:
        return None
    try:
        exists = await asyncio.to_thread(client.file_exists, AI_COVER_OSS_LOGICAL_BUCKET, object_name)
        if not exists:
            return None
        data = await client.adownload_file(AI_COVER_OSS_LOGICAL_BUCKET, object_name)
        guessed, _ = mimetypes.guess_type(object_name)
        return data, guessed or "image/png"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"AI 封面 OSS 下载失败 {object_name}: {exc}")
        return None


async def resolve_ai_cover_preview_bytes(
    variant: str,
    *,
    content_type_code: str = "",
    content_type_name: str = "",
) -> tuple[bytes, str] | None:
    spec = spec_for_variant(variant, content_type_code=content_type_code, content_type_name=content_type_name)
    if not spec:
        return None
    for object_name in _oss_object_name_candidates(spec):
        for url in _signed_urls_for_object_name(object_name):
            resolved = await _fetch_http(url)
            if resolved:
                return resolved
        resolved = await _fetch_via_ai_cover_oss(object_name)
        if resolved:
            return resolved
        for url in _public_urls_for_object_name(object_name):
            resolved = await _fetch_http(url)
            if resolved:
                return resolved
    return None


def _oss_prefix_label() -> str:
    bucket = (os.getenv("AI_COVER_OSS_BUCKET") or _DEFAULT_AI_COVER_BUCKET).strip()
    return f"oss://{bucket}/{AI_COVER_OSS_LOGICAL_BUCKET}/{AI_COVER_OSS_PREFIX}"


async def list_ai_cover_prompt_catalog(
    *,
    content_type_code: str = "",
    content_type_name: str = "",
) -> dict[str, Any]:
    specs = specs_for_content_type(content_type_code=content_type_code, content_type_name=content_type_name)
    prompts: list[dict[str, Any]] = []
    for spec in specs:
        preview = api_preview_url_for_variant(spec.variant)
        prompts.append(
            spec_to_dict(
                spec,
                preview_url=preview,
                content_type_code=content_type_code,
                content_type_name=content_type_name,
            )
        )
    return {"prompts": prompts, "oss_prefix": _oss_prefix_label()}
