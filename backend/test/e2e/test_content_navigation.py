"""内容生产导航：真实页面和路由，固定任务与素材响应，不调用生成模型。"""

import json
import secrets
import socket
import uuid

import pytest
from patchright.async_api import async_playwright, expect

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, User
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("width", [1440, 390])
async def test_content_navigation_returns_to_empty_editable_home(width):
    uid = f"content_nav_{uuid.uuid4().hex}"
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        department = Department(name=uid)
        db.add(department)
        await db.flush()
        user = User(
            username=uid,
            uid=uid,
            password_hash=AuthUtils.hash_password(secrets.token_urlsafe(24)),
            role="user",
            department_id=department.id,
        )
        db.add(user)
        await db.commit()
        token = AuthUtils.create_access_token({"sub": str(user.id)})

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}"],
            )
            try:
                page = await browser.new_page(viewport={"width": width, "height": 1000})
                await page.add_init_script(f'localStorage.setItem("user_token", {json.dumps(token)})')
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                bootstrap_response = await page.request.get(
                    "http://localhost:5050/api/content/bootstrap", headers={"Authorization": f"Bearer {token}"}
                )
                assert bootstrap_response.ok, await bootstrap_response.text()
                bootstrap = await bootstrap_response.json()
                template = next(item for item in bootstrap["industry_templates"] if item["slug"] == "decoration")
                task = {
                    "id": "ct_navigation_test",
                    "name": "导航回归任务",
                    "mode": "pro",
                    "content_goal": "acquire",
                    "content_type_code": "CT01",
                    "current_stage": "review",
                    "status": "review_required",
                    "runtime_config_snapshot": {"creation_mode": "viral_rewrite"},
                    "brief": {"user_request": "旧任务内容，不应出现在新建首页"},
                }
                await page.route(
                    "**/api/content/tasks/ct_navigation_test",
                    lambda route: route.fulfill(json={"task": task, "template": template, "artifact": None}),
                )
                await page.route("**/api/content/tasks?*", lambda route: route.fulfill(json={"items": []}))
                await page.route(
                    "**/api/material-library/galleries*", lambda route: route.fulfill(json={"galleries": []})
                )
                await page.route(
                    "**/api/content/covers/hycanvas/templates", lambda route: route.fulfill(json={"templates": []})
                )
                await page.route(
                    "**/api/content/covers/photo-layouts", lambda route: route.fulfill(json={"layouts": []})
                )
                for entry in ("sidebar", "back"):
                    await page.goto("http://localhost:5173/content/tasks/ct_navigation_test")
                    await expect(page.get_by_role("heading", name=task["name"], exact=True)).to_be_visible(
                        timeout=30000
                    )
                    if entry == "sidebar":
                        await page.get_by_role("link", name="内容生产", exact=True).click()
                    else:
                        await page.get_by_role("button", name="返回内容创作", exact=True).click()
                    await expect(page).to_have_url("http://localhost:5173/content/new")
                    if width < 600:
                        await page.get_by_role("button", name="折叠侧边栏").click()
                    await expect(page.get_by_role("heading", name="内容创作", exact=True)).to_be_visible()
                    await expect(page.locator(".creation-form")).to_be_visible()
                    await expect(page.locator("#content-request")).to_have_value("")
                    await expect(page.locator(".creation-fields")).not_to_have_attribute("inert", "")
                    await expect(page.get_by_role("button", name="开始生成", exact=True)).to_be_enabled()
                    # 已在首页时重复点击不会清空正在填写的新需求。
                    await page.locator("#content-request").fill("新的创作需求")
                    if width < 600:
                        await page.get_by_role("button", name="展开侧边栏").click()
                    await page.get_by_role("link", name="内容生产", exact=True).click()
                    await expect(page.locator("#content-request")).to_have_value("新的创作需求")
                assert not errors, errors
            finally:
                await browser.close()
    finally:
        async with pg_manager.AsyncSession() as db:
            await db.delete(await db.get(User, user.id))
            await db.delete(await db.get(Department, department.id))
            await db.commit()
        await pg_manager.async_engine.dispose()
