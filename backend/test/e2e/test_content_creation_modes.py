"""验证新建页只提交爆款仿写；拦截创建请求，不写入业务任务。"""

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
async def test_creation_card_submits_viral_mode_on_desktop_and_narrow_screen():
    uid = f"viral_mode_test_{uuid.uuid4().hex}"
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
            context = await browser.new_context()
            await context.add_init_script(f'localStorage.setItem("user_token", {json.dumps(token)})')
            page = await context.new_page()
            # 只验证真实页面构造的请求，避免创建测试业务数据。
            await page.route("**/api/content/tasks", lambda route: route.abort())
            for width in (1440, 480):
                await page.set_viewport_size({"width": width, "height": 1000})
                await page.goto("http://localhost:5173/content/new")
                group = page.locator(".creation-mode-options")
                await expect(group.get_by_text("爆款仿写", exact=True)).to_be_visible(timeout=20000)
                if width == 480:
                    await page.get_by_role("button", name="折叠侧边栏").click()
                await expect(group.get_by_role("radio")).to_have_count(0)
                await expect(page.get_by_text("使用模式", exact=True)).to_have_count(0)
                await expect(page.get_by_text("原创模式", exact=True)).to_have_count(0)
                await page.locator(".template-card").filter(has_text="装修").first.click()
                await page.locator(".creation-type-field").get_by_text("工种总价", exact=True).click()
                selected = group.locator(".selected")
                await expect(selected).to_have_count(1)
                await expect(selected).to_have_text("爆款仿写")
                async with page.expect_request(
                    lambda request: request.method == "POST" and request.url.endswith("/api/content/tasks")
                ) as sent:
                    await page.get_by_role("button", name="创建任务并填写素材").click()
                payload = (await sent.value).post_data_json
                assert payload["creation_mode"] == "viral_rewrite"
                assert await group.evaluate("el => el.getBoundingClientRect().right <= innerWidth")
                await page.screenshot(path=f"/tmp/content-creation-modes-{width}.png", full_page=True)
            await browser.close()
    finally:
        async with pg_manager.AsyncSession() as db:
            await db.delete(await db.get(User, user.id))
            await db.delete(await db.get(Department, department.id))
            await db.commit()
        await pg_manager.async_engine.dispose()
