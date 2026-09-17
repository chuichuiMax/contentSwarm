"""只读验证真实历史任务的候选名称和证据中文展示，不触发模型生成。"""

import json
import os
import re
import socket

import pytest
from patchright.async_api import async_playwright, expect
from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentNodeRun, ContentTask
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_saved_strategy_displays_chinese_names_and_evidence():
    uid = os.getenv("RULE_EDITOR_TEST_UID")
    if not uid:
        pytest.skip("需要配置测试管理员")
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one()
        token = AuthUtils.create_access_token({"sub": str(user.id)})
        task_id = (
            await db.execute(
                select(ContentTask.id)
                .join(ContentNodeRun, ContentNodeRun.task_id == ContentTask.id)
                .where(
                    ContentTask.created_by == uid,
                    ContentTask.deleted_at.is_(None),
                    ContentNodeRun.node_id == "select_creation_strategy",
                    ContentNodeRun.status == "completed",
                )
                .order_by(ContentNodeRun.finished_at.desc())
                .limit(1)
            )
        ).scalar_one()
    await pg_manager.async_engine.dispose()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}",
            ],
        )
        try:
            context = await browser.new_context(viewport={"width": 1440, "height": 1100})
            await context.add_init_script(f'localStorage.setItem("user_token", {json.dumps(token)})')
            page = await context.new_page()
            async with page.expect_response(
                lambda response: response.url.endswith(f"/tasks/{task_id}/strategy/decision")
            ) as response_info:
                await page.goto(f"http://localhost:5173/content/tasks/{task_id}")
            saved = await (await response_info.value).json()
            strategy = saved["decision"]["strategy"]
            expected_keys = [
                f"标题公式:{strategy['title_formula_code']}",
                f"正文公式:{strategy['body_formula_code']}",
                *[f"创作手法:{code}" for code in strategy["creation_method_codes"]],
            ]
            section = page.locator(".strategy-decision").first
            await expect(section).to_be_visible(timeout=20000)
            await section.get_by_text("已选公式与创作手法的依据", exact=True).click()
            rows = section.locator(".ant-table-tbody tr.ant-table-row")
            await expect(rows).to_have_count(len(expected_keys))
            actual_keys = await rows.evaluate_all("rows => rows.map(row => row.dataset.rowKey)")
            assert set(actual_keys) == set(expected_keys)
            await expect(rows.locator(".ant-tag")).to_have_text(["已选"] * len(expected_keys))
            cells = section.locator(".ant-table-tbody tr.ant-table-row td:first-child")
            await expect(cells.first).to_contain_text(re.compile(r"标题公式 · [\u4e00-\u9fff]"))
            for text in await cells.all_text_contents():
                assert not re.search(r"·\s*(?:T\d+|C\d+|M\d+|vav_)", text), text
                assert "名称未留存" not in text, text
            await section.get_by_text("输入依据", exact=True).first.click()
            evidence = section.locator("td details[open] li")
            await expect(evidence.first).to_be_visible()
            texts = await evidence.all_text_contents()
            assert any("：" in text for text in texts), texts
            assert all("content_brief." not in text and "evidence_bundle." not in text for text in texts), texts
            await page.screenshot(path="/tmp/strategy-chinese-display.png", full_page=True)
        finally:
            await browser.close()
