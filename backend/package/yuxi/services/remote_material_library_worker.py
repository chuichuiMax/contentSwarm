from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from yuxi.services.remote_material_library_service import sync_remote_material_library
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import RemoteMaterialSyncJob
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger


async def _set_job(job_id: str, **values: Any) -> RemoteMaterialSyncJob | None:
    async with pg_manager.get_async_session_context() as db:
        job = await db.scalar(select(RemoteMaterialSyncJob).where(RemoteMaterialSyncJob.id == job_id).with_for_update())
        if job is None:
            return None
        for key, value in values.items():
            setattr(job, key, value)
        job.updated_at = utc_now_naive()
        await db.commit()
        return job


async def process_remote_material_sync_job(ctx: dict[str, Any], job_id: str) -> None:
    del ctx
    async with pg_manager.get_async_session_context() as db:
        job = await db.scalar(select(RemoteMaterialSyncJob).where(RemoteMaterialSyncJob.id == job_id))
        if job is None:
            logger.warning("Remote material sync job not found: {}", job_id)
            return
        user = await db.scalar(select(User).where(User.id == job.requested_by, User.is_deleted == 0))
        if user is None:
            await _set_job(
                job_id,
                status="failed",
                phase="failed",
                error_code="REMOTE_MATERIAL_USER_NOT_FOUND",
                error_message="发起同步的管理员不存在",
                completed_at=utc_now_naive(),
            )
            return
        await _set_job(job_id, status="running", phase="starting", progress=1, started_at=utc_now_naive())

    async def report(**values: Any) -> None:
        await _set_job(job_id, **values)

    try:
        async with pg_manager.get_async_session_context() as db:
            result = await sync_remote_material_library(db, user, progress_callback=report)
        await _set_job(
            job_id,
            status="succeeded",
            phase="completed",
            progress=100,
            summary_json=result["summary"],
            completed_at=utc_now_naive(),
        )
    except HTTPException as exc:
        detail = exc.detail.get("error", {}) if isinstance(exc.detail, dict) else {}
        await _set_job(
            job_id,
            status="failed",
            phase="failed",
            error_code=str(detail.get("code") or "REMOTE_MATERIAL_SYNC_FAILED"),
            error_message=str(detail.get("message") or "远程素材同步失败"),
            completed_at=utc_now_naive(),
        )
        logger.warning("Remote material sync job failed: {} {}", job_id, detail)
    except Exception:
        await _set_job(
            job_id,
            status="failed",
            phase="failed",
            error_code="REMOTE_MATERIAL_WORKER_FAILED",
            error_message="远程素材同步执行失败，请稍后重试",
            completed_at=utc_now_naive(),
        )
        logger.exception("Remote material sync job failed: {}", job_id)
