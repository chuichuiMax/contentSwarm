"""PC 个人素材同时保留固定入口与自建图库。"""

import json
import re
import secrets
import socket
import uuid

import pytest
from patchright.async_api import async_playwright, expect
from sqlalchemy import delete

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, User
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_personal_materials_keep_fixed_and_custom_galleries():
    uid = f"material_ui_{uuid.uuid4().hex}"
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        department = Department(name=uid)
        db.add(department)
        await db.flush()
        user = User(
            username=uid,
            uid=uid,
            password_hash=AuthUtils.hash_password(secrets.token_urlsafe(24)),
            role="superadmin",
            department_id=department.id,
        )
        db.add(user)
        await db.commit()
        user_id = user.id
        department_id = department.id
        token = AuthUtils.create_access_token({"sub": str(user.id)})

    custom_gallery = {
        "id": "custom-gallery",
        "code": "custom-gallery",
        "name": "客厅灵感",
        "description": "我的自建图库",
        "visibility": "private",
        "parent_id": None,
        "industry_slug": "decoration",
        "industry_name": "装修与家居",
        "is_system": False,
        "can_manage": True,
        "count": 0,
        "child_count": 0,
        "cover_item_id": None,
    }
    fixed_folders = [
        {"id": "generated", "name": "生图图库", "count": 2, "can_upload": False},
        {"id": "rough", "name": "毛坯房图库", "count": 1, "can_upload": True},
        {"id": "works", "name": "我的作品", "count": 3, "can_upload": False},
        {"id": "uploads", "name": "我的上传", "count": 4, "can_upload": True},
    ]

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}"],
            )
            try:
                context = await browser.new_context(viewport={"width": 1440, "height": 1000})
                await context.add_init_script(f'localStorage.setItem("user_token", {json.dumps(token)})')
                page = await context.new_page()
                await page.route(
                    "**/api/material-library/my-materials/folders",
                    lambda route: route.fulfill(json={"folders": fixed_folders}),
                )
                await page.route(
                    re.compile(r"/api/material-library/my-materials/(rough|generated|uploads|works)(?:\?|$)"),
                    lambda route: route.fulfill(json={"items": [], "total": 0, "page": 1, "page_size": 24}),
                )
                await page.route(
                    "**/api/material-library/galleries*",
                    lambda route: route.fulfill(
                        json={
                            "galleries": [custom_gallery],
                            "industries": [{"slug": "decoration", "name": "装修与家居"}],
                        }
                    ),
                )
                await page.route(
                    "**/api/material-library/categories?*",
                    lambda route: route.fulfill(json={"categories": [custom_gallery], "can_create_shared": True}),
                )
                await page.route(
                    "**/api/material-library/items?*",
                    lambda route: route.fulfill(json={"items": [], "total": 0, "page": 1, "page_size": 24}),
                )
                await page.route(
                    "**/api/material-library/remote-sync/status*",
                    lambda route: route.fulfill(json={"job": None}),
                )

                await page.goto("http://localhost:5173/materials/images")
                await expect(page.get_by_role("heading", name="素材库")).to_be_visible(timeout=15_000)

                generated_card = page.locator("button.gallery-open").filter(has_text="生图图库")
                custom_card = page.locator("button.gallery-open").filter(has_text="客厅灵感")
                await expect(page.get_by_role("button", name="同步远程素材")).to_be_visible()
                await expect(page.get_by_role("button", name="新建图库")).to_be_visible()
                await expect(generated_card).to_be_visible()
                await expect(custom_card).to_be_visible()

                await generated_card.click()
                await expect(page.get_by_role("button", name="新建图库")).to_have_count(0)
                await page.get_by_role("button", name="返回我的图库").click()

                await custom_card.click()
                await expect(page.get_by_role("button", name="新建二级图库")).to_be_visible()
                await page.get_by_role("button", name="上传图片").click()
                upload_modal = page.locator(".ant-modal-content").filter(has_text="上传素材图片")
                await expect(upload_modal).to_contain_text("客厅灵感")
                await context.close()
            finally:
                await browser.close()
    finally:
        async with pg_manager.AsyncSession() as db:
            await db.execute(delete(User).where(User.id == user_id))
            await db.execute(delete(Department).where(Department.id == department_id))
            await db.commit()
        await pg_manager.async_engine.dispose()
