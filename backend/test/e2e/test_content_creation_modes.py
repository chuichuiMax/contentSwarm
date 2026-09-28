"""统一创作表单：真实页面与素材模板，拦截业务写入，不调用生成模型。"""

import asyncio
import io
import json
import secrets
import socket
import uuid
from urllib.parse import urlsplit

import pytest
from patchright.async_api import async_playwright, expect
from PIL import Image

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, User
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_unified_creation_preserves_inputs_and_existing_request_sequence():
    uid = f"creation_ui_{uuid.uuid4().hex}"
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
                for width in (1440, 390):
                    context = await browser.new_context(viewport={"width": width, "height": 1000})
                    await context.add_init_script(f'localStorage.setItem("user_token", {json.dumps(token)})')
                    page = await context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    bootstrap_response = await context.request.get(
                        "http://localhost:5050/api/content/bootstrap", headers={"Authorization": f"Bearer {token}"}
                    )
                    assert bootstrap_response.ok, await bootstrap_response.text()
                    bootstrap = await bootstrap_response.json()
                    template = next(item for item in bootstrap["industry_templates"] if item["slug"] == "decoration")
                    task_template = {
                        key: template[key] for key in ("id", "slug", "name", "quick_form_schema", "pro_form_schema")
                    }
                    task = {}
                    writes = []
                    attempts = {"compile": 0, "run": 0}

                    async def task_api(route):
                        request = route.request
                        path = urlsplit(request.url).path
                        payload = request.post_data_json if request.method != "GET" else None
                        if request.method != "GET":
                            writes.append((path, payload))
                        if path == "/api/content/tasks" and request.method == "POST":
                            task.update(
                                **payload,
                                id="ct_unified_ui_test",
                                status="draft",
                                current_stage="brief",
                                workflow_version_id="content-workflow-standardized-factory-v4",
                                runtime_config_snapshot={
                                    "creation_mode": "viral_rewrite",
                                    "strategy_mode": "direction_scoped",
                                },
                                brief={},
                            )
                        elif path.endswith("/compile-brief"):
                            attempts["compile"] += 1
                            if attempts["compile"] == 1:
                                await route.fulfill(status=422, json={"detail": "测试简报校验失败"})
                                return
                            await asyncio.sleep(0.2)
                            task.update(brief=payload["brief"], current_stage="generation")
                        elif path.endswith("/brief"):
                            task["brief"] = payload["brief"]
                        elif path.endswith("/runs"):
                            attempts["run"] += 1
                            if attempts["run"] == 1:
                                await route.fulfill(status=503, json={"detail": "测试启动失败"})
                                return
                            task.update(latest_run_id="run_ui_test", status="queued")
                            await route.fulfill(json={"run_id": "run_ui_test", "status": "queued"})
                            return
                        elif path.endswith("/ocr-results"):
                            await route.fulfill(json={"items": []})
                            return
                        elif request.method == "PATCH":
                            task.update(payload)
                        await route.fulfill(json={"task": task, "template": task_template, "artifact": None})

                    await page.route("**/api/content/tasks**", task_api)
                    await page.route(
                        "**/api/content/runs/**",
                        lambda route: route.fulfill(content_type="text/event-stream", body=""),
                    )
                    # 图片只用于验证选择及请求透传，封面模板与缩略图使用真实服务。
                    preview = io.BytesIO()
                    Image.new("RGB", (180, 240), "#aec9bc").save(preview, format="PNG")
                    image_bytes = preview.getvalue()
                    await page.route(
                        "**/api/material-library/galleries*",
                        lambda route: route.fulfill(
                            json={"galleries": [{"id": "ui-gallery", "name": "测试图库", "count": 1}]}
                        ),
                    )

                    async def material_api(route):
                        if urlsplit(route.request.url).path.endswith(("/file", "/thumbnail")):
                            await route.fulfill(content_type="image/png", body=image_bytes)
                        else:
                            await route.fulfill(
                                json={
                                    "items": [{"id": "ui-image", "name": "测试素材", "category": "ui-gallery"}],
                                    "total": 1,
                                }
                            )

                    await page.route("**/api/material-library/items**", material_api)
                    await page.route(
                        "**/api/content/covers/hycanvas/templates/*/preview.png",
                        lambda route: (
                            route.fulfill(content_type="image/png", body=image_bytes)
                            if route.request.method == "POST"
                            else route.continue_()
                        ),
                    )
                    await page.goto("http://localhost:5173/content/new")
                    await expect(page.locator(".creation-form")).to_be_visible(timeout=30000)
                    if width < 600:
                        await page.get_by_role("button", name="折叠侧边栏").click()
                    request_input = page.locator("#content-request")
                    submit = page.get_by_role("button", name="开始生成", exact=True)
                    await expect(request_input).to_be_visible()
                    await expect(page.get_by_role("button", name="图片识别", exact=True)).to_be_disabled()
                    await expect(page.get_by_text("爆款仿写", exact=True)).to_have_count(1)
                    await expect(page.get_by_text("业务素材与事实简报", exact=True)).to_have_count(0)
                    await expect(page.get_by_role("button", name="创建任务并填写素材")).to_have_count(0)
                    await expect(page.locator("#creation-model")).to_be_visible()
                    await expect(submit).to_have_count(1)
                    await expect(submit).to_be_enabled(timeout=30000)
                    await submit.click()
                    assert not writes
                    await page.locator(".creation-type-field").get_by_text("工种总价", exact=True).click()
                    await page.locator('.ant-select[aria-label="选择具体案例"]').click()
                    await expect(page.locator(".ant-select-item-option-content")).to_have_text(["成都工种总价 · 003"])
                    await page.locator(".ant-select-item-option-content").click()
                    await expect(request_input).not_to_have_value("")
                    await page.locator(".creation-type-field").get_by_text("自我介绍", exact=True).click()
                    await page.locator('.ant-select[aria-label="选择具体案例"]').click()
                    await expect(page.locator(".ant-select-dropdown:visible .ant-select-item-option")).to_have_count(3)
                    await page.keyboard.press("Escape")
                    content = "长沙装修工长，承接泥瓦施工，服务本地业主。"
                    await request_input.fill(content)
                    await page.locator("#creation-model").fill("ui-test-model")
                    await page.locator(".gallery-folder-card").filter(has_text="测试图库").click()
                    await page.locator(".gallery-modal-content .image-choice").filter(has_text="测试素材").click()
                    await page.locator(".gallery-modal-content").get_by_role("button", name="确认选择 （1）").click()
                    await page.get_by_text("AI 封面", exact=True).click()
                    await expect(page.get_by_text("使用 image2 智能生成", exact=True)).to_be_visible()
                    await expect(page.locator(".poster-choice")).to_have_count(0)
                    await expect(page.locator(".selected-gallery-preview-grid.single")).to_be_visible()
                    await expect(page.locator(".selected-gallery-preview-card")).to_have_count(1)
                    await submit.click()
                    await expect(page.get_by_text("测试简报校验失败", exact=True)).to_be_visible()
                    await expect(
                        page.get_by_text("请选择一个封面模板（内置封面或精选封面）", exact=True)
                    ).to_have_count(0)
                    await page.get_by_text("内置封面", exact=True).click()
                    covers = page.locator(".poster-choice")
                    await expect(covers.first).to_be_visible(timeout=30000)
                    await covers.first.click()
                    await expect(covers.first.locator("img")).to_be_visible(timeout=30000)
                    assert await covers.first.locator("img").evaluate("el => el.complete && el.naturalWidth > 0")
                    await expect(page.locator(".ant-message-notice")).to_have_count(0, timeout=10000)
                    await page.locator(".studio-creation").evaluate("el => { el.scrollTop = 0 }")
                    await page.screenshot(path=f"/tmp/content-unified-{width}.png", full_page=True)
                    assert await page.locator(".studio-creation").evaluate("el => el.scrollWidth <= el.clientWidth")
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    assert await submit.evaluate("el => el.getBoundingClientRect().right <= innerWidth")
                    await covers.first.scroll_into_view_if_needed()
                    await page.screenshot(path=f"/tmp/content-unified-materials-{width}.png", full_page=True)
                    if width < 600:
                        # 图片识别可以提前创建草稿，但不清空用户已填内容或选中素材。
                        await page.get_by_role("button", name="图片识别", exact=True).click()
                        await expect(page.get_by_role("heading", name="图片 OCR 识别", exact=True)).to_be_visible()
                        await page.keyboard.press("Escape")
                        await expect(request_input).to_have_value(content)
                        await page.reload()
                        await expect(request_input).to_have_value(content, timeout=30000)
                        await expect(page.locator(".poster-choice.selected")).to_have_count(1)
                        await page.locator("#creation-model").fill("ui-test-model")

                    await expect(request_input).to_have_value(content)
                    await expect(page.locator(".creation-fields")).not_to_have_attribute("inert", "")
                    async with page.expect_response(lambda response: response.url.endswith("/brief")):
                        await page.get_by_role("button", name="保存草稿", exact=True).click()
                    await expect(page.locator(".creation-save-status")).to_have_text("草稿已自动保存")
                    await submit.evaluate("el => { el.click(); el.click() }")
                    await expect(page.get_by_text("测试启动失败", exact=True)).to_be_visible()
                    await expect(page.locator(".creation-fields")).to_have_attribute("inert", "")
                    await expect(page.locator(".creation-save-status")).to_have_text("简报已锁定")
                    await page.evaluate("localStorage.setItem('theme', 'dark')")
                    await page.reload()
                    await expect(page.locator(".creation-save-status")).to_have_text("简报已锁定", timeout=30000)
                    await expect(request_input).to_have_value(content)
                    await page.locator("#creation-model").fill("ui-test-model")
                    await expect(submit).to_be_enabled(timeout=30000)
                    await expect(page.locator(".poster-choice.selected img")).to_be_visible(timeout=30000)
                    await page.locator(".poster-choice.selected").scroll_into_view_if_needed()
                    await page.screenshot(path=f"/tmp/content-unified-locked-dark-{width}.png", full_page=True)
                    await submit.click()
                    await expect(page.locator(".active-run-layout")).to_be_visible(timeout=10000)
                    assert attempts == {"compile": 2, "run": 2}
                    creates = [payload for path, payload in writes if path == "/api/content/tasks"]
                    assert len(creates) == 1
                    assert creates[0]["creation_mode"] == "viral_rewrite"
                    assert creates[0]["content_type_code"] == "CT01"
                    briefs = [payload["brief"] for path, payload in writes if path.endswith("/compile-brief")]
                    assert all(brief["user_request"] == content for brief in briefs)
                    assert all(brief["visual_material"]["image_item_id"] == "ui-image" for brief in briefs)
                    assert briefs[0]["visual_material"]["cover_mode"] == "ai"
                    assert briefs[0]["visual_material"]["hycanvas_template_id"] is None
                    assert all(brief["visual_material"]["hycanvas_template_id"] for brief in briefs[1:])
                    assert all(
                        payload["model_spec"] == "ui-test-model" for path, payload in writes if path.endswith("/runs")
                    )
                    assert not errors, errors
                    # 去掉素材夹具，用真实图库和封面目录检查最终界面，不提交任何写入。
                    await page.unroute("**/api/material-library/galleries*")
                    await page.unroute("**/api/material-library/items**")
                    await page.unroute("**/api/content/covers/hycanvas/templates/*/preview.png")
                    await page.evaluate("localStorage.setItem('theme', 'light')")
                    await page.goto("http://localhost:5173/content/new")
                    await expect(submit).to_be_enabled(timeout=30000)
                    await covers.first.scroll_into_view_if_needed()
                    await expect(covers.first.locator("img")).to_be_visible(timeout=30000)
                    await expect(covers.first.locator("img")).to_have_js_property("complete", True)
                    assert await covers.first.locator("img").evaluate("el => el.naturalWidth > 0")
                    await page.locator(".studio-creation").evaluate("el => { el.scrollTop = 0 }")
                    await page.screenshot(path=f"/tmp/content-unified-real-{width}.png", full_page=True)
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    await context.close()
            except Exception:
                if not page.is_closed():
                    await page.screenshot(path="/tmp/content-unified-failure.png", full_page=True)
                raise
            finally:
                await browser.close()
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
                await page.locator(".content-goal-field .ant-select").click()
                await page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)").get_by_text(
                    "获客转化"
                ).click()
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
