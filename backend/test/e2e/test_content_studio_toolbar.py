"""验证生产页整页滚动、工具栏导航和窄屏布局；不修改服务端任务。"""

import json
import os
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
async def test_toolbar_inside_editor_aligns_right_and_preserves_actions():
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
            context = await browser.new_context(viewport={"width": 1920, "height": 1100})
            await context.add_init_script(f'localStorage.setItem("user_token", {json.dumps(token)})')
            page = await context.new_page()
            await page.goto(f"http://localhost:5173/content/tasks/{task_id}")
            toolbar = page.locator(".workflow-chat-panel .content-studio-toolbar")
            await expect(toolbar).to_be_visible(timeout=20000)
            await expect(toolbar.get_by_role("button")).to_have_count(4)
            await expect(page.locator(".studio-header button")).to_have_count(0)
            await expect(page.get_by_role("button", name="返回上一页", exact=True)).to_have_count(0)
            studio = page.locator(".content-studio-page")
            await studio.evaluate("el => { el.scrollTop = 0 }")
            await expect(page.locator(".completion-results")).to_be_visible()
            results_box = await page.locator(".completion-results").bounding_box()
            assert abs(results_box["width"] - 394) <= 1
            conversation = await page.locator(".completion-conversation").bounding_box()
            studio_box = await studio.bounding_box()
            left_margin = conversation["x"] - studio_box["x"]
            right_margin = studio_box["x"] + studio_box["width"] - results_box["x"] - results_box["width"]
            assert abs(left_margin - right_margin) <= 1
            assert conversation["width"] > 1000
            assert conversation["height"] > 1100
            stream = page.locator(".workflow-stream")
            body_heading = stream.locator("p > strong").filter(has_text="正文内容").first
            await expect(body_heading).to_be_visible()
            body_paragraphs = await body_heading.evaluate(
                "el => { const paragraphs = []; let node = el.parentElement.nextElementSibling;"
                " while (node?.tagName === 'P' && !node.textContent.startsWith('建议话题')) {"
                " paragraphs.push(node.textContent); node = node.nextElementSibling } return paragraphs }"
            )
            assert len(body_paragraphs) >= 5
            assert any("ENF" in paragraph for paragraph in body_paragraphs)
            # 覆盖长正文、单换行与 Markdown 列表，展示不能再次合并段落或截断全文。
            formatted_body = (
                "第一段：居住痛点。\n\n第二段：案例数据。\n第三行：补充说明。\n\n- 改造方案\n- 施工细节\n\n"
            )
            formatted_body += "完整案例说明。" * 220 + "正文末尾标记"
            narrative = await page.evaluate(
                "async body => { const { buildContentNarrativeStream } ="
                " await import('/src/utils/contentWorkflowPresentation.js');"
                " return buildContentNarrativeStream([{ id: 'formatting-test',"
                " outputPreview: { draft: { body } } }])[0].text }",
                formatted_body,
            )
            assert narrative == f"**正文内容**\n\n{formatted_body}"
            assert await stream.evaluate("el => el.scrollHeight <= el.clientHeight + 1")
            assert await studio.evaluate("el => el.scrollHeight > el.clientHeight")
            input_box = page.locator(".workflow-chat-panel .input-box")
            toolbar_box, editor_box = await toolbar.bounding_box(), await input_box.bounding_box()
            assert toolbar_box["y"] >= editor_box["y"]
            assert toolbar_box["y"] + toolbar_box["height"] < editor_box["y"] + editor_box["height"]
            await expect(input_box.locator(".content-studio-toolbar")).to_have_count(1)
            assert await toolbar.evaluate("el => getComputedStyle(el).borderBottomWidth") == "0px"
            last_button = await toolbar.get_by_role("button").last.bounding_box()
            assert abs(last_button["x"] + last_button["width"] - toolbar_box["x"] - toolbar_box["width"]) <= 1
            right_inset = await input_box.evaluate(
                "el => parseFloat(getComputedStyle(el).paddingRight)"
                " + parseFloat(getComputedStyle(el).borderRightWidth)"
            )
            assert (
                abs(editor_box["x"] + editor_box["width"] - last_button["x"] - last_button["width"] - right_inset) <= 1
            )
            await page.screenshot(path="/tmp/content-toolbar-desktop.png", full_page=True)
            await page.mouse.move(conversation["x"] + 150, 400)
            await page.mouse.wheel(0, 500)
            await page.wait_for_function("document.querySelector('.studio-header').getBoundingClientRect().bottom < 0")
            editor_box = await input_box.bounding_box()
            assert 0 <= editor_box["y"] < 1100
            assert editor_box["y"] + editor_box["height"] <= 1100
            composer_box = await page.locator(".workflow-chat-panel").bounding_box()
            assert abs(composer_box["y"] + composer_box["height"] - 1100) <= 1
            await page.screenshot(path="/tmp/content-studio-scrolled.png", full_page=True)
            await toolbar.get_by_role("button", name="图片识别", exact=True).click()
            await expect(page.get_by_role("heading", name="图片 OCR 识别", exact=True)).to_be_visible()
            await page.goto(f"http://localhost:5173/content/tasks/{task_id}")
            for name, route in (("账号管理", "accounts"), ("生产历史", "history"), ("创作规则库", "admin/rules")):
                await toolbar.get_by_role("button", name=name, exact=True).click()
                await expect(page).to_have_url(f"http://localhost:5173/content/{route}")
                await page.goto(f"http://localhost:5173/content/tasks/{task_id}")
            await page.set_viewport_size({"width": 720, "height": 1000})
            await expect(toolbar).to_be_visible()
            await studio.evaluate("el => { el.scrollTop = 0 }")
            assert await studio.evaluate("el => el.scrollWidth <= el.clientWidth")
            box = await toolbar.bounding_box()
            for button in await toolbar.get_by_role("button").all():
                bounds = await button.bounding_box()
                assert bounds["x"] >= box["x"] - 1
                assert bounds["x"] + bounds["width"] <= box["x"] + box["width"] + 1
            await page.screenshot(path="/tmp/content-toolbar-narrow.png", full_page=True)
            # 在浏览器内重放新增叙述，验证阅读位置和自动追随，不提交生成或修改请求。
            await studio.evaluate("el => { el.scrollTop = 300 }")
            await page.wait_for_function(
                "document.querySelector('.content-studio-page').__vueParentComponent"
                ".setupState.followWorkflowOutput === false"
            )
            before_scroll = await studio.evaluate("el => el.scrollTop")
            await studio.evaluate(
                "el => el.__vueParentComponent.setupState.accumulatedWorkflowNarrative"
                ".push('阅读位置测试。'.repeat(100))"
            )
            await expect(stream).to_contain_text("阅读位置测试。")
            assert abs(await studio.evaluate("el => el.scrollTop") - before_scroll) <= 1
            await studio.evaluate("el => { el.scrollTop = el.scrollHeight }")
            await page.wait_for_function(
                "document.querySelector('.content-studio-page').__vueParentComponent"
                ".setupState.followWorkflowOutput === true"
            )
            await studio.evaluate(
                "el => el.__vueParentComponent.setupState.accumulatedWorkflowNarrative"
                ".push('继续追随测试。'.repeat(100))"
            )
            await expect(stream).to_contain_text("继续追随测试。")
            await page.wait_for_function(
                "(() => { const el = document.querySelector('.content-studio-page');"
                " return el.scrollHeight - el.scrollTop - el.clientHeight <= 1 })()"
            )
            await studio.evaluate(
                "el => { const store = el.__vueParentComponent.setupState.store;"
                " store.currentRun.status = 'running';"
                " store.runEvents.push({ node_id: 'generate_content', status: 'running' }) }"
            )
            await expect(page.locator(".active-run-layout")).to_be_visible()
            await expect(page.locator(".workflow-chat-panel [role='textbox']")).to_have_attribute(
                "contenteditable", "false"
            )
            assert await stream.evaluate("el => el.scrollHeight <= el.clientHeight + 1")
            await studio.evaluate("el => { el.scrollTop = 300 }")
            await page.wait_for_function(
                "document.querySelector('.content-studio-page').__vueParentComponent"
                ".setupState.followWorkflowOutput === false"
            )
            await studio.evaluate(
                "el => el.__vueParentComponent.setupState.accumulatedWorkflowNarrative.push('运行中新增输出。')"
            )
            await expect(stream).to_contain_text("运行中新增输出。")
            assert abs(await studio.evaluate("el => el.scrollTop") - 300) <= 1
            await page.goto("http://localhost:5173/content/new")
            await expect(page.get_by_role("navigation", name="内容工具栏")).to_be_visible()
            await expect(page.get_by_role("button", name="图片识别", exact=True)).to_be_disabled()
        finally:
            await browser.close()
