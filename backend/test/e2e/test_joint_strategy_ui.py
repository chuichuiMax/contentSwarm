"""独立 Chromium 校验真实前端/API 的行业分支及持久化评分展示。"""

import json
import os
import re
import socket

import pytest
from patchright.async_api import async_playwright, expect
from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentTask
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_industry_input_and_saved_decision_in_real_browser():
    uid = os.environ.get("RULE_EDITOR_TEST_UID")
    if not uid:
        pytest.skip("需要隔离测试管理员")
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one()
        token = AuthUtils.create_access_token({"sub": str(user.id)})
        task = (
            await db.execute(
                select(ContentTask)
                .where(
                    ContentTask.name == "pytest 联合策略完整工作流 decoration",
                    ContentTask.created_by == uid,
                    ContentTask.deleted_at.is_(None),
                )
                .order_by(ContentTask.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if task is None:
            pytest.skip("先执行 test_joint_strategy_workflow.py 生成可查看的装修任务")
        task_id = task.id
    await pg_manager.async_engine.dispose()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}",
            ],
        )
        try:
            context = await browser.new_context(viewport={"width": 1440, "height": 1000})
            await context.add_init_script(f"localStorage.setItem('user_token', {json.dumps(token)})")
            page = await context.new_page()
            origin = os.environ.get("TEST_WEB_URL", "http://localhost:5173")
            await page.goto(f"{origin}/content/new")
            await page.get_by_role("button", name=re.compile("装修")).first.click()
            await expect(page.get_by_text("一级内容方向", exact=True)).to_have_count(0)
            await expect(page.locator(".auto-strategy-hint")).to_be_visible()
            await page.get_by_role("button", name=re.compile("教育")).first.click()
            await expect(page.get_by_text("一级内容方向", exact=True)).to_have_count(0)
            await expect(page.locator(".auto-strategy-hint")).to_be_visible()
            await page.goto(f"{origin}/content/tasks/{task_id}")
            decision = page.locator("section.strategy-decision").first
            await expect(decision.get_by_text("本次选择依据", exact=True)).to_be_visible()
            await decision.get_by_text("已选公式与创作手法的依据", exact=True).click()
            await expect(decision.get_by_text("方向内匹配", exact=True).first).to_be_visible()
            await expect(decision.get_by_text(re.compile(r"\d+(?:\.\d+)? 分")).first).to_be_visible()
            await page.screenshot(path="/tmp/yuxi-strategy-ui.png", full_page=True)
        finally:
            await browser.close()
