"""规则编辑的真实 HTTP 回归。RULE_EDITOR_TEST_UID 指定本地测试管理员。"""

import os
from copy import deepcopy

import pytest
import pytest_asyncio
from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.utils.auth_utils import AuthUtils


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def rule_editor_headers():
    uid = os.environ.get("RULE_EDITOR_TEST_UID")
    assert uid, "请设置 RULE_EDITOR_TEST_UID 为本地测试管理员 UID"
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        user = (await db.execute(select(User).where(User.uid == uid, User.is_deleted == 0))).scalar_one()
        assert user.role in {"admin", "superadmin"}
        token = AuthUtils.create_access_token({"sub": str(user.id)})
    await pg_manager.async_engine.dispose()
    return {"Authorization": f"Bearer {token}"}


async def test_new_task_preview_uses_published_catalog_and_exact_matrix(test_client, rule_editor_headers):
    from yuxi.content.v3.fixtures import load_decoration_matrix

    headers = rule_editor_headers
    bootstrap = (await test_client.get("/api/content/bootstrap", headers=headers)).json()
    template = next(item for item in bootstrap["industry_templates"] if item["slug"] == "decoration")
    versions = (await test_client.get("/api/content/admin/rules", headers=headers)).json()["items"]
    published = next(item for item in versions if item["status"] == "published" and item["schema_version"] == 3)
    response = await test_client.post(
        "/api/content/tasks",
        headers=headers,
        json={
            "industry_template_id": template["id"],
            "mode": "quick",
            "content_goal": "brand",
            "content_type_code": "CT05",
            "name": "pytest 规则版本与矩阵端到端验证",
        },
    )
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["rule_version_id"] == published["id"]
    try:
        response = await test_client.put(
            f"/api/content/tasks/{task['id']}/brief",
            headers=headers,
            json={
                "brief": {"audience": ["装修业主"], "form_values": {"brand_name": "pytest", "process": ["施工验收"]}},
            },
        )
        assert response.status_code == 200, response.text
        bundle = (
            await test_client.get(
                f"/api/content/admin/rules/{published['id']}/bundle",
                headers=headers,
            )
        ).json()["bundle"]
        by_id = {item["id"]: item for item in bundle["combination_rules"]}
        matrix = load_decoration_matrix()["groups"]
        for direction in dict.fromkeys(item["content_direction"]["code"] for item in matrix):
            response = await test_client.post(
                f"/api/content/tasks/{task['id']}/strategy/recommend-v3",
                headers=headers,
                json={"content_direction_code": direction},
            )
            assert response.status_code == 200, response.text
            preview = response.json()
            assert preview["rule_version_id"] == task["rule_version_id"]
            assert preview["creates_run"] is False
            eligible = preview["decision"]["eligible_groups"]
            expected = [item for item in matrix if item["content_direction"]["code"] == direction]
            assert len(eligible) == len(expected) == 4
            for actual, source in zip(eligible, expected, strict=True):
                assert actual["title_formula_candidate_codes"] == source["title_formula_candidate_codes"]
                assert actual["body_formula_candidate_codes"] == source["body_formula_candidate_codes"]
                assert by_id[actual["group_code"]]["scenario_description"] == source["scenario_description"]
    finally:
        response = await test_client.delete(f"/api/content/tasks/{task['id']}", headers=headers)
        assert response.status_code == 200, response.text


async def test_rule_formula_and_combination_edit_disable_delete_persistence(test_client, rule_editor_headers):
    headers = rule_editor_headers
    versions = (await test_client.get("/api/content/admin/rules", headers=headers)).json()["items"]
    assert not any(item["status"] == "draft" for item in versions), "已有用户草稿，测试不会覆盖"
    published = next(item for item in versions if item["status"] == "published" and item["schema_version"] == 3)
    url = f"/api/content/admin/rules/{published['id']}/bundle"
    original = (await test_client.get(url, headers=headers)).json()["bundle"]
    assert (await test_client.put(url, headers=headers, json={**original, "changelog": "不可修改"})).status_code == 409
    assert (
        await test_client.post("/api/content/admin/rules/drafts", json={"source_version_id": published["id"]})
    ).status_code == 401

    response = await test_client.post(
        "/api/content/admin/rules/drafts",
        headers=headers,
        json={"source_version_id": published["id"], "changelog": "pytest 规则回归"},
    )
    assert response.status_code == 200, response.text
    draft = response.json()["bundle"]
    version_id = draft["version"]["id"]
    draft_url = f"/api/content/admin/rules/{version_id}/bundle"
    try:
        title = draft["title_formulas"][0]
        body = draft["content_formulas"][0]
        title["core_goal"] = "pytest 编辑标题核心目标"
        title["source_content"] = {"variables": ["pytest 原文变量说明"]}
        title["enabled"] = False
        body["structure_schema"][0] = "pytest 编辑正文第一段"
        body["enabled"] = False
        draft["combination_rules"][0]["enabled"] = False
        draft["combination_rules"][0]["scenario_description"] = "pytest 编辑组合场景"
        response = await test_client.put(draft_url, headers=headers, json={**draft, "changelog": "pytest 编辑与停用"})
        assert response.status_code == 200, response.text
        assert response.json()["validation"]["errors"] == []
        stored = (await test_client.get(draft_url, headers=headers)).json()["bundle"]
        assert stored["title_formulas"][0]["source_content"]["variables"] == ["pytest 原文变量说明"]
        assert stored["title_formulas"][0]["core_goal"] == "pytest 编辑标题核心目标"
        assert stored["content_formulas"][0]["structure_schema"][0] == "pytest 编辑正文第一段"
        assert stored["title_formulas"][0]["enabled"] is False
        assert stored["content_formulas"][0]["enabled"] is False
        assert stored["combination_rules"][0]["enabled"] is False

        active = (await test_client.get(f"/api/content/rule-versions/{version_id}/bundle", headers=headers)).json()[
            "bundle"
        ]
        assert title["code"] not in {item["code"] for item in active["title_formulas"]}
        assert body["code"] not in {item["code"] for item in active["content_formulas"]}
        assert all(title["code"] not in item["title_formula_candidate_codes"] for item in active["combination_rules"])
        assert all(body["code"] not in item["body_formula_candidate_codes"] for item in active["combination_rules"])
        assert all(item["enabled"] for item in active["combination_rules"])

        restored = deepcopy(original)
        response = await test_client.put(draft_url, headers=headers, json={**restored, "changelog": "pytest 重新启用"})
        assert response.status_code == 200, response.text
        assert all(item["enabled"] for item in response.json()["bundle"]["title_formulas"])
        assert all(item["enabled"] for item in response.json()["bundle"]["content_formulas"])
        assert all(item["enabled"] for item in response.json()["bundle"]["combination_rules"])

        removed_title = restored["title_formulas"].pop()["code"]
        removed_body = restored["content_formulas"].pop()["code"]
        restored["combination_rules"].pop(0)
        for group in restored["combination_rules"]:
            group["title_formula_candidate_codes"] = [
                code for code in group["title_formula_candidate_codes"] if code != removed_title
            ]
            group["body_formula_candidate_codes"] = [
                code for code in group["body_formula_candidate_codes"] if code != removed_body
            ]
        restored["combination_rules"] = [
            item
            for item in restored["combination_rules"]
            if item["title_formula_candidate_codes"] and item["body_formula_candidate_codes"]
        ]
        response = await test_client.put(
            draft_url, headers=headers, json={**restored, "changelog": "pytest 删除及引用清理"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["validation"]["errors"] == []
        stored = (await test_client.get(draft_url, headers=headers)).json()["bundle"]
        assert removed_title not in {item["code"] for item in stored["title_formulas"]}
        assert removed_body not in {item["code"] for item in stored["content_formulas"]}
        assert len(stored["combination_rules"]) == len(restored["combination_rules"])
        assert (await test_client.get(url, headers=headers)).json()["bundle"] == original
    finally:
        response = await test_client.delete(f"/api/content/admin/rules/{version_id}", headers=headers)
        assert response.status_code == 200, response.text
