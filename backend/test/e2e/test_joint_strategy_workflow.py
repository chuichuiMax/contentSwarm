"""隔离任务运行正式 Worker 工作流，验收正文审批节点并取消后续封面步骤。"""

import asyncio
import os
import time
import uuid

import httpx
import pytest
from sqlalchemy import select

from test.e2e.test_joint_strategy_agent import make_brief
from yuxi.content.model.strategy import load_selection_policy
from yuxi.content.model.workflows.definition import workflow_definition_hash
from yuxi.content.v3.joint_workflow import PLATFORM_WORKFLOW_JOINT_ID, WORKFLOW_JOINT
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentTask
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("industry", ["decoration", "education"])
async def test_http_worker_joint_strategy_reaches_content_approval(industry):
    uid = os.environ.get("RULE_EDITOR_TEST_UID")
    if not uid:
        pytest.skip("需要隔离测试管理员")
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one()
        headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(user.id)})}"}
    await pg_manager.async_engine.dispose()
    task_id = run_id = None
    terminal = False
    async with httpx.AsyncClient(
        base_url=os.environ.get("TEST_BASE_URL", "http://localhost:5050"), timeout=30
    ) as client:
        try:
            response = await client.post(
                "/api/content/tasks",
                headers=headers,
                json={
                    "industry_template_id": f"industry-{industry}-v3",
                    "content_goal": "educate",
                    "content_type_code": "CT02",
                    "name": f"pytest 联合策略完整工作流 {industry}",
                },
            )
            assert response.status_code == 200, response.text
            task_id = response.json()["task"]["id"]
            response = await client.put(
                f"/api/content/tasks/{task_id}/brief", headers=headers, json={"brief": make_brief(industry)}
            )
            assert response.status_code == 200, response.text
            async with pg_manager.AsyncSession() as db:
                task = await db.get(ContentTask, task_id)
                task.workflow_version_id = PLATFORM_WORKFLOW_JOINT_ID
                task.workflow_definition_hash = workflow_definition_hash(WORKFLOW_JOINT)
                if industry != "decoration":
                    task.content_type_code = None
                task.runtime_config_snapshot_json = {
                    **task.runtime_config_snapshot_json,
                    "workflow_version_id": task.workflow_version_id,
                    "workflow_definition_hash": task.workflow_definition_hash,
                    "selection_policy_snapshot": load_selection_policy(),
                    "strategy_mode": "direction_scoped" if industry == "decoration" else "scored",
                }
                await db.commit()
            await pg_manager.async_engine.dispose()
            started = time.monotonic()
            response = await client.post(
                f"/api/content/tasks/{task_id}/runs", headers=headers, json={"request_id": uuid.uuid4().hex}
            )
            assert response.status_code == 200, response.text
            run_id = response.json()["run_id"]
            print(f"workflow test task={task_id} run={run_id} industry={industry}")
            while time.monotonic() - started < 360:
                response = await client.get(f"/api/content/runs/{run_id}", headers=headers)
                assert response.status_code == 200, response.text
                result = response.json()
                if any(
                    n["node_id"] == "human_content_approval" and n["status"] == "completed" for n in result["nodes"]
                ):
                    break
                if result["run"]["status"] in {"completed", "interrupted", "failed", "cancelled"}:
                    terminal = True
                    break
                await asyncio.sleep(3)
            assert any(
                n["node_id"] == "human_content_approval" and n["status"] == "completed" for n in result["nodes"]
            ), result["run"].get("error_message") or result["nodes"]
            node_ids = {node["node_id"] for node in result["nodes"]}
            assert "human_content_approval" in node_ids, str(result["nodes"])
            assert not {"collect_viral_candidates", "select_viral_reference"} & node_ids
            decision = (await client.get(f"/api/content/tasks/{task_id}/strategy/decision", headers=headers)).json()
            assert decision["snapshot"]["industry_slug"] == industry
            assert decision["snapshot"]["snapshot_hash"]
            print(f"{industry}: HTTP/Worker to content approval {time.monotonic() - started:.1f}s")
        finally:
            if run_id and not terminal:
                await client.post(f"/api/content/runs/{run_id}/cancel", headers=headers)
            if task_id and not os.environ.get("KEEP_STRATEGY_TEST_TASK"):
                response = await client.delete(f"/api/content/tasks/{task_id}", headers=headers)
                assert response.status_code == 200, response.text
