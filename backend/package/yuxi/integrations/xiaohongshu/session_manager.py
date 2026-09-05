from __future__ import annotations

import asyncio
import math
import os
from contextlib import suppress
from dataclasses import dataclass, field
from time import monotonic

from yuxi.integrations.xiaohongshu.runtime import XHS_HOME_URL, XHS_LOGIN_URL, XiaohongshuRuntime


class BrowserSessionCapacityError(RuntimeError):
    pass


@dataclass
class BrowserSession:
    session_id: str
    owner_uid: str
    account_id: str
    context: object
    page: object
    target: str = "home"
    view: str = "home"
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used_at: float = field(default_factory=monotonic)


class XiaohongshuBrowserSessionManager:
    """Owns long-lived account browser contexts for the internal gateway.

    The manager deliberately exposes only bounded page operations. It never
    returns a browser context, storage state, cookies, or a filesystem path to
    callers.
    """

    def __init__(self, runtime: XiaohongshuRuntime | None = None, max_sessions: int | None = None):
        self.runtime = runtime or XiaohongshuRuntime(
            headless=os.getenv("XHS_GATEWAY_HEADLESS", "true").lower() != "false"
        )
        self._sessions: dict[str, BrowserSession] = {}
        self._opening: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._playwright = None
        self._manager_lock = asyncio.Lock()
        self.max_sessions = max(1, max_sessions or int(os.getenv("XHS_GATEWAY_MAX_SESSIONS", "5")))

    @staticmethod
    def _key(owner_uid: str, account_id: str) -> str:
        return f"{owner_uid}:{account_id}"

    async def _account_lock(self, owner_uid: str, account_id: str) -> asyncio.Lock:
        key = self._key(owner_uid, account_id)
        async with self._manager_lock:
            return self._locks.setdefault(key, asyncio.Lock())

    async def _ensure_playwright(self):
        async with self._manager_lock:
            if self._playwright is None:
                from patchright.async_api import async_playwright

                self._playwright = await async_playwright().start()
        return self._playwright

    async def open(
        self,
        session_id: str,
        owner_uid: str,
        account_id: str,
        *,
        target: str = "home",
    ) -> dict:
        if target not in {"home", "drafts"}:
            raise ValueError("不支持的浏览器目标页面")
        key = self._key(owner_uid, account_id)
        lock = await self._account_lock(owner_uid, account_id)
        async with lock:
            existing = self._sessions.get(key)
            if existing is not None:
                if existing.session_id != session_id:
                    async with existing.lock:
                        await existing.context.close()
                    self._sessions.pop(key, None)
                else:
                    existing.target = target
                    existing.last_used_at = monotonic()
                    async with existing.lock:
                        return await self._status_unlocked(existing)

            async with self._manager_lock:
                if len(self._sessions) + len(self._opening) >= self.max_sessions:
                    raise BrowserSessionCapacityError("browser session capacity reached")
                self._opening.add(key)
            context = None
            try:
                playwright = await self._ensure_playwright()
                context = await self.runtime._launch_context(playwright, owner_uid, account_id)
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(XHS_HOME_URL, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(1200)
                if not await self.runtime._is_logged_in(page):
                    await page.goto(XHS_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(1200)
                session = BrowserSession(
                    session_id=session_id,
                    owner_uid=owner_uid,
                    account_id=account_id,
                    context=context,
                    page=page,
                    target=target,
                    view="login" if page.url.startswith(XHS_LOGIN_URL) else "home",
                )
                async with self._manager_lock:
                    self._sessions[key] = session
                    self._opening.discard(key)
                async with session.lock:
                    return await self._status_unlocked(session)
            except Exception:
                async with self._manager_lock:
                    self._sessions.pop(key, None)
                if context is not None:
                    with suppress(Exception):
                        await context.close()
                raise
            finally:
                async with self._manager_lock:
                    self._opening.discard(key)

    async def get(self, session_id: str, owner_uid: str, account_id: str) -> BrowserSession:
        session = self._sessions.get(self._key(owner_uid, account_id))
        if session is None or session.session_id != session_id:
            raise KeyError("browser session not found")
        session.last_used_at = monotonic()
        return session

    async def status(self, *, session_id: str, owner_uid: str, account_id: str) -> dict:
        session = await self.get(session_id, owner_uid, account_id)
        async with session.lock:
            return await self._status_unlocked(session)

    async def _status_unlocked(self, session: BrowserSession) -> dict:
        logged_in = await self.runtime._is_logged_in(session.page)
        profile = await self.runtime._profile(session.page) if logged_in else {"nickname": "", "account_id": ""}
        if not logged_in:
            session.view = "login"
        elif session.target == "drafts" and session.view != "drafts":
            await self.runtime.open_drafts(session.page)
            session.view = "drafts"
        return {
            "session_id": session.session_id,
            "owner_uid": session.owner_uid,
            "account_id": session.account_id,
            "status": "ready" if logged_in else "login_required",
            "logged_in": logged_in,
            "nickname": profile.get("nickname", ""),
            "platform_account_id": profile.get("account_id", ""),
            "view": session.view,
        }

    async def screenshot(self, *, session_id: str, owner_uid: str, account_id: str) -> bytes:
        session = await self.get(session_id, owner_uid, account_id)
        async with session.lock:
            return await session.page.screenshot(type="png", full_page=False)

    async def action(self, *, session_id: str, owner_uid: str, account_id: str, payload: dict) -> dict:
        session = await self.get(session_id, owner_uid, account_id)
        action = str(payload.get("action") or "")
        async with session.lock:
            if action == "click":
                if payload.get("x") is None or payload.get("y") is None:
                    raise ValueError("点击操作缺少坐标")
                x = float(payload["x"])
                y = float(payload["y"])
                if not math.isfinite(x) or not math.isfinite(y) or x < 0 or y < 0 or x > 4000 or y > 4000:
                    raise ValueError("点击坐标超出允许范围")
                await session.page.mouse.click(x, y)
            elif action == "type":
                text = str(payload.get("text") or "")
                if len(text) > 2000:
                    raise ValueError("输入内容过长")
                await session.page.keyboard.insert_text(text)
            elif action == "keypress":
                key = str(payload.get("key") or "")
                allowed = {
                    "Enter",
                    "Escape",
                    "Tab",
                    "Backspace",
                    "Delete",
                    "Home",
                    "End",
                    "PageUp",
                    "PageDown",
                    "ArrowUp",
                    "ArrowDown",
                    "ArrowLeft",
                    "ArrowRight",
                    "Control+A",
                }
                if key not in allowed:
                    raise ValueError("不支持的按键")
                await session.page.keyboard.press(key)
            elif action == "scroll":
                delta_y = max(-2000, min(2000, int(payload.get("delta_y") or 0)))
                await session.page.mouse.wheel(0, delta_y)
            else:
                raise ValueError("不支持的浏览器操作")
            await session.page.wait_for_timeout(250)
            session.last_used_at = monotonic()
            return await self._status_unlocked(session)

    @property
    def active_session_count(self) -> int:
        return len(self._sessions)

    async def reap_idle(self, idle_seconds: int) -> list[str]:
        if idle_seconds <= 0:
            return []
        deadline = monotonic() - idle_seconds
        reaped: list[str] = []
        for session in list(self._sessions.values()):
            if session.last_used_at > deadline:
                continue
            key = self._key(session.owner_uid, session.account_id)
            lock = await self._account_lock(session.owner_uid, session.account_id)
            async with lock:
                current = self._sessions.get(key)
                if current is not session:
                    continue
                async with session.lock:
                    if session.last_used_at > deadline:
                        continue
                    await session.context.close()
                    if self._sessions.get(key) is session:
                        self._sessions.pop(key, None)
                    reaped.append(session.session_id)
        return reaped

    async def distribute(
        self,
        *,
        session_id: str,
        owner_uid: str,
        account_id: str,
        job_id: str,
        title: str,
        body: str,
        topics: list[str],
        mode: str,
        cover_bytes: bytes | None = None,
    ) -> dict[str, str]:
        session = await self.get(session_id, owner_uid, account_id)
        async with session.lock:
            outcome = await self.runtime.distribute_on_page(
                session.page,
                owner_uid,
                account_id,
                job_id,
                title=title,
                body=body,
                topics=topics,
                mode=mode,
                cover_bytes=cover_bytes,
            )
            session.last_used_at = monotonic()
            return outcome

    async def close(self, *, session_id: str, owner_uid: str, account_id: str) -> None:
        key = self._key(owner_uid, account_id)
        lock = await self._account_lock(owner_uid, account_id)
        async with lock:
            session = self._sessions.get(key)
            if session is None or session.session_id != session_id:
                raise KeyError("browser session not found")
            async with session.lock:
                await session.context.close()
            if self._sessions.get(key) is session:
                self._sessions.pop(key, None)

    async def close_all(self) -> None:
        for session in list(self._sessions.values()):
            try:
                await session.context.close()
            except Exception:
                pass
        self._sessions.clear()
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
