"""Exercise numeric sorting and person drilldowns on the real Vue pages with controlled responses."""

import os
import re
import socket
from contextlib import suppress
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest
from patchright.async_api import Error as BrowserError
from patchright.async_api import async_playwright, expect


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_overview_sorting_and_person_drilldown_persist_through_filters_export_and_reload():
    origin = os.getenv("PC_E2E_ORIGIN", f"http://{socket.gethostbyname('web')}:5173")
    day = date.today().isoformat()
    employees = [
        {
            "id": f"emp-{index}",
            "case_share_uid": f"mp_emp{index}",
            "name": "同名员工",
            "employee_code": f"E{index:03}",
            "login_account": f"1880000{index:04}",
            "decoration_count": index,
            "review_notes_count": 24 - index,
            "personal_generated_count": (index * 7) % 25,
            "enterprise_generated_count": (index * 11) % 25,
            "case_share_count": index,
        }
        for index in range(25)
    ]
    employees[-1].update(id="user:standalone:25", case_share_uid="standalone:25")
    shares = [
        {
            "id": f"share-{index}",
            "sharer_name": "同名员工",
            "employee_code": "E024",
            "branch": "门店甲",
            "source_platform": "pc" if index % 2 else "mp",
            "source_url": f"{origin}/share/case/token-{index}",
            "shared_at": f"{day}T06:00:00Z",
        }
        for index in range(24)
    ]
    searches, exports = [], []

    async def respond(route):
        url = urlparse(route.request.url)
        params = parse_qs(url.query)
        if url.path == "/api/auth/me":
            data = {"id": 1, "uid": "overview-admin", "username": "管理员", "role": "admin"}
        elif url.path == "/api/auth/check-first-run":
            data = {"first_run": False}
        elif url.path == "/api/employees/traffic":
            data = {
                "employees": employees,
                "total": len(employees),
                "branches": [],
                "departments": [],
                "summary": {
                    "decoration": 300,
                    "review_notes": 300,
                    "personal_generated": 300,
                    "enterprise_generated": 300,
                    "case_shares": 300,
                },
            }
        elif url.path == "/api/case-shares/summary":
            data = {"total": 0, "today": 0}
        elif url.path == "/api/case-shares/export":
            exports.append(params)
            return await route.fulfill(
                body=b"test-xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        elif url.path == "/api/case-shares":
            searches.append(params)
            if params.get("sharer_uid"):
                matched = shares if params["sharer_uid"] == ["standalone:25"] else []
            else:
                matched = shares + [{**shares[0], "id": "other-person", "employee_code": "E023"}]
            matched = [
                row
                for row in matched
                if (not params.get("source") or row["source_platform"] == params["source"][0])
                and (not params.get("keyword") or row["employee_code"] == params["keyword"][0])
            ]
            page, size = int(params.get("page", [1])[0]), int(params.get("page_size", [10])[0])
            data = {"items": matched[(page - 1) * size : page * size], "total": len(matched)}
        else:
            data = {}
        await route.fulfill(json=data)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, args=["--no-sandbox", f"--unsafely-treat-insecure-origin-as-secure={origin}"]
        )
        try:
            context = await browser.new_context(viewport={"width": 1600, "height": 1000}, accept_downloads=True)
            await context.grant_permissions(["local-network-access"], origin=origin)
            await context.add_init_script('localStorage.setItem("user_token", "traffic-drilldown-test")')
            await context.route("**/api/**", respond)
            page = await context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(origin + "/traffic", wait_until="domcontentloaded", timeout=60_000)
            rows = page.locator(".ant-table-tbody > tr.ant-table-row")
            await expect(page.get_by_role("heading", name="数据概览", exact=True)).to_be_visible(timeout=30_000)
            await expect(rows).to_have_count(10, timeout=30_000)
            await expect(rows.first.locator("td").nth(3)).to_have_text("E000")

            for title, column, key in [
                ("装修家居", 4, "decoration_count"),
                ("好评笔记", 5, "review_notes_count"),
                ("生图（个人素材）", 6, "personal_generated_count"),
                ("生图（企业共享）", 7, "enterprise_generated_count"),
                ("案例分享", 8, "case_share_count"),
            ]:
                header = page.get_by_role("columnheader").filter(has_text=title)
                await header.click()
                expected = sorted([row[key] for row in employees], reverse=True)
                await expect(rows.first.locator("td").nth(column)).to_have_text(str(expected[0]))
                assert [
                    int(value) for value in await rows.locator(f"td:nth-child({column + 1})").all_text_contents()
                ] == expected[:10]
                await page.locator(".ant-pagination-item-2").click()
                await expect(rows.first.locator("td").nth(column)).to_have_text(str(expected[10]))
                await header.click()
                await expect(page.locator(".ant-pagination-item-active")).to_have_text("1")
                expected.reverse()
                await expect(rows.first.locator("td").nth(column)).to_have_text(str(expected[0]))
                assert [
                    int(value) for value in await rows.locator(f"td:nth-child({column + 1})").all_text_contents()
                ] == expected[:10]

            # Reset clears controlled sorting, then select a date and drill into an independent PC account.
            await page.get_by_role("button", name=re.compile(r"重\s*置")).click()
            await expect(rows.first.locator("td").nth(3)).to_have_text("E000")
            await page.get_by_placeholder("开始日期").click()
            await page.locator(f'.ant-picker-cell-in-view[title="{day}"]').first.click()
            await page.locator(f'.ant-picker-cell-in-view[title="{day}"]').first.click()
            await page.get_by_role("columnheader").filter(has_text="案例分享").click()
            await expect(rows.first.locator("td").nth(3)).to_have_text("E024")
            await page.screenshot(path="/tmp/traffic-overview-sorted.png", full_page=True)
            await rows.first.locator("td").nth(8).get_by_role("button").click()
            await expect(page).to_have_url(re.compile(r"/case-shares\?"), timeout=30_000)
            await expect(page.locator(".employee-scope")).to_contain_text(
                "同名员工 的案例分享 · 共 24 条", timeout=30_000
            )
            assert searches[-1]["sharer_uid"] == ["standalone:25"]
            assert searches[-1]["start_date"] == searches[-1]["end_date"] == [day]
            await expect(page.get_by_role("button", name="删除", exact=True)).to_have_count(0)
            await page.screenshot(path="/tmp/traffic-case-person.png", full_page=True)
            await page.locator(".ant-pagination-item-2").click()
            await expect(page.locator(".ant-table-tbody > tr.ant-table-row").first).to_contain_text("11")
            assert searches[-1]["page"] == ["2"] and searches[-1]["sharer_uid"] == ["standalone:25"]

            await page.reload(wait_until="domcontentloaded")
            await expect(page.locator(".employee-scope")).to_contain_text("共 24 条")
            keyword = page.get_by_placeholder("分享人姓名、工号、所属门店")
            await keyword.fill("E024")
            await keyword.press("Enter")
            await expect(page).to_have_url(re.compile(r"keyword=E024"))
            await page.locator(".filters .ant-select").click()
            await page.get_by_text("PC端案例分享", exact=True).last.click()
            await expect(page.locator(".employee-scope")).to_contain_text("共 12 条")
            async with page.expect_download():
                await page.get_by_role("button", name="导出", exact=True).click()
            assert exports[-1] == {
                "sharer_uid": ["standalone:25"],
                "keyword": ["E024"],
                "source": ["pc"],
                "start_date": [day],
                "end_date": [day],
            }
            await page.get_by_role("button", name=re.compile(r"重\s*置")).click()
            await expect(page.locator(".employee-scope")).to_contain_text("共 24 条")
            assert searches[-1] == {"sharer_uid": ["standalone:25"], "page": ["1"], "page_size": ["10"]}
            await page.reload(wait_until="domcontentloaded")
            await expect(page.locator(".employee-scope")).to_contain_text("共 24 条")
            assert "start_date" not in searches[-1]
            await page.get_by_role("button", name="查看全部", exact=True).click()
            await expect(page.locator(".employee-scope")).to_have_count(0)
            await expect(page.locator(".ant-pagination-total-text")).to_have_text("共 25 条")
            assert "sharer_uid" not in searches[-1]
            await page.goto(origin + "/traffic", wait_until="domcontentloaded")
            await expect(rows).to_have_count(10, timeout=30_000)
            await rows.first.locator("td").nth(8).get_by_role("button").click()
            await expect(page.locator(".employee-scope")).to_contain_text("共 0 条")
            await expect(page.get_by_role("cell", name="暂无案例分享", exact=True)).to_be_visible()
            assert searches[-1]["sharer_uid"] == ["mp_emp0"]
            await page.screenshot(path="/tmp/traffic-case-drilldown.png", full_page=True)

            # Upgrade an existing browser layout, then preserve explicit sort/layout choices after saving.
            await page.evaluate("""() => localStorage.setItem('traffic-stats-column-settings', JSON.stringify([
                { key: 'case_share_count', visible: true, width: '180', sortable: false, fixed: true },
                { key: 'review_notes_count', visible: false, width: 'auto', sortable: false, fixed: false },
                { key: 'name', visible: true, width: '120', sortable: true, fixed: false }
            ]))""")
            await page.goto(origin + "/traffic", wait_until="domcontentloaded")
            await expect(rows).to_have_count(10, timeout=30_000)
            case_header = page.get_by_role("columnheader").filter(has_text="案例分享")
            await expect(case_header.locator(".ant-table-column-sorters")).to_have_count(1)
            await expect(case_header).to_have_class(re.compile("ant-table-cell-fix-left"))
            await expect(page.get_by_role("columnheader").filter(has_text="好评笔记")).to_have_count(0)
            await case_header.click()
            await expect(rows.first).to_contain_text("E024")

            await page.get_by_role("button", name="列设置", exact=True).click()
            panel = page.locator(".column-settings-panel")
            await panel.get_by_role("switch", name="排序装修家居", exact=True).click()
            await panel.get_by_role("switch", name="排序案例分享", exact=True).click()
            name_row = panel.locator("li").filter(has_text="姓名")
            await name_row.get_by_role("textbox", name="宽度", exact=True).fill("200")
            await name_row.get_by_role("button", name="调整列顺序", exact=True).drag_to(
                panel.locator("li").filter(has_text="案例分享").get_by_role("button", name="调整列顺序", exact=True)
            )
            await panel.get_by_role("button", name=re.compile(r"保\s*存")).click()
            await expect(panel).to_have_count(0)
            await expect(case_header.locator(".ant-table-column-sorters")).to_have_count(0)
            await expect(rows.first).to_contain_text("E000")
            saved = await page.evaluate("JSON.parse(localStorage.getItem('traffic-stats-column-settings'))")
            assert saved["version"] == 1
            assert [column["key"] for column in saved["columns"][:2]] == ["name", "case_share_count"]
            await page.reload(wait_until="domcontentloaded")
            await expect(rows).to_have_count(10, timeout=30_000)
            await expect(case_header.locator(".ant-table-column-sorters")).to_have_count(0)
            await expect(case_header).to_have_class(re.compile("ant-table-cell-fix-left"))
            await expect(
                page.get_by_role("columnheader").filter(has_text="装修家居").locator(".ant-table-column-sorters")
            ).to_have_count(0)
            await expect(page.get_by_role("columnheader").filter(has_text="好评笔记")).to_have_count(0)
            assert next(column for column in saved["columns"] if column["key"] == "name")["width"] == "200"

            await page.get_by_role("button", name="列设置", exact=True).click()
            await panel.get_by_role("button", name=re.compile(r"重\s*置")).click()
            await panel.get_by_role("button", name=re.compile(r"保\s*存")).click()
            await expect(page.get_by_role("columnheader").filter(has_text="好评笔记")).to_have_count(1)
            await expect(case_header.locator(".ant-table-column-sorters")).to_have_count(1)
            await page.screenshot(path="/tmp/traffic-column-settings-integrated.png", full_page=True)
            assert not errors
        except Exception:
            if "page" in locals():
                with suppress(BrowserError):
                    await page.screenshot(path="/tmp/traffic-case-drilldown-failure.png", full_page=True, timeout=3000)
                assert not errors, errors
            raise
        finally:
            await browser.close()
