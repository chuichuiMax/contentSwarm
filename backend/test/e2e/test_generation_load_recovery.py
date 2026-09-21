"""真实 Worker 验证正文模型视图与调用预算，只操作失败任务的隔离副本。"""

import asyncio
import json
import os
import re
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
async def test_projected_strategy_for_viral_generation():
    creation_mode = "viral_rewrite"
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

                    assert size(model_input) < size(original_input)
                    assert runtime["model_input_contract"] == "JointStrategyPromptV1"
                    assert model_input["channel_profile"]["version_id"] == source.channel_profile_version_id
                    assert "visual_material" not in model_input["runtime_config_snapshot"]
                    assert "source_rules" not in model_input["strategy_candidates"]
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
async def test_frozen_viral_generation_replay():
    """单节点真实回放：沿用已锁定蓝图和证据，不重新选择参考或确认用户事实。"""
    from copy import deepcopy
    from yuxi.agents.buildin import agent_manager
    from yuxi.content.control.workflow.agent_node import AgentNodeHandler
    from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
    from yuxi.content.v3.joint_workflow import WORKFLOW_MODULAR_AUTHOR, WORKFLOW_PRICE_RECOVERY, WORKFLOW_VIRAL_AUTHOR
    from yuxi.content.v3.modular_rules import build_modular_rule_bundle, select_modular_generation_skills
    from yuxi.repositories.agent_run_repository import AgentRunRepository

    source_id = os.getenv("GENERATION_REPLAY_TASK_ID")
    if not source_id:
        pytest.skip("需指定已有冻结生成输入的仿写任务，只回放到隔离副本")
    variant = os.getenv("GENERATION_REPLAY_VARIANT", "candidate")
    assert variant in {"baseline", "candidate", "modular"}
    workflow = (
        WORKFLOW_PRICE_RECOVERY
        if variant == "baseline"
        else WORKFLOW_MODULAR_AUTHOR
        if variant == "modular"
        else WORKFLOW_VIRAL_AUTHOR
    )
    expected_skills = (
        {
            "content-title-generator",
            "content-outline-builder",
            "content-body-generator",
            "viral-structure-rewriter",
            "viral-layout-formatter",
            "humanizer-zh",
            "content-human-expression",
        }
        if variant == "baseline"
        else set()
        if variant == "modular"
        else {"viral-content-author"}
    )
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
    assert state["runtime_config_snapshot"]["creation_mode"] == "viral_rewrite"
    if variant == "modular":
        state["runtime_config_snapshot"]["content_rule_bundle"] = build_modular_rule_bundle(state["content_brief"])
        generation_node = next(n for n in workflow["nodes"] if n["id"] == "generate_content")
        expected_skills = set(select_modular_generation_skills(tuple(generation_node["required_skills"]), state))
    async with httpx.AsyncClient(
        base_url="http://localhost:5050", timeout=30, headers={"Authorization": f"Bearer {token}"}
    ) as client:
        response = await client.post(
            "/api/content/tasks",
            json={
                "industry_template_id": template_id,
                "content_goal": "acquire",
                "creation_mode": "viral_rewrite",
                "name": "pytest 冻结仿写正文回放",
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
                node = next(n for n in workflow["nodes"] if n["id"] == "generate_content")
                print(f"frozen viral replay variant={variant} task={task_id} run={run_id}", flush=True)
                update = await AgentNodeHandler().execute(db=db, node=node, state=state, node_run_id=node_run.id)
                state.update(update)
                adaptation = await V3DeterministicNodeHandler._adapt_to_channel(
                    db=db,
                    state=state,
                    node_run_id=node_run.id,
                )
                state.update(adaptation)
                validation = await V3DeterministicNodeHandler._deterministic_validate(
                    db=db,
                    state=state,
                    node_run_id=node_run.id,
                )
                if validation["validation_report"]["status"] != "passed":
                    print(json.dumps(validation["validation_report"], ensure_ascii=False), flush=True)
                    print(state["content_draft"]["body"], flush=True)
                assert validation["validation_report"]["status"] == "passed", validation
                state.update(validation)
                draft = state["content_draft"]
                cited = {eid for p in draft["paragraph_evidence"] for eid in p["evidence_ids"]}
                if prices:
                    assert "标准单价" in draft["body"]
                    assert prices.intersection(cited)
                events = await list_run_stream_events(run_id, limit=500)
                calls = [
                    event["payload"]["payload"]
                    for event in events
                    if event["event_type"] == "content.model.started"
                    and event["payload"]["payload"].get("node_id") == "generate_content"
                ]
                assert calls
                assert all(set(call["applied_skills"]) == expected_skills for call in calls)
                completions = [
                    event["payload"]["payload"]
                    for event in events
                    if event["event_type"] == "content.model.completed"
                    and event["payload"]["payload"].get("node_id") == "generate_content"
                ]
                if variant in {"candidate", "modular"}:
                    review_run = ContentNodeRun(
                        id=f"cnr_{uuid.uuid4().hex}",
                        task_id=task_id,
                        agent_run_id=run_id,
                        node_id="semantic_review",
                        node_type="agent",
                    )
                    db.add(review_run)
                    await db.commit()
                    review_node = next(n for n in workflow["nodes"] if n["id"] == "semantic_review")
                    review_update = await AgentNodeHandler().execute(
                        db=db, node=review_node, state=state, node_run_id=review_run.id
                    )
                    state.update(review_update)
                    review = review_update["review_report"]
                    required_checks = {
                        "EMOJI_COVERAGE",
                        "EMOJI_APPROPRIATENESS",
                        "EMOJI_RESTRICTIONS",
                        "PERSONA_OPENING",
                        "PERSONA_CLOSING",
                        "PERSONA_GROUNDING",
                        "CREATION_TYPE_ALIGNMENT",
                        "COMPOSITION_ALIGNMENT",
                    }
                    if variant == "modular":
                        required_checks.update(
                            {
                                "TITLE_ALIGNMENT",
                                "BODY_VALUE",
                                "NATURAL_EXPRESSION",
                                "LAYOUT_READABILITY",
                                "PLATFORM_CTA",
                                "TOPIC_ALIGNMENT",
                            }
                        )
                    assert required_checks <= {item["code"] for item in review["checks"]}
                    if os.getenv("GENERATION_REPLAY_REPAIR") == "1" and review["status"] == "blocked":
                        blocked_codes = {item["code"] for item in review["checks"] if item["status"] == "blocked"}
                        original_body = state["content_draft"]["body"]
                        repair_run = ContentNodeRun(
                            id=f"cnr_{uuid.uuid4().hex}",
                            task_id=task_id,
                            agent_run_id=run_id,
                            node_id="generate_content",
                            node_type="agent",
                            attempt=2,
                        )
                        db.add(repair_run)
                        await db.commit()
                        repair_update = await AgentNodeHandler().execute(
                            db=db, node=node, state=state, node_run_id=repair_run.id
                        )
                        state.update(repair_update)
                        if blocked_codes <= {"PERSONA_OPENING", "PERSONA_CLOSING", "PERSONA_GROUNDING"}:
                            original_parts = [
                                part.strip() for part in re.split(r"\n\s*\n", original_body) if part.strip()
                            ]
                            repaired_parts = [
                                part.strip()
                                for part in re.split(r"\n\s*\n", state["content_draft"]["body"])
                                if part.strip()
                            ]
                            assert original_parts[1:-1] == repaired_parts[1:-1]
                        state.update(
                            await V3DeterministicNodeHandler._adapt_to_channel(
                                db=db, state=state, node_run_id=repair_run.id
                            )
                        )
                        repaired_validation = await V3DeterministicNodeHandler._deterministic_validate(
                            db=db, state=state, node_run_id=repair_run.id
                        )
                        assert repaired_validation["validation_report"]["status"] == "passed", repaired_validation
                        state.update(repaired_validation)
                        second_review_run = ContentNodeRun(
                            id=f"cnr_{uuid.uuid4().hex}",
                            task_id=task_id,
                            agent_run_id=run_id,
                            node_id="semantic_review",
                            node_type="agent",
                            attempt=2,
                        )
                        db.add(second_review_run)
                        await db.commit()
                        state.update(
                            await AgentNodeHandler().execute(
                                db=db, node=review_node, state=state, node_run_id=second_review_run.id
                            )
                        )
                        review = state["review_report"]
                        print(
                            json.dumps(
                                {"repair_review_status": review["status"], "repair_checks": review["checks"]},
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                        assert review["status"] == "passed", review
                else:
                    review = None
                events = await list_run_stream_events(run_id, limit=500)
                review_calls = [
                    event["payload"]["payload"]
                    for event in events
                    if event["event_type"] == "content.model.started"
                    and event["payload"]["payload"].get("node_id") == "semantic_review"
                ]
                if variant == "modular":
                    assert len(review_calls) == 1, review_calls
                succeeded = True
                print(
                    json.dumps(
                        {
                            "standard_price_cited": bool(prices.intersection(cited)),
                            "variant": variant,
                            "body_chars": len(draft["body"]),
                            "validation": "passed",
                            "skill_chars": sum(
                                item["instruction_chars"] for item in calls[0]["applied_skills"].values()
                            ),
                            "model_calls": len(calls),
                            "review_model_calls": len(review_calls),
                            "model_durations_ms": [item["duration_ms"] for item in completions],
                            "first_progress_ms": [item["first_progress_ms"] for item in completions],
                            "review_status": review["status"] if review else None,
                            "review_blocked_codes": (
                                [item["code"] for item in review["checks"] if item["status"] == "blocked"]
                                if review
                                else []
                            ),
                        }
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
