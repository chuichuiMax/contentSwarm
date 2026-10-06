"""PC 风格字段、上传校验与图库切换；API 保存行为由集成测试覆盖。"""

import json
import re
import socket
from email.parser import BytesParser
from email.policy import default

import pytest
from patchright.async_api import async_playwright, expect

from test.integration.api import test_material_library_router as material_fixtures
from test.integration.api.test_material_library_router import _png
from test.integration.conftest import test_client as test_client

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

material_users = material_fixtures.material_users


async def test_only_enterprise_cases_show_style_and_clear_it_on_upload_target_change(material_users):  # noqa: F811
    galleries = []
    for code, name, scope, role, parent, style in [
        ("private", "个人案例图库", "private", None, None, "江南印象"),
        ("case", "已改名企业案例", "enterprise", "reference", None, None),
        ("rough", "企业毛坯测试", "enterprise", "rough", None, "江南印象"),
        ("generated", "企业生图测试", "enterprise", "generated", None, None),
        ("custom", "企业自建测试", "enterprise", None, None, None),
        ("case-modern", "现代案例子图库", "enterprise", None, "case", "雅致现代"),
        ("case-oriental", "东方案例子图库", "enterprise", None, "case", "江南印象"),
    ]:
        galleries.append(
            {
                "id": code,
                "code": code,
                "name": name,
                "visibility": scope,
                "image_design_role": role,
                "parent_id": parent,
                "design_style": style,
                "industry_slug": "uncategorized" if role == "reference" or parent else "decoration",
                "description": "风格范围测试",
                "is_system": False,
                "is_global_personal": scope == "private",
                "can_manage": True,
                "can_upload": True,
                "count": 0,
                "child_count": 0,
            }
        )

    uploads = []

    async def capture_upload(route):
        request = route.request
        mime = (f"Content-Type: {request.headers['content-type']}\r\nMIME-Version: 1.0\r\n\r\n").encode()
        parts = BytesParser(policy=default).parsebytes(mime + request.post_data_buffer)
        fields = {
            part.get_param("name", header="content-disposition"): part.get_payload(decode=True).decode("utf-8")
            for part in parts.iter_parts()
            if not part.get_filename()
        }
        uploads.append(fields)
        await route.fulfill(status=201, json={"items": [{"category": fields["category"]}], "summary": {}})

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}"],
        )
        try:
            context = await browser.new_context(viewport={"width": 1440, "height": 1000})
            token = material_users["owner"]["Authorization"].removeprefix("Bearer ")
            await context.add_init_script(f'localStorage.setItem("user_token", {json.dumps(token)})')
            page = await context.new_page()
            await page.route(
                "**/api/material-library/galleries*",
                lambda route: route.fulfill(
                    json={
                        "galleries": galleries,
                        "industries": [{"slug": "decoration", "name": "装修与家居"}],
                    }
                ),
            )
            await page.route(
                "**/api/material-library/categories?*",
                lambda route: route.fulfill(
                    json={
                        "categories": galleries,
                        "can_create_shared": True,
                    }
                ),
            )
            await page.route(
                "**/api/material-library/items?*",
                lambda route: route.fulfill(
                    json={
                        "items": [],
                        "total": 0,
                        "page": 1,
                        "page_size": 24,
                    }
                ),
            )
            await page.route(
                "**/api/material-library/remote-sync/status*", lambda route: route.fulfill(json={"job": None})
            )
            await page.route("**/api/material-library/images/import*", capture_upload)
            await page.goto("http://localhost:5173/materials/images")
            await expect(page.get_by_role("heading", name="素材库")).to_be_visible(timeout=60_000)
            await page.locator("button.gallery-open").filter(has_text="个人案例图库").click()
            await expect(page.get_by_role("tablist", name="设计风格筛选")).to_have_count(0)
            await page.get_by_role("button", name="上传图片").click()
            modal = page.locator(".ant-modal-content").filter(has_text="上传素材图片")
            await expect(modal.locator("label").filter(has_text="设计风格")).to_have_count(0)
            await modal.locator('input[type="file"]').first.set_input_files(
                {
                    "name": "style.png",
                    "mimeType": "image/png",
                    "buffer": _png(),
                }
            )
            async with page.expect_response("**/api/material-library/images/import*"):
                await modal.get_by_role("button", name="开始上传").click()
            assert uploads[-1] == {"category": "private"}
            await expect(modal).not_to_be_visible()
            await page.get_by_role("button", name="返回图库").click()
            await page.locator(".ant-radio-button-wrapper").filter(has_text="企业共享").click()

            for name in ["企业毛坯测试", "企业生图测试", "企业自建测试"]:
                await page.locator("button.gallery-open").filter(has_text=name).click()
                await expect(page.get_by_role("tablist", name="设计风格筛选")).to_have_count(0)
                await page.get_by_role("button", name="上传图片").click()
                await expect(modal.locator("label").filter(has_text="设计风格")).to_have_count(0)
                await modal.get_by_role("button", name=re.compile(r"取\s*消")).click()
                await page.get_by_role("button", name="返回图库").click()

            await page.locator("button.gallery-open").filter(has_text="已改名企业案例").click()
            style_tabs = page.get_by_role("tablist", name="设计风格筛选")
            await expect(style_tabs).to_be_visible()
            await style_tabs.get_by_role("tab", name="雅致现代", exact=True).click()
            await expect(page.locator("button.gallery-open").filter(has_text="现代案例子图库")).to_be_visible()
            await expect(page.locator("button.gallery-open").filter(has_text="东方案例子图库")).to_have_count(0)
            await style_tabs.get_by_role("tab", name="全部", exact=True).click()
            await page.get_by_role("button", name="上传图片").click()
            style_label = modal.locator("label").filter(has_text="设计风格")
            await expect(style_label).to_be_visible()
            await modal.locator('input[type="file"]').first.set_input_files(
                {
                    "name": "style.png",
                    "mimeType": "image/png",
                    "buffer": _png(),
                }
            )
            await modal.get_by_role("button", name="开始上传").click()
            await expect(page.locator(".ant-message-notice").filter(has_text="请选择设计风格")).to_be_visible()
            assert len(uploads) == 1
            await style_label.locator(".ant-select").click()
            await (
                page.locator(".ant-select-dropdown:visible .ant-select-item-option").filter(has_text="雅致现代").click()
            )
            async with page.expect_response("**/api/material-library/images/import*"):
                await modal.get_by_role("button", name="开始上传").click()
            assert uploads[-1] == {"category": "case", "design_style": "雅致现代"}
            await expect(modal).not_to_be_visible()

            await page.get_by_role("button", name="上传图片").click()
            await style_label.locator(".ant-select").click()
            await (
                page.locator(".ant-select-dropdown:visible .ant-select-item-option").filter(has_text="雅致现代").click()
            )
            category_select = modal.locator("label").filter(has_text="分类").locator(".ant-select")
            await category_select.click()
            await (
                page.locator(".ant-select-dropdown:visible .ant-select-item-option")
                .filter(has_text="企业毛坯测试")
                .click()
            )
            await expect(style_label).to_have_count(0)
            await category_select.click()
            await (
                page.locator(".ant-select-dropdown:visible .ant-select-item-option")
                .filter(has_text="[企业共享] 已改名企业案例")
                .click()
            )
            await expect(style_label).not_to_contain_text("雅致现代")
            await modal.locator('input[type="file"]').first.set_input_files(
                {"name": "style.png", "mimeType": "image/png", "buffer": _png()}
            )
            await modal.get_by_role("button", name="开始上传").click()
            await expect(page.locator(".ant-message-notice").filter(has_text="请选择设计风格")).to_be_visible()
            assert len(uploads) == 2
            await category_select.click()
            await (
                page.locator(".ant-select-dropdown:visible .ant-select-item-option")
                .filter(has_text="企业毛坯测试")
                .click()
            )
            async with page.expect_response("**/api/material-library/images/import*"):
                await modal.get_by_role("button", name="开始上传").click()
            assert uploads[-1] == {"category": "rough"}
        finally:
            await browser.close()
