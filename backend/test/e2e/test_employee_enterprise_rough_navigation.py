"""Exercise employee column navigation against the real Vue page with controlled API responses."""

import io
import re
import socket
from urllib.parse import parse_qs, urlparse

import pytest
from patchright.async_api import async_playwright, expect
from PIL import Image


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_employee_columns_preserve_scope_pagination_refresh_and_preview():
    png = io.BytesIO()
    Image.new("RGB", (10, 10), "blue").save(png, format="PNG")
    requests = []
    employees = [
        {
            "id": "user:alice",
            "source": "user",
            "employee_code": "A1",
            "name": "欧美玲",
            "login_account": "13800000001",
            "current_branch": "长沙袁家岭店",
            "current_department": "工程交付部/工程交付二组",
            "role": "家装顾问",
            "enabled": True,
            "login_port": ["pc"],
            "rough_image_count": 7,
            "enterprise_rough_image_count": 25,
        },
        {
            "id": "user:bob",
            "source": "user",
            "employee_code": "B1",
            "name": "员工乙",
            "login_account": "bob",
            "role": "普通用户",
            "enabled": True,
            "login_port": ["pc"],
            "rough_image_count": 0,
            "enterprise_rough_image_count": 0,
        },
    ]

    async def respond(route):
        url = urlparse(route.request.url)
        params = parse_qs(url.query)
        path = url.path
        requests.append((path, params))
        if path.endswith("/thumbnail"):
            return await route.fulfill(body=png.getvalue(), content_type="image/png")
        if path == "/api/auth/me":
            data = {"id": 1, "uid": "admin", "username": "管理员", "role": "superadmin"}
        elif path == "/api/auth/check-first-run":
            data = {"first_run": False}
        elif path == "/api/employees":
            data = {"employees": employees, "total": 2}
        elif path == "/api/roles":
            data = {"roles": []}
        elif path == "/api/material-library/remote-sync/status":
            data = {"job": None}
        elif path == "/api/material-library/categories":
            enterprise = "uploader_employee_id" in params
            reference = params.get("uploader_employee_id", params.get("employee_id", ["user:alice"]))[0]
            employee = next(row for row in employees if row["id"] == reference)
            gallery_id = "shared-rough" if enterprise else "private-rough"
            data = {
                "categories": [
                    {
                        "id": gallery_id,
                        "code": gallery_id,
                        "name": "毛坯房图库",
                        "parent_id": None,
                        "visibility": "enterprise" if enterprise else "private",
                        "image_design_role": "rough",
                        "personal_folder": None if enterprise else "rough",
                        "can_manage": True,
                        "can_upload": True,
                        "count": 0,
                    }
                ],
                "target_employee": employee,
                "enterprise_rough_category_id" if enterprise else "personal_rough_category_id": gallery_id,
            }
        elif path == "/api/material-library/items":
            enterprise = "uploader_employee_id" in params
            reference = params.get("uploader_employee_id", params.get("employee_id", ["user:alice"]))[0]
            page = int(params.get("page", [1])[0])
            total = (25 if enterprise else 7) if reference == "user:alice" else 0
            prefix = "企业甲" if enterprise else "个人甲"
            data = {
                "items": [
                    {
                        "id": f"{prefix}-{i}",
                        "name": f"{prefix}-{i}",
                        "category_name": "毛坯房图库",
                        "uploaded_by_name": "员工甲",
                        "can_manage": True,
                        "width": 10,
                        "height": 10,
                        "file_size": 50,
                    }
                    for i in range((page - 1) * 24, min(page * 24, total))
                ],
                "total": total,
            }
        else:
            data = {}
        await route.fulfill(json=data)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}",
            ],
        )
        try:
            context = await browser.new_context(viewport={"width": 1600, "height": 1000})
            await context.add_init_script('localStorage.setItem("user_token", "test-ui-token")')
            page = await context.new_page()
            await page.route("**/api/**", respond)
            await page.goto(
                "http://localhost:5173/model-manage/employees", wait_until="domcontentloaded", timeout=60_000
            )
            private_header = page.get_by_role("columnheader", name=re.compile(r"毛坯图上传数\s*（我的素材）"))
            enterprise_header = page.get_by_role("columnheader", name=re.compile(r"毛坯图上传数\s*（企业共享）"))
            await expect(private_header).to_be_visible(timeout=30_000)
            await expect(enterprise_header).to_be_visible()
            for width in (1920, 1366, 1024):
                await page.set_viewport_size({"width": width, "height": 1000})
                for label, minimum_width in (("姓名", 80), ("角色", 96)):
                    header = page.get_by_role("columnheader", name=label, exact=True)
                    assert minimum_width <= (await header.bounding_box())["width"] <= minimum_width + 8
                    cell = page.locator('tr[data-row-key="user:alice"] > td').nth(
                        await header.evaluate("el => el.cellIndex")
                    )
                    assert await cell.evaluate("el => el.scrollWidth <= el.clientWidth")
                    assert (
                        await cell.evaluate(
                            "el => { const range = document.createRange(); range.selectNodeContents(el); "
                            "return range.getClientRects().length; }"
                        )
                        == 1
                    )
                for header, scope in ((private_header, "我的素材"), (enterprise_header, "企业共享")):
                    assert await header.inner_text() == f"毛坯图上传数\n（{scope}）"
                    assert await header.evaluate(
                        "el => { const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT); "
                        "let node; while ((node = walker.nextNode())) { "
                        "if (!node.textContent.trim()) continue; const range = document.createRange(); "
                        "range.selectNodeContents(node); if (range.getClientRects().length !== 1) return false; "
                        "} return true; }"
                    )
                if width == 1024:
                    assert await page.locator(".employee-table .ant-table-content").evaluate(
                        "el => el.scrollWidth > el.clientWidth && getComputedStyle(el).overflowX === 'auto'"
                    )
                    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            await page.set_viewport_size({"width": 1600, "height": 1000})
            await page.get_by_role("button", name="25", exact=True).click()
            await expect(page).to_have_url(
                "http://localhost:5173/materials/images?employee_id=user:alice&gallery=rough&scope=enterprise",
                timeout=30_000,
            )
            await expect(
                page.get_by_text("本页仅显示该员工上传到企业共享毛坯房图库的图片。", exact=False)
            ).to_be_visible()
            await expect(page.locator("article.material-card")).to_have_count(24)
            await expect(page.get_by_alt_text("企业甲-0")).to_be_visible()
            await expect(page.get_by_alt_text("企业甲-0")).to_have_js_property("naturalWidth", 10)
            await page.locator(".ant-pagination-item-2").click()
            await expect(page.locator("article.material-card")).to_have_count(1)
            await expect(page.get_by_alt_text("企业甲-24")).to_be_visible()
            await page.reload(wait_until="domcontentloaded", timeout=60_000)
            await expect(page.locator("article.material-card")).to_have_count(24)
            assert all("employee_id" not in p for path, p in requests if path.endswith("/thumbnail"))

            await page.go_back(wait_until="domcontentloaded")
            await page.get_by_role("button", name="7", exact=True).click()
            await expect(page).to_have_url(
                "http://localhost:5173/materials/images?employee_id=user:alice&gallery=rough"
            )
            await expect(page.locator("article.material-card")).to_have_count(7)
            await expect(page.get_by_alt_text("个人甲-0")).to_be_visible()
            # Change scope and employee without reloading the component.
            await page.evaluate(
                "url => { const state = {...history.state, current: url}; "
                "history.pushState(state, '', url); "
                "dispatchEvent(new PopStateEvent('popstate', {state})); }",
                "/materials/images?employee_id=user:bob&gallery=rough&scope=enterprise",
            )
            await expect(page.locator("article.material-card")).to_have_count(0)
            await expect(page.get_by_role("heading", name="员工乙 / 毛坯房图库")).to_be_visible()
            item_requests = [p for path, p in requests if path == "/api/material-library/items"]
            assert any(
                p.get("uploader_employee_id") == ["user:alice"] and p.get("page") == ["2"] for p in item_requests
            )
            assert item_requests[-1]["uploader_employee_id"] == ["user:bob"]
            assert "employee_id" not in item_requests[-1]
        finally:
            await browser.close()
