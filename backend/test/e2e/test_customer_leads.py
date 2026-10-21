"""Exercise the real PC page against controlled lead API responses."""

import io
import os
import re
import socket
from urllib.parse import urlparse

import pytest
from patchright.async_api import async_playwright, expect
from PIL import Image


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_report_full_phone_source_new_tab_confirm_delete_and_error_state():
    port = os.getenv("PC_E2E_PORT", "5173")
    origin = os.getenv("PC_E2E_ORIGIN", f"http://{socket.gethostbyname('web')}:{port}")
    data = io.BytesIO()
    Image.new("RGB", (32, 32), "royalblue").save(data, "WEBP")
    deleted = []
    failure = False
    rows = [
        {
            "id": f"lead-{index}",
            "nickname": f"测试客户{index}",
            "phone": f"1990000000{index}",
            "avatar_url": f"/api/customer-leads/lead-{index}/avatar",
            "source_title": f"原始案例{index}",
            "source_url": f"{origin}/share/case/token-{index}",
            "acquired_at": "2026-10-15T06:11:00Z",
        }
        for index in (1, 2)
    ]
    rows.append({
        **rows[0], "id": "lead-phone-only", "nickname": None,
        "phone": "19900000003", "avatar_url": None, "acquired_at": "2026-10-16T06:11:00Z",
        "source_title": "手机号预约案例",
    })

    async def respond(route):
        path = urlparse(route.request.url).path
        if path == "/api/auth/me":
            return await route.fulfill(
                json={
                    "id": 1,
                    "uid": "employee-a",
                    "username": "员工甲",
                    "role": "user",
                    "department_id": 1,
                    "permission_scoped": True,
                    "permissions": ["customer_lead.view_list", "customer_lead.delete"],
                }
            )
        if path == "/api/customer-leads":
            if failure:
                return await route.fulfill(status=503, json={"detail": "测试服务暂不可用"})
            return await route.fulfill(json={"items": rows, "total": len(rows)})
        if path.endswith("/avatar"):
            assert route.request.headers.get("authorization") == "Bearer test-lead-token"
            return await route.fulfill(body=data.getvalue(), content_type="image/webp")
        if path.startswith("/api/customer-leads/") and route.request.method == "DELETE":
            lead_id = path.rsplit("/", 1)[1]
            deleted.append(lead_id)
            rows[:] = [row for row in rows if row["id"] != lead_id]
            return await route.fulfill(json={"success": True})
        return await route.fulfill(json={})

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                f"--unsafely-treat-insecure-origin-as-secure={origin}",
            ],
        )
        try:
            context = await browser.new_context(viewport={"width": 1440, "height": 900})
            await context.grant_permissions(["local-network-access"], origin=origin)
            await context.add_init_script('localStorage.setItem("user_token", "test-lead-token")')
            await context.route("**/api/**", respond)
            await context.route("**/share/case/token-*", lambda route: route.fulfill(body="原始案例分享快照"))
            page = await context.new_page()
            errors = []
            traffic = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda event: errors.append(event.text) if event.type == "error" else None)
            page.on("requestfailed", lambda request: traffic.append((request.url, request.failure)))
            page.on("response", lambda response: traffic.append((response.url, response.status)))
            await page.goto(f"{origin}/customer-leads", wait_until="domcontentloaded", timeout=60_000)
            try:
                await expect(page.get_by_role("heading", name="客资报表")).to_be_visible(timeout=30_000)
            except AssertionError:
                await page.screenshot(path="/tmp/case-visit-debug.png", full_page=True)
                pytest.fail(
                    f"Report did not open: url={page.url}, errors={errors}, "
                    f"body={(await page.locator('body').inner_text())[:1500]}, traffic={traffic[-15:]}"
                )
            await expect(page.get_by_text("19900000001", exact=True)).to_be_visible()
            await expect(page.get_by_text("预约客户", exact=True)).to_be_visible()
            await expect(page.get_by_text("19900000003", exact=True)).to_be_visible()
            await expect(page.get_by_text("2026/10/15 14:11", exact=True)).to_have_count(2)
            await expect(page.get_by_alt_text("客户头像")).to_have_count(2)
            await expect(page.get_by_alt_text("客户头像").first).to_have_js_property("naturalWidth", 32)
            await page.screenshot(path="/tmp/case-visit-report.png", full_page=True)
            link = page.get_by_role("link", name="原始案例1", exact=True)
            async with context.expect_page() as popup_event:
                await link.click()
            popup = await popup_event.value
            await expect(popup).to_have_url(f"{origin}/share/case/token-1")
            await popup.close()
            await page.get_by_role("button", name="删除", exact=True).first.click()
            await page.get_by_role("button", name=re.compile(r"取\s*消")).click()
            assert deleted == []
            await page.get_by_role("button", name="删除", exact=True).first.click()
            await page.locator(".ant-modal-confirm-btns .ant-btn-dangerous").click()
            await expect(page.get_by_text("19900000001", exact=True)).to_have_count(0)
            assert deleted == ["lead-1"]
            await expect(page.get_by_text("19900000002", exact=True)).to_be_visible()
            await page.set_viewport_size({"width": 960, "height": 900})
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            failure = True
            await page.get_by_role("button", name="刷新", exact=True).click()
            await expect(page.get_by_text("客资暂时无法加载", exact=True)).to_be_visible()
            await expect(page.get_by_text("暂无参观预约", exact=True)).to_have_count(0)
        finally:
            await browser.close()
