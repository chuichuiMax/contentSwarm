"""Render the real PC page; network fixtures isolate contact UI from business data."""

import base64
import json
import socket
from urllib.parse import parse_qs, urlsplit

import pytest
from patchright.async_api import async_playwright, expect


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_contact_modal_confirm_cancel_last_used_and_layout():
    posts = []
    requests, errors = [], []
    local_link = True
    root = {
        "id": "case-root", "code": "case-root", "name": "案例图库", "visibility": "private",
        "parent_id": None, "industry_slug": "decoration", "count": 1, "child_count": 1,
    }
    child = {
        **root, "id": "case", "code": "case", "name": "洋湖天序-复古写意-120㎡",
        "parent_id": "case-root", "child_count": 0,
    }

    async def api(route):
        url = urlsplit(route.request.url)
        requests.append(url.path)
        if url.path == "/api/auth/me":
            data = {
                "id": 1, "uid": "contact-ui-sender", "username": "平台用户名", "role": "superadmin",
                "phone_number": "19900000001", "permission_scoped": False, "permissions": [],
            }
        elif url.path == "/api/employees/me":
            data = {"employee": {"name": "登录员工", "login_account": "19900000001"}}
        elif url.path == "/api/material-library/categories":
            data = {"categories": [root, child], "can_create_shared": True}
        elif url.path == "/api/material-library/galleries":
            data = {"galleries": [root, child], "industries": [{"slug": "decoration", "name": "装修与家居"}]}
        elif url.path == "/api/material-library/items":
            items = [{"id": "image", "name": "案例图片", "width": 640, "height": 480}]
            data = {"items": items if parse_qs(url.query).get("category") == ["case"] else [], "total": 1}
        elif url.path.endswith("/thumbnail"):
            await route.fulfill(content_type="image/png", body=base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII="
            ))
            return
        elif url.path == "/api/material-library/shares":
            posts.append(route.request.post_data_json)
            data = {"share": {
                "page_url": "http://localhost:5173/share/case/contact-ui",
                "mini_program_url": (
                    "http://127.0.0.1:5173/api/material-library/shares/contact-ui/open-mp?booking=false"
                    if local_link else "https://cases.test/api/material-library/shares/contact-ui/open-mp?booking=false"
                ),
            }}
        elif url.path == "/api/material-library/remote-sync/status":
            data = {"job": None}
        else:
            data = {"items": [], "agents": [], "first_run": False}
        await route.fulfill(json=data)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=[
            "--no-sandbox", f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}",
        ])
        try:
            context = await browser.new_context(viewport={"width": 1366, "height": 900})
            await context.add_init_script("""
                localStorage.setItem('user_token', 'contact-ui-fixture');
                localStorage.setItem('contact-ui-copied', '[]');
                Object.defineProperty(navigator, 'clipboard', { value: {
                    writeText: async text => { localStorage.setItem('contact-ui-copied', JSON.stringify([text])); }
                }});
            """)
            page = await context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.route("**/api/**", api)
            await page.goto("http://localhost:5173/materials/images", wait_until="domcontentloaded", timeout=60_000)
            try:
                await page.locator("button.gallery-open").filter(has_text="案例图库").click(timeout=20_000)
            except Exception as exc:
                raise AssertionError({
                    "url": page.url, "errors": errors, "requests": requests,
                    "body": (await page.locator("body").inner_text())[:1500],
                }) from exc
            await page.locator("button.gallery-open").filter(has_text=child["name"]).click()
            await page.get_by_role("button", name="选择图片 案例图片", exact=True).click()
            share_button = page.get_by_role("button", name="分享 (1)", exact=True)
            await share_button.click()
            dialog = page.get_by_role("dialog").filter(has_text="修改联系方式")
            await expect(dialog.get_by_label("联系人", exact=True)).to_have_value("登录员工")
            await expect(dialog.get_by_label("手机号码", exact=True)).to_have_value("19900000001")
            await expect(dialog.get_by_role("button", name="上次使用")).to_be_disabled()
            await dialog.get_by_role("button", name="取 消").click()
            assert posts == []
            await share_button.click()
            phone = dialog.get_by_label("手机号码", exact=True)
            await phone.fill("123")
            await expect(dialog.get_by_role("button", name="确 定")).to_be_disabled()
            await expect(dialog).to_contain_text("请填写正确的11位手机号码")
            await dialog.get_by_label("联系人", exact=True).fill("代接联系人")
            await phone.fill("13800000003")
            await expect(dialog).to_contain_text("格式正确，点击确定保存")
            await dialog.get_by_role("button", name="确 定").click()
            await expect(page.get_by_text(
                "当前分享地址是本机地址，手机微信无法访问。请使用公网 PC 地址，或配置同一测试环境的公网预览入口。",
                exact=True,
            )).to_be_visible()
            await expect(dialog).to_be_visible()
            assert json.loads(await page.evaluate("localStorage.getItem('contact-ui-copied')")) == []
            local_link = False
            await dialog.get_by_role("button", name="确 定").click()
            await expect(dialog).not_to_be_visible()
            assert posts == [{"item_ids": ["image"], "contact_name": "代接联系人", "contact_phone": "13800000003"}] * 2
            assert json.loads(await page.evaluate("localStorage.getItem('contact-ui-copied')")) == [
                "https://cases.test/api/material-library/shares/contact-ui/open-mp?booking=false",
            ]
            await share_button.click()
            await expect(phone).to_have_value("19900000001")
            await dialog.get_by_role("button", name="上次使用").click()
            await expect(phone).to_have_value("13800000003")
            await expect(dialog.get_by_label("联系人", exact=True)).to_have_value("代接联系人")
            await page.screenshot(path="/tmp/case-share-contact-pc-light.png")
            await dialog.get_by_role("button", name="取 消").click()
            await page.evaluate("localStorage.setItem('theme', 'dark')")
            await page.reload(wait_until="domcontentloaded")
            await page.locator("button.gallery-open").filter(has_text="案例图库").click()
            await page.locator("button.gallery-open").filter(has_text=child["name"]).click()
            await page.get_by_role("button", name="选择图片 案例图片", exact=True).click()
            await share_button.click()
            await expect(phone).to_have_value("19900000001")
            await dialog.get_by_role("button", name="上次使用").click()
            await expect(phone).to_have_value("13800000003")
            await page.screenshot(path="/tmp/case-share-contact-pc-dark.png")
            await page.set_viewport_size({"width": 375, "height": 812})
            bounds = await dialog.bounding_box()
            assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 375
            await page.screenshot(path="/tmp/case-share-contact-pc-narrow.png")
            saved = await page.evaluate("localStorage.getItem('case-share-contact:contact-ui-sender')")
            assert json.loads(saved)["contact_phone"] == "13800000003"
            await context.close()
        finally:
            await browser.close()
