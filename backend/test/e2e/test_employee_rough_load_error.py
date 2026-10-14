"""Employee gallery load failures must not appear as an empty gallery."""

import io
import re
import socket
from urllib.parse import parse_qs, urlparse

import pytest
from patchright.async_api import async_playwright, expect
from PIL import Image


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("failed_resource", ["categories", "items", "thumbnail"])
async def test_employee_gallery_load_error_can_retry_with_the_same_target(failed_resource):
    png = io.BytesIO()
    Image.new("RGB", (10, 10), "blue").save(png, format="PNG")
    failing = True
    requests = []

    async def respond(route):
        url = urlparse(route.request.url)
        params = parse_qs(url.query)
        path = url.path
        if path.startswith("/api/material-library/"):
            requests.append((path, params))
        if failing and path.endswith(f"/{failed_resource}"):
            return await route.fulfill(status=403, json={"detail": "图库访问被拒绝"})
        if path.endswith("/thumbnail"):
            return await route.fulfill(body=png.getvalue(), content_type="image/png")
        if path == "/api/auth/me":
            data = {"id": 1, "uid": "admin", "username": "管理员", "role": "admin"}
        elif path == "/api/auth/check-first-run":
            data = {"first_run": False}
        elif path == "/api/material-library/categories":
            if failing:
                return await route.fulfill(status=404, json={"detail": "个人毛坯图库已停用"})
            data = {"categories": [], "target_employee": {"id": "user:designer", "name": "测试员工", "employee_code": "A1"}}
        elif path == "/api/material-library/items":
            data = {
                "items": [{"id": "target-image", "name": "员工毛坯图", "category_name": "毛坯房图库",
                           "uploaded_by_name": "测试员工", "width": 10, "height": 10, "file_size": 50}],
                "total": 1,
            }
        else:
            data = {}
        await route.fulfill(json=data)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}"],
        )
        try:
            context = await browser.new_context(viewport={"width": 1440, "height": 1000})
            await context.add_init_script('localStorage.setItem("user_token", "gallery-error-test")')
            page = await context.new_page()
            await page.route("**/api/**", respond)
            await page.goto(
                "http://localhost:5173/materials/images?employee_id=user:designer&gallery=rough",
                wait_until="domcontentloaded", timeout=60_000,
            )
            await expect(page.get_by_text("素材加载失败", exact=True)).to_be_visible(timeout=60_000)
            await expect(page.locator(".ant-empty")).to_have_count(0)
            await expect(page.get_by_role("button", name="上传第一份素材")).to_have_count(0)
            failing = False
            await page.get_by_role("button", name=re.compile(r"重\s*试")).click()
            await expect(page.get_by_text("素材加载失败", exact=True)).to_be_visible()
            await expect(page.locator(".ant-empty")).to_have_count(0)
            assert requests
            assert all(params.get("employee_id") == ["user:designer"]
                       for path, params in requests if path.endswith(("/categories", "/items", "/thumbnail")))
        finally:
            await browser.close()
