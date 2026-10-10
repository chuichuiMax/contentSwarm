"""Exercise employee column navigation against the real Vue page with controlled API responses."""

import io
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
            "name": "员工甲",
            "login_account": "alice",
            "role": "普通用户",
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
            await expect(page.get_by_role("columnheader", name="毛坯图上传数（我的素材）")).to_be_visible()
            await expect(page.get_by_role("columnheader", name="毛坯图上传数（企业共享）")).to_be_visible()
            await page.get_by_role("button", name="25", exact=True).click()
            await expect(page).to_have_url(
                "http://localhost:5173/materials/images?employee_id=user:alice&gallery=rough&scope=enterprise"
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

            # Change scope and employee without reloading the component.
            await page.evaluate(
                "url => { const state = {...history.state, current: url}; "
                "history.pushState(state, '', url); "
                "dispatchEvent(new PopStateEvent('popstate', {state})); }",
                "/materials/images?employee_id=user:alice&gallery=rough",
            )
            await expect(page.locator("article.material-card")).to_have_count(7)
            await expect(page.get_by_alt_text("个人甲-0")).to_be_visible()
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
