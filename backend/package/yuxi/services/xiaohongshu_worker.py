from __future__ import annotations

import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager

from fastapi import HTTPException

from yuxi.integrations.xiaohongshu import XiaohongshuRuntime, XiaohongshuRuntimeError
from yuxi.repositories.xiaohongshu_repository import XiaohongshuRepository
from yuxi.services.xiaohongshu_service import (
    _browser_session_id,
    _gateway_request,
    _sync_browser_state,
)
from yuxi.services.run_queue_service import get_redis_client
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger

DISTRIBUTION_TIMEOUT_SECONDS = max(60, int(os.getenv("XHS_DISTRIBUTION_TIMEOUT_SECONDS", "360")))
ACCOUNT_LOCK_SECONDS = max(
    DISTRIBUTION_TIMEOUT_SECONDS + 60,
    int(os.getenv("XHS_ACCOUNT_LOCK_SECONDS", "600")),
)
GATEWAY_DISTRIBUTION_TIMEOUT_SECONDS = max(45, DISTRIBUTION_TIMEOUT_SECONDS - 15)
LOGIN_STATE_TTL_SECONDS = int(os.getenv("XHS_LOGIN_STATE_TTL_SECONDS", "300"))
_BROWSER_SEMAPHORE = asyncio.Semaphore(int(os.getenv("XHS_WORKER_MAX_JOBS", "1")))
UNCERTAIN_PUBLISH_CODES = {
    "XHS_PREVIOUS_ATTEMPT_INTERRUPTED",
    "XHS_PUBLISH_UNCONFIRMED",
    "XHS_BROWSER_ERROR",
    "XHS_BROWSER_GATEWAY_ERROR",
    "XHS_BROWSER_GATEWAY_UNAVAILABLE",
    "XHS_GATEWAY_DISTRIBUTION_FAILED",
    "XHS_DISTRIBUTION_TIMEOUT",
}


def _login_key(session_id: str) -> str:
    return f"xhs:login:{session_id}"


def _account_lock_key(owner_uid: str, account_id: str) -> str:
    return f"xhs:account-lock:{owner_uid}:{account_id}"


def _browser_control_key(owner_uid: str, account_id: str) -> str:
    return f"xhs:browser-control:{owner_uid}:{account_id}"


async def _manual_control_active(owner_uid: str, account_id: str) -> bool:
    value = await (await get_redis_client()).get(_browser_control_key(owner_uid, account_id))
    return bool(value)


async def _acquire_account_lock(owner_uid: str, account_id: str):
    redis = await get_redis_client()
    lock = redis.lock(
        _account_lock_key(owner_uid, account_id),
        timeout=ACCOUNT_LOCK_SECONDS,
        blocking_timeout=1,
    )
    if not await lock.acquire():
        raise XiaohongshuRuntimeError("XHS_ACCOUNT_BUSY", "该账号正在执行其他任务，请稍后再试")
    return lock


async def _release_lock(lock) -> None:
    try:
        await lock.release()
    except Exception as exc:
        logger.warning(f"Failed to release Xiaohongshu account lock error_type={type(exc).__name__}")


@asynccontextmanager
async def _browser_account_slot(owner_uid: str, account_id: str):
    async with _BROWSER_SEMAPHORE:
        lock = await _acquire_account_lock(owner_uid, account_id)
        try:
            if await _manual_control_active(owner_uid, account_id):
                raise XiaohongshuRuntimeError(
                    "XHS_ACCOUNT_BUSY",
                    "该账号正在人工接管，请结束操作或等待接管租约到期后重试",
                )
            yield
        finally:
            await _release_lock(lock)


async def process_xiaohongshu_login(ctx, session_id: str) -> None:
    del ctx
    redis = await get_redis_client()
    runtime = XiaohongshuRuntime()
    async with pg_manager.get_async_session_context() as db:
        repo = XiaohongshuRepository(db)
        session = await repo.get_login_session_for_worker(session_id)
        if session is None or session.status != "pending":
            return
        account = await repo.get_account_for_worker(session.account_id, session.owner_uid)
        if account is None or account.deleted_at is not None:
            session.status = "failed"
            session.error_code = "XHS_ACCOUNT_NOT_FOUND"
            session.error_message = "小红书账号不存在"
            session.completed_at = utc_now_naive()
            await db.commit()
            return

        if os.getenv("XHS_BROWSER_GATEWAY_URL"):
            session.status = "failed"
            session.error_code = "XHS_REMOTE_LOGIN_REQUIRED"
            session.error_message = "登录入口已迁移到平台远程浏览器，请打开账号登录界面完成扫码"
            session.completed_at = utc_now_naive()
            account.login_status = "unbound"
            account.last_error_code = session.error_code
            account.last_error_message = session.error_message
            await redis.set(
                _login_key(session.id),
                json.dumps({"tip": session.error_message}, ensure_ascii=False),
                ex=LOGIN_STATE_TTL_SECONDS,
            )
            await db.commit()
            return

        try:

            async def publish_qr(qr_code: str) -> None:
                await redis.set(
                    _login_key(session.id),
                    json.dumps({"qr_code": qr_code, "tip": "请使用小红书 App 扫码登录"}, ensure_ascii=False),
                    ex=LOGIN_STATE_TTL_SECONDS,
                )

            async with _browser_account_slot(account.owner_uid, account.id):
                result = await asyncio.wait_for(
                    runtime.login(account.owner_uid, account.id, qr_callback=publish_qr),
                    timeout=180,
                )
            account.login_status = "logged_in"
            account.platform_nickname = str(result.get("nickname") or "") or None
            account.platform_account_id = str(result.get("account_id") or "") or None
            account.last_verified_at = utc_now_naive()
            account.last_error_code = None
            account.last_error_message = None
            session.status = "completed"
            session.completed_at = utc_now_naive()
            await redis.set(
                _login_key(session.id),
                json.dumps({"tip": "登录成功"}, ensure_ascii=False),
                ex=LOGIN_STATE_TTL_SECONDS,
            )
        except (XiaohongshuRuntimeError, TimeoutError) as exc:
            code = exc.code if isinstance(exc, XiaohongshuRuntimeError) else "XHS_LOGIN_EXPIRED"
            session.status = "expired" if code == "XHS_LOGIN_EXPIRED" else "failed"
            session.error_code = code
            session.error_message = str(exc) if isinstance(exc, XiaohongshuRuntimeError) else "登录任务超时"
            session.completed_at = utc_now_naive()
            account.login_status = "expired" if session.status == "expired" else "error"
            account.last_error_code = code
            account.last_error_message = session.error_message
            await redis.set(
                _login_key(session.id),
                json.dumps({"tip": session.error_message}, ensure_ascii=False),
                ex=LOGIN_STATE_TTL_SECONDS,
            )
        finally:
            await db.commit()


async def process_xiaohongshu_status_check(ctx, account_id: str, owner_uid: str) -> None:
    del ctx
    runtime = XiaohongshuRuntime()
    async with pg_manager.get_async_session_context() as db:
        repo = XiaohongshuRepository(db)
        account = await repo.get_account_for_worker(account_id, owner_uid)
        if account is None or account.deleted_at is not None:
            return
        try:
            async with _browser_account_slot(owner_uid, account_id):
                if os.getenv("XHS_BROWSER_GATEWAY_URL"):
                    _, result = await _open_gateway_browser(db, repo, account)
                else:
                    result = await asyncio.wait_for(runtime.check_status(owner_uid, account_id), timeout=90)
                    account.login_status = "logged_in" if result.get("logged_in") else "expired"
                    account.platform_nickname = str(result.get("nickname") or "") or account.platform_nickname
                    account.platform_account_id = str(result.get("account_id") or "") or account.platform_account_id
                    account.last_verified_at = utc_now_naive()
                    account.last_error_code = None if result.get("logged_in") else "XHS_LOGIN_REQUIRED"
                    account.last_error_message = None if result.get("logged_in") else "账号登录已失效，请重新扫码"
        except Exception as exc:
            code = exc.code if isinstance(exc, XiaohongshuRuntimeError) else "XHS_STATUS_CHECK_FAILED"
            account.login_status = "error"
            account.last_error_code = code
            account.last_error_message = (
                str(exc) if isinstance(exc, XiaohongshuRuntimeError) else "账号状态检查失败，请稍后重试"
            )
        finally:
            await db.commit()


async def process_xiaohongshu_profile_cleanup(ctx, account_id: str, owner_uid: str) -> None:
    del ctx
    lock = await _acquire_account_lock(owner_uid, account_id)
    try:
        XiaohongshuRuntime().remove_account_dir(owner_uid, account_id)
    finally:
        await _release_lock(lock)


async def _open_gateway_browser(db, repo: XiaohongshuRepository, account):
    session = await repo.get_browser_session(account.id, account.owner_uid, for_update=True)
    if session is None:
        session = await repo.create_browser_session(
            owner_uid=account.owner_uid,
            account_id=account.id,
            session_id=_browser_session_id(account.id),
        )
        await db.commit()
    try:
        opened = await _gateway_request(
            "POST",
            "/internal/sessions/open",
            json={"session_id": session.id, "owner_uid": account.owner_uid, "account_id": account.id},
        )
    except HTTPException as exc:
        detail = exc.detail.get("error", {}) if isinstance(exc.detail, dict) else {}
        code = str(detail.get("code") or "XHS_BROWSER_GATEWAY_ERROR")
        message = str(detail.get("message") or "远程浏览器启动失败")
        session.status = "error"
        session.last_error_code = code
        session.last_error_message = message
        await db.commit()
        raise XiaohongshuRuntimeError(code, message) from exc
    state = opened.json()
    await _sync_browser_state(db, account, session, state)
    await db.commit()
    return session, state


async def _distribute_via_browser_gateway(
    db, repo: XiaohongshuRepository, account, job, payload: dict
) -> dict[str, str]:
    session, _ = await _open_gateway_browser(db, repo, account)
    try:
        distributed = await _gateway_request(
            "POST",
            f"/internal/sessions/{session.id}/distribute",
            timeout=GATEWAY_DISTRIBUTION_TIMEOUT_SECONDS,
            json={
                "session_id": session.id,
                "owner_uid": account.owner_uid,
                "account_id": account.id,
                "job_id": job.id,
                "title": str(payload.get("title") or ""),
                "body": str(payload.get("body") or ""),
                "topics": [str(item) for item in payload.get("topics") or []],
                "mode": job.mode,
                "cover_bucket_name": (payload.get("cover") or {}).get("bucket_name"),
                "cover_object_name": (payload.get("cover") or {}).get("object_name"),
                "cover_sha256": (payload.get("cover") or {}).get("sha256"),
            },
        )
    except HTTPException as exc:
        detail = exc.detail.get("error", {}) if isinstance(exc.detail, dict) else {}
        code = str(detail.get("code") or "XHS_BROWSER_GATEWAY_ERROR")
        message = str(detail.get("message") or "远程浏览器操作失败")
        session.status = "error"
        session.last_error_code = code
        session.last_error_message = message
        await db.commit()
        raise XiaohongshuRuntimeError(code, message) from exc
    await db.commit()
    outcome = distributed.json()
    outcome["browser_session_id"] = session.id
    return outcome


async def process_xiaohongshu_distribution(ctx, job_id: str) -> None:
    del ctx
    runtime = XiaohongshuRuntime()
    async with pg_manager.get_async_session_context() as db:
        repo = XiaohongshuRepository(db)
        job = await repo.get_distribution_job_for_worker(job_id)
        if job is None or job.status not in {"queued", "running"}:
            return
        results = await repo.list_distribution_results(job.id)
        job.status = "running"
        job.started_at = job.started_at or utc_now_naive()
        await db.commit()

        succeeded = sum(result.status in {"draft_saved", "published"} for result in results)
        for result in results:
            if result.status in {"draft_saved", "published", "failed", "uncertain"}:
                continue
            if result.status == "running":
                result.error_code = "XHS_PREVIOUS_ATTEMPT_INTERRUPTED"
                result.uncertain = job.mode == "publish"
                result.status = "uncertain" if result.uncertain else "failed"
                result.error_message = (
                    "上次发布执行中断，无法确认平台结果，请先到小红书后台核对，系统不会自动重发"
                    if job.mode == "publish"
                    else "上次保存草稿时执行中断，无法确认平台结果，请先到小红书草稿箱核对"
                )
                result.completed_at = utc_now_naive()
                await db.commit()
                continue

            account = await repo.get_account_for_worker(result.account_id, job.owner_uid)
            if account is None or account.deleted_at is not None or not account.enabled:
                result.status = "failed"
                result.error_code = "XHS_ACCOUNT_NOT_READY"
                result.error_message = "账号不存在或已停用"
                result.completed_at = utc_now_naive()
                await db.commit()
                continue

            result.status = "running"
            result.started_at = utc_now_naive()
            await db.commit()
            try:
                payload = job.payload_snapshot or {}
                cover = payload.get("cover") or {}
                cover_bytes = None
                if cover.get("type") == "asset" and not os.getenv("XHS_BROWSER_GATEWAY_URL"):
                    from yuxi.storage.minio.client import get_minio_client

                    cover_bytes = await get_minio_client().adownload_file(
                        cover["bucket_name"],
                        cover["object_name"],
                    )
                    if hashlib.sha256(cover_bytes).hexdigest() != cover.get("sha256"):
                        raise XiaohongshuRuntimeError(
                            "XHS_COVER_INTEGRITY_ERROR",
                            "当前封面文件完整性校验失败，请重新选择封面",
                        )
                async with _browser_account_slot(job.owner_uid, account.id):
                    if os.getenv("XHS_BROWSER_GATEWAY_URL"):
                        outcome = await asyncio.wait_for(
                            _distribute_via_browser_gateway(db, repo, account, job, payload),
                            timeout=DISTRIBUTION_TIMEOUT_SECONDS,
                        )
                    else:
                        outcome = await asyncio.wait_for(
                            runtime.distribute(
                                job.owner_uid,
                                account.id,
                                job.id,
                                title=str(payload.get("title") or ""),
                                body=str(payload.get("body") or ""),
                                topics=[str(item) for item in payload.get("topics") or []],
                                mode=job.mode,
                                cover_bytes=cover_bytes,
                            ),
                            timeout=DISTRIBUTION_TIMEOUT_SECONDS,
                        )
                result.status = "draft_saved" if job.mode == "draft" else "published"
                result.note_url = outcome.get("note_url") or None
                result.screenshot_path = outcome.get("screenshot_path") or None
                result.browser_session_id = outcome.get("browser_session_id") or None
                result.evidence_type = "platform_confirmation"
                result.uncertain = False
                result.error_code = None
                result.error_message = None
                account.login_status = "logged_in"
                account.last_verified_at = utc_now_naive()
                succeeded += 1
            except Exception as exc:
                if isinstance(exc, XiaohongshuRuntimeError):
                    code = exc.code
                elif isinstance(exc, TimeoutError):
                    code = "XHS_DISTRIBUTION_TIMEOUT"
                else:
                    code = "XHS_BROWSER_ERROR"
                result.uncertain = job.mode == "publish" and code in UNCERTAIN_PUBLISH_CODES
                result.status = "uncertain" if result.uncertain else "failed"
                result.error_code = code
                result.error_message = (
                    str(exc)
                    if isinstance(exc, XiaohongshuRuntimeError)
                    else (
                        "分发任务超时，请稍后重试"
                        if isinstance(exc, TimeoutError)
                        else "分发任务执行异常，请联系管理员核对"
                    )
                )
                browser_session = await repo.get_browser_session(account.id, account.owner_uid)
                result.browser_session_id = browser_session.id if browser_session is not None else None
                evidence_path = runtime.account_dir(job.owner_uid, account.id) / "jobs" / job.id / "result.png"
                if evidence_path.is_file():
                    result.screenshot_path = str(evidence_path)
                    result.evidence_type = "failure_screenshot"
                if code == "XHS_LOGIN_REQUIRED":
                    account.login_status = "expired"
                    account.last_error_code = code
                    account.last_error_message = result.error_message
            finally:
                result.completed_at = utc_now_naive()
                await db.commit()

        job.completed_at = utc_now_naive()
        uncertain = sum(result.status == "uncertain" for result in results)
        if uncertain:
            job.status = "uncertain"
            job.error_code = "XHS_PUBLISH_RESULT_UNCERTAIN"
            job.error_message = "存在无法确认发布结果的账号，请人工核对后再决定后续操作"
        elif succeeded == len(results):
            job.status = "completed"
        elif succeeded:
            job.status = "partial_failed"
            job.error_code = "XHS_PARTIAL_FAILURE"
            job.error_message = "部分账号分发失败"
        else:
            job.status = "failed"
            job.error_code = "XHS_DISTRIBUTION_FAILED"
            job.error_message = "所有账号分发失败"
        await db.commit()
