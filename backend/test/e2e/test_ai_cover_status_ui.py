"""Real Vue management UI against isolated HTTP/PostgreSQL cover endpoints."""

import io
import os
import re
import socket
from contextlib import suppress
from urllib.parse import urlparse

import pytest
from PIL import Image
from patchright.async_api import Error as BrowserError
from patchright.async_api import async_playwright, expect

from test.integration.api.test_ai_cover_status_api import BASE, cover_api as cover_api, cover_upload
from test.integration.api.test_case_visit_router import visit_api as visit_api

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_management_status_switch_and_create_edit_radios_use_persisted_state(cover_api):
    origin = os.getenv("PC_E2E_ORIGIN", f"http://{socket.gethostbyname('web')}:5173")
    admin = cover_api.tokens[5]
    created = await cover_api.client.post(
        BASE + "/manage",
        headers=admin,
        data={"name": "状态验收封面", "prompt": "测试提示词", "content_type_names": ["装修案例分享"]},
        files=cover_upload("e2e-status.png"),
    )
    assert created.status_code == 201, created.text
    variant = created.json()["cover"]["variant"]
    fail_next_status = False
    preview = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(preview, "PNG")

    async def respond(route):
        nonlocal fail_next_status
        url = urlparse(route.request.url)
        if url.path.endswith("/preview.png"):
            await route.fulfill(body=preview.getvalue(), content_type="image/png")
            return
        if url.path.startswith(BASE):
            if fail_next_status and url.path.endswith("/status"):
                fail_next_status = False
                await route.fulfill(status=500, json={"detail": "测试保存失败"})
                return
            response = await cover_api.client.request(
                route.request.method,
                url.path + ("?" + url.query if url.query else ""),
                headers={
                    **{
                        key: value
                        for key, value in route.request.headers.items()
                        if key.lower() not in {"authorization", "host", "content-length"}
                    },
                    **admin,
                },
                content=route.request.post_data_buffer,
            )
            await route.fulfill(status=response.status_code, body=response.content, content_type="application/json")
            return
        if url.path == "/api/auth/me":
            data = {"id": 5, "uid": "visitor-test-user-5", "username": "管理员", "role": "superadmin"}
        elif url.path == "/api/auth/check-first-run":
            data = {"first_run": False}
        elif url.path == "/api/content-types":
            data = {"content_types": [{"id": "test-type", "name": "装修案例分享"}]}
        else:
            data = {}
        await route.fulfill(json=data)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, args=["--no-sandbox", f"--unsafely-treat-insecure-origin-as-secure={origin}"]
        )
        try:
            context = await browser.new_context(viewport={"width": 1600, "height": 1000})
            await context.grant_permissions(["local-network-access"], origin=origin)
            await context.add_init_script('localStorage.setItem("user_token", "cover-status-test")')
            await context.route("**/api/**", respond)
            page = await context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(origin + "/cover-manage", wait_until="domcontentloaded", timeout=60_000)
            rows = page.locator(".ant-table-tbody > tr.ant-table-row")
            row = rows.filter(has_text="状态验收封面")
            switch = row.get_by_role("switch", name="状态验收封面状态")
            await expect(switch).to_be_checked(timeout=30_000)
            smart_row = rows.filter(has_text="智能随机适配")
            await expect(smart_row).to_have_count(1)
            await expect(smart_row.get_by_role("switch")).to_have_count(0)
            await switch.click()
            await expect(switch).not_to_be_checked()
            await page.reload(wait_until="domcontentloaded")
            await expect(switch).not_to_be_checked()
            # A failed save leaves both the switch and database at the previous value.
            fail_next_status = True
            await switch.click()
            await expect(page.locator(".ant-message-error")).to_be_visible()
            await expect(switch).not_to_be_checked()
            response = await cover_api.client.get(BASE + "/manage", headers=admin)
            assert next(item for item in response.json()["covers"] if item["variant"] == variant)["enabled"] is False

            await row.get_by_role("button", name=re.compile(r"编\s*辑")).click()
            dialog = page.get_by_role("dialog")
            await expect(dialog.get_by_role("radio", name="禁用", exact=True)).to_be_checked()
            await dialog.get_by_role("radio", name="启用", exact=True).check()
            await dialog.get_by_role("button", name=re.compile(r"确\s*定")).click()
            await expect(dialog).to_have_count(0)
            await expect(switch).to_be_checked()

            await smart_row.get_by_role("button", name=re.compile(r"编\s*辑")).click()
            dialog = page.get_by_role("dialog")
            await expect(dialog.get_by_role("radio")).to_have_count(0)
            await dialog.get_by_role("button", name=re.compile(r"取\s*消")).click()
            await expect(dialog).to_have_count(0)

            await page.get_by_role("button", name="新增封面", exact=True).click()
            dialog = page.get_by_role("dialog")
            await expect(dialog.get_by_role("radio", name="启用", exact=True)).to_be_checked()
            await dialog.get_by_placeholder("请输入封面名称").fill("新增禁用封面")
            await dialog.get_by_placeholder("请输入封面提示词").fill("新增提示词")
            await dialog.locator(".ant-select-selector").click()
            await (
                page.locator(".ant-select-dropdown .ant-select-item-option-content")
                .filter(has_text="装修案例分享")
                .click()
            )
            await dialog.get_by_text("上传封面模板", exact=True).click()
            await dialog.locator('input[type="file"]').set_input_files(
                {"name": "e2e-disabled.png", "mimeType": "image/png", "buffer": preview.getvalue()}
            )
            await dialog.get_by_role("radio", name="禁用", exact=True).check()
            await dialog.get_by_role("button", name=re.compile(r"确\s*定")).click()
            await expect(dialog).to_have_count(0)
            await expect(rows.filter(has_text="新增禁用封面").get_by_role("switch")).not_to_be_checked()
            await page.screenshot(path="/tmp/ai-cover-status-management.png", full_page=True)
            assert not errors, errors
        except Exception:
            if "page" in locals():
                with suppress(BrowserError):
                    await page.screenshot(path="/tmp/ai-cover-status-failure.png", full_page=True, timeout=3000)
            raise
        finally:
            await browser.close()
