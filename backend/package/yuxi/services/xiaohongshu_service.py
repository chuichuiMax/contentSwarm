from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content.schemas import (
    XiaohongshuAccountCreate,
    XiaohongshuAccountUpdate,
    XiaohongshuDistributionCreate,
)
from yuxi.integrations.xiaohongshu import XiaohongshuRuntime
from yuxi.repositories.content_repository import ContentRepository
from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.repositories.xiaohongshu_repository import XiaohongshuRepository
from yuxi.services.run_queue_service import get_arq_pool, get_redis_client
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentDistributionJob
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger

LOGIN_SESSION_SECONDS = 180
XHS_BROWSER_GATEWAY_URL = os.getenv("XHS_BROWSER_GATEWAY_URL", "http://xhs-browser-gateway:5051").rstrip("/")
XHS_GATEWAY_TOKEN = os.getenv("XHS_GATEWAY_TOKEN", "")
XHS_BROWSER_OPERATION_LOCK_SECONDS = max(60, int(os.getenv("XHS_BROWSER_OPERATION_LOCK_SECONDS", "180")))
XHS_BROWSER_OPERATION_BLOCK_SECONDS = max(1, int(os.getenv("XHS_BROWSER_OPERATION_BLOCK_SECONDS", "3")))
XHS_GATEWAY_IDLE_SECONDS = max(60, int(os.getenv("XHS_GATEWAY_IDLE_SECONDS", "900")))
XHS_BROWSER_CONTROL_LEASE_SECONDS = max(30, int(os.getenv("XHS_BROWSER_CONTROL_LEASE_SECONDS", "120")))


def _error(status_code: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": code,
                "message": message,
                "retryable": bool(extra.pop("retryable", False)),
                **extra,
            }
        },
    )


def _owner_uid(user: User) -> str:
    return str(user.uid)


def _login_state_key(session_id: str) -> str:
    return f"xhs:login:{session_id}"


def _serialize_job(job: ContentDistributionJob, results) -> dict[str, Any]:
    payload = job.to_dict()
    payload["results"] = [item.to_dict() for item in results]
    return payload


async def _gateway_request(method: str, path: str, **kwargs) -> httpx.Response:
    headers = dict(kwargs.pop("headers", {}) or {})
    if XHS_GATEWAY_TOKEN:
        headers["Authorization"] = f"Bearer {XHS_GATEWAY_TOKEN}"
    try:
        async with httpx.AsyncClient(base_url=XHS_BROWSER_GATEWAY_URL, timeout=45.0) as client:
            response = await client.request(method, path, headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        raise _error(
            503,
            "XHS_BROWSER_GATEWAY_UNAVAILABLE",
            "小红书远程浏览器服务暂不可用",
            retryable=True,
        ) from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail") if response.content else None
        except ValueError:
            detail = None
        if isinstance(detail, dict):
            code = str(detail.get("code") or detail.get("error", {}).get("code") or "XHS_GATEWAY_ERROR")
            message = str(detail.get("message") or detail.get("error", {}).get("message") or "远程浏览器操作失败")
        else:
            code = "XHS_GATEWAY_ERROR"
            message = "远程浏览器操作失败"
        status_code = response.status_code if response.status_code in {404, 409, 422, 429} else 502
        raise _error(
            status_code,
            code,
            message,
            retryable=response.status_code == 429 or response.status_code >= 500,
        )
    return response


def _browser_operation_lock_key(owner_uid: str, account_id: str) -> str:
    return f"xhs:account-lock:{owner_uid}:{account_id}"


def _browser_control_key(owner_uid: str, account_id: str) -> str:
    return f"xhs:browser-control:{owner_uid}:{account_id}"


async def _has_browser_control(
    session_id: str,
    owner_uid: str,
    account_id: str,
    *,
    refresh: bool = False,
) -> bool:
    try:
        redis = await get_redis_client()
        key = _browser_control_key(owner_uid, account_id)
        value = await redis.get(key)
        if isinstance(value, bytes):
            value = value.decode()
        claimed = value == session_id
        if claimed and refresh:
            await redis.expire(key, XHS_BROWSER_CONTROL_LEASE_SECONDS)
        return claimed
    except Exception as exc:
        raise _error(
            503,
            "XHS_CONTROL_LEASE_UNAVAILABLE",
            "人工接管状态暂不可用",
            retryable=True,
        ) from exc


@asynccontextmanager
async def _browser_operation_slot(owner_uid: str, account_id: str):
    try:
        redis = await get_redis_client()
        lock = redis.lock(
            _browser_operation_lock_key(owner_uid, account_id),
            timeout=XHS_BROWSER_OPERATION_LOCK_SECONDS,
            blocking_timeout=XHS_BROWSER_OPERATION_BLOCK_SECONDS,
        )
        acquired = await lock.acquire()
    except Exception as exc:
        raise _error(
            503,
            "XHS_ACCOUNT_LOCK_UNAVAILABLE",
            "账号操作锁暂不可用",
            retryable=True,
        ) from exc
    if not acquired:
        raise _error(409, "XHS_ACCOUNT_BUSY", "该账号正在执行其他操作，请稍后重试", retryable=True)
    try:
        yield
    finally:
        try:
            await lock.release()
        except Exception as exc:
            logger.warning(
                f"Failed to release Xiaohongshu browser operation lock "
                f"account={account_id} error_type={type(exc).__name__}"
            )


async def _open_gateway_session(
    session_id: str,
    owner_uid: str,
    account_id: str,
    *,
    target: str = "home",
) -> dict[str, Any]:
    response = await _gateway_request(
        "POST",
        "/internal/sessions/open",
        json={
            "session_id": session_id,
            "owner_uid": owner_uid,
            "account_id": account_id,
            "target": target,
        },
    )
    return response.json()


async def _current_browser_session(
    repo: XiaohongshuRepository,
    owner_uid: str,
    account_id: str,
    expected_session_id: str,
):
    account = await repo.get_account(account_id, owner_uid)
    if account is None:
        raise _error(404, "XHS_ACCOUNT_NOT_FOUND", "小红书账号不存在")
    if not account.enabled:
        raise _error(409, "XHS_ACCOUNT_DISABLED", "请先启用该账号")
    session = await repo.get_browser_session(account_id, owner_uid)
    if session is None or session.id != expected_session_id:
        raise _error(404, "XHS_BROWSER_SESSION_NOT_FOUND", "远程浏览器会话不存在")
    return account, session


async def _recover_gateway_session(
    repo: XiaohongshuRepository,
    session_id: str,
    owner_uid: str,
    account_id: str,
    *,
    target: str = "drafts",
):
    async with _browser_operation_slot(owner_uid, account_id):
        account, session = await _current_browser_session(repo, owner_uid, account_id, session_id)
        state = await _open_gateway_session(session.id, owner_uid, account_id, target=target)
    return account, session, state


def _browser_session_id(account_id: str) -> str:
    return f"xhbs_{account_id}_{uuid.uuid4().hex[:24]}"


async def _sync_browser_state(db: AsyncSession, account, session, state: dict[str, Any]) -> None:
    now = utc_now_naive()
    logged_in = bool(state.get("logged_in"))
    session.status = "ready" if logged_in else "login_required"
    session.last_heartbeat_at = now
    session.last_used_at = now
    session.expires_at = now + timedelta(seconds=XHS_GATEWAY_IDLE_SECONDS)
    session.last_error_code = None
    session.last_error_message = None
    if logged_in:
        session.started_at = session.started_at or now
        account.login_status = "logged_in"
        account.platform_nickname = str(state.get("nickname") or "") or account.platform_nickname
        account.platform_account_id = str(state.get("platform_account_id") or "") or account.platform_account_id
        account.last_verified_at = now
        account.last_error_code = None
        account.last_error_message = None
    elif account.login_status == "logged_in":
        account.login_status = "expired"
        account.last_error_code = "XHS_LOGIN_REQUIRED"
        account.last_error_message = "账号需要重新登录"


async def open_browser_session(
    db: AsyncSession,
    user: User,
    account_id: str,
    *,
    target: str = "home",
) -> dict[str, Any]:
    owner_uid = _owner_uid(user)
    repo = XiaohongshuRepository(db)
    account = await repo.get_account(account_id, owner_uid, for_update=True)
    if account is None:
        raise _error(404, "XHS_ACCOUNT_NOT_FOUND", "小红书账号不存在")
    if not account.enabled:
        raise _error(409, "XHS_ACCOUNT_DISABLED", "请先启用该账号")
    session = await repo.get_browser_session(account_id, owner_uid, for_update=True)
    if session is None:
        session = await repo.create_browser_session(
            owner_uid=owner_uid,
            account_id=account_id,
            session_id=_browser_session_id(account_id),
        )
    await db.commit()
    try:
        async with _browser_operation_slot(owner_uid, account_id):
            account, session = await _current_browser_session(repo, owner_uid, account_id, session.id)
            state = await _open_gateway_session(session.id, owner_uid, account_id, target=target)
    except HTTPException as exc:
        session.status = "error"
        session.last_error_code = (
            exc.detail.get("error", {}).get("code") if isinstance(exc.detail, dict) else "XHS_GATEWAY_ERROR"
        )
        session.last_error_message = str(exc.detail)
        await db.commit()
        raise
    await _sync_browser_state(db, account, session, state)
    session.worker_id = os.getenv("HOSTNAME") or None
    session.browser_version = os.getenv("XHS_GATEWAY_BROWSER_VERSION") or None
    await db.commit()
    return {"session": session.to_dict(), "browser": state}


async def get_browser_session(db: AsyncSession, user: User, account_id: str) -> dict[str, Any]:
    owner_uid = _owner_uid(user)
    repo = XiaohongshuRepository(db)
    account = await repo.get_account(account_id, owner_uid)
    session = await repo.get_browser_session(account_id, owner_uid)
    if account is None:
        raise _error(404, "XHS_ACCOUNT_NOT_FOUND", "小红书账号不存在")
    if session is None:
        raise _error(404, "XHS_BROWSER_SESSION_NOT_FOUND", "远程浏览器会话不存在")
    try:
        response = await _gateway_request(
            "GET",
            f"/internal/sessions/{session.id}/status",
            params={"owner_uid": owner_uid, "account_id": account_id},
        )
        state = response.json()
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        account, session, state = await _recover_gateway_session(
            repo,
            session.id,
            owner_uid,
            account_id,
        )
    await _sync_browser_state(db, account, session, state)
    await db.commit()
    return {"session": session.to_dict(), "browser": state}


async def heartbeat_browser_session(db: AsyncSession, user: User, account_id: str) -> dict[str, Any]:
    result = await get_browser_session(db, user, account_id)
    result["control_claimed"] = await _has_browser_control(
        result["session"]["id"],
        _owner_uid(user),
        account_id,
        refresh=True,
    )
    return result


async def claim_browser_session(db: AsyncSession, user: User, account_id: str) -> dict[str, Any]:
    owner_uid = _owner_uid(user)
    repo = XiaohongshuRepository(db)
    account = await repo.get_account(account_id, owner_uid)
    session = await repo.get_browser_session(account_id, owner_uid)
    if account is None:
        raise _error(404, "XHS_ACCOUNT_NOT_FOUND", "小红书账号不存在")
    if session is None or session.status not in {"ready", "login_required"}:
        raise _error(409, "XHS_BROWSER_SESSION_NOT_READY", "请先打开远程浏览器")
    async with _browser_operation_slot(owner_uid, account_id):
        _, session = await _current_browser_session(repo, owner_uid, account_id, session.id)
        if session.status not in {"ready", "login_required"}:
            raise _error(409, "XHS_BROWSER_SESSION_NOT_READY", "请先打开远程浏览器")
        try:
            redis = await get_redis_client()
            await redis.set(
                _browser_control_key(owner_uid, account_id),
                session.id,
                ex=XHS_BROWSER_CONTROL_LEASE_SECONDS,
            )
        except Exception as exc:
            raise _error(
                503,
                "XHS_CONTROL_LEASE_UNAVAILABLE",
                "人工接管状态暂不可用",
                retryable=True,
            ) from exc
    return {
        "claimed": True,
        "session_id": session.id,
        "expires_in": XHS_BROWSER_CONTROL_LEASE_SECONDS,
    }


async def browser_session_action(
    db: AsyncSession, user: User, account_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    owner_uid = _owner_uid(user)
    repo = XiaohongshuRepository(db)
    account = await repo.get_account(account_id, owner_uid)
    session = await repo.get_browser_session(account_id, owner_uid)
    if account is None:
        raise _error(404, "XHS_ACCOUNT_NOT_FOUND", "小红书账号不存在")
    if session is None:
        raise _error(404, "XHS_BROWSER_SESSION_NOT_FOUND", "请先打开远程浏览器")
    async with _browser_operation_slot(owner_uid, account_id):
        account, session = await _current_browser_session(repo, owner_uid, account_id, session.id)
        if not await _has_browser_control(session.id, owner_uid, account_id, refresh=True):
            raise _error(409, "XHS_BROWSER_CONTROL_REQUIRED", "请先启用人工接管")
        try:
            response = await _gateway_request(
                "POST",
                f"/internal/sessions/{session.id}/action",
                json={**payload, "session_id": session.id, "owner_uid": owner_uid, "account_id": account_id},
            )
            state = response.json()
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            await _open_gateway_session(session.id, owner_uid, account_id, target="drafts")
            raise _error(
                409,
                "XHS_BROWSER_SESSION_RECOVERED",
                "远程浏览器会话已恢复，请根据新画面重新操作",
                retryable=True,
            ) from exc
    await _sync_browser_state(db, account, session, state)
    await db.commit()
    return {"session": session.to_dict(), "browser": state}


async def get_browser_screenshot(db: AsyncSession, user: User, account_id: str) -> bytes:
    owner_uid = _owner_uid(user)
    repo = XiaohongshuRepository(db)
    account = await repo.get_account(account_id, owner_uid)
    if account is None:
        raise _error(404, "XHS_ACCOUNT_NOT_FOUND", "小红书账号不存在")
    session = await repo.get_browser_session(account_id, owner_uid)
    if session is None:
        raise _error(404, "XHS_BROWSER_SESSION_NOT_FOUND", "请先打开远程浏览器")
    try:
        response = await _gateway_request(
            "GET",
            f"/internal/sessions/{session.id}/screenshot",
            params={"owner_uid": owner_uid, "account_id": account_id},
        )
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        _, session, _ = await _recover_gateway_session(
            repo,
            session.id,
            owner_uid,
            account_id,
        )
        response = await _gateway_request(
            "GET",
            f"/internal/sessions/{session.id}/screenshot",
            params={"owner_uid": owner_uid, "account_id": account_id},
        )
    session.last_used_at = utc_now_naive()
    await db.commit()
    return response.content


async def close_browser_session(db: AsyncSession, user: User, account_id: str) -> dict[str, Any]:
    owner_uid = _owner_uid(user)
    repo = XiaohongshuRepository(db)
    session = await repo.get_browser_session(account_id, owner_uid)
    if session is None:
        return {"closed": True, "account_id": account_id}
    async with _browser_operation_slot(owner_uid, account_id):
        session = await repo.get_browser_session(account_id, owner_uid, for_update=True)
        if session is None:
            return {"closed": True, "account_id": account_id}
        await _gateway_request(
            "DELETE",
            f"/internal/sessions/{session.id}",
            params={"owner_uid": owner_uid, "account_id": account_id},
        )
    session.status = "stopped"
    session.last_heartbeat_at = utc_now_naive()
    try:
        await (await get_redis_client()).delete(_browser_control_key(owner_uid, account_id))
    except Exception as exc:
        logger.warning(
            f"Failed to revoke Xiaohongshu browser control lease account={account_id} error_type={type(exc).__name__}"
        )
    await db.commit()
    return {"closed": True, "account_id": account_id, "session": session.to_dict()}


async def list_accounts(db: AsyncSession, user: User) -> dict[str, Any]:
    items = await XiaohongshuRepository(db).list_accounts(_owner_uid(user))
    return {"items": [item.to_dict() for item in items]}


async def create_account(db: AsyncSession, user: User, payload: XiaohongshuAccountCreate) -> dict[str, Any]:
    repo = XiaohongshuRepository(db)
    try:
        account = await repo.create_account(
            owner_uid=_owner_uid(user),
            display_name=payload.display_name,
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _error(409, "XHS_ACCOUNT_NAME_EXISTS", "账号备注名称已存在") from exc
    return {"account": account.to_dict()}


async def update_account(
    db: AsyncSession,
    user: User,
    account_id: str,
    payload: XiaohongshuAccountUpdate,
) -> dict[str, Any]:
    repo = XiaohongshuRepository(db)
    account = await repo.get_account(account_id, _owner_uid(user), for_update=True)
    if account is None:
        raise _error(404, "XHS_ACCOUNT_NOT_FOUND", "小红书账号不存在")
    changes = payload.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(account, key, value)
    account.updated_at = utc_now_naive()
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _error(409, "XHS_ACCOUNT_NAME_EXISTS", "账号备注名称已存在") from exc
    return {"account": account.to_dict()}


async def delete_account(db: AsyncSession, user: User, account_id: str) -> dict[str, Any]:
    owner_uid = _owner_uid(user)
    repo = XiaohongshuRepository(db)
    account = await repo.get_account(account_id, owner_uid)
    if account is None:
        raise _error(404, "XHS_ACCOUNT_NOT_FOUND", "小红书账号不存在")
    async with _browser_operation_slot(owner_uid, account_id):
        account = await repo.get_account(account_id, owner_uid, for_update=True)
        if account is None:
            raise _error(404, "XHS_ACCOUNT_NOT_FOUND", "小红书账号不存在")
        browser_session = await repo.get_browser_session(account_id, owner_uid, for_update=True)
        if browser_session is not None and browser_session.status in {
            "starting",
            "ready",
            "login_required",
            "busy",
        }:
            await _gateway_request(
                "DELETE",
                f"/internal/sessions/{browser_session.id}",
                params={"owner_uid": account.owner_uid, "account_id": account.id},
            )
            browser_session.status = "stopped"
            browser_session.last_heartbeat_at = utc_now_naive()
        try:
            await (await get_redis_client()).delete(_browser_control_key(owner_uid, account_id))
        except Exception as exc:
            logger.warning(
                f"Failed to revoke Xiaohongshu browser control lease "
                f"account={account_id} error_type={type(exc).__name__}"
            )
        await repo.delete_account(account)
        await db.commit()
    try:
        queue = await get_arq_pool()
        await queue.enqueue_job(
            "process_xiaohongshu_profile_cleanup",
            account.id,
            account.owner_uid,
            _job_id=f"xhs-cleanup:{account.id}",
        )
    except Exception as exc:
        logger.warning(
            f"Falling back to immediate Xiaohongshu profile cleanup "
            f"account={account.id} error_type={type(exc).__name__}"
        )
        try:
            XiaohongshuRuntime().remove_account_dir(account.owner_uid, account.id)
        except Exception as cleanup_exc:
            logger.error(
                f"Failed to clean Xiaohongshu profile account={account.id} error_type={type(cleanup_exc).__name__}"
            )
            raise _error(
                503,
                "XHS_PROFILE_CLEANUP_PENDING",
                "账号已移除，但登录凭据清理失败，请联系管理员处理",
                retryable=True,
            ) from cleanup_exc
    return {"deleted": True, "account_id": account_id}


async def start_account_login(db: AsyncSession, user: User, account_id: str) -> dict[str, Any]:
    if XHS_BROWSER_GATEWAY_URL:
        result = await open_browser_session(db, user, account_id)
        return {**result, "reused": result["session"]["status"] != "starting"}

    repo = XiaohongshuRepository(db)
    account = await repo.get_account(account_id, _owner_uid(user), for_update=True)
    if account is None:
        raise _error(404, "XHS_ACCOUNT_NOT_FOUND", "小红书账号不存在")
    if not account.enabled:
        raise _error(409, "XHS_ACCOUNT_DISABLED", "请先启用该账号")

    pending = await repo.get_pending_login_session(account.id, account.owner_uid, utc_now_naive())
    if pending is not None:
        return {"session": pending.to_dict(), "reused": True}

    await repo.delete_completed_login_sessions(account.id)
    session = await repo.create_login_session(
        account=account,
        expires_at=utc_now_naive() + timedelta(seconds=LOGIN_SESSION_SECONDS),
    )
    account.login_status = "pending"
    account.last_error_code = None
    account.last_error_message = None
    await db.commit()

    try:
        queue = await get_arq_pool()
        queued = await queue.enqueue_job(
            "process_xiaohongshu_login",
            session.id,
            _job_id=f"xhs-login:{session.id}",
        )
    except Exception as exc:
        session.status = "failed"
        session.error_code = "XHS_QUEUE_UNAVAILABLE"
        session.error_message = "登录队列暂不可用"
        session.completed_at = utc_now_naive()
        account.login_status = "error"
        account.last_error_code = session.error_code
        account.last_error_message = session.error_message
        await db.commit()
        raise _error(503, session.error_code, session.error_message, retryable=True) from exc
    if queued is None:
        raise _error(409, "XHS_LOGIN_ALREADY_QUEUED", "该登录任务已在处理中")
    return {"session": session.to_dict()}


async def get_login_session(db: AsyncSession, user: User, session_id: str) -> dict[str, Any]:
    repo = XiaohongshuRepository(db)
    owner_uid = _owner_uid(user)
    session = await repo.get_login_session(session_id, owner_uid, for_update=True)
    if session is None:
        raise _error(404, "XHS_LOGIN_SESSION_NOT_FOUND", "登录会话不存在")
    if session.status == "pending" and session.expires_at <= utc_now_naive():
        session.status = "expired"
        session.error_code = "XHS_LOGIN_EXPIRED"
        session.error_message = "二维码已过期，请重新扫码"
        session.completed_at = utc_now_naive()
        account = await repo.get_account(session.account_id, owner_uid, for_update=True)
        if account is not None and account.login_status == "pending":
            account.login_status = "expired"
            account.last_error_code = session.error_code
            account.last_error_message = session.error_message
        await db.commit()

    result = session.to_dict()
    if session.status != "pending":
        result["qr_code"] = None
        result["tip"] = session.error_message
        return {"session": result}

    state: dict[str, Any] = {}
    try:
        raw = await (await get_redis_client()).get(_login_state_key(session_id))
    except Exception as exc:
        raise _error(
            503,
            "XHS_LOGIN_STATE_UNAVAILABLE",
            "登录状态暂时不可用，请稍后重试",
            retryable=True,
        ) from exc
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                state = parsed
        except json.JSONDecodeError:
            state = {}
    result["qr_code"] = state.get("qr_code")
    result["tip"] = state.get("tip")
    return {"session": result}


async def check_account_login(db: AsyncSession, user: User, account_id: str) -> dict[str, Any]:
    if XHS_BROWSER_GATEWAY_URL:
        result = await open_browser_session(db, user, account_id)
        account = await XiaohongshuRepository(db).get_account(account_id, _owner_uid(user))
        return {
            "accepted": False,
            "completed": True,
            "account": account.to_dict(),
            **result,
        }

    account = await XiaohongshuRepository(db).get_account(account_id, _owner_uid(user))
    if account is None:
        raise _error(404, "XHS_ACCOUNT_NOT_FOUND", "小红书账号不存在")
    try:
        queue = await get_arq_pool()
        await queue.enqueue_job(
            "process_xiaohongshu_status_check",
            account.id,
            account.owner_uid,
            _job_id=f"xhs-check:{account.id}:{uuid.uuid4().hex}",
        )
    except Exception as exc:
        raise _error(
            503,
            "XHS_STATUS_QUEUE_UNAVAILABLE",
            "账号状态检查暂时不可用，请稍后重试",
            retryable=True,
        ) from exc
    return {"accepted": True, "account": account.to_dict()}


async def create_distribution(
    db: AsyncSession,
    user: User,
    artifact_id: str,
    payload: XiaohongshuDistributionCreate,
) -> dict[str, Any]:
    owner_uid = _owner_uid(user)
    content_repo = ContentRepository(db)
    artifact = await content_repo.get_artifact(artifact_id)
    if artifact is None or artifact.created_by != owner_uid:
        raise _error(404, "CONTENT_ARTIFACT_NOT_FOUND", "内容作品不存在")
    review_status = (artifact.review_snapshot or {}).get("status")
    if review_status not in {"passed", "warning"}:
        raise _error(409, "CONTENT_REVIEW_BLOCKED", "内容需通过审核后才能分发")
    if payload.mode == "publish" and not payload.confirm_publish:
        raise _error(422, "XHS_PUBLISH_CONFIRM_REQUIRED", "直接发布前需要明确确认")

    account_ids = list(dict.fromkeys(payload.account_ids))
    repo = XiaohongshuRepository(db)
    accounts = await repo.get_accounts(account_ids, owner_uid)
    if len(accounts) != len(account_ids):
        raise _error(404, "XHS_ACCOUNT_NOT_FOUND", "包含不存在或不属于当前用户的账号")
    unavailable = [item.id for item in accounts if not item.enabled or item.login_status != "logged_in"]
    if unavailable:
        raise _error(
            409,
            "XHS_ACCOUNT_NOT_READY",
            "存在未登录或已停用的账号",
            account_ids=unavailable,
        )

    title = payload.title if payload.title is not None else artifact.title.strip()
    body = payload.body if payload.body is not None else artifact.body.strip()
    topics = list(
        dict.fromkeys(item.strip().lstrip("#") for item in (payload.topics or artifact.topics or []) if item.strip())
    )
    if not title or len(title) > 20:
        raise _error(422, "XHS_TITLE_INVALID", "小红书标题需为 1–20 个字符")
    if not body or len(body) > 1000:
        raise _error(422, "XHS_BODY_INVALID", "小红书正文需为 1–1000 个字符")
    if len(topics) > 10 or any(len(item) > 20 for item in topics):
        raise _error(422, "XHS_TOPICS_INVALID", "话题最多 10 个，单个话题不超过 20 个字符")

    artifact_version = await repo.get_artifact_version(artifact.id, artifact.current_version)
    if artifact_version is None:
        raise _error(409, "CONTENT_ARTIFACT_VERSION_MISSING", "当前内容版本不存在")

    request_key = hashlib.sha256(f"{owner_uid}:{payload.request_id}".encode()).hexdigest()
    existing = await repo.get_job_by_idempotency_key(request_key, owner_uid)
    if existing is not None:
        results = await repo.list_distribution_results(existing.id)
        return {"job": _serialize_job(existing, results), "deduplicated": True}

    cover_snapshot = {"type": "generated", "template": "title-card-v1"}
    if artifact.cover_asset_id:
        cover_asset = await ContentCoverRepository(db).get_asset_for_user(artifact.cover_asset_id, owner_uid)
        if cover_asset is None or cover_asset.role != "output":
            raise _error(409, "CONTENT_COVER_MISSING", "当前封面不存在，请重新选择封面")
        cover_snapshot = {
            "type": "asset",
            "asset_id": cover_asset.id,
            "bucket_name": cover_asset.bucket_name,
            "object_name": cover_asset.object_name,
            "sha256": cover_asset.sha256,
        }

    snapshot = {
        "schema_version": 1,
        "account_ids": account_ids,
        "title": title,
        "body": body,
        "topics": topics,
        "cover": cover_snapshot,
    }
    dedupe_key = None
    if payload.mode == "publish":
        dedupe_source = json.dumps(
            {
                "owner_uid": owner_uid,
                "artifact_id": artifact.id,
                "artifact_version": artifact.current_version,
                **snapshot,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        dedupe_key = hashlib.sha256(dedupe_source.encode()).hexdigest()
        recent = await repo.get_recent_publish_job(
            dedupe_key,
            owner_uid,
            utc_now_naive() - timedelta(hours=24),
        )
        if recent is not None:
            results = await repo.list_distribution_results(recent.id)
            return {
                "job": _serialize_job(recent, results),
                "deduplicated": True,
                "dedupe_reason": "same_publish_within_24_hours",
            }
    job = await repo.create_distribution_job(
        owner_uid=owner_uid,
        artifact_id=artifact.id,
        artifact_version=artifact.current_version,
        mode=payload.mode,
        payload_snapshot=snapshot,
        idempotency_key=request_key,
        dedupe_key=dedupe_key,
        accounts=accounts,
        confirmed_by=owner_uid if payload.mode == "publish" else None,
        confirmed_at=utc_now_naive() if payload.mode == "publish" else None,
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await repo.get_job_by_idempotency_key(request_key, owner_uid)
        if existing is None:
            raise
        results = await repo.list_distribution_results(existing.id)
        return {"job": _serialize_job(existing, results), "deduplicated": True}

    try:
        queue = await get_arq_pool()
        queued = await queue.enqueue_job(
            "process_xiaohongshu_distribution",
            job.id,
            _job_id=f"xhs-distribution:{job.id}",
        )
    except Exception as exc:
        job.status = "failed"
        job.error_code = "XHS_QUEUE_UNAVAILABLE"
        job.error_message = "分发队列暂不可用"
        job.completed_at = utc_now_naive()
        await db.commit()
        raise _error(503, job.error_code, job.error_message, job_id=job.id, retryable=True) from exc
    if queued is None:
        job.status = "failed"
        job.error_code = "XHS_QUEUE_REJECTED"
        job.error_message = "分发任务未能进入执行队列"
        job.completed_at = utc_now_naive()
        await db.commit()
        raise _error(503, "XHS_QUEUE_REJECTED", job.error_message, job_id=job.id, retryable=True)
    results = await repo.list_distribution_results(job.id)
    return {"job": _serialize_job(job, results), "deduplicated": False}


async def get_distribution_job(db: AsyncSession, user: User, job_id: str) -> dict[str, Any]:
    repo = XiaohongshuRepository(db)
    job = await repo.get_distribution_job(job_id, _owner_uid(user))
    if job is None:
        raise _error(404, "XHS_DISTRIBUTION_NOT_FOUND", "分发任务不存在")
    return {"job": _serialize_job(job, await repo.list_distribution_results(job.id))}


async def list_artifact_distributions(db: AsyncSession, user: User, artifact_id: str) -> dict[str, Any]:
    owner_uid = _owner_uid(user)
    artifact = await ContentRepository(db).get_artifact(artifact_id)
    if artifact is None or artifact.created_by != owner_uid:
        raise _error(404, "CONTENT_ARTIFACT_NOT_FOUND", "内容作品不存在")
    repo = XiaohongshuRepository(db)
    jobs = await repo.list_artifact_jobs(artifact_id, owner_uid)
    items = []
    for job in jobs:
        items.append(_serialize_job(job, await repo.list_distribution_results(job.id)))
    return {"items": items}


async def get_result_screenshot(db: AsyncSession, user: User, result_id: str) -> Path:
    result = await XiaohongshuRepository(db).get_result(result_id, _owner_uid(user))
    if result is None or not result.screenshot_path:
        raise _error(404, "XHS_SCREENSHOT_NOT_FOUND", "执行截图不存在")
    path = Path(result.screenshot_path).resolve()
    runtime_root = Path(os.getenv("XHS_RUNTIME_ROOT", "/app/saves/xiaohongshu")).resolve()
    if not path.is_relative_to(runtime_root) or not path.is_file():
        raise _error(404, "XHS_SCREENSHOT_NOT_FOUND", "执行截图不存在")
    return path
