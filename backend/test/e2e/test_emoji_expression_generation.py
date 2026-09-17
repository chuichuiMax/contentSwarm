"""用指定已完成节点的冻结资料回放真实生成 Agent，不覆盖原任务或调用封面发布。"""

import copy
import json
import os
import re
import time
import uuid
from pathlib import Path

import pytest
import regex
from sqlalchemy import inspect, select

from yuxi.content.control.workflow.agent_node import AgentNodeHandler
from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
from yuxi.content.v3.joint_workflow import WORKFLOW_BLUEPRINT_FIRST
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import AgentRun
from yuxi.storage.postgres.models_content import ContentNodeRun, ContentTask
from yuxi.utils.datetime_utils import utc_now_naive


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("emoji_allowed", [True, False])
async def test_real_generation_covers_semantic_emoji_and_respects_channel(emoji_allowed):
    source_id = os.getenv("EMOJI_EVAL_SOURCE_NODE_ID")
    if not source_id:
        pytest.skip("需要配置可回放的装修案例节点 EMOJI_EVAL_SOURCE_NODE_ID")
    pg_manager.initialize()
    task_id, run_id, node_id = (f"emoji_{uuid.uuid4().hex}" for _ in range(3))
    try:
        async with pg_manager.AsyncSession() as db:
            source = await db.get(ContentNodeRun, source_id)
            assert source and source.status == "completed" and source.node_id == "generate_content"
            original = await db.get(ContentTask, source.task_id)
            values = {
                item.key: copy.deepcopy(getattr(original, item.key)) for item in inspect(ContentTask).column_attrs
            }
            values.update(
                id=task_id,
                name="pytest Emoji 功能覆盖",
                latest_run_id=run_id,
                status="running",
                created_at=utc_now_naive(),
                updated_at=utc_now_naive(),
                deleted_at=None,
            )
            task = ContentTask(**values)
            db.add(task)
            parent = AgentRun(
                id=run_id,
                thread_id=task_id,
                agent_id="content-workflow",
                uid=task.created_by,
                request_id=uuid.uuid4().hex,
                status="running",
            )
            db.add(parent)
            await db.flush()
            node_run = ContentNodeRun(
                id=node_id,
                task_id=task_id,
                agent_run_id=run_id,
                node_id="generate_content",
                node_type="agent",
                status="running",
            )
            db.add(node_run)
            await db.commit()
            state = copy.deepcopy(source.input_snapshot["visible_payload"])
            state.update(task_id=task_id, run_id=run_id, uid=task.created_by)
            state["formula_selection_snapshot"] = {
                "selected_title_formula_code": state["strategy_snapshot"]["title_formula"]["code"],
                "selected_body_formula_code": state["strategy_snapshot"]["body_formula"]["code"],
            }
            state["channel_profile"]["body_constraints"]["emoji_allowed"] = emoji_allowed
            state["channel_profile"]["title_constraints"]["emoji_allowed"] = emoji_allowed
            node = next(item for item in WORKFLOW_BLUEPRINT_FIRST["nodes"] if item["id"] == "generate_content")
            started = time.monotonic()
            state.update(await AgentNodeHandler().execute(db=db, node=node, state=state, node_run_id=node_id))
            duration = time.monotonic() - started
            validation = await V3DeterministicNodeHandler._deterministic_validate(
                db=db, state=state, node_run_id=node_id
            )
            body = state["content_draft"]["body"]
            emojis = regex.findall(r"\p{Extended_Pictographic}", body)
            result = {
                "emoji_allowed": emoji_allowed,
                "seconds": round(duration, 2),
                "body": body,
                "emoji_count": len(emojis),
                "validation": validation,
                "runtime": node_run.input_snapshot.get("runtime_config_snapshot", {}),
            }
            Path(f"/tmp/emoji-expression-{emoji_allowed}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2)
            )
            assert validation["validation_report"]["status"] != "blocked", validation
            # 本夹具含痛点、面积/预算/工期、三项改造、验收与互动；数量仅作回归信号，非产品规则。
            if emoji_allowed:
                assert len(emojis) >= 8, body
                assert regex.search(r"[\U0001F600-\U0001F64F\U0001F910-\U0001F92F\U0001F970-\U0001F97F]", body), body
                for term in ("89", "18", "90", "厨房", "ENF", "闭水"):
                    assert any(
                        regex.search(r"\p{Extended_Pictographic}", body[max(0, m.start() - 18) : m.end() + 18])
                        for m in re.finditer(term, body)
                    ), (term, body)
                item_lines = [line for line in body.splitlines() if regex.match(r"\p{Extended_Pictographic}", line)]
                assert len(item_lines) >= 2, body
                for item in ("玄关", "厨房", "儿童"):
                    assert any(item in line for line in item_lines), (item, body)
            else:
                assert not emojis, body
            skills = {item["slug"]: item["version"] for item in result["runtime"]["skills"]}
            assert skills["content-human-expression"] == "2.0.0"
            assert skills["content-body-generator"] == "2.3.0"
            parent.status = "completed"
            node_run.status = "completed"
            await db.commit()
    finally:
        async with pg_manager.AsyncSession() as db:
            task = await db.get(ContentTask, task_id)
            if task:
                task.deleted_at = utc_now_naive()
                task.status = "completed"
            runs = (await db.execute(select(AgentRun).where(AgentRun.id == run_id))).scalars()
            for run in runs:
                if run.status == "running":
                    run.status = "failed"
            node_run = await db.get(ContentNodeRun, node_id)
            if node_run and node_run.status == "running":
                node_run.status = "failed"
            await db.commit()
        await pg_manager.async_engine.dispose()
