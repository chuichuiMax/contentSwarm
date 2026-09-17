"""复用完整链路测试留下的隔离任务，核验正文及失败节点恢复，不重跑成功分支。"""

import asyncio
import os
import time
import uuid

import httpx
import pytest
from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentTask
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("industry", ["education", "decoration"])
async def test_existing_workflow_text_and_failed_node_recovery(industry):
    uid = os.environ.get("RULE_EDITOR_TEST_UID")
    if not uid or os.environ.get("STRATEGY_RECOVERY_E2E") != "1":
        pytest.skip("需显式复用隔离工作流测试留下的任务")
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one()
        headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(user.id)})}"}
        task = (
            await db.execute(
                select(ContentTask)
                .where(
                    ContentTask.name == f"pytest 联合策略完整工作流 {industry}",
                    ContentTask.created_by == uid,
                )
                .order_by(ContentTask.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
        task_id, run_id = task.id, task.latest_run_id
    await pg_manager.async_engine.dispose()
    async with httpx.AsyncClient(
        base_url=os.environ.get("TEST_BASE_URL", "http://localhost:5050"), timeout=30
    ) as client:
        response = await client.get(f"/api/content/runs/{run_id}", headers=headers)
        response.raise_for_status()
        result = response.json()

        def approved():
            return any(
                node["node_id"] == "human_content_approval" and node["status"] == "completed"
                for node in result["nodes"]
            )

        retried = False
        try:
            if not approved():
                assert result["run"]["status"] == "failed"
                failed = next(node for node in result["nodes"] if node["status"] == "failed")
                response = await client.post(
                    f"/api/content/runs/{run_id}/retry-node",
                    headers=headers,
                    json={
                        "request_id": uuid.uuid4().hex,
                        "node_id": failed["node_id"],
                    },
                )
                assert response.status_code == 200, response.text
                run_id = response.json()["run_id"]
                retried = True
                started = time.monotonic()
                while time.monotonic() - started < 300:
                    response = await client.get(f"/api/content/runs/{run_id}", headers=headers)
                    response.raise_for_status()
                    result = response.json()
                    if approved() or result["run"]["status"] in {"failed", "cancelled", "interrupted", "completed"}:
                        break
                    await asyncio.sleep(2)
            assert approved(), result["run"].get("error_message") or result["nodes"]
            assert any(n["node_id"] == "generate_content" and n["status"] == "completed" for n in result["nodes"])
            selection_runs = [
                run for run in result["delegated_agents"] if run["agent_slug"] == "content-joint-strategy-agent"
            ]
            assert len(selection_runs) == 1, "重试不得再次调用已完成的策略 Agent"
            decision = (await client.get(f"/api/content/tasks/{task_id}/strategy/decision", headers=headers)).json()
            assert decision["snapshot"]["industry_slug"] == industry
            print(f"{industry}: text generation/validation/approval passed, retry={retried}")
        finally:
            if retried and result["run"]["status"] not in {"failed", "cancelled", "completed", "interrupted"}:
                await client.post(f"/api/content/runs/{run_id}/cancel", headers=headers)
