"""Exercise the real PC page against controlled lead API responses."""

import os
import re
import socket
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest
from patchright.async_api import async_playwright, expect


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_report_masked_phone_filters_export_confirm_delete_and_error_state():
    port = os.getenv("PC_E2E_PORT", "5173")
    origin = os.getenv("PC_E2E_ORIGIN", f"http://{socket.gethostbyname('web')}:{port}")
    deleted = []
    searches = []
    exports = []
    failure = False
    branches = ["长沙门店"]
    rows = [
        {
            "id": f"lead-{index}",
            "phone_masked": f"199****000{index}",
            "sharer_name": f"分享员工{index}",
            "employee_code": f"000{index}",
            "branch": "长沙门店",
            "source_platform": "pc" if index == 1 else "mp",
            "source_title": f"原始案例{index}",
            "source_url": f"{origin}/share/case/token-{index}",
            "acquired_at": "2026-10-15T06:11:00Z",
        }
        for index in (1, 2)
    ]
    rows.append(
        {
            **rows[0],
            "id": "lead-phone-only",
            "sharer_name": None,
            "phone_masked": "199****0003",
            "employee_code": None,
            "branch": None,
            "acquired_at": "2026-10-16T06:11:00Z",
            "source_title": "手机号预约案例",
        }
    )

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
            params = parse_qs(urlparse(route.request.url).query)
            searches.append(params)
            filtered = (
                rows
                if not params.get("keyword")
                else [row for row in rows if row["employee_code"] == params["keyword"][0]]
            )
            return await route.fulfill(json={"items": filtered, "total": len(filtered)})
        if path == "/api/customer-leads/summary":
            assert not urlparse(route.request.url).query
            return await route.fulfill(json={"total": 128, "today": 10, "branches": branches})
        if path.endswith("/phone"):
            assert route.request.headers.get("authorization") == "Bearer test-lead-token"
            return await route.fulfill(json={"phone": "19900000001"})
        if path == "/api/customer-leads/export":
            exports.append(parse_qs(urlparse(route.request.url).query))
            return await route.fulfill(
                body=b"test-xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
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
                await expect(page.get_by_role("heading", name="客资列表")).to_be_visible(timeout=30_000)
            except AssertionError:
                await page.screenshot(path="/tmp/case-visit-debug.png", full_page=True)
                pytest.fail(
                    f"Report did not open: url={page.url}, errors={errors}, "
                    f"body={(await page.locator('body').inner_text())[:1500]}, traffic={traffic[-15:]}"
                )
            await expect(page.get_by_text("19900000001", exact=True)).to_have_count(0)
            await expect(page.get_by_text("199****0001", exact=True)).to_be_visible()
            await expect(page.get_by_text("分享员工1", exact=True)).to_be_visible()
            await expect(page.get_by_text("0001", exact=True)).to_be_visible()
            await expect(page.get_by_role("button", name=re.compile(r"查\s*询"))).to_have_count(0)
            for width in (1440, 1366, 1280):
                await page.set_viewport_size({"width": width, "height": 900})
                boxes = await page.locator(".filters .ant-form-item-control, .filter-actions").evaluate_all(
                    "els => els.map(el => { const r = el.getBoundingClientRect(); "
                    "return { bottom: r.bottom, right: r.right }; })"
                )
                assert max(box["bottom"] for box in boxes) - min(box["bottom"] for box in boxes) <= 2
                assert boxes[-1]["right"] <= width
            await page.set_viewport_size({"width": 1440, "height": 900})
            search_box = page.locator(".filters .ant-input-affix-wrapper")
            search_button = page.get_by_role("button", name="搜索客资", exact=True)
            box, button = await search_box.bounding_box(), await search_button.bounding_box()
            assert box["x"] + box["width"] - 48 <= button["x"]
            assert button["x"] + button["width"] <= box["x"] + box["width"]
            await expect(page.get_by_role("columnheader", name="客户姓名", exact=True)).to_have_count(0)
            await expect(page.get_by_role("columnheader", name="头像", exact=True)).to_have_count(0)
            await page.get_by_role("button", name="查看完整号码", exact=True).first.click()
            await expect(page.get_by_text("19900000001", exact=True)).to_be_visible()
            await page.get_by_role("button", name="隐藏完整号码", exact=True).click()
            await expect(page.get_by_text("19900000001", exact=True)).to_have_count(0)
            await expect(page.get_by_text("2026/10/15 14:11", exact=True)).to_have_count(2)
            await expect(page.get_by_alt_text("客户头像")).to_have_count(0)
            await page.get_by_placeholder("电话号码、分享人姓名、工号、所属门店").fill("0001")
            await search_button.click()
            try:
                await expect(page.get_by_text("分享员工2", exact=True)).to_have_count(0)
            except AssertionError:
                await page.screenshot(path="/tmp/pc-leads-search-debug.png", full_page=True)
                body = (await page.locator("body").inner_text())[-1800:]
                pytest.fail(f"searches={searches}, errors={errors}, body={body}")
            await expect(page.locator(".summary-card strong").first).to_have_text("128")
            await expect(page.locator(".summary-card strong").nth(1)).to_have_text("10")
            async with page.expect_download() as download_event:
                await page.get_by_role("button", name="导出", exact=True).click()
            assert (await download_event.value).suggested_filename == "客资列表.xlsx"
            assert exports == [{"keyword": ["0001"]}]
            assert searches[-1]["keyword"] == ["0001"]
            await page.locator(".filters .ant-input-clear-icon").click()
            await expect(page.get_by_text("分享员工2", exact=True)).to_be_visible()
            assert "keyword" not in searches[-1]
            keyword = page.get_by_placeholder("电话号码、分享人姓名、工号、所属门店")
            await keyword.fill("0001")
            await keyword.press("Enter")
            await expect(page.get_by_text("分享员工2", exact=True)).to_have_count(0)
            await page.locator(".filters .ant-select").first.click()
            async with page.expect_response(
                lambda response: "/api/customer-leads?" in response.url and "branch=" in response.url
            ):
                await page.locator(".ant-select-item-option-content").get_by_text("长沙门店", exact=True).click()
            await page.locator(".filters .ant-select").nth(1).click()
            async with page.expect_response(
                lambda response: "/api/customer-leads?" in response.url and "source=pc" in response.url
            ):
                await page.locator(".ant-select-item-option-content").get_by_text("PC端-案例分享", exact=True).click()
            start_date = date.today().replace(day=1).isoformat()
            end_date = date.today().replace(day=24).isoformat()
            await page.get_by_placeholder("开始日期").click()
            await page.locator(f'.ant-picker-cell-in-view[title="{start_date}"]').click()
            async with page.expect_response(
                lambda response: "/api/customer-leads?" in response.url and f"end_date={end_date}" in response.url
            ):
                await page.locator(f'.ant-picker-cell-in-view[title="{end_date}"]').click()
            async with page.expect_download():
                await page.get_by_role("button", name="导出", exact=True).click()
            assert exports[-1] == {
                "keyword": ["0001"],
                "branch": ["长沙门店"],
                "source": ["pc"],
                "start_date": [start_date],
                "end_date": [end_date],
            }
            await expect(page.locator(".summary-card strong").first).to_have_text("128")
            await page.get_by_role("button", name=re.compile(r"重\s*置")).click()
            await expect(page.get_by_text("分享员工2", exact=True)).to_be_visible()
            await page.screenshot(path="/tmp/case-visit-report.png", full_page=True)
            link = page.get_by_role("link", name="PC端-案例分享", exact=True).first
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
            await expect(page.get_by_text("199****0002", exact=True)).to_be_visible()
            await page.set_viewport_size({"width": 960, "height": 900})
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            branches = []
            await page.get_by_role("button", name="刷新", exact=True).click()
            await page.locator(".filters .ant-select").first.click()
            await expect(page.get_by_text("暂无可选门店", exact=True)).to_be_visible()
            await page.keyboard.press("Escape")
            failure = True
            await page.get_by_role("button", name="刷新", exact=True).click()
            await expect(page.get_by_text("客资暂时无法加载", exact=True)).to_be_visible()
            await expect(page.get_by_text("暂无客资", exact=True)).to_have_count(0)
        finally:
            await browser.close()
