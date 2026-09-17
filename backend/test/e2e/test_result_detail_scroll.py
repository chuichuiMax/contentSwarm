"""真实结果弹窗的长原文与正文可滚动；只读，不发布、不修改文章。"""

import json
import os
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
async def test_result_detail_long_reference_and_body_scroll():
    task_id = os.getenv("RESULT_SCROLL_TASK_ID")
    if not task_id:
        pytest.skip("需提供包含长正文和爆款参考的任务")
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        task = await db.get(ContentTask, task_id)
        user = await db.scalar(select(User).where(User.uid == task.created_by))
        token = AuthUtils.create_access_token({"sub": str(user.id)})
    await pg_manager.async_engine.dispose()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, args=["--no-sandbox", f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}"]
        )
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            await page.add_init_script(f"localStorage.setItem('user_token', {json.dumps(token)})")
            await page.goto(f"http://localhost:5173/content/tasks/{task_id}?resultDetail=1")
            modal = page.locator(".result-detail-modal")
            await expect(modal).to_be_visible()
            await modal.get_by_role("tab", name="爆款原文").click()
            reference = page.locator(".result-detail-viral-body")
            await expect(reference).to_be_visible()
            for height in (900, 650):
                await page.set_viewport_size({"width": 1440, "height": height})
                for selector in (".result-detail-viral-body", ".result-detail-body"):
                    area = page.locator(selector)
                    assert await area.evaluate("el => el.scrollHeight > el.clientHeight + 20")
                    await area.hover()
                    await page.mouse.wheel(0, 30000)
                    await _at_bottom(page, selector)
                footer = page.locator(".result-detail-footer")
                box = await footer.bounding_box()
                assert box and box["y"] >= 0 and box["y"] + box["height"] <= height
            await page.set_viewport_size({"width": 720, "height": 800})
            layout = page.locator(".result-detail-layout")
            assert await layout.evaluate("el => el.scrollWidth <= el.clientWidth")
            await layout.hover()
            await page.mouse.wheel(0, 30000)
            await _at_bottom(page, ".result-detail-layout")
            await modal.screenshot(path="/tmp/result-detail-scroll.png")
        finally:
            await browser.close()


async def _at_bottom(page, selector):
    await page.wait_for_function(
        "(selector) => { const el = document.querySelector(selector); "
        "return el.scrollTop > 0 && el.scrollTop + el.clientHeight >= el.scrollHeight - 2; }",
        arg=selector,
    )
