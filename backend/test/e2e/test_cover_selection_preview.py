"""真实封面选择页：历史被审核拦截的成图可直接选择；不提交确认。"""

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
async def test_cover_selection_allows_generated_image_despite_legacy_review():
    uid = os.getenv("RULE_EDITOR_TEST_UID")
    task_id = os.getenv("COVER_PREVIEW_TEST_TASK_ID")
    if not uid or not task_id:
        pytest.skip("需要配置测试管理员与等待封面确认的任务")
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
            page = await browser.new_page(viewport={"width": 1440, "height": 1100})
            await page.add_init_script(f'localStorage.setItem("user_token", {json.dumps(token)})')
            await page.goto(f"http://localhost:5173/content/tasks/{task_id}")
            panel = page.locator(".human-review-card").filter(has_text="选择最终封面")
            await expect(panel).to_be_visible(timeout=20000)
            preview = panel.locator("img").first
            await expect(preview).to_be_visible()
            await expect(preview).to_have_js_property("naturalWidth", 1080)
            await expect(preview).to_have_js_property("naturalHeight", 1440)
            await expect(panel).not_to_contain_text("未通过审核")
            await expect(panel).not_to_contain_text("1440x810")
            await expect(panel.get_by_role("radio")).to_be_enabled()
            confirm = panel.get_by_role("button", name="确认封面并保存")
            await expect(confirm).to_be_disabled()
            assert await preview.evaluate("el => getComputedStyle(el).objectFit") == "contain"
            await panel.get_by_role("radio").check()
            await expect(confirm).to_be_enabled()
            await panel.screenshot(path="/tmp/cover-selection-preview.png")
            await page.set_viewport_size({"width": 720, "height": 1000})
            await expect(preview).to_be_visible()
            assert await panel.evaluate("el => el.scrollWidth <= el.clientWidth")
        finally:
            await browser.close()
