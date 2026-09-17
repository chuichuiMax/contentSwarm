"""验证新建页模式选择与提交参数；拦截创建请求，不写入业务任务。"""

import json
import os
import socket

import pytest
from patchright.async_api import async_playwright, expect
from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_creation_cards_submit_professional_mode_on_desktop_and_narrow_screen():
    uid = os.getenv("RULE_EDITOR_TEST_UID")
    if not uid:
        pytest.skip("需要配置测试管理员 RULE_EDITOR_TEST_UID")
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one()
        token = AuthUtils.create_access_token({"sub": str(user.id)})
    await pg_manager.async_engine.dispose()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}"],
        )
        try:
            context = await browser.new_context()
            await context.add_init_script(f'localStorage.setItem("user_token", {json.dumps(token)})')
            page = await context.new_page()
            # 只验证真实页面构造的请求，避免创建测试业务数据。
            await page.route("**/api/content/tasks", lambda route: route.abort())
            for width in (1440, 480):
                await page.set_viewport_size({"width": width, "height": 1000})
                await page.goto("http://localhost:5173/content/new")
                group = page.get_by_role("radiogroup", name="创作模式")
                await expect(group).to_be_visible(timeout=20000)
                if width == 480:
                    await page.get_by_role("button", name="折叠侧边栏").click()
                await expect(group.get_by_role("radio")).to_have_count(2)
                await expect(page.get_by_text("使用模式", exact=True)).to_have_count(0)
                await expect(group.get_by_role("radio", name="原创模式")).to_be_checked()
                await page.locator(".template-card").filter(has_text="装修").first.click()
                for label, value in (("爆款仿写", "viral_rewrite"), ("原创模式", "original")):
                    await group.get_by_text(label, exact=True).click()
                    await expect(group.get_by_role("radio", name=label)).to_be_checked()
                    selected = group.locator(".selected")
                    await expect(selected).to_have_count(1)
                    await expect(selected).to_have_text(label)
                    await expect(selected).to_have_css("background-color", "rgb(230, 247, 255)")
                    await expect(group.locator(".creation-mode-card:not(.selected)")).not_to_have_css(
                        "background-color", "rgb(230, 247, 255)"
                    )
                    async with page.expect_request(
                        lambda request: request.method == "POST" and request.url.endswith("/api/content/tasks")
                    ) as sent:
                        await page.get_by_role("button", name="创建任务并填写素材").click()
                    payload = (await sent.value).post_data_json
                    assert payload["mode"] == "pro"
                    assert payload["creation_mode"] == value
                await group.get_by_role("radio", name="原创模式").focus()
                await page.keyboard.press("ArrowRight")
                await expect(group.get_by_role("radio", name="爆款仿写")).to_be_checked()
                assert await group.evaluate("el => el.getBoundingClientRect().right <= innerWidth")
                await page.screenshot(path=f"/tmp/content-creation-modes-{width}.png", full_page=True)
        finally:
            await browser.close()
