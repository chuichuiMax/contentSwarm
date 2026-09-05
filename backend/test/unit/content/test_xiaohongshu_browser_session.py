from __future__ import annotations

import asyncio
from time import monotonic

import pytest
from fastapi import HTTPException

from server import xhs_browser_gateway
from yuxi.integrations.xiaohongshu.session_manager import (
    BrowserSessionCapacityError,
    XiaohongshuBrowserSessionManager,
)


class FakeMouse:
    def __init__(self):
        self.clicks = []
        self.wheels = []

    async def click(self, x, y):
        self.clicks.append((x, y))

    async def wheel(self, x, y):
        self.wheels.append((x, y))


class FakeKeyboard:
    def __init__(self):
        self.inserted = []
        self.pressed = []

    async def insert_text(self, text):
        self.inserted.append(text)

    async def press(self, key):
        self.pressed.append(key)


class FakePage:
    def __init__(self):
        self.url = "https://creator.xiaohongshu.com/new/home"
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()

    async def goto(self, url, **kwargs):
        del kwargs
        self.url = url

    async def wait_for_timeout(self, milliseconds):
        del milliseconds

    async def screenshot(self, **kwargs):
        del kwargs
        return b"png"


class FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self.closed = False

    async def close(self):
        self.closed = True


class FakePlaywright:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


class FakeRuntime:
    def __init__(self):
        self.page = FakePage()
        self.context = FakeContext(self.page)
        self.drafts_opened = 0

    async def _launch_context(self, playwright, owner_uid, account_id):
        del playwright, owner_uid, account_id
        return self.context

    async def _is_logged_in(self, page):
        del page
        return True

    async def _profile(self, page):
        del page
        return {"nickname": "测试账号", "account_id": "platform-1"}

    async def open_drafts(self, page):
        self.drafts_opened += 1
        page.url = "https://creator.xiaohongshu.com/publish/drafts"


class IsolatedFakeRuntime(FakeRuntime):
    def __init__(self):
        self.contexts = {}
        self.all_contexts = []

    async def _launch_context(self, playwright, owner_uid, account_id):
        del playwright
        page = FakePage()
        context = FakeContext(page)
        self.contexts[(owner_uid, account_id)] = context
        self.all_contexts.append(context)
        return context


@pytest.mark.asyncio
async def test_action_returns_status_without_reentrant_lock_deadlock():
    runtime = FakeRuntime()
    manager = XiaohongshuBrowserSessionManager(runtime=runtime)
    manager._playwright = FakePlaywright()

    opened = await asyncio.wait_for(manager.open("session-1", "owner-1", "account-1"), timeout=1)
    assert opened["status"] == "ready"

    acted = await asyncio.wait_for(
        manager.action(
            session_id="session-1",
            owner_uid="owner-1",
            account_id="account-1",
            payload={"action": "click", "x": 10, "y": 20},
        ),
        timeout=1,
    )
    assert acted["logged_in"] is True
    assert runtime.page.mouse.clicks == [(10.0, 20.0)]

    await manager.close_all()
    assert runtime.context.closed is True


@pytest.mark.asyncio
async def test_drafts_target_opens_once_and_is_reported_in_status():
    runtime = FakeRuntime()
    manager = XiaohongshuBrowserSessionManager(runtime=runtime)
    manager._playwright = FakePlaywright()

    opened = await manager.open(
        "session-1",
        "owner-1",
        "account-1",
        target="drafts",
    )
    reopened = await manager.open(
        "session-1",
        "owner-1",
        "account-1",
        target="drafts",
    )

    assert opened["view"] == "drafts"
    assert reopened["view"] == "drafts"
    assert runtime.drafts_opened == 1
    await manager.close_all()


@pytest.mark.asyncio
async def test_direct_takeover_keyboard_and_wheel_actions_are_bounded():
    runtime = FakeRuntime()
    manager = XiaohongshuBrowserSessionManager(runtime=runtime)
    manager._playwright = FakePlaywright()
    await manager.open("session-1", "owner-1", "account-1")

    for payload in (
        {"action": "type", "text": "直接输入中文"},
        {"action": "keypress", "key": "Control+A"},
        {"action": "keypress", "key": "Delete"},
        {"action": "scroll", "delta_y": 5000},
    ):
        await manager.action(
            session_id="session-1",
            owner_uid="owner-1",
            account_id="account-1",
            payload=payload,
        )

    assert runtime.page.keyboard.inserted == ["直接输入中文"]
    assert runtime.page.keyboard.pressed == ["Control+A", "Delete"]
    assert runtime.page.mouse.wheels == [(0, 2000)]
    await manager.close_all()


@pytest.mark.asyncio
async def test_idle_sessions_are_closed_and_removed():
    runtime = FakeRuntime()
    manager = XiaohongshuBrowserSessionManager(runtime=runtime)
    manager._playwright = FakePlaywright()
    await manager.open("session-1", "owner-1", "account-1")
    session = await manager.get("session-1", "owner-1", "account-1")
    session.last_used_at = 0

    assert await manager.reap_idle(60) == ["session-1"]
    assert manager.active_session_count == 0
    assert runtime.context.closed is True


@pytest.mark.asyncio
async def test_idle_reaper_does_not_close_a_session_that_became_active():
    runtime = FakeRuntime()
    manager = XiaohongshuBrowserSessionManager(runtime=runtime)
    manager._playwright = FakePlaywright()
    await manager.open("session-1", "owner-1", "account-1")
    session = await manager.get("session-1", "owner-1", "account-1")
    session.last_used_at = 0
    await session.lock.acquire()

    reap_task = asyncio.create_task(manager.reap_idle(60))
    await asyncio.sleep(0)
    session.last_used_at = monotonic()
    session.lock.release()

    assert await reap_task == []
    assert manager.active_session_count == 1
    assert runtime.context.closed is False
    await manager.close_all()


@pytest.mark.asyncio
async def test_click_rejects_missing_or_out_of_range_coordinates():
    runtime = FakeRuntime()
    manager = XiaohongshuBrowserSessionManager(runtime=runtime)
    manager._playwright = FakePlaywright()
    await manager.open("session-1", "owner-1", "account-1")

    with pytest.raises(ValueError, match="缺少坐标"):
        await manager.action(
            session_id="session-1",
            owner_uid="owner-1",
            account_id="account-1",
            payload={"action": "click"},
        )
    with pytest.raises(ValueError, match="超出允许范围"):
        await manager.action(
            session_id="session-1",
            owner_uid="owner-1",
            account_id="account-1",
            payload={"action": "click", "x": -1, "y": 20},
        )

    await manager.close_all()


@pytest.mark.asyncio
async def test_concurrent_accounts_keep_sessions_and_pages_isolated():
    runtime = IsolatedFakeRuntime()
    manager = XiaohongshuBrowserSessionManager(runtime=runtime, max_sessions=10)
    manager._playwright = FakePlaywright()

    opened = await asyncio.gather(
        *(manager.open(f"session-{index}", "owner-1", f"account-{index}") for index in range(10))
    )

    assert manager.active_session_count == 10
    assert {item["session_id"] for item in opened} == {f"session-{index}" for index in range(10)}
    assert len({id(context) for context in runtime.contexts.values()}) == 10
    assert len({id(context.pages[0]) for context in runtime.contexts.values()}) == 10

    with pytest.raises(KeyError, match="browser session not found"):
        await manager.status(
            session_id="session-0",
            owner_uid="owner-1",
            account_id="account-1",
        )

    await manager.close_all()
    assert all(context.closed for context in runtime.contexts.values())


@pytest.mark.asyncio
async def test_session_capacity_is_enforced_under_concurrent_opens():
    runtime = IsolatedFakeRuntime()
    manager = XiaohongshuBrowserSessionManager(runtime=runtime, max_sessions=2)
    manager._playwright = FakePlaywright()

    results = await asyncio.gather(
        *(manager.open(f"session-{index}", "owner-1", f"account-{index}") for index in range(3)),
        return_exceptions=True,
    )

    assert manager.active_session_count == 2
    assert sum(isinstance(item, BrowserSessionCapacityError) for item in results) == 1
    await manager.close_all()


@pytest.mark.asyncio
async def test_new_authoritative_session_id_replaces_stale_account_context():
    runtime = IsolatedFakeRuntime()
    manager = XiaohongshuBrowserSessionManager(runtime=runtime, max_sessions=1)
    manager._playwright = FakePlaywright()

    await manager.open("session-old", "owner-1", "account-1")
    old_context = runtime.all_contexts[0]
    replaced = await manager.open("session-new", "owner-1", "account-1")

    assert replaced["session_id"] == "session-new"
    assert old_context.closed is True
    assert manager.active_session_count == 1
    await manager.close_all()


@pytest.mark.asyncio
async def test_stale_close_cannot_remove_a_replacement_session():
    runtime = IsolatedFakeRuntime()
    manager = XiaohongshuBrowserSessionManager(runtime=runtime, max_sessions=1)
    manager._playwright = FakePlaywright()
    await manager.open("session-old", "owner-1", "account-1")
    account_lock = await manager._account_lock("owner-1", "account-1")
    await account_lock.acquire()

    open_task = asyncio.create_task(manager.open("session-new", "owner-1", "account-1"))
    await asyncio.sleep(0)
    stale_close_task = asyncio.create_task(
        manager.close(session_id="session-old", owner_uid="owner-1", account_id="account-1")
    )
    await asyncio.sleep(0)
    account_lock.release()

    opened = await open_task
    with pytest.raises(KeyError, match="browser session not found"):
        await stale_close_task
    status = await manager.status(
        session_id="session-new",
        owner_uid="owner-1",
        account_id="account-1",
    )
    assert opened["session_id"] == "session-new"
    assert status["session_id"] == "session-new"
    assert manager.active_session_count == 1
    await manager.close_all()


@pytest.mark.asyncio
async def test_gateway_capacity_returns_retryable_http_status(monkeypatch: pytest.MonkeyPatch):
    async def capacity_reached(*args, **kwargs):
        del args, kwargs
        raise BrowserSessionCapacityError("capacity reached")

    monkeypatch.setattr(xhs_browser_gateway.manager, "open", capacity_reached)
    request = xhs_browser_gateway.SessionRequest(
        session_id="session-123",
        owner_uid="owner-1",
        account_id="account-1",
    )

    with pytest.raises(HTTPException) as exc_info:
        await xhs_browser_gateway.open_session(request)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["code"] == "XHS_GATEWAY_CAPACITY_REACHED"


@pytest.mark.asyncio
async def test_gateway_forwards_restricted_drafts_target(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    async def open_drafts(session_id, owner_uid, account_id, *, target):
        captured.update(
            session_id=session_id,
            owner_uid=owner_uid,
            account_id=account_id,
            target=target,
        )
        return {"status": "ready", "view": target}

    monkeypatch.setattr(xhs_browser_gateway.manager, "open", open_drafts)
    request = xhs_browser_gateway.SessionRequest(
        session_id="session-123",
        owner_uid="owner-1",
        account_id="account-1",
        target="drafts",
    )

    response = await xhs_browser_gateway.open_session(request)

    assert response == {"status": "ready", "view": "drafts"}
    assert captured == {
        "session_id": "session-123",
        "owner_uid": "owner-1",
        "account_id": "account-1",
        "target": "drafts",
    }
