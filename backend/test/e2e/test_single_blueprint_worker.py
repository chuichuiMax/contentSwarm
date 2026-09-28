"""真实 HTTP → Redis 队列 → Worker → PostgreSQL 正文 checkpoint；图片阶段前取消。"""

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

import httpx
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select

from yuxi.content.model.locked_blocks import extract_locked_quote_block
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import AgentRun, User
from yuxi.storage.postgres.models_content import ContentNodeRun
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_worker_persists_single_blueprint_body_without_semantic_review():
    await _run_body_via_worker("industry-decoration-single-blueprint-candidate", "CT02", "北京报价002.txt")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_default_factory_worker_replaces_forbidden_words():
    await _run_body_via_worker("industry-decoration-v3", "CT03", "施工报价json模板.txt")


async def _run_body_via_worker(template_id, content_type, filename):
    semantic_review_enabled = template_id == "industry-decoration-v3"
    case_dir, uid = os.getenv("BUILTIN_CONTENT_CASE_DIR"), os.getenv("RULE_EDITOR_TEST_UID")
    if not case_dir or not uid:
        pytest.skip("需配置本地真实服务测试用户与报价案例目录")
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one()
        token = AuthUtils.create_access_token({"sub": str(user.id)})
    run_id = None
    async with httpx.AsyncClient(
        base_url="http://localhost:5050", timeout=120, headers={"Authorization": f"Bearer {token}"}
    ) as client:
        try:
            bootstrap = await client.get("/api/content/bootstrap")
            bootstrap.raise_for_status()
            template = next(t for t in bootstrap.json()["industry_templates"] if t["id"] == template_id)
            response = await client.post(
                "/api/content/tasks",
                json={
                    "industry_template_id": template_id,
                    "content_goal": template["default_goal"],
                    "content_type_code": content_type,
                    "creation_mode": "viral_rewrite",
                    "name": f"{content_type} 真实 Worker 封禁词与正文验收",
                },
            )
            assert response.status_code == 200, response.text
            task_id = response.json()["task"]["id"]
            response = await client.post(
                f"/api/content/tasks/{task_id}/compile-brief",
                json={"brief": {"user_request": (Path(case_dir) / filename).read_text()}},
            )
            assert response.status_code == 200, response.text
            response = await client.post(
                f"/api/content/tasks/{task_id}/runs",
                json={"request_id": uuid.uuid4().hex, "model_spec": os.getenv("BUILTIN_CONTENT_MODEL")},
            )
            assert response.status_code == 200, response.text
            run_id = response.json()["run_id"]
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                async with pg_manager.AsyncSession() as db:
                    run = await db.get(AgentRun, run_id)
                    assert run.run_type == "content"
                    assert run.status not in {"failed", "cancelled"}, run.error_message
                    nodes = (
                        (await db.execute(select(ContentNodeRun).where(ContentNodeRun.agent_run_id == run_id)))
                        .scalars()
                        .all()
                    )
                if not semantic_review_enabled:
                    assert not any(n.node_id == "semantic_review" for n in nodes)
                approvals = [n for n in nodes if n.node_id == "human_content_approval" and n.status == "completed"]
                if approvals:
                    snapshot = await AsyncPostgresSaver(pg_manager.langgraph_pool).aget(
                        {"configurable": {"thread_id": run.checkpoint_thread_id}}
                    )
                    state = snapshot["channel_values"] if snapshot else {}
                    if state.get("approval_result", {}).get("status") == "approved":
                        response = await client.post(f"/api/content/runs/{run_id}/cancel")
                        assert response.status_code == 200, response.text
                        quote = extract_locked_quote_block(state["production_pack"])
                        assert state["content_draft"]["body"].count(quote["rendered_content"]) == 1
                        assert state["composed_content_validation_report"]["status"] == "passed"
                        if semantic_review_enabled:
                            assert state["review_report"]["status"] in {"passed", "warning"}
                        else:
                            assert state["content_draft"]["blueprint_content"]["blocks"]
                            assert not state.get("review_report")
                        lexicon = state["production_pack"]["content_rule_bundle"]["runtime_rules"][
                            "viral-platform-expression"
                        ]["forbidden_lexicon"]
                        assert lexicon["sources"] and lexicon["alternatives"]
                        final_text = "\n".join(
                            [
                                state["selected_title"]["text"],
                                state["content_draft"]["body"],
                                *state["content_draft"]["topics"],
                            ]
                        )
                        assert not [term for term in lexicon["alternatives"] if term in final_text]
                        output = Path(os.environ["BLUEPRINT_WORKER_TEST_OUTPUT"])
                        output.parent.mkdir(parents=True, exist_ok=True)
                        output.write_text(
                            json.dumps(
                                {
                                    "task_id": task_id,
                                    "run_id": run_id,
                                    "checkpoint_thread_id": run.checkpoint_thread_id,
                                    "title": state["selected_title"],
                                    "draft": state["content_draft"],
                                    "review": state.get("review_report"),
                                    "semantic_review_status": "run" if semantic_review_enabled else "not_run",
                                    "forbidden_lexicon": lexicon,
                                    "replacement_diffs": state["channel_result"]["replacement_diffs"],
                                    "workflow_version_id": template["default_workflow_version_id"],
                                    "nodes": [{"id": n.node_id, "status": n.status} for n in nodes],
                                    "scope": "真实 Worker 正文与封禁词校验，正文 checkpoint 持久化后取消图片流程",
                                },
                                ensure_ascii=False,
                                indent=2,
                            )
                        )
                        break
                await asyncio.sleep(0.5)
            else:
                pytest.fail("Worker 正文未在 600 秒内通过程序校验")
        finally:
            if run_id:
                await client.post(f"/api/content/runs/{run_id}/cancel")
            await pg_manager.close()
