"""真实 Worker 验证正文模型视图与调用预算，只操作失败任务的隔离副本。"""

import asyncio
import json
import os
import time
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from yuxi.content.schemas import ContentVisualMaterialSelection
from yuxi.services.run_queue_service import list_run_stream_events
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentNodeRun, ContentTask
from yuxi.utils.auth_utils import AuthUtils


@pytest_asyncio.fixture(scope="module", loop_scope="module", autouse=True)
async def close_replay_queue_clients():
    yield
    from yuxi.services.run_queue_service import close_queue_clients

    await close_queue_clients()


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("creation_mode", ["original", "viral_rewrite"])
async def test_projected_strategy_and_original_generation(creation_mode):
    source_id = os.getenv("GENERATION_SOURCE_TASK_ID")
    if not source_id:
        pytest.skip("需指定失败任务；只执行隔离副本")
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
        base_url="http://localhost:5050",
        timeout=30,
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        task_id = run_id = None
        try:
            response = await client.post(
                "/api/content/tasks",
                json={
                    "industry_template_id": template_id,
                    "content_goal": "acquire",
                    "creation_mode": creation_mode,
                    "name": "pytest 策略输入隔离验证",
                },
            )
            assert response.status_code == 200, response.text
            task_id = response.json()["task"]["id"]
            response = await client.put(f"/api/content/tasks/{task_id}/brief", json={"brief": brief})
            assert response.status_code == 200, response.text
            response = await client.post(f"/api/content/tasks/{task_id}/runs", json={"request_id": uuid.uuid4().hex})
            assert response.status_code == 200, response.text
            run_id = response.json()["run_id"]
            print(f"generation validation task={task_id} run={run_id}", flush=True)
            deadline = time.monotonic() + 600
            strategy_verified = False
            while time.monotonic() < deadline:
                response = await client.get(f"/api/content/runs/{run_id}")
                assert response.status_code == 200, response.text

                result = response.json()
                nodes = {node["node_id"]: node for node in result["nodes"]}
                if not strategy_verified and nodes.get("select_creation_strategy", {}).get("status") == "completed":
                    async with pg_manager.AsyncSession() as db:
                        selection = (
                            (
                                await db.execute(
                                    select(ContentNodeRun).where(
                                        ContentNodeRun.task_id == task_id,
                                        ContentNodeRun.node_id == "select_creation_strategy",
                                        ContentNodeRun.status == "completed",
                                    )
                                )
                            )
                            .scalars()
                            .one()
                        )
                        snapshot = selection.input_snapshot
                    await pg_manager.async_engine.dispose()
                    original_input = snapshot["visible_payload"]
                    model_input = snapshot["model_visible_payload"]
                    runtime = snapshot["runtime_config_snapshot"]

                    def size(value):
                        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

                    assert size(model_input) < size(original_input) * 0.85
                    assert runtime["model_input_contract"] == "JointStrategyPromptV1"
                    assert model_input["channel_profile"]["version_id"] == source.channel_profile_version_id
                    assert "visual_material" not in model_input["runtime_config_snapshot"]
                    if creation_mode == "original":
                        assert "reference_candidates" not in model_input
                        assert "prepared-viral-reference-selector" not in [s["slug"] for s in runtime["skills"]]
                    else:
                        assert model_input["reference_candidates"]
                        assert "prepared-viral-reference-selector" in [s["slug"] for s in runtime["skills"]]
                        assert all("reference_blueprint" not in item for item in model_input["reference_candidates"])
                    events = await list_run_stream_events(run_id, limit=500)
                    calls = [e["payload"]["payload"] for e in events if e["event_type"] == "content.model.started"]
                    for call in calls:
                        if call.get("node_id") == "select_creation_strategy":
                            assert set(call["applied_skills"]) == {s["slug"] for s in runtime["skills"]}
                    print(
                        json.dumps(
                            {
                                "mode": creation_mode,
                                "strategy_before": size(original_input),
                                "strategy_after": size(model_input),
                                "channel": model_input["channel_profile"]["code"],
                            }
                        ),
                        flush=True,
                    )
                    strategy_verified = True
                    if creation_mode == "viral_rewrite":
                        break
                if nodes.get("human_content_approval", {}).get("status") == "completed":
                    async with pg_manager.AsyncSession() as db:
                        generated = (
                            (
                                await db.execute(
                                    select(ContentNodeRun)
                                    .where(
                                        ContentNodeRun.task_id == task_id,
                                        ContentNodeRun.node_id == "generate_content",
                                    )
                                    .order_by(ContentNodeRun.started_at.desc())
                                )
                            )
                            .scalars()
                            .first()
                        )
                        snapshot = generated.input_snapshot
                    await pg_manager.async_engine.dispose()
                    original = snapshot["visible_payload"]
                    projected = snapshot["model_visible_payload"]

                    def size(obj):
                        return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))

                    assert size(projected) < size(original) * 0.85
                    runtime = snapshot["runtime_config_snapshot"]
                    assert runtime["reasoning_effort"] == "medium"
                    assert runtime["limits"]["max_model_calls"] == 2
                    assert "viral-structure-rewriter" not in [s["slug"] for s in runtime["skills"]]
                    events = await list_run_stream_events(run_id, limit=500)
                    calls = [e["payload"]["payload"] for e in events if e["event_type"] == "content.model.started"]
                    generation_calls = [e for e in calls if e.get("node_id") == "generate_content"]
                    assert generation_calls and all(e["call_number"] <= 2 for e in generation_calls)
                    for call in generation_calls:
                        assert "viral-structure-rewriter" not in call["applied_skills"]
                        assert set(call["applied_skills"]) == {s["slug"] for s in runtime["skills"]}
                    assert nodes["deterministic_validate"]["status"] == "completed"
                    print(
                        json.dumps(
                            {
                                "before_chars": size(original),
                                "after_chars": size(projected),
                                "generation_calls": len(generation_calls),
                                "approved": True,
                            }
                        ),
                        flush=True,
                    )
                    break
                if result["run"]["status"] in {"failed", "cancelled", "completed", "interrupted"}:
                    pytest.fail(str(result["run"].get("error_message") or result))
                await asyncio.sleep(2)
            else:
                pytest.fail("隔离任务生成超时")
        finally:
            if run_id:
                await client.post(f"/api/content/runs/{run_id}/cancel")
            if task_id:
                for _ in range(30):
                    response = await client.delete(f"/api/content/tasks/{task_id}")
                    if response.status_code == 200:
                        break
                    await asyncio.sleep(1)
                assert response.status_code == 200, response.text


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="module")
async def test_frozen_standard_price_generation_replay():
    """单节点真实回放：沿用已确认报价和已锁定蓝图，不重新选择参考或确认用户事实。"""
    from copy import deepcopy
    from yuxi.agents.buildin import agent_manager
    from yuxi.content.control.workflow.agent_node import AgentNodeHandler
    from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
    from yuxi.content.v3.joint_workflow import WORKFLOW_PRICE_RECOVERY
    from yuxi.repositories.agent_run_repository import AgentRunRepository

    source_id = os.getenv("GENERATION_REPLAY_TASK_ID")
    if not source_id:
        pytest.skip("需指定已冻结标准单价的历史任务，只回放到隔离副本")
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        source = await db.get(ContentTask, source_id)
        historical = (
            (
                await db.execute(
                    select(ContentNodeRun)
                    .where(
                        ContentNodeRun.task_id == source_id,
                        ContentNodeRun.node_id == "generate_content",
                    )
                    .order_by(ContentNodeRun.started_at)
                )
            )
            .scalars()
            .first()
        )
        assert historical is not None
        state = deepcopy(historical.input_snapshot["visible_payload"])
        user = (await db.execute(select(User).where(User.uid == source.created_by))).scalar_one()
        token = AuthUtils.create_access_token({"sub": str(user.id)})
        uid, template_id = user.uid, source.industry_template_version_id
    await pg_manager.async_engine.dispose()
    prices = {
        item["id"]
        for item in state["evidence_bundle"]["items"]
        if item.get("metadata", {}).get("price_basis") == "standard_unit_price"
    }
    assert prices
    assert state["runtime_config_snapshot"]["creation_mode"] == "viral_rewrite"
    async with httpx.AsyncClient(
        base_url="http://localhost:5050", timeout=30, headers={"Authorization": f"Bearer {token}"}
    ) as client:
        response = await client.post(
            "/api/content/tasks",
            json={
                "industry_template_id": template_id,
                "content_goal": "acquire",
                "creation_mode": "viral_rewrite",
                "name": "pytest 冻结报价正文回放",
            },
        )
        assert response.status_code == 200, response.text
        task_id = response.json()["task"]["id"]
        run_id = f"replay_{uuid.uuid4().hex}"
        succeeded = False
        try:
            async with pg_manager.AsyncSession() as db:
                runs = AgentRunRepository(db)
                await runs.create_run(
                    run_id=run_id,
                    thread_id=task_id,
                    agent_id="content-workflow-agent",
                    uid=uid,
                    request_id=uuid.uuid4().hex,
                    input_payload={"task_id": task_id},
                )
                node_run = ContentNodeRun(
                    id=f"cnr_{uuid.uuid4().hex}",
                    task_id=task_id,
                    agent_run_id=run_id,
                    node_id="generate_content",
                    node_type="agent",
                )
                db.add(node_run)
                await db.commit()
                state.update(
                    task_id=task_id,
                    run_id=run_id,
                    uid=uid,
                    formula_selection_snapshot={
                        "selected_title_formula_code": state["strategy_snapshot"]["title_formula"]["code"],
                        "selected_body_formula_code": state["strategy_snapshot"]["body_formula"]["code"],
                    },
                )
                node = next(n for n in WORKFLOW_PRICE_RECOVERY["nodes"] if n["id"] == "generate_content")
                print(f"frozen price replay task={task_id} run={run_id}", flush=True)
                update = await AgentNodeHandler().execute(db=db, node=node, state=state, node_run_id=node_run.id)
                state.update(update)
                validation = await V3DeterministicNodeHandler._deterministic_validate(
                    db=db,
                    state=state,
                    node_run_id=node_run.id,
                )
                assert validation["validation_report"]["status"] == "passed", validation
                draft = state["content_draft"]
                assert "标准单价" in draft["body"]
                cited = {eid for p in draft["paragraph_evidence"] for eid in p["evidence_ids"]}
                assert prices.intersection(cited)
                succeeded = True
                print(
                    json.dumps(
                        {"standard_price_cited": True, "body_chars": len(draft["body"]), "validation": "passed"}
                    ),
                    flush=True,
                )
        finally:
            async with pg_manager.AsyncSession() as db:
                await AgentRunRepository(db).set_terminal_status(run_id, status="completed" if succeeded else "failed")
                await db.commit()
            await pg_manager.async_engine.dispose()
            backend = agent_manager.get_agent("ChatbotAgent")
            if backend._async_conn is not None:
                await backend._async_conn.close()
                backend._async_conn = None
                backend.checkpointer = None
            response = await client.delete(f"/api/content/tasks/{task_id}")
            assert response.status_code == 200, response.text
