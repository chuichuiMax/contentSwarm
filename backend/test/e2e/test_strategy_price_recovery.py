"""用指定失败任务的资料创建隔离副本，验证真实 Worker/价格库/确认/复评链路。"""

import asyncio
import os
import re
import time
import uuid

import httpx
import pytest
from sqlalchemy import select

from yuxi.content.model.workflows.definition import workflow_definition_hash
from yuxi.content.schemas import ContentVisualMaterialSelection
from yuxi.content.v3.joint_workflow import PLATFORM_WORKFLOW_PRICE_RECOVERY_ID, WORKFLOW_PRICE_RECOVERY
from yuxi.services.run_queue_service import list_run_stream_events
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentNodeRun, ContentTask
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_worker_uses_standard_quotes_without_project_quantities():
    source_id = os.getenv("PRICE_RECOVERY_SOURCE_TASK_ID")
    if not source_id:
        pytest.skip("需指定含报价缺口的任务；测试只修改隔离副本")
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        source = await db.get(ContentTask, source_id)
        user = (await db.execute(select(User).where(User.uid == source.created_by))).scalar_one()
        token = AuthUtils.create_access_token({"sub": str(user.id)})
        brief = dict(source.brief_json)
        brief["visual_material"] = {
            k: v
            for k, v in (brief.get("visual_material") or {}).items()
            if k in ContentVisualMaterialSelection.model_fields
        }
        template_id = source.industry_template_version_id
    await pg_manager.async_engine.dispose()

    async with httpx.AsyncClient(
        base_url="http://localhost:5050", timeout=30, headers={"Authorization": f"Bearer {token}"}
    ) as client:
        task_id = run_id = None
        terminal = False
        try:
            response = await client.post(
                "/api/content/tasks",
                json={
                    "industry_template_id": template_id,
                    "content_goal": "acquire",
                    "creation_mode": "viral_rewrite",
                    "name": "pytest 报价补证隔离验证",
                },
            )
            assert response.status_code == 200, response.text
            task_id = response.json()["task"]["id"]
            response = await client.put(f"/api/content/tasks/{task_id}/brief", json={"brief": brief})
            assert response.status_code == 200, response.text
            async with pg_manager.AsyncSession() as db:
                task = await db.get(ContentTask, task_id)
                task.workflow_version_id = PLATFORM_WORKFLOW_PRICE_RECOVERY_ID
                task.workflow_definition_hash = workflow_definition_hash(WORKFLOW_PRICE_RECOVERY)
                task.runtime_config_snapshot_json = {
                    **task.runtime_config_snapshot_json,
                    "workflow_version_id": task.workflow_version_id,
                    "workflow_definition_hash": task.workflow_definition_hash,
                }
                await db.commit()
            await pg_manager.async_engine.dispose()
            response = await client.post(f"/api/content/tasks/{task_id}/runs", json={"request_id": uuid.uuid4().hex})
            assert response.status_code == 200, response.text
            run_id = response.json()["run_id"]
            print(f"price recovery task={task_id} run={run_id}", flush=True)
            deadline = time.monotonic() + 720
            resumed = False
            price_ids = set()
            while time.monotonic() < deadline:
                response = await client.get(f"/api/content/runs/{run_id}")
                assert response.status_code == 200, response.text
                result = response.json()
                status = result["run"]["status"]
                nodes = {n["node_id"]: n for n in result["nodes"]}
                if "plan_visuals" in nodes:
                    assert resumed
                    assert nodes["lock_creation_strategy"]["status"] == "completed"
                    assert nodes["human_content_approval"]["status"] == "completed"
                    # The next node receives the final, validated draft even when semantic review is disabled.
                    async with pg_manager.AsyncSession() as db:
                        reviewed = (await db.execute(select(ContentNodeRun).where(
                            ContentNodeRun.task_id == task_id,
                            ContentNodeRun.node_id == "plan_visuals",
                        ).order_by(ContentNodeRun.started_at.desc()))).scalars().first()
                        payload = reviewed.input_snapshot.get("visible_payload")
                    await pg_manager.async_engine.dispose()
                    if payload is None:
                        await asyncio.sleep(2)
                        continue
                    draft = payload["content_draft"]
                    assert "标准单价" in draft["body"], draft["body"]
                    cited = {eid for p in draft["paragraph_evidence"] for eid in p["evidence_ids"]}
                    used_prices = [i for i in payload["evidence_bundle"]["items"] if i["id"] in price_ids & cited]
                    assert used_prices
                    assert any(
                        price in draft["body"]
                        for item in used_prices
                        for price in re.findall(r"\d+(?:\.\d+)?(?=\s*元)", item["value"])
                    ), draft["body"]
                    print("标准单价通过策略锁定、正文生成及确定性校验：\n" + draft["body"], flush=True)
                    terminal = status in {"failed", "cancelled", "completed", "interrupted"}
                    break
                if status == "interrupted":
                    events = await list_run_stream_events(run_id, limit=500)
                    interrupt = next(
                        e["payload"]["payload"] for e in reversed(events) if e["event_type"] == "interrupt"
                    )
                    assert result["event_summary"]["knowledge_retrieval_count"] >= 1
                    assert interrupt, result.keys()
                    assert interrupt["node_id"] in {"confirm_strategy_prices", "confirm_high_risk_facts"}, interrupt
                    items = interrupt["evidence_items"]
                    if interrupt["node_id"] == "confirm_strategy_prices":
                        assert items and all(i["source_type"] == "knowledge_base" for i in items)
                        assert all(i["verified_status"] == "retrieved" for i in items)
                        price_ids = {i["id"] for i in items if i["metadata"]["price_basis"] == "standard_unit_price"}
                        assert price_ids
                    # Only the disposable test task is approved; the user's task remains untouched.
                    response = await client.post(
                        f"/api/content/runs/{run_id}/resume",
                        json={
                            "request_id": uuid.uuid4().hex,
                            "resume": {**interrupt, "confirmed_evidence_ids": interrupt["evidence_ids"]},
                        },
                    )
                    assert response.status_code == 200, response.text
                    run_id = response.json()["run_id"]
                    resumed = True
                elif status in {"failed", "cancelled", "completed"}:
                    terminal = True
                    pytest.fail(str(result["run"].get("error_message") or result))
                await asyncio.sleep(2)
            else:
                pytest.fail("报价补证链路超时")
        finally:
            if run_id and not terminal:
                await client.post(f"/api/content/runs/{run_id}/cancel")
                for _ in range(30):
                    response = await client.get(f"/api/content/runs/{run_id}")
                    assert response.status_code == 200, response.text
                    if response.json()["run"]["status"] in {"cancelled", "failed", "completed", "interrupted"}:
                        break
                    await asyncio.sleep(1)
                else:
                    pytest.fail("测试运行未停止，保留隔离任务供排查")
            if task_id:
                for _ in range(15):
                    response = await client.delete(f"/api/content/tasks/{task_id}")
                    if response.status_code == 200:
                        break
                    await asyncio.sleep(1)
                assert response.status_code == 200, response.text
