from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from yuxi.integrations.xiaohongshu.session_manager import (
    BrowserSessionCapacityError,
    XiaohongshuBrowserSessionManager,
)
from yuxi.storage.minio.client import get_minio_client
from yuxi.utils.logging_config import logger

GATEWAY_TOKEN = os.getenv("XHS_GATEWAY_TOKEN", "")
SESSION_IDLE_SECONDS = max(60, int(os.getenv("XHS_GATEWAY_IDLE_SECONDS", "900")))
REAPER_INTERVAL_SECONDS = max(10, int(os.getenv("XHS_GATEWAY_REAPER_INTERVAL_SECONDS", "30")))
manager = XiaohongshuBrowserSessionManager()


async def reap_idle_sessions() -> None:
    while True:
        await asyncio.sleep(REAPER_INTERVAL_SECONDS)
        reaped = await manager.reap_idle(SESSION_IDLE_SECONDS)
        if reaped:
            logger.info(f"Reaped {len(reaped)} idle Xiaohongshu browser session(s)")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not GATEWAY_TOKEN:
        raise RuntimeError("XHS_GATEWAY_TOKEN must be configured")
    if os.getenv("YUXI_ENV", "development") != "development" and GATEWAY_TOKEN == "local-dev-change-me":
        raise RuntimeError("XHS_GATEWAY_TOKEN must be replaced outside development")
    reaper = asyncio.create_task(reap_idle_sessions())
    try:
        yield
    finally:
        reaper.cancel()
        with suppress(asyncio.CancelledError):
            await reaper
        await manager.close_all()


app = FastAPI(title="Yuxi Xiaohongshu Browser Gateway", lifespan=lifespan)


async def require_internal_token(authorization: str | None = Header(default=None)) -> None:
    expected = f"Bearer {GATEWAY_TOKEN}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid gateway credentials")


class SessionRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=80)
    owner_uid: str = Field(min_length=1, max_length=255)
    account_id: str = Field(min_length=1, max_length=80)
    target: str = Field(default="home", pattern="^(home|drafts)$")


class ActionRequest(SessionRequest):
    action: str = Field(min_length=1, max_length=32)
    x: float | None = None
    y: float | None = None
    text: str | None = Field(default=None, max_length=2000)
    key: str | None = Field(default=None, max_length=32)
    delta_y: int | None = Field(default=None, ge=-2000, le=2000)


class DistributeRequest(SessionRequest):
    job_id: str = Field(min_length=8, max_length=80)
    title: str = Field(min_length=1, max_length=20)
    body: str = Field(min_length=1, max_length=1000)
    topics: list[str] = Field(default_factory=list, max_length=10)
    mode: str = Field(pattern="^(draft|publish)$")
    cover_bucket_name: str | None = Field(default=None, max_length=255)
    cover_object_name: str | None = Field(default=None, max_length=1024)
    cover_sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")


@app.get("/health")
async def health() -> dict[str, str | int]:
    return {
        "status": "ok",
        "active_sessions": manager.active_session_count,
        "max_sessions": manager.max_sessions,
    }


@app.post("/internal/sessions/open", dependencies=[Depends(require_internal_token)])
async def open_session(payload: SessionRequest) -> dict:
    try:
        return await manager.open(
            payload.session_id,
            payload.owner_uid,
            payload.account_id,
            target=payload.target,
        )
    except BrowserSessionCapacityError as exc:
        raise HTTPException(
            status_code=429,
            detail={"code": "XHS_GATEWAY_CAPACITY_REACHED", "message": "远程浏览器容量已满，请稍后重试"},
        ) from exc
    except Exception as exc:
        logger.error(f"Xiaohongshu browser open failed account={payload.account_id} error_type={type(exc).__name__}")
        raise HTTPException(
            status_code=502,
            detail={"code": "XHS_GATEWAY_OPEN_FAILED", "message": "远程浏览器启动失败"},
        ) from exc


@app.get("/internal/sessions/{session_id}/status", dependencies=[Depends(require_internal_token)])
async def session_status(session_id: str, owner_uid: str, account_id: str) -> dict:
    try:
        return await manager.status(session_id=session_id, owner_uid=owner_uid, account_id=account_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="browser session not found") from exc
    except Exception as exc:
        logger.error(f"Xiaohongshu status failed account={account_id} error_type={type(exc).__name__}")
        raise HTTPException(
            status_code=502,
            detail={"code": "XHS_GATEWAY_STATUS_FAILED", "message": "远程浏览器状态读取失败"},
        ) from exc


@app.get("/internal/sessions/{session_id}/screenshot", dependencies=[Depends(require_internal_token)])
async def session_screenshot(session_id: str, owner_uid: str, account_id: str) -> Response:
    try:
        data = await manager.screenshot(session_id=session_id, owner_uid=owner_uid, account_id=account_id)
        return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store"})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="browser session not found") from exc
    except Exception as exc:
        logger.error(f"Xiaohongshu screenshot failed account={account_id} error_type={type(exc).__name__}")
        raise HTTPException(
            status_code=502,
            detail={"code": "XHS_GATEWAY_SCREENSHOT_FAILED", "message": "远程浏览器画面读取失败"},
        ) from exc


@app.post("/internal/sessions/{session_id}/action", dependencies=[Depends(require_internal_token)])
async def session_action(session_id: str, payload: ActionRequest) -> dict:
    if payload.session_id != session_id:
        raise HTTPException(status_code=400, detail="session id mismatch")
    try:
        return await manager.action(
            session_id=session_id,
            owner_uid=payload.owner_uid,
            account_id=payload.account_id,
            payload=payload.model_dump(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="browser session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Xiaohongshu action failed account={payload.account_id} error_type={type(exc).__name__}")
        raise HTTPException(
            status_code=502,
            detail={"code": "XHS_GATEWAY_ACTION_FAILED", "message": "远程浏览器操作失败"},
        ) from exc


@app.post("/internal/sessions/{session_id}/distribute", dependencies=[Depends(require_internal_token)])
async def session_distribute(session_id: str, payload: DistributeRequest) -> dict:
    if payload.session_id != session_id:
        raise HTTPException(status_code=400, detail="session id mismatch")
    try:
        cover_bytes = None
        cover_fields = (
            payload.cover_bucket_name,
            payload.cover_object_name,
            payload.cover_sha256,
        )
        if any(cover_fields) and not all(cover_fields):
            raise HTTPException(status_code=400, detail="cover storage reference is incomplete")
        if payload.cover_bucket_name and payload.cover_object_name:
            cover_bytes = await get_minio_client().adownload_file(
                payload.cover_bucket_name,
                payload.cover_object_name,
            )
            if payload.cover_sha256 and hashlib.sha256(cover_bytes).hexdigest() != payload.cover_sha256:
                raise HTTPException(status_code=409, detail="cover integrity check failed")
        return await manager.distribute(
            session_id=session_id,
            owner_uid=payload.owner_uid,
            account_id=payload.account_id,
            job_id=payload.job_id,
            title=payload.title,
            body=payload.body,
            topics=payload.topics,
            mode=payload.mode,
            cover_bytes=cover_bytes,
        )
    except HTTPException:
        raise
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="browser session not found") from exc
    except Exception as exc:
        logger.error(
            f"Xiaohongshu distribution failed account={payload.account_id} "
            f"job={payload.job_id} error_type={type(exc).__name__}"
        )
        code = getattr(exc, "code", "XHS_GATEWAY_DISTRIBUTION_FAILED")
        raise HTTPException(
            status_code=502,
            detail={"code": code, "message": "小红书分发操作失败"},
        ) from exc


@app.delete("/internal/sessions/{session_id}", dependencies=[Depends(require_internal_token)])
async def close_session(session_id: str, owner_uid: str, account_id: str) -> dict[str, bool]:
    try:
        await manager.close(session_id=session_id, owner_uid=owner_uid, account_id=account_id)
        return {"closed": True}
    except KeyError:
        return {"closed": True}
