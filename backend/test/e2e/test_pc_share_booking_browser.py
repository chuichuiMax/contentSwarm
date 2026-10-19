"""Browser case-to-booking handoff. Simulated WeChat responses do not establish device acceptance."""

import asyncio
import io
import socket
from unittest.mock import AsyncMock

import pytest
import uvicorn
from patchright.async_api import async_playwright, expect
from PIL import Image
from sqlalchemy import select

from test.integration.api import test_material_share_jobs as share_tests
from test.unit.services.test_case_share_entry import EntryRedis
from yuxi.services import case_share_entry_service as entry
from yuxi.storage.postgres.models_business import Department, User
from yuxi.storage.postgres.models_content import ContentMaterialShare
from yuxi.utils.auth_utils import AuthUtils

share_env = share_tests.env
pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


async def test_case_cta_qrcode_mobile_open_and_explicit_failure(share_env, monkeypatch, httpx_mock, tmp_path):
    env = share_env
    async with env.sessions() as db:
        db.add(Department(id=7, name="浏览器测试部门"))
        await db.flush()
        user = await db.get(User, 1)
        user.department_id = 7
        await db.commit()
    headers = {"Authorization": "Bearer " + AuthUtils.create_access_token({"sub": "1"})}
    response = await env.client.post("/api/material-library/shares", headers=headers, json={"item_ids": ["i1"]})
    assert response.status_code == 201, response.text
    token = response.json()["share"]["id"]
    async with env.sessions() as db:
        share = await db.scalar(select(ContentMaterialShare).where(ContentMaterialShare.token == token))
        share.title = "浏览器验收测试案例"
        await db.commit()
    redis = EntryRedis()
    monkeypatch.setattr(entry, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(entry, "_wechat_credentials", lambda: ("wx-browser-test", "test-only"))
    monkeypatch.setattr(entry, "_wechat_access_token", AsyncMock(return_value="test-only"))
    monkeypatch.setenv("WECHAT_MP_ENTRY_ENV_VERSION", "trial")
    stream = io.BytesIO()
    Image.new("RGB", (430, 430), "#267345").save(stream, "PNG")
    httpx_mock.add_response(
        url="https://api.weixin.qq.com/wxa/getwxacodeunlimit?access_token=test-only",
        content=stream.getvalue(),
        headers={"content-type": "image/png"},
    )
    httpx_mock.add_response(
        url="https://api.weixin.qq.com/wxa/generate_urllink?access_token=test-only",
        json={"url_link": "https://wxaurl.cn/browser-test"},
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(env.app, log_level="error", lifespan="off"))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        if serving.done():
            await serving
        await asyncio.sleep(0.01)
    origin = f"http://127.0.0.1:{port}"
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                page = await browser.new_page(viewport={"width": 1280, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto(f"{origin}/share/case/{token}")
                await page.get_by_role("link", name="免费预约参观工地").click()
                await expect(page).to_have_url(f"{origin}/api/material-library/shares/{token}/booking")
                await expect(page.locator("#entry-code")).to_have_js_property("naturalWidth", 430)
                await expect(page.locator("#code-error")).to_be_hidden()
                await expect(page.get_by_role("link", name="打开小程序预约")).to_be_hidden()
                await page.screenshot(path=str(tmp_path / "pc-booking-desktop.png"), full_page=True)
                await page.set_viewport_size({"width": 375, "height": 812})
                await expect(page.get_by_role("link", name="打开小程序预约")).to_be_visible()
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await page.screenshot(path=str(tmp_path / "pc-booking-mobile.png"), full_page=True)
                await page.route("https://wxaurl.cn/browser-test", lambda route: route.fulfill(body="模拟微信打开目标"))
                await page.get_by_role("link", name="打开小程序预约").click()
                await expect(page).to_have_url("https://wxaurl.cn/browser-test")
                redis.values.clear()
                monkeypatch.setattr(entry, "_wechat_credentials", lambda: ("", ""))
                await page.goto(f"{origin}/api/material-library/shares/{token}/booking")
                await expect(page.locator("#code-error")).to_be_visible()
                await expect(page.get_by_role("button", name="重新加载小程序码")).to_be_visible()
                await page.get_by_role("link", name="打开小程序预约").click()
                await expect(page.get_by_text("微信预约服务未配置，请联系分享人")).to_be_visible()
                await page.get_by_role("link", name="返回预约页，重试或使用小程序码").click()
                await expect(page).to_have_url(f"{origin}/api/material-library/shares/{token}/booking")
                assert not errors
            finally:
                await browser.close()
    finally:
        server.should_exit = True
        await serving
