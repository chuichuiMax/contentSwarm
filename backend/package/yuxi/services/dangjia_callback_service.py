"""当家内容生成结果回调。"""

from __future__ import annotations

import os
from typing import Literal

import httpx

# 先加载接入契约，保持与现有当家服务一致的 content 包初始化顺序。
from yuxi.services.dangjia_service import EXTERNAL_SOURCE
from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.repositories.content_repository import ContentRepository
from yuxi.storage.minio.client import get_minio_client
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.logging_config import logger

TerminalStatus = Literal["completed", "failed", "cancelled"]

GENERATION_FAILED_REASON = "内容生成失败，请重新发起"
CANCELLED_REASON = "内容生成任务已取消，请重新发起"
REVIEW_BLOCKED_REASON = "内容审核未通过，请调整后重新发起"
CALLBACK_PATH = "/v1/callback/ai/gen/result/notify"
CALLBACK_TIMEOUT = httpx.Timeout(15.0, connect=3.0)

_COVER_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def build_success_payload(
    *,
    serial_no: str,
    title: str,
    body: str,
    keywords: list[str],
    image_url: str,
) -> dict:
    return {
        "serialNo": serial_no,
        "generationStatus": 30,
        "title": title,
        "body": body,
        "keywords": keywords,
        "images": [image_url],
    }


def build_failure_payload(*, serial_no: str, terminal_status: TerminalStatus, task_status: str) -> dict:
    if terminal_status == "cancelled":
        reason = CANCELLED_REASON
    elif task_status == "review_blocked":
        reason = REVIEW_BLOCKED_REASON
    else:
        reason = GENERATION_FAILED_REASON
    return {
        "serialNo": serial_no,
        "generationStatus": 40,
        "failReason": reason,
    }


def build_dangjia_media_url(*, base_url: str, asset_id: str, content_type: str) -> str:
    extension = _COVER_EXTENSIONS.get(content_type.lower())
    if extension is None:
        raise RuntimeError("Dangjia callback cover content type is unsupported")
    return f"{base_url.rstrip('/')}/{asset_id}/1.{extension}"


async def read_dangjia_cover_media(asset_id: str) -> tuple[bytes, str, str] | None:
    """读取当家任务当前绑定的最终封面，供永久业务 URL 公开访问。"""

    async with pg_manager.get_async_session_context() as db:
        cover_asset = await ContentCoverRepository(db).get_asset(asset_id)
        if cover_asset is None or cover_asset.role != "output" or not cover_asset.content_task_id:
            return None
        content_repo = ContentRepository(db)
        task = await content_repo.get_task(cover_asset.content_task_id)
        artifact = await content_repo.get_artifact_for_task(cover_asset.content_task_id)
        form_values = (task.brief_json or {}).get("form_values") if task is not None else None
        if (
            task is None
            or artifact is None
            or (form_values or {}).get("external_source") != EXTERNAL_SOURCE
            or artifact.cover_asset_id != cover_asset.id
        ):
            return None
        content_type = cover_asset.content_type.lower()
        extension = _COVER_EXTENSIONS.get(content_type)
        if extension is None:
            return None
        bucket_name = cover_asset.bucket_name
        object_name = cover_asset.object_name

    data = await get_minio_client().adownload_file(bucket_name, object_name)
    return data, content_type, f"1.{extension}"


async def notify_dangjia_content_result(
    *,
    task_id: str,
    run_id: str,
    terminal_status: TerminalStatus,
) -> None:
    """向当家发送一次终态通知；任何回调故障都不得影响本地生成结果。"""

    serial_no = ""
    generation_status = 40
    http_status: int | None = None
    failure_reason = "callback_failed"
    try:
        async with pg_manager.get_async_session_context() as db:
            content_repo = ContentRepository(db)
            task = await content_repo.get_task(task_id)
            if task is None:
                failure_reason = "task_not_found"
                raise RuntimeError("Dangjia callback task is missing")
            form_values = (task.brief_json or {}).get("form_values") or {}
            if form_values.get("external_source") != EXTERNAL_SOURCE:
                return
            base_url = os.getenv("DANGJIA_CALLBACK_BASE_URL", "").strip()
            api_key = os.getenv("DANGJIA_CALLBACK_API_KEY", "").strip()
            if not base_url or not api_key:
                failure_reason = "callback_config_missing"
                raise RuntimeError("Dangjia callback configuration is missing")
            serial_no = str(form_values.get("external_serial_no") or "")
            if not serial_no:
                failure_reason = "serial_no_missing"
                raise RuntimeError("Dangjia callback serial number is missing")

            is_success = terminal_status == "completed" and task.status != "review_blocked"
            if is_success:
                if task.status != "reviewed":
                    failure_reason = "task_not_reviewed"
                    raise RuntimeError("Dangjia callback task is not reviewed")
                artifact = await content_repo.get_artifact_for_task(task_id)
                if artifact is None or not artifact.cover_asset_id:
                    failure_reason = "artifact_or_cover_missing"
                    raise RuntimeError("Dangjia callback artifact or cover is missing")
                cover_asset = await ContentCoverRepository(db).get_asset(artifact.cover_asset_id)
                if cover_asset is None:
                    failure_reason = "cover_asset_missing"
                    raise RuntimeError("Dangjia callback cover asset is missing")
                success_values = {
                    "title": artifact.title,
                    "body": artifact.body,
                    "keywords": [str(item).strip() for item in (artifact.topics or []) if str(item).strip()],
                    "asset_id": cover_asset.id,
                    "content_type": cover_asset.content_type,
                }
            else:
                success_values = None

        if success_values is None:
            payload = build_failure_payload(
                serial_no=serial_no,
                terminal_status=terminal_status,
                task_status=task.status,
            )
        else:
            media_base_url = os.getenv("DANGJIA_MEDIA_PUBLIC_BASE_URL", "").strip()
            if not media_base_url:
                failure_reason = "media_base_url_missing"
                raise RuntimeError("Dangjia media public base URL is missing")
            failure_reason = "cover_url_failed"
            image_url = build_dangjia_media_url(
                base_url=media_base_url,
                asset_id=success_values["asset_id"],
                content_type=success_values["content_type"],
            )
            payload = build_success_payload(
                serial_no=serial_no,
                title=success_values["title"],
                body=success_values["body"],
                keywords=success_values["keywords"],
                image_url=image_url,
            )
            generation_status = 30

        failure_reason = "http_request_failed"
        async with httpx.AsyncClient(timeout=CALLBACK_TIMEOUT) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}{CALLBACK_PATH}",
                headers={
                    "Content-Type": "application/json; charset=UTF-8",
                    "x-api-key": api_key,
                },
                json=payload,
            )
        http_status = response.status_code
        if response.status_code != 200:
            failure_reason = "unexpected_http_status"
            raise RuntimeError("Dangjia callback returned a non-200 response")
        failure_reason = "invalid_ack"
        ack = response.json()
        if not isinstance(ack, dict) or ack.get("code") != "200":
            raise RuntimeError("Dangjia callback returned an invalid acknowledgement")
        logger.info(
            "Dangjia callback succeeded task_id={} run_id={} serial_no={} generation_status={} http_status={}",
            task_id,
            run_id,
            serial_no,
            generation_status,
            http_status,
        )
    except Exception as exc:
        logger.error(
            "Dangjia callback failed task_id={} run_id={} serial_no={} generation_status={} "
            "http_status={} reason={} error_type={}",
            task_id,
            run_id,
            serial_no,
            generation_status,
            http_status,
            failure_reason,
            type(exc).__name__,
        )
