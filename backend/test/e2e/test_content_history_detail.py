"""历史文章列表与最近生成复用真实详情，验证只读切换与刷新。"""

import json
import os
import socket

import httpx
import pytest
from patchright.async_api import async_playwright, expect
from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentTask
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_history_selects_latest_generated_article_and_switches_details():
    task_id = os.getenv("CONTENT_HISTORY_TEST_TASK_ID")
    if not task_id:
        pytest.skip("需提供有至少两篇生成文章的用户任务")
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        task = await db.get(ContentTask, task_id)
        user = await db.scalar(select(User).where(User.uid == task.created_by))
        token = AuthUtils.create_access_token({"sub": str(user.id)})
    await pg_manager.async_engine.dispose()
    async with httpx.AsyncClient(
        base_url="http://localhost:5050", headers={"Authorization": f"Bearer {token}"}
    ) as client:
        response = await client.get("/api/content/tasks", params={"generated_only": True, "page_size": 20})
        response.raise_for_status()
        items = response.json()["items"]
        assert len(items) >= 2
        articles = []
        for item in items[:2]:
            detail = await client.get(f"/api/content/tasks/{item['id']}")
            detail.raise_for_status()
            assert detail.json()["artifact"]
            articles.append(detail.json()["artifact"])
        second = await client.get("/api/content/tasks", params={"generated_only": True, "page_size": 1, "page": 2})
        assert second.json()["items"][0]["id"] == items[1]["id"]
        assert second.json()["total"] == response.json()["total"]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, args=["--no-sandbox", f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}"]
        )
        try:
            page = await browser.new_page(viewport={"width": 1920, "height": 1080})
            await page.add_init_script(f"localStorage.setItem('user_token', {json.dumps(token)})")
            await page.goto("http://localhost:5173/content/history")
            await expect(page.locator(".history-result .content-card h1")).to_have_text(articles[0]["title"])
            await expect(page.locator(".history-item.active")).to_have_count(1)
            await expect(page.locator(".history-result").get_by_text("质量审核", exact=True)).to_be_visible()
            await page.locator(".history-item .task-link").nth(1).click()
            await expect(page.locator(".history-result .content-card h1")).to_have_text(articles[1]["title"])
            await expect(page).to_have_url(f"http://localhost:5173/content/history?task={items[1]['id']}")
            await page.reload()
            await expect(page.locator(".history-result .content-card h1")).to_have_text(articles[1]["title"])
            await page.screenshot(path="/tmp/content-history-desktop.png", full_page=False)
            await page.set_viewport_size({"width": 720, "height": 900})
            assert await page.locator(".content-history-page").evaluate("el => el.scrollWidth <= el.clientWidth")
            await page.goto(f"http://localhost:5173/content/results/{items[1]['id']}")
            await expect(page.locator(".content-card h1")).to_have_text(articles[1]["title"])
        finally:
            await browser.close()
