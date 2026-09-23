"""在隔离任务中回放生产快照，真实生成/回修/复审并保存内容版本；不生成封面。

重复身份场景从原任务的阻断报告续跑，避免语义模型重新分类时的波动改变待测回修入口。
"""

from __future__ import annotations

import json
import os
import uuid
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from yuxi.agents.buildin.content_workflow.graph import ContentWorkflowAgent
from yuxi.content.control.workflow.agent_node import AgentNodeHandler
from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
from yuxi.content.model.locked_blocks import extract_locked_quote_block
from yuxi.content.model.materials import FrozenProductionPackV1
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import AgentRun, User
from yuxi.storage.postgres.models_content import ContentArtifactVersion, ContentNodeRun, ContentTask


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("scenario", ["fresh", "opening", "duplicate", "emoji"])
async def test_frozen_quote_input_reaches_reviewed_artifact_with_bounded_repairs(scenario):
    snapshot_dir = os.getenv("PERSONA_REPAIR_SNAPSHOT_DIR")
    uid = os.getenv("RULE_EDITOR_TEST_UID")
    if not snapshot_dir or not uid:
        pytest.skip("需配置隔离回放快照目录及测试账户")
    source = json.loads((Path(snapshot_dir) / "snapshot.json").read_text())
    state = deepcopy(json.loads((Path(snapshot_dir) / "state.json").read_text())["state"])
    original_pack = deepcopy(state["production_pack"])
    FrozenProductionPackV1.model_validate(original_pack)
    task_id, run_id = f"ct_test_{uuid.uuid4().hex}", f"run_test_{uuid.uuid4().hex}"
    definition = source["workflow"]["definition_json"]
    nodes = {node["id"]: node for node in definition["nodes"]}
    reports = []
    pg_manager.initialize()
    try:
        async with pg_manager.AsyncSession() as db:
            assert (await db.execute(select(User).where(User.uid == uid))).scalar_one()
            original_task = source["task"]
            task = ContentTask(
                id=task_id,
                name=f"pytest 人设回修 {scenario}",
                created_by=uid,
                updated_by=uid,
                industry_template_version_id=original_task["industry_template_version_id"],
                workflow_version_id=original_task["workflow_version_id"],
                rule_version_id=original_task["rule_version_id"],
                content_type_code=original_task["content_type_code"],
                industry_pack_version_id=original_task["industry_pack_version_id"],
                channel_profile_version_id=original_task["channel_profile_version_id"],
                brief_json=deepcopy(original_task["brief_json"]),
                runtime_config_snapshot_json=deepcopy(original_task["runtime_config_snapshot_json"]),
                latest_run_id=run_id,
                status="running",
            )
            db.add(task)
            db.add(
                AgentRun(
                    id=run_id,
                    thread_id=task_id,
                    agent_id="content-workflow",
                    uid=uid,
                    request_id=uuid.uuid4().hex,
                    status="running",
                )
            )
            await db.commit()
        state.update(task_id=task_id, run_id=run_id, uid=uid, retry_counts={}, selected_cover=None)
        if model := os.getenv("EXPRESSION_TEST_MODEL_SPEC"):
            state["model_spec"] = model
        # 开头场景验证历史冻结包续跑；其他场景验证新编译包，均不回写源快照。
        if scenario != "opening":
            state.update(
                await V3DeterministicNodeHandler._freeze_production_pack(db=None, state=state, node_run_id="freeze")
            )
            assert "persona_value" in {slot["slot_id"] for slot in state["production_pack"]["generation_slots"]}
            assert original_pack != state["production_pack"]
        attempts = {}

        async def execute(name):
            attempts[name] = attempts.get(name, 0) + 1
            node = nodes[name]
            if node["type"] == "agent":
                node_run_id = f"cn_test_{uuid.uuid4().hex}"
                async with pg_manager.AsyncSession() as db:
                    db.add(
                        ContentNodeRun(
                            id=node_run_id,
                            task_id=task_id,
                            agent_run_id=run_id,
                            node_id=name,
                            node_type="agent",
                            status="running",
                            attempt=attempts[name],
                        )
                    )
                    await db.commit()
                    state.update(
                        await AgentNodeHandler().execute(db=db, node=node, state=state, node_run_id=node_run_id)
                    )
                    await db.commit()
            elif name == "save_artifact_snapshot":
                state.update(await ContentWorkflowAgent._save_artifact(object(), state))
            elif node["type"] == "deterministic":
                async with pg_manager.AsyncSession() as db:
                    state.update(
                        await V3DeterministicNodeHandler().execute(db=db, node=node, state=state, node_run_id=name)
                    )
                    await db.commit()
            elif node["type"] == "human_review":
                state.update(await ContentWorkflowAgent._v3_human_review(object(), node, state))
            else:
                state.update(await ContentWorkflowAgent._execute_node(object(), node, state, definition))
            state["current_node"] = name

        if scenario == "fresh":
            for key in (
                "content_draft",
                "creative_content_draft",
                "review_report",
                "validation_report",
                "selected_title",
                "content_outline",
                "reviewed_draft_hash",
            ):
                state[key] = None
            await execute("generate_content")
        else:
            round_number = {"opening": 1, "duplicate": 2, "emoji": 3}[scenario]
            review_input = next(
                n["input_snapshot"]["model_visible_payload"]
                for n in source["nodes"]
                if n["node_id"] == "semantic_review" and n["attempt"] == round_number
            )
            quote = extract_locked_quote_block(original_pack)["rendered_content"]
            draft = deepcopy(review_input["content_draft"])
            draft["body"] = draft["body"].replace("\n\n" + quote, "", 1)
            draft["paragraph_evidence"] = [
                p for p in draft["paragraph_evidence"] if p["paragraph_id"] != "locked_quote_block"
            ]
            state.update(
                content_draft=draft,
                creative_content_draft=deepcopy(draft),
                review_report=None,
                selected_title=deepcopy(review_input["selected_title"]),
                content_outline=deepcopy(review_input["content_outline"]),
            )

        for attempt in range(3):
            await execute("adapt_to_channel")
            await execute("deterministic_validate")
            if state["validation_report"]["status"] == "blocked":
                reports.append(deepcopy(state["validation_report"]))
                await execute("revise_if_needed")
                assert state["revision_target"] == "generate_content"
                await execute("generate_content")
                continue
            for name in ("compose_locked_quote_block", "validate_composed_content"):
                await execute(name)
            if scenario == "duplicate" and attempt == 0:
                repair_input = next(
                    n["input_snapshot"]["model_visible_payload"]
                    for n in source["nodes"]
                    if n["node_id"] == "generate_content" and n["attempt"] == 3
                )
                state["review_report"] = deepcopy(repair_input["review_report"])
                state["current_node"] = "semantic_review"
            else:
                await execute("semantic_review")
            reports.append(deepcopy(state["review_report"]))
            if scenario != "fresh" and attempt == 0:
                expected = {
                    "opening": "PERSONA_OPENING",
                    "duplicate": "NATURAL_EXPRESSION",
                    "emoji": "EMOJI_APPROPRIATENESS",
                }[scenario]
                assert any(c["code"] == expected and c["status"] == "blocked" for c in reports[0]["checks"]), reports[0]
            if state["review_report"]["status"] == "passed":
                break
            await execute("revise_if_needed")
            assert state["retry_counts"]["generate_content"] <= 2
            await execute("generate_content")
        assert state["review_report"]["status"] == "passed", reports
        if scenario == "opening":
            assert state["production_pack"] == original_pack
        if scenario == "duplicate":
            assert state["content_draft"]["body"].count("45岁") <= 1
            assert state["content_draft"]["body"].count("做装修6年") <= 1
        await execute("human_content_approval")
        await execute("save_artifact_snapshot")
        async with pg_manager.AsyncSession() as db:
            version = await db.get(ContentArtifactVersion, state["artifact_version"]["id"])
            assert version is not None
            assert version.body == state["content_draft"]["body"]
            assert version.review_snapshot["status"] == "passed"
            assert version.body.count(extract_locked_quote_block(original_pack)["rendered_content"]) == 1
            generated = (
                (
                    await db.execute(
                        select(ContentNodeRun).where(
                            ContentNodeRun.task_id == task_id,
                            ContentNodeRun.node_id == "generate_content",
                        )
                    )
                )
                .scalars()
                .all()
            )
            for node in generated:
                visible = node.input_snapshot["model_visible_payload"]
                assert extract_locked_quote_block(original_pack)["original_content"] not in json.dumps(
                    visible, ensure_ascii=False
                )
                assert "创作稿第一段" in visible["production_pack"]["creative_opening_instruction"]
        assert json.loads((Path(snapshot_dir) / "state.json").read_text())["state"]["production_pack"] == original_pack
    finally:
        Path(f"/tmp/persona-repair-{scenario}.json").write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "reports": reports,
                    "draft": state.get("content_draft"),
                    "retry_counts": state.get("retry_counts"),
                    "artifact_version": state.get("artifact_version"),
                    "validation_report": state.get("validation_report"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        async with pg_manager.AsyncSession() as db:
            await db.execute(delete(ContentTask).where(ContentTask.id == task_id))
            await db.execute(delete(AgentRun).where(AgentRun.parent_agent_run_id == run_id))
            await db.execute(delete(AgentRun).where(AgentRun.id == run_id))
            await db.commit()
        from yuxi.services.run_queue_service import close_queue_clients

        await close_queue_clients()
        await pg_manager.async_engine.dispose()
