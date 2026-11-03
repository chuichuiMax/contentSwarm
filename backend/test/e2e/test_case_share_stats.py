"""PC case-share report interactions against controlled API responses."""

import os
import re
import socket
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest
from patchright.async_api import async_playwright, expect


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_case_report_filters_links_export_delete_pagination_and_error():
    origin = os.getenv("PC_E2E_ORIGIN", f"http://{socket.gethostbyname('web')}:5173")
    rows = [
        {
            "id": f"share-{i}",
            "sharer_name": f"操作员工{i}",
            "employee_code": f"000{i}",
            "branch": "分享时门店",
            "source_platform": "pc" if i == 1 else "mp",
            "source_url": f"{origin}/share/case/token-{i}",
            "shared_at": "2026-10-15T06:11:00Z",
        }
        for i in (1, 2)
    ]
    rows.append(
        {
            **rows[0],
            "id": "history",
            "sharer_name": None,
            "employee_code": None,
            "branch": None,
            "source_platform": None,
            "shared_at": None,
        }
    )
    searches, exports, deleted = [], [], []
    failure = False
    permissions = ["customer_lead.view_list", "customer_lead.delete"]

    async def respond(route):
        path = urlparse(route.request.url).path
        params = parse_qs(urlparse(route.request.url).query)
        if path == "/api/auth/me":
            return await route.fulfill(
                json={
                    "id": 1,
                    "uid": "report-employee",
                    "username": "测试员工",
                    "role": "user",
                    "permission_scoped": True,
                    "permissions": permissions,
                }
            )
        if path == "/api/case-shares/summary":
            assert not params
            return await route.fulfill(json={"total": len(rows), "today": 2 - len(deleted)})
        if path == "/api/case-shares/export":
            exports.append(params)
            return await route.fulfill(
                body=b"test-xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        if path == "/api/case-shares":
            if failure:
                return await route.fulfill(status=503, json={"detail": "测试服务不可用"})
            searches.append(params)
            filtered = [
                row
                for row in rows
                if (not params.get("keyword") or row["employee_code"] == params["keyword"][0])
                and (not params.get("source") or row["source_platform"] == params["source"][0])
            ]
            page = int(params.get("page", ["1"])[0])
            size = int(params.get("page_size", ["10"])[0])
            return await route.fulfill(
                json={"items": filtered[(page - 1) * size : page * size], "total": len(filtered)}
            )
        if path.startswith("/api/case-shares/") and route.request.method == "DELETE":
            identifier = path.rsplit("/", 1)[1]
            deleted.append(identifier)
            rows[:] = [row for row in rows if row["id"] != identifier]
            return await route.fulfill(json={"success": True})
        return await route.fulfill(json={})

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, args=["--no-sandbox", f"--unsafely-treat-insecure-origin-as-secure={origin}"]
        )
        try:
            context = await browser.new_context(viewport={"width": 1440, "height": 900})
            await context.grant_permissions(["local-network-access"], origin=origin)
            await context.add_init_script('localStorage.setItem("user_token", "test-report-token")')
            await context.route("**/api/**", respond)
            await context.route("**/share/case/token-*", lambda route: route.fulfill(body="对应的分享快照"))
            page = await context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(f"{origin}/case-shares", wait_until="domcontentloaded", timeout=60_000)
            await expect(page.get_by_role("heading", name="案例分享", exact=True)).to_be_visible(timeout=30_000)
            await expect(page.locator(".summary-card strong").first).to_have_text("3")
            await expect(page.get_by_role("columnheader", name="案例名称", exact=True)).to_have_count(0)
            await expect(page.get_by_role("columnheader", name="分享时间", exact=True)).to_be_visible()
            await expect(page.get_by_text("2026/10/15 14:11", exact=True)).to_have_count(2)
            await expect(page.get_by_role("link", name="来源未知", exact=True)).to_be_visible()
            for width in (1440, 1366, 1280):
                await page.set_viewport_size({"width": width, "height": 900})
                boxes = await page.locator(".filters .ant-form-item-control, .filter-actions").evaluate_all(
                    "els => els.map(el => { const r=el.getBoundingClientRect(); "
                    "return {bottom:r.bottom,right:r.right} })"
                )
                assert max(box["bottom"] for box in boxes) - min(box["bottom"] for box in boxes) <= 2
                assert boxes[-1]["right"] <= width
            await page.set_viewport_size({"width": 1440, "height": 900})
            await page.get_by_role("button", name="数据统计", exact=True).click()
            menu = page.get_by_role("link", name="案例分享", exact=True)
            await expect(menu).to_be_visible()
            await menu.click()
            await page.screenshot(path="/tmp/case-share-stats-light.png", full_page=True)
            await page.evaluate("localStorage.setItem('theme', 'dark')")
            await page.reload(wait_until="domcontentloaded")
            await expect(page.locator(".summary-card strong").first).to_have_text("3")
            await page.get_by_role("button", name="数据统计", exact=True).click()
            await expect(page.locator("html")).to_have_class(re.compile("dark"))
            await page.screenshot(path="/tmp/case-share-stats-dark.png", full_page=True)
            await page.evaluate("localStorage.setItem('theme', 'light')")
            await page.reload(wait_until="domcontentloaded")
            await expect(page.locator(".summary-card strong").first).to_have_text("3")
            for label, token in (("PC端案例分享", "token-1"), ("小程序案例分享", "token-2")):
                async with context.expect_page() as popup_event:
                    await page.get_by_role("link", name=label, exact=True).click()
                popup = await popup_event.value
                await expect(popup).to_have_url(f"{origin}/share/case/{token}")
                await popup.close()
            keyword = page.get_by_placeholder("分享人姓名、工号、所属门店")
            await keyword.fill("0001")
            await page.get_by_role("button", name="搜索案例分享", exact=True).click()
            await expect(page.get_by_text("操作员工2", exact=True)).to_have_count(0)
            await expect(page.locator(".summary-card strong").first).to_have_text("3")
            await page.locator(".filters .ant-select").click()
            await page.locator(".ant-select-item-option-content").get_by_text("PC端案例分享", exact=True).click()
            start, end = date.today().replace(day=1).isoformat(), date.today().replace(day=24).isoformat()
            await page.get_by_placeholder("开始日期").click()
            await page.locator(f'.ant-picker-cell-in-view[title="{start}"]').click()
            async with page.expect_response(
                lambda response: "/api/case-shares?" in response.url and "end_date=" in response.url
            ):
                await page.locator(f'.ant-picker-cell-in-view[title="{end}"]').click()
            async with page.expect_download() as download:
                await page.get_by_role("button", name="导出", exact=True).click()
            assert (await download.value).suggested_filename == "案例分享.xlsx"
            assert exports[-1] == {"keyword": ["0001"], "source": ["pc"], "start_date": [start], "end_date": [end]}
            await page.get_by_role("button", name=re.compile(r"重\s*置")).click()
            await expect(page.get_by_text("操作员工2", exact=True)).to_be_visible()
            await keyword.fill("0002")
            await keyword.press("Enter")
            await expect(page.get_by_text("操作员工1", exact=True)).to_have_count(0)
            await page.locator(".filters .ant-input-clear-icon").click()
            await expect(page.get_by_text("操作员工1", exact=True)).to_be_visible()
            await page.get_by_role("button", name="删除", exact=True).first.click()
            await expect(page.get_by_text(re.compile("删除后该分享链接将失效"))).to_be_visible()
            await page.get_by_role("button", name=re.compile(r"取\s*消")).click()
            assert not deleted
            await page.get_by_role("button", name="删除", exact=True).first.click()
            await page.locator(".ant-modal-confirm-btns .ant-btn-dangerous").click()
            await expect(page.get_by_text("操作员工1", exact=True)).to_have_count(0)
            await expect(page.locator(".summary-card strong").first).to_have_text("2")
            await expect(page.locator(".summary-card strong").nth(1)).to_have_text("1")
            # Losing delete permission hides the action while keeping read/export available.
            permissions[:] = ["customer_lead.view_list"]
            await page.reload(wait_until="domcontentloaded")
            await expect(page.get_by_text("操作员工2", exact=True)).to_be_visible()
            await expect(page.get_by_role("button", name="删除", exact=True)).to_have_count(0)
            permissions.append("customer_lead.delete")
            rows.extend({**rows[0], "id": f"extra-{i}", "sharer_name": f"分页员工{i}"} for i in range(9))
            await page.reload(wait_until="domcontentloaded")
            await page.get_by_title("2", exact=True).click()
            await expect(page.get_by_text("分页员工8", exact=True)).to_be_visible()
            await page.get_by_role("button", name="删除", exact=True).first.click()
            await page.locator(".ant-modal-confirm-btns .ant-btn-dangerous").click()
            await expect(page.get_by_text("操作员工2", exact=True)).to_be_visible()
            assert searches[-1]["page"] == ["1"]
            await page.set_viewport_size({"width": 960, "height": 900})
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            failure = True
            await page.get_by_role("button", name="刷新", exact=True).click()
            await expect(page.get_by_text("案例分享暂时无法加载", exact=True)).to_be_visible()
            await expect(page.get_by_text("暂无案例分享", exact=True)).to_have_count(0)
            assert not errors
        except Exception as exc:
            await page.screenshot(path="/tmp/case-share-stats-failure.png", full_page=True)
            exc.add_note(
                f"searches={searches[-5:]}, deleted={deleted}, body={(await page.locator('body').inner_text())[-1600:]}"
            )
            raise
        finally:
            await browser.close()
