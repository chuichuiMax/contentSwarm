"""新候选接口的真实 HTTP 验证；仅创建及清理测试任务，不调用模型。"""

import pytest
from sqlalchemy import update

from test.integration.api.test_rule_library_lifecycle import rule_editor_headers  # noqa: F401
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import ContentTask


pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("decoration", [True, False])
async def test_industry_candidates_use_task_scope_without_creating_run(test_client, rule_editor_headers, decoration):  # noqa: F811
    headers = rule_editor_headers
    response = await test_client.get("/api/content/bootstrap", headers=headers)
    assert response.status_code == 200
    bootstrap = response.json()
    template = next(item for item in bootstrap["industry_templates"] if (item["slug"] == "decoration") == decoration)
    response = await test_client.post(
        "/api/content/tasks",
        headers=headers,
        json={
            "industry_template_id": template["id"],
            "content_type_code": "CT01",
            "content_goal": "brand",
            "name": "pytest 行业策略候选接口",
        },
    )
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    try:
        if not decoration:
            # 构造没有装修方向的历史/新行业任务，直接验证 API 不依赖 CT 枚举。
            pg_manager.initialize()
            async with pg_manager.AsyncSession() as db:
                await db.execute(update(ContentTask).where(ContentTask.id == task["id"]).values(content_type_code=None))
                await db.commit()
            await pg_manager.async_engine.dispose()
        response = await test_client.get(f"/api/content/tasks/{task['id']}/strategy/candidates", headers=headers)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["creates_run"] is False
        candidates = result["strategy_candidates"]
        assert candidates["industry_slug"] == template["slug"]
        assert candidates["rule_version_id"] == task["rule_version_id"]
        assert candidates["strategy_mode"] == ("direction_scoped" if decoration else "scored")
        assert candidates["direction_code"] == ("CT01" if decoration else None)
        assert ("formula" in candidates["scoring"]) is not decoration
        assert candidates["methods"] and candidates["valid_formula_pairs"]
        assert all(
            not row["industry_scope"] or template["slug"] in row["industry_scope"] for row in candidates["source_rules"]
        )
        if decoration:
            assert all("CT01" in row["content_type_codes"] for row in candidates["source_rules"])
        else:
            assert len({tuple(row["content_type_codes"]) for row in candidates["source_rules"]}) > 1
        again = await test_client.get(f"/api/content/tasks/{task['id']}/strategy/candidates", headers=headers)
        assert again.json() == result
    finally:
        response = await test_client.delete(f"/api/content/tasks/{task['id']}", headers=headers)
        assert response.status_code == 200, response.text


async def test_strategy_candidates_require_authentication_and_hide_missing_task(test_client, rule_editor_headers):  # noqa: F811
    path = "/api/content/tasks/nonexistent-strategy-test/strategy/candidates"
    response = await test_client.get(path)
    assert response.status_code in {401, 403}
    response = await test_client.get(path, headers=rule_editor_headers)
    assert response.status_code == 404, response.text
