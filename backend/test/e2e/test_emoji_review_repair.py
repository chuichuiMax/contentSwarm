"""用指定已生成文章的隔离副本验证真实审核、表情回修、再次审核。"""

import json
import os
import re
import uuid
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from yuxi.content.control.workflow.agent_node import AgentNodeHandler
from yuxi.content.v3.joint_workflow import WORKFLOW_PRICE_RECOVERY
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import AgentRun
from yuxi.storage.postgres.models_content import ContentArtifact, ContentNodeRun, ContentTask


def without_symbols(text):
    return re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B05-\u2B07\u200d\ufe0e\ufe0f]|\s", "", text)


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("persona_repair", [False, True], ids=["emoji", "persona"])
async def test_real_emoji_review_repairs_existing_article_without_changing_text(persona_repair):
    source_id = os.getenv("EMOJI_TEST_SOURCE_TASK_ID")
    if not source_id:
        pytest.skip("需要指定隔离回归使用的原文任务 EMOJI_TEST_SOURCE_TASK_ID")
    pg_manager.initialize()
    task_id, run_id = f"ct_test_{uuid.uuid4().hex}", f"run_test_{uuid.uuid4().hex}"
    try:
        async with pg_manager.AsyncSession() as db:
            source = await db.get(ContentTask, source_id)
            artifact = (
                await db.execute(select(ContentArtifact).where(ContentArtifact.task_id == source_id))
            ).scalar_one()
            source_node = (
                (
                    await db.execute(
                        select(ContentNodeRun)
                        .where(
                            ContentNodeRun.task_id == source_id,
                            ContentNodeRun.node_id == "generate_content",
                        )
                        .order_by(ContentNodeRun.started_at.desc())
                    )
                )
                .scalars()
                .first()
            )
            state = deepcopy(source_node.input_snapshot["visible_payload"])
            original_body, original_title = artifact.body, artifact.title
            if persona_repair:
                paragraphs = original_body.split("\n\n")
                assert len(paragraphs) >= 3
                original_body = "这次聊聊旧房改造报价。\n\n" + "\n\n".join(paragraphs[1:-1]) + "\n\n欢迎咨询。"
            values = {column.name: deepcopy(getattr(source, column.name)) for column in source.__table__.columns}
            values.update(id=task_id, name="pytest 表情审核回修", status="draft", latest_run_id=run_id)
            db.add(ContentTask(**values))
            db.add(
                AgentRun(
                    id=run_id,
                    thread_id=task_id,
                    agent_id="content-workflow",
                    uid=source.created_by,
                    request_id=uuid.uuid4().hex,
                    status="running",
                )
            )
            await db.commit()
            state.update(task_id=task_id, run_id=run_id, uid=source.created_by)
            if model_spec := os.getenv("EXPRESSION_TEST_MODEL_SPEC"):
                state["model_spec"] = model_spec
            state["runtime_config_snapshot"]["strict_semantic_review"] = False
            state["content_draft"]["body"] = original_body
            state["selected_title"]["text"] = original_title
            state["validation_report"] = {"status": "passed", "checks": []}
            state["channel_result"] = {"body": original_body, "title": original_title, "topics": artifact.topics}
            state["formula_selection_snapshot"] = {
                "selected_title_formula_code": state["strategy_snapshot"]["title_formula"]["code"],
                "selected_body_formula_code": state["strategy_snapshot"]["body_formula"]["code"],
            }
            reports = []
            for attempt, node_name in enumerate(("semantic_review", "generate_content", "semantic_review"), 1):
                node_id = f"cn_test_{uuid.uuid4().hex}"
                db.add(
                    ContentNodeRun(
                        id=node_id,
                        task_id=task_id,
                        agent_run_id=run_id,
                        node_id=node_name,
                        node_type="agent",
                        status="running",
                        attempt=attempt,
                    )
                )
                await db.commit()
                node = next(item for item in WORKFLOW_PRICE_RECOVERY["nodes"] if item["id"] == node_name)
                state.update(await AgentNodeHandler().execute(db=db, node=node, state=state, node_run_id=node_id))
                if node_name == "semantic_review":
                    reports.append(deepcopy(state["review_report"]))
                    if attempt == 1:
                        assert state["review_report"]["status"] == "blocked", state["review_report"]
                        if persona_repair:
                            assert any(item["code"] in {"PERSONA_OPENING", "PERSONA_CLOSING"}
                                       and item["status"] == "blocked"
                                       for item in state["review_report"]["checks"])
                else:
                    state["channel_result"]["body"] = state["content_draft"]["body"]
            Path("/tmp/emoji-review-repair.json").write_text(
                json.dumps(
                    {
                        "body": state["content_draft"]["body"],
                        "reports": reports,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            assert reports[-1]["status"] == "passed", reports[-1]
            assert state["selected_title"]["text"] == original_title
            if persona_repair:
                final_body = without_symbols(state["content_draft"]["body"])
                for paragraph in original_body.split("\n\n")[1:-1]:
                    assert without_symbols(paragraph) in final_body
                assert {item["code"] for item in reports[-1]["checks"]} >= {
                    "PERSONA_OPENING", "PERSONA_CLOSING", "PERSONA_GROUNDING"
                }
            else:
                assert without_symbols(state["content_draft"]["body"]) == without_symbols(original_body)
            assert state["content_draft"]["body"] != original_body
    finally:
        async with pg_manager.AsyncSession() as db:
            await db.execute(delete(ContentNodeRun).where(ContentNodeRun.task_id == task_id))
            await db.execute(delete(AgentRun).where(AgentRun.parent_agent_run_id == run_id))
            await db.execute(delete(AgentRun).where(AgentRun.id == run_id))
            await db.execute(delete(ContentTask).where(ContentTask.id == task_id))
            await db.commit()
        await pg_manager.async_engine.dispose()
