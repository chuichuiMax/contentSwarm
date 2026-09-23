"""本地内置 CT01～CT07 案例：真实编译及内容链路，图片规划前停止并保存正文。"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy import select

from yuxi.agents.buildin.content_workflow.context import ContentWorkflowContext
from yuxi.agents.buildin.content_workflow.graph import ContentWorkflowAgent
from yuxi.services.content_run_worker import _load_content_run
from yuxi.services.run_queue_service import close_queue_clients
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import AgentRun, User
from yuxi.storage.postgres.models_content import ContentArtifactVersion, ContentNodeRun, ContentTask
from yuxi.utils.auth_utils import AuthUtils

CASES = {
    "CT01": "自我介绍001.txt",
    "CT02": "北京报价002.txt",
    "CT03": "施工报价json模板.txt",
    "CT04": "成都报价003.txt",
    "CT05": "广州报价004.txt",
    "CT06": "施工工艺001.txt",
    "CT07": "日常工作001.txt",
}


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_builtin_cases_reach_content_artifact_without_images():
    case_dir = os.getenv("BUILTIN_CONTENT_CASE_DIR")
    uid = os.getenv("RULE_EDITOR_TEST_UID")
    if not case_dir or not uid:
        pytest.skip("需指定内置案例目录和本地测试用户")
    selected = os.getenv("BUILTIN_CONTENT_TYPES", ",".join(CASES)).split(",")
    assert selected and set(selected) <= set(CASES), selected
    output_dir = Path(os.getenv("BUILTIN_CONTENT_OUTPUT_DIR", "/tmp/builtin-content-only"))
    output_dir.mkdir(parents=True, exist_ok=True)
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one()
        token = AuthUtils.create_access_token({"sub": str(user.id)})
    limiter = asyncio.Semaphore(2)
    results = []

    async def produce(code, filename):
        async with limiter:
            started = time.monotonic()
            record = {"type": code, "case": filename, "status": "running"}
            task_id = run_id = None
            graph = config = None
            state = {}
            try:
                async with httpx.AsyncClient(
                    base_url="http://localhost:5050",
                    timeout=180,
                    headers={"Authorization": f"Bearer {token}"},
                ) as client:
                    response = await client.get("/api/content/bootstrap")
                    response.raise_for_status()
                    template = next(t for t in response.json()["industry_templates"] if t["slug"] == "decoration")
                    response = await client.post(
                        "/api/content/tasks",
                        json={
                            "industry_template_id": template["id"],
                            "content_goal": template["default_goal"],
                            "content_type_code": code,
                            "creation_mode": "viral_rewrite",
                            "name": f"内置案例正文验证 {code} {filename}",
                        },
                    )
                    assert response.status_code == 200, response.text
                    task_id = response.json()["task"]["id"]
                    record["task_id"] = task_id
                    response = await client.post(
                        f"/api/content/tasks/{task_id}/compile-brief",
                        json={
                            "brief": {"user_request": (Path(case_dir) / filename).read_text()},
                        },
                    )
                    assert response.status_code == 200, response.text
                run_id = f"run_{uuid.uuid4().hex}"
                async with pg_manager.AsyncSession() as db:
                    task = await db.get(ContentTask, task_id)
                    task.latest_run_id = run_id
                    task.status = "running"
                    db.add(
                        AgentRun(
                            id=run_id,
                            thread_id=task_id,
                            agent_id="ContentWorkflowAgent",
                            uid=uid,
                            request_id=uuid.uuid4().hex,
                            status="running",
                        )
                    )
                    await db.commit()
                run, task, workflow, rules = await _load_content_run(run_id)
                agent = ContentWorkflowAgent()
                agent.checkpointer = InMemorySaver()
                context = ContentWorkflowContext(
                    uid=uid,
                    thread_id=f"content:{task_id}",
                    run_id=run_id,
                    request_id=run.request_id,
                    task_id=task_id,
                    workflow_definition=workflow.definition_json,
                    rule_bundle=rules,
                )
                full_graph = await agent.get_graph(context)
                graph = full_graph.builder.compile(checkpointer=agent.checkpointer, interrupt_before=["plan_visuals"])
                config = {"configurable": {"thread_id": context.thread_id, "uid": uid}, "recursion_limit": 150}
                state = {
                    "task_id": task_id,
                    "run_id": run_id,
                    "uid": uid,
                    "model_spec": None,
                    "workflow_version_id": task.workflow_version_id,
                    "rule_version_id": task.rule_version_id,
                    "industry_template_version_id": task.industry_template_version_id,
                    "schema_version": 3,
                    "runtime_config_snapshot": task.runtime_config_snapshot_json or {},
                    "content_brief": task.brief_json,
                    "evidence_bundle": task.evidence_json or {"items": []},
                    "content_type": {},
                    "industry_pack": {},
                    "persona_profile": {},
                    "channel_profile": {},
                    "compliance_policies": [],
                    "lexicon_entries": [],
                    "media_evidence_items": [],
                    "content_angles": [],
                    "selected_angle": None,
                    "content_outline": {},
                    "strategy_snapshot": {},
                    "title_candidates": [],
                    "selected_title": None,
                    "content_draft": None,
                    "validation_report": None,
                    "title_validation_report": None,
                    "review_report": None,
                    "current_node": "queued",
                    "retry_counts": {},
                    "state_version": 0,
                    "task_mode": task.mode,
                    "resume_parent_run_id": None,
                }
                print(f"START {code} {task_id}", flush=True)
                await graph.ainvoke(state, config=config, context=context)
                snapshot = await graph.aget_state(config)
                while snapshot.next != ("plan_visuals",):
                    interrupts = list(snapshot.interrupts)
                    assert interrupts, f"Unexpected stop: {snapshot.next}"
                    prompt = interrupts[0].value
                    assert prompt["node_id"] in {"confirm_strategy_prices", "confirm_high_risk_facts"}, prompt
                    await graph.ainvoke(
                        Command(resume={**prompt, "confirmed_evidence_ids": prompt.get("evidence_ids", [])}),
                        config=config,
                        context=context,
                    )
                    snapshot = await graph.aget_state(config)
                state = snapshot.values
                assert state["review_report"]["status"] == "passed", state["review_report"]
                state.update(await agent._save_artifact(state))
                async with pg_manager.AsyncSession() as db:
                    artifact = await db.get(ContentArtifactVersion, state["artifact_version"]["id"])
                    assert artifact is not None and artifact.body == state["content_draft"]["body"]
                    node_ids = set(
                        (
                            await db.execute(select(ContentNodeRun.node_id).where(ContentNodeRun.task_id == task_id))
                        ).scalars()
                    )
                    assert not node_ids & {"plan_visuals", "submit_cover_job", "wait_cover_job", "select_cover"}
                record.update(
                    status="passed",
                    title=state["selected_title"]["text"],
                    body=state["content_draft"]["body"],
                    topics=state["content_draft"].get("topics"),
                    artifact=state["artifact_version"],
                )
            except Exception as exc:
                record.update(status="failed", error_type=type(exc).__name__, error=str(exc))
                if graph is not None and config is not None:
                    state = (await graph.aget_state(config)).values
            finally:
                record.update(
                    seconds=round(time.monotonic() - started, 1),
                    retries=state.get("retry_counts"),
                    current_node=state.get("current_node"),
                    draft=state.get("content_draft"),
                    validation=state.get("validation_report"),
                    review=state.get("review_report"),
                    run_id=run_id,
                )
                if run_id:
                    async with pg_manager.AsyncSession() as db:
                        run = await db.get(AgentRun, run_id)
                        run.status = "completed" if record["status"] == "passed" else "failed"
                        run.error_message = record.get("error")
                        if record["status"] == "failed":
                            task = await db.get(ContentTask, task_id)
                            task.status = "failed"
                        await db.commit()
                results.append(record)
                (output_dir / f"{code}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
                print(
                    f"RESULT {code} {record['status']} {record['seconds']}s {record.get('error', '')[:250]}", flush=True
                )

    try:
        await asyncio.gather(*(produce(code, CASES[code]) for code in selected))
        (output_dir / "summary.json").write_text(
            json.dumps(sorted(results, key=lambda r: r["type"]), ensure_ascii=False, indent=2)
        )
        assert all(r["status"] == "passed" for r in results), [
            (r["type"], r.get("error")) for r in results if r["status"] != "passed"
        ]
    finally:
        await close_queue_clients()
        await pg_manager.async_engine.dispose()
