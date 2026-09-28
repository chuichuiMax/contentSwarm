"""本地内置 CT01～CT07 案例：真实编译及内容链路，图片规划前停止并保存正文。"""

from __future__ import annotations

import asyncio
import hashlib
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
from yuxi.content.model.locked_blocks import extract_locked_quote_block
from yuxi.content.model.single_blueprint import project_input
from yuxi.services.content_run_worker import _load_content_run
from yuxi.services.run_queue_service import close_queue_clients, list_run_stream_events
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
async def test_builtin_cases_reach_content_artifact_without_images(monkeypatch):
    await _run_builtin_cases(monkeypatch, preflight_only=False)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_builtin_cases_preserve_materials_before_generation(monkeypatch):
    await _run_builtin_cases(monkeypatch, preflight_only=True)


async def _run_builtin_cases(monkeypatch, *, preflight_only):
    case_dir = os.getenv("BUILTIN_CONTENT_CASE_DIR")
    uid = os.getenv("RULE_EDITOR_TEST_UID")
    if not case_dir or not uid:
        pytest.skip("需指定内置案例目录和本地测试用户")
    selected = os.getenv("BUILTIN_CONTENT_TYPES", ",".join(CASES)).split(",")
    assert selected and set(selected) <= set(CASES), selected
    repeats = int(os.getenv("BUILTIN_CONTENT_REPEATS", "1"))
    concurrency = int(os.getenv("BUILTIN_CONTENT_CONCURRENCY", "2"))
    assert repeats > 0 and concurrency > 0
    inputs = {code: (Path(case_dir) / CASES[code]).read_text() for code in selected}
    frozen_references = {}
    if frozen_path := os.getenv("BUILTIN_CONTENT_FROZEN_REFERENCES"):
        # 仅限对照实验：仍走现有检索、映射和授权，只从可用候选中选基线资产。
        from yuxi.content.control.workflow import creation_plan

        frozen_references = json.loads(Path(frozen_path).read_text())
        rank = creation_plan.rank_reference_candidates

        def frozen_rank(candidates, **kwargs):
            expected = frozen_references[kwargs["direction_code"]]
            ranked = rank(candidates, **kwargs)
            return [item for item in ranked if item["id"] == expected["id"]]

        monkeypatch.setattr(creation_plan, "rank_reference_candidates", frozen_rank)
    output_dir = Path(os.getenv("BUILTIN_CONTENT_OUTPUT_DIR", "/tmp/builtin-content-only"))
    output_dir.mkdir(parents=True, exist_ok=True)
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one()
        token = AuthUtils.create_access_token({"sub": str(user.id)})
    limiter = asyncio.Semaphore(concurrency)
    results = []

    async def produce(code, filename, repetition):
        async with limiter:
            started = time.monotonic()
            label = f"{code}-{repetition:02d}" if repeats > 1 else code
            record = {
                "validation_scope": "material_preflight" if preflight_only else "generated_artifact",
                "type": code,
                "case": filename,
                "repetition": repetition,
                "input_sha256": hashlib.sha256(inputs[code].encode()).hexdigest(),
                "status": "running",
            }
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
                    template_id = os.getenv("BUILTIN_CONTENT_TEMPLATE_ID", "industry-decoration-v3")
                    template = next(t for t in response.json()["industry_templates"] if t["id"] == template_id)
                    response = await client.post(
                        "/api/content/tasks",
                        json={
                            "industry_template_id": template["id"],
                            "content_goal": template["default_goal"],
                            "content_type_code": code,
                            "creation_mode": "viral_rewrite",
                            "name": f"内置案例正文验证 {label} {filename}",
                        },
                    )
                    assert response.status_code == 200, response.text
                    task_id = response.json()["task"]["id"]
                    record["task_id"] = task_id
                    response = await client.post(
                        f"/api/content/tasks/{task_id}/compile-brief",
                        json={
                            "brief": {"user_request": inputs[code]},
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
                record.update(rule_version_id=task.rule_version_id, workflow_version_id=task.workflow_version_id)
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
                stop_node = "generate_content" if preflight_only else "plan_visuals"
                graph = full_graph.builder.compile(checkpointer=agent.checkpointer, interrupt_before=[stop_node])
                config = {"configurable": {"thread_id": context.thread_id, "uid": uid}, "recursion_limit": 150}
                state = {
                    "task_id": task_id,
                    "run_id": run_id,
                    "uid": uid,
                    "model_spec": os.getenv("BUILTIN_CONTENT_MODEL"),
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
                print(f"START {label} {task_id}", flush=True)
                await graph.ainvoke(state, config=config, context=context)
                snapshot = await graph.aget_state(config)
                while snapshot.next != (stop_node,):
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
                if frozen_references:
                    actual = state["production_pack"]["reference_snapshot"]
                    expected = frozen_references[code]
                    assert all(actual[key] == expected[key] for key in ("id", "source_hash", "reference_blueprint"))
                    record["frozen_reference_verified"] = True
                if preflight_only:
                    view = project_input(state)
                    assert view["reference"]["body"]
                    assert view["reference"]["blocks"]
                    assert state["material_quality_report"]["status"] == "passed"
                    codes = {code for fact in view["facts"] for code in fact["variables"]}
                    assert {"scene", "quantity", "product", "persona_fact", "case_background"} <= codes
                    assert not {"external_serial_no", "number"} & codes
                    assert any(fact["role"] == "reader_context" for fact in view["facts"])
                    assert (
                        view["quote"]["context"]
                        == extract_locked_quote_block(state["production_pack"])["rendered_content"]
                    )
                    record.update(status="passed", model_view=view)
                    return
                if workflow.definition_json.get("semantic_review_enabled", True):
                    assert state["review_report"]["status"] in {"passed", "warning"}, state["review_report"]
                    assert not any(item["status"] == "blocked" for item in state["review_report"]["checks"])
                else:
                    assert not state.get("review_report")
                    assert state["validation_report"]["status"] == "passed"
                if os.getenv("BUILTIN_CONTENT_EXPECT_FORBIDDEN_KB"):
                    bundle = state["production_pack"]["content_rule_bundle"]
                    platform = bundle["runtime_rules"]["viral-platform-expression"]
                    lexicon = platform["forbidden_lexicon"]
                    assert lexicon["name"] == "封禁词库" and lexicon["sources"] and lexicon["alternatives"]
                    combined = "\n".join(
                        [
                            state["selected_title"]["text"],
                            state["content_draft"]["body"],
                            *state["content_draft"]["topics"],
                        ]
                    )
                    assert not [term for term in lexicon["alternatives"] if term in combined]
                    canonical = json.dumps(
                        {key: value for key, value in bundle.items() if key != "bundle_hash"},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    assert bundle["bundle_hash"] == hashlib.sha256(canonical.encode()).hexdigest()
                if code in {"CT02", "CT03", "CT04", "CT05"}:
                    quote = extract_locked_quote_block(state["production_pack"])
                    assert quote is not None and quote["render_policy"] == "checkmark-lines-v1"
                    assert all(
                        line.lstrip().startswith("✅")
                        for line in quote["rendered_content"].splitlines()
                        if line.strip()
                    )
                    assert state["content_draft"]["body"].count(quote["rendered_content"]) == 1
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
                    model_spec=artifact.model_spec,
                )
            except asyncio.CancelledError:
                record.update(status="cancelled", error="测试批次主动中止，不计入内容成功率")
                raise
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
                    production_order=state.get("production_order"),
                    body_formula=(state.get("strategy_snapshot") or {}).get("body_formula"),
                )
                review_history = []
                if graph is not None and config is not None:
                    async for checkpoint in graph.aget_state_history(config):
                        report = checkpoint.values.get("review_report")
                        if report and report not in review_history:
                            review_history.append(report)
                record["review_history"] = list(reversed(review_history))
                if run_id:
                    async with pg_manager.AsyncSession() as db:
                        run = await db.get(AgentRun, run_id)
                        run.status = "completed" if record["status"] == "passed" else record["status"]
                        run.error_message = record.get("error")
                        if record["status"] in {"failed", "cancelled"}:
                            task = await db.get(ContentTask, task_id)
                            task.status = record["status"]
                        await db.commit()
                if state.get("production_pack", {}).get("content_rule_bundle"):
                    platform = state["production_pack"]["content_rule_bundle"]["runtime_rules"][
                        "viral-platform-expression"
                    ]
                    record["forbidden_lexicon"] = platform.get("forbidden_lexicon")
                    record["replacement_diffs"] = (state.get("channel_result") or {}).get("replacement_diffs", [])
                    record["reference_snapshot"] = state["production_pack"]["reference_snapshot"]
                    record["rule_bundle_hash"] = state["production_pack"]["content_rule_bundle"]["bundle_hash"]
                    record["skill_versions"] = state["production_pack"]["content_rule_bundle"]["modules"]
                    async with pg_manager.AsyncSession() as db:
                        nodes = (
                            (await db.execute(select(ContentNodeRun).where(ContentNodeRun.task_id == task_id)))
                            .scalars()
                            .all()
                        )
                    model_inputs = output_dir / "model-inputs" / label
                    model_inputs.mkdir(parents=True, exist_ok=True)
                    record["model_events"] = []
                    for node in nodes:
                        view = node.input_snapshot.get("model_visible_payload")
                        if view is not None:
                            (model_inputs / f"{node.node_id}-{node.attempt}.json").write_text(
                                json.dumps(view, ensure_ascii=False, indent=2)
                            )
                        if node.delegated_agent_run_id:
                            for event in await list_run_stream_events(node.delegated_agent_run_id, limit=1000):
                                if event["event_type"] in {
                                    "content.model.started",
                                    "content.model.completed",
                                    "content.model.usage",
                                }:
                                    record["model_events"].append(
                                        {"event": event["event_type"], **event["payload"]["payload"]}
                                    )
                    frozen_skills = {item["slug"]: item for item in record["skill_versions"]}
                    for event in record["model_events"]:
                        if event["event"] != "content.model.started":
                            continue
                        for slug, applied in event.get("applied_skills", {}).items():
                            if slug not in frozen_skills:
                                continue
                            frozen = frozen_skills[slug]
                            if "instructions" not in frozen:
                                continue
                            assert applied["content_hash"] == frozen["content_hash"]
                            assert (
                                applied["applied_hash"] == hashlib.sha256(frozen["instructions"].encode()).hexdigest()
                            )
                results.append(record)
                (output_dir / f"{label}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
                print(
                    f"RESULT {label} {record['status']} {record['seconds']}s {record.get('error', '')[:250]}",
                    flush=True,
                )

    try:
        await asyncio.gather(
            *(produce(code, CASES[code], repetition) for repetition in range(1, repeats + 1) for code in selected)
        )
        (output_dir / "summary.json").write_text(
            json.dumps(sorted(results, key=lambda r: (r["type"], r["repetition"])), ensure_ascii=False, indent=2)
        )
        assert all(r["status"] == "passed" for r in results), [
            (r["type"], r.get("error")) for r in results if r["status"] != "passed"
        ]
    finally:
        await close_queue_clients()
        await pg_manager.close()
        # 本测试在进程内加载检索器，显式关闭它创建的 gRPC 客户端，避免退出等待。
        from pymilvus import connections

        for alias, _ in connections.list_connections():
            connections.disconnect(alias)
