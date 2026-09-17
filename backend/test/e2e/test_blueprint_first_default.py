"""验证当前部署的 Blueprint First 默认入口及新任务版本，不调用模型。"""

import json
import os
import secrets
import socket
import uuid

import httpx
import pytest
from patchright.async_api import async_playwright, expect

from yuxi.content.v3.joint_workflow import PLATFORM_WORKFLOW_PRICE_RECOVERY_ID
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, User
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_deployed_templates_create_blueprint_first_tasks():
    pg_manager.initialize()
    uid = f"blueprint_test_{uuid.uuid4().hex}"
    password = secrets.token_urlsafe(24)
    async with pg_manager.AsyncSession() as db:
        department = Department(name=uid)
        db.add(department)
        await db.flush()
        department_id = department.id
        user = User(
            username=uid,
            uid=uid,
            password_hash=AuthUtils.hash_password(password),
            role="user",
            department_id=department_id,
        )
        db.add(user)
        await db.commit()
        user_id = user.id
    try:
        async with httpx.AsyncClient(
            base_url=os.getenv("TEST_BASE_URL", "http://localhost:5050"), timeout=30
        ) as client:
            response = await client.post("/api/auth/token", data={"username": uid, "password": password})
            response.raise_for_status()
            token = response.json()["access_token"]
            client.headers["Authorization"] = f"Bearer {token}"
            response = await client.get("/api/content/bootstrap")
            response.raise_for_status()
            bootstrap = response.json()
            templates = bootstrap["industry_templates"]
            assert {item["slug"] for item in bootstrap["industry_packs"]} == {"decoration"}
            for section in ("methods", "title_formulas", "content_formulas", "combination_rules"):
                assert bootstrap["rule_bundle"][section]
                assert all(item["industry_scope"] == ["decoration"] for item in bootstrap["rule_bundle"][section])
            assert len(templates) == 1
            assert templates[0]["slug"] == "decoration"
            for template in templates:
                assert template["blueprint_first"], template["id"]
                assert template["default_workflow_version_id"] == PLATFORM_WORKFLOW_PRICE_RECOVERY_ID
                response = await client.post(
                    "/api/content/tasks",
                    json={
                        "industry_template_id": template["id"],
                        "name": "pytest Blueprint First default",
                        "content_goal": template["default_goal"],
                        "creation_mode": "original",
                    },
                )
                response.raise_for_status()
                task = response.json()["task"]
                try:
                    assert task["workflow_version_id"] == PLATFORM_WORKFLOW_PRICE_RECOVERY_ID
                finally:
                    response = await client.delete(f"/api/content/tasks/{task['id']}")
                    response.raise_for_status()
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}"],
                )
                try:
                    context = await browser.new_context()
                    await context.add_init_script(f"localStorage.setItem('user_token', {json.dumps(token)})")
                    page = await context.new_page()
                    await page.goto("http://localhost:5173/content/new")
                    await expect(page.locator(".template-card")).to_have_count(1)
                    await expect(page.locator(".template-card.selected")).to_contain_text("装修与家居")
                    await expect(page.get_by_text("内容目标", exact=True)).to_have_count(0)
                    await expect(page.get_by_text("默认目标", exact=False)).to_have_count(0)
                    await expect(page.locator(".auto-strategy-hint")).to_be_visible()
                    await expect(page.get_by_text("一级内容方向", exact=True)).to_have_count(0)
                    choices = page.get_by_role("radio")
                    await expect(choices).to_have_count(7)
                    for name in ("自我介绍", "项目单价", "单价+面积", "工种总价", "人工+辅材", "工艺展示", "日常工作"):
                        await expect(page.locator(".creation-type-field").get_by_text(name, exact=True)).to_be_visible()
                    await page.locator(".creation-type-field").get_by_text("工种总价", exact=True).click()
                    await expect(page.get_by_role("radio", name="工种总价", exact=True)).to_be_checked()
                    async with page.expect_response(
                        lambda response: response.url.endswith("/api/content/tasks")
                        and response.request.method == "POST"
                    ) as created:
                        await page.get_by_role("button", name="创建任务并填写素材").click()
                    response = await created.value
                    assert response.ok
                    task = (await response.json())["task"]
                    try:
                        submitted = response.request.post_data_json
                        decoration = next(item for item in templates if item["slug"] == "decoration")
                        assert submitted["industry_template_id"] == decoration["id"]
                        assert submitted["content_goal"] == decoration["default_goal"]
                        assert submitted["content_type_code"] == "CT04"
                        assert task["content_type_code"] == "CT04"
                        candidates_response = await client.get(f"/api/content/tasks/{task['id']}/strategy/candidates")
                        candidates_response.raise_for_status()
                        candidates = candidates_response.json()["strategy_candidates"]
                        assert {item["code"] for item in candidates["direction_options"]} == {"CT04"}
                        await expect(page).to_have_url(f"http://localhost:5173/content/tasks/{task['id']}")
                        await page.get_by_role("button", name="形成事实简报并进入 V3 生产").click()
                        await expect(page.get_by_text("请填写内容需求", exact=True)).to_be_visible()
                        await expect(page.get_by_text("请补充：品牌", exact=False)).to_have_count(0)
                    finally:
                        response = await client.delete(f"/api/content/tasks/{task['id']}")
                        response.raise_for_status()
                finally:
                    await browser.close()
    finally:
        async with pg_manager.AsyncSession() as db:
            await db.delete(await db.get(User, user_id))
            await db.delete(await db.get(Department, department_id))
            await db.commit()
        await pg_manager.async_engine.dispose()
