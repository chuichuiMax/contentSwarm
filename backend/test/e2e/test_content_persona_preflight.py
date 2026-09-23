"""真实 HTTP 创建、编译和预检人设物料，不调用生成模型。"""

import secrets
import uuid

import pytest

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, User
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_craft_preflight_requests_persona_then_accepts_confirmed_fact(e2e_client):
    uid = f"persona_preflight_{uuid.uuid4().hex}"
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
        headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(user.id)})}"}

    task_id = None
    try:
        response = await e2e_client.get("/api/content/bootstrap", headers=headers)
        assert response.status_code == 200, response.text
        template = next(item for item in response.json()["industry_templates"] if item["slug"] == "decoration")
        response = await e2e_client.post(
            "/api/content/tasks",
            headers=headers,
            json={
                "industry_template_id": template["id"],
                "content_goal": template["default_goal"],
                "content_type_code": "CT06",
                "creation_mode": "viral_rewrite",
                "name": "pytest 人设物料预检",
            },
        )
        assert response.status_code == 200, response.text
        task_id = response.json()["task"]["id"]
        brief = {
            "user_request": "我从事装修行业五年了，在长沙做拆除，自有工人无转包。",
            "form_values": {},
        }
        response = await e2e_client.post(
            f"/api/content/tasks/{task_id}/compile-brief", headers=headers, json={"brief": brief}
        )
        assert response.status_code == 200, response.text
        response = await e2e_client.get(f"/api/content/tasks/{task_id}/creation-plan/preview", headers=headers)
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview["requires_fact_extraction"] is True
        assert "persona_fact" in preview["gaps"]["missing_variable_codes"]
        assert any(
            item["variable_code"] == "persona_fact" and item["required"]
            for item in preview["material_manifest"]["requirements"]
        )
        assert preview["plan"]["title_formula"]["code"] == "FRT05"
        assert preview["plan"]["body_formula"]["code"] == "FRB04"

        brief.pop("user_request")
        brief["form_values"] = {
            "brand_name": "测试工长",
            "audience": ["长沙业主"],
            "pain": ["担心施工质量"],
            "advantage": ["自有工人无转包"],
            "renovation_scene": "旧房拆除",
            "quote_type": "项目报价",
            "project_type": "两居室",
            "craft_and_materials": ["拆除"],
            "persona_fact": "我从事装修行业五年了",
            "location": "长沙",
            "product": "装修",
            "process": ["拆除"],
            "advantages": ["自有工人无转包"],
        }
        response = await e2e_client.post(
            f"/api/content/tasks/{task_id}/compile-brief", headers=headers, json={"brief": brief}
        )
        assert response.status_code == 200, response.text
        response = await e2e_client.get(f"/api/content/tasks/{task_id}/creation-plan/preview", headers=headers)
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview["gaps"]["missing_variable_codes"] == []
        assert preview["requires_fact_extraction"] is False
    finally:
        try:
            if task_id:
                response = await e2e_client.delete(f"/api/content/tasks/{task_id}", headers=headers)
                assert response.status_code == 200, response.text
        finally:
            async with pg_manager.AsyncSession() as db:
                await db.delete(await db.get(User, user.id))
                await db.delete(await db.get(Department, department.id))
                await db.commit()
            await pg_manager.async_engine.dispose()
