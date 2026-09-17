"""切换后六行业真实 HTTP 创建：装修必须选方向，其他行业不强制装修方向。"""

import pytest

from test.integration.api.test_rule_library_lifecycle import rule_editor_headers  # noqa: F401
from yuxi.content.v3.joint_workflow import PLATFORM_WORKFLOW_JOINT_ID, BLUEPRINT_FIRST_WORKFLOW_IDS


pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("industry", ["decoration", "education", "food", "beauty", "retail", "professional-services"])
async def test_new_tasks_use_joint_policy_in_each_industry(test_client, rule_editor_headers, industry):  # noqa: F811
    payload = {
        "industry_template_id": f"industry-{industry}-v3",
        "content_goal": "educate",
        "name": "pytest 联合策略行业创建",
    }
    if industry == "decoration":
        payload["content_type_code"] = "CT02"
    response = await test_client.post("/api/content/tasks", headers=rule_editor_headers, json=payload)
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    try:
        assert task["workflow_version_id"] in {PLATFORM_WORKFLOW_JOINT_ID, *BLUEPRINT_FIRST_WORKFLOW_IDS}
        automatic = task["workflow_version_id"] in BLUEPRINT_FIRST_WORKFLOW_IDS
        assert task["content_type_code"] == ("CT02" if industry == "decoration" and not automatic else None)
        mode = "direction_scoped" if industry == "decoration" else "scored"
        assert task["runtime_config_snapshot"]["strategy_mode"] == mode
        response = await test_client.get(
            f"/api/content/tasks/{task['id']}/strategy/candidates", headers=rule_editor_headers
        )
        assert response.status_code == 200, response.text
        pool = response.json()["strategy_candidates"]
        assert pool["industry_slug"] == industry and pool["strategy_mode"] == mode
        assert bool(pool["scoring"].get("formula")) == (industry != "decoration")
        response = await test_client.get(
            f"/api/content/tasks/{task['id']}/strategy/decision", headers=rule_editor_headers
        )
        assert response.json() == {"decision": None, "snapshot": None}
    finally:
        response = await test_client.delete(f"/api/content/tasks/{task['id']}", headers=rule_editor_headers)
        assert response.status_code == 200, response.text


async def test_decoration_direction_requirement_matches_active_workflow(test_client, rule_editor_headers):  # noqa: F811
    response = await test_client.post(
        "/api/content/tasks",
        headers=rule_editor_headers,
        json={
            "industry_template_id": "industry-decoration-v3",
            "content_goal": "educate",
        },
    )
    if response.status_code == 200:
        task = response.json()["task"]
        try:
            assert task["workflow_version_id"] in BLUEPRINT_FIRST_WORKFLOW_IDS
            assert task["content_type_code"] is None
        finally:
            deleted = await test_client.delete(f"/api/content/tasks/{task['id']}", headers=rule_editor_headers)
            assert deleted.status_code == 200
    else:
        assert response.status_code == 422
        assert "CONTENT_DIRECTION_REQUIRED" in response.text
