"""PC 浏览器实际下载与日期选择、分页、清空验收。"""

import io
import json
import re
import socket
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import load_workbook
from patchright.async_api import async_playwright, expect
from PIL import Image

from test.integration.api.test_employee_export_dates import export_date_users as export_date_users


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_pc_export_and_both_rough_gallery_date_ranges(export_date_users):
    png = io.BytesIO()
    Image.new("RGB", (32, 24), "#4A7BF7").save(png, format="PNG")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, args=["--no-sandbox", f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}"]
        )
        try:
            context = await browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
            await context.add_init_script(
                f'localStorage.setItem("user_token", {json.dumps(export_date_users["token"])})'
            )
            page = await context.new_page()
            # 本测试验证数据查询与日期交互，缩略图使用内存图片，不写入对象存储。
            await page.route(
                re.compile(r"/api/material-library/items/.+/thumbnail"),
                lambda route: route.fulfill(body=png.getvalue(), content_type="image/png"),
            )
            await page.goto("http://localhost:5173/model-manage/employees")
            await expect(page.get_by_role("button", name="导出", exact=True)).to_be_visible(timeout=15000)
            await page.get_by_placeholder("员工编码、姓名、登录账号、分部、部门").fill("no-such-employee")
            async with page.expect_response(lambda r: "/api/employees?" in r.url and r.status == 200):
                await page.get_by_placeholder("员工编码、姓名、登录账号、分部、部门").press("Enter")
            async with page.expect_download() as download_info:
                await page.get_by_role("button", name="导出", exact=True).click()
            download = await download_info.value
            assert download.suggested_filename == "员工管理.xlsx"
            rows = list(load_workbook(io.BytesIO(Path(await download.path()).read_bytes())).active.values)
            assert any(row[1] == export_date_users["uid"] and row[10] == 28 for row in rows[1:])

            await page.goto("http://localhost:5173/materials/images")
            await page.locator("button.gallery-open").filter(has_text="毛坯房图库").first.click()
            for scope, path in (("private", "/my-materials/rough"), ("enterprise", "/items")):
                await expect(page.get_by_placeholder("开始日期")).to_be_visible()
                await page.clock.set_fixed_time(datetime(2026, 10, 5))
                await page.get_by_placeholder("开始日期").click()
                await page.locator('.ant-picker-cell[title="2026-10-01"]').first.click()
                async with page.expect_response(
                    lambda r: (
                        path in r.url
                        and "date_from=2026-10-01" in r.url
                        and "date_to=2026-10-01" in r.url
                        and r.status == 200
                    )
                ) as filtered:
                    await page.locator('.ant-picker-cell[title="2026-10-01"]').first.click()
                assert (await (await filtered.value).json())["total"] == 26
                async with page.expect_response(
                    lambda r: (
                        path in r.url and "page=2" in r.url and "date_from=2026-10-01" in r.url and r.status == 200
                    )
                ):
                    await page.locator(".ant-pagination-item-2").click()
                await page.locator(".ant-picker").hover()
                async with page.expect_response(
                    lambda r: path in r.url and "page=1" in r.url and "date_from=" not in r.url and r.status == 200
                ) as cleared:
                    await page.locator(".ant-picker-clear").click()
                assert (await (await cleared.value).json())["total"] == 28
                await page.screenshot(path=f"/tmp/rough-dates-{scope}.png", full_page=True)
                if scope == "private":
                    await page.get_by_role("button", name="返回我的图库").click()
                    await page.get_by_text("企业共享", exact=True).click()
                    await page.locator("button.gallery-open").filter(has_text="毛坯房图库").first.click()
                    await page.locator("button.gallery-open").filter(has_text="日期测试毛坯图库").click()
            await context.close()
        finally:
            await browser.close()
