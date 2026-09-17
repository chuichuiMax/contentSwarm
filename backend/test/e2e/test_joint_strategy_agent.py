"""真实 HTTP 创建任务，运行受管联合 Agent，并锁定新策略快照。"""

import os
import time
import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from yuxi.content.control.workflow.agent_node import AgentNodeHandler
from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
from yuxi.content.control.workflow.joint_strategy import lock_joint_strategy, prepare_strategy_candidates
from yuxi.content.model.contracts.joint_strategy import StrategySnapshotV2
from yuxi.content.model.strategy import load_selection_policy
from yuxi.content.v3.joint_workflow import WORKFLOW_JOINT
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import AgentRun, User
from yuxi.storage.postgres.models_content import ContentNodeRun, ContentTask, ContentViralArticleVersion
from yuxi.storage.postgres.models_knowledge import KnowledgeFile
from yuxi.utils.auth_utils import AuthUtils


def make_brief(industry):
    product = "厨房动线规划服务" if industry == "decoration" else "成人英语入门课程"
    audience = "准备装修厨房的新手业主" if industry == "decoration" else "零基础职场英语学习者"
    pain = "备菜洗菜炒菜来回走动、收纳位置不顺手" if industry == "decoration" else "不知道先学单词还是句型，怕开口表达"
    brief = {
        "topic": product,
        "audience": [audience],
        "content_goal": "educate",
        "channel": "小红书",
        "persona": {"style": "亲切、具体、专业"},
        "form_values": {
            "brand_name": "隔离测试品牌",
            "product": product,
            "audience": audience,
            "pain": pain,
            "process": ["先收集实际需求", "列出三个常见问题", "根据使用习惯制定方案"],
            "service_advantage": "先沟通再给方案",
            "core_selling_points": ["清晰说明步骤", "建议可以直接实践"],
            "project_name": "仅用于自动化验证的合成案例",
            "price": "咨询不收费",
            "city": "长沙",
            "proof": "这是方法说明内容；未提供效果数据，不得编造已发生结果",
            "cta": "分享自己遇到的问题",
        },
    }
    return brief


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize(
    "industry,creation_mode",
    [
        ("decoration", "original"),
        ("education", "original"),
        ("decoration", "viral_rewrite"),
        ("education", "viral_rewrite"),
    ],
)
async def test_managed_joint_agent_respects_industry_mode_and_locks_snapshot(industry, creation_mode):
    uid = os.environ.get("RULE_EDITOR_TEST_UID")
    if not uid:
        pytest.skip("未配置隔离测试管理员")
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one()
        headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(user.id)})}"}
    await pg_manager.async_engine.dispose()
    task_id = run_id = node_id = None
    reference_file_id = None
    reference_assets = []
    async with httpx.AsyncClient(
        base_url=os.environ.get("TEST_BASE_URL", "http://localhost:5050"), timeout=30
    ) as client:
        try:
            response = await client.get("/api/content/bootstrap", headers=headers)
            response.raise_for_status()
            template = next(item for item in response.json()["industry_templates"] if item["slug"] == industry)
            response = await client.post(
                "/api/content/tasks",
                headers=headers,
                json={
                    "industry_template_id": template["id"],
                    "content_goal": "educate",
                    "content_type_code": "CT02",
                    "creation_mode": creation_mode,
                    "name": f"pytest 联合策略 Agent {industry}",
                },
            )
            assert response.status_code == 200, response.text
            task_id = response.json()["task"]["id"]
            brief = make_brief(industry)
            response = await client.put(f"/api/content/tasks/{task_id}/brief", headers=headers, json={"brief": brief})
            assert response.status_code == 200, response.text
            async with pg_manager.AsyncSession() as db:
                if creation_mode == "viral_rewrite":
                    from test.unit.content.test_viral_asset_preparation import prepared, source
                    from yuxi.content.model.viral_assets import validate_prepared_asset
                    from yuxi.repositories.viral_asset_repository import ViralAssetRepository
                    from yuxi.services.agent_runtime_service import resolve_agent_runtime_context
                    from yuxi.services.content_viral_assets import preparation_skill_hash

                    managed = await resolve_agent_runtime_context(
                        db=db, user=user, bound_agent_id="content-joint-strategy-agent"
                    )
                    assert managed.knowledges, "测试需要联合 Agent 已配置知识库"
                    kb_id = managed.knowledges[0]
                    reference_file_id = f"file_test_{uuid.uuid4().hex[:16]}"
                    db.add(
                        KnowledgeFile(
                            file_id=reference_file_id,
                            kb_id=kb_id,
                            filename="pytest 联合参考.md",
                            status="parsed",
                            content_hash="file-version-1",
                            created_by=uid,
                        )
                    )
                    await db.flush()
                    for index in range(2):
                        article = source(
                            kb_id=kb_id,
                            file_id=reference_file_id,
                            locator=f"row:{index}",
                            industry_slug=industry,
                            **(
                                {
                                    "title": "成人英语怎么开始",
                                    "body": "先了解学习基础。\n再制定英语学习计划。\n你想先练哪种表达？",
                                }
                                if industry == "education"
                                else {}
                            ),
                        )
                        asset = await ViralAssetRepository(db).register(
                            article, skill_hash=preparation_skill_hash(), uid=uid
                        )
                        preparation = prepared(article)
                        if industry == "education":
                            preparation["reference_card"].update(
                                audience="零基础职场学习者",
                                scene="成人英语入门",
                                goal="知识讲解",
                                summary="先了解学习基础，再制定成人英语学习计划",
                                channel="小红书",
                            )
                            preparation["reference_blueprint"]["content_block_sequence"] = ["基础", "计划", "互动"]
                        asset.prepared_json = validate_prepared_asset(preparation, article).model_dump(mode="json")
                        asset.status = "ready"
                        reference_assets.append(asset.id)
                task = await db.get(ContentTask, task_id)
                if industry != "decoration":
                    task.content_type_code = None
                task.runtime_config_snapshot_json = {
                    **task.runtime_config_snapshot_json,
                    "selection_policy_snapshot": load_selection_policy(),
                }
                run_id, node_id = f"run_{uuid.uuid4().hex}", f"cn_{uuid.uuid4().hex}"
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
                db.add(
                    ContentNodeRun(
                        id=node_id,
                        task_id=task_id,
                        agent_run_id=run_id,
                        node_id="select_creation_strategy",
                        node_type="agent",
                        status="running",
                    )
                )
                await db.commit()
                state = {
                    "task_id": task_id,
                    "run_id": run_id,
                    "uid": uid,
                    "content_brief": brief,
                    "evidence_bundle": {"items": []},
                    "runtime_config_snapshot": task.runtime_config_snapshot_json,
                }
                state.update(await prepare_strategy_candidates(db=db, state=state, node_run_id=node_id))
                node = next(item for item in WORKFLOW_JOINT["nodes"] if item["id"] == "select_creation_strategy")
                started = time.monotonic()
                state.update(await AgentNodeHandler().execute(db=db, node=node, state=state, node_run_id=node_id))
                duration = time.monotonic() - started
                state.update(await lock_joint_strategy(db=db, state=state, node_run_id=node_id))
                snapshot = StrategySnapshotV2.model_validate(state["strategy_snapshot"])
                assert snapshot.strategy_mode == ("direction_scoped" if industry == "decoration" else "scored")
                assert snapshot.content_direction == ("CT02" if industry == "decoration" else None)
                if creation_mode == "original":
                    assert snapshot.decision.reference.status == "not_requested"
                else:
                    assert snapshot.decision.reference.status == "selected"
                    assert snapshot.decision.reference.selected_asset_id in reference_assets
                    assert len(snapshot.decision.reference.assessments) == 2
                    assert snapshot.reference_snapshot["reference_blueprint"]
                    assert "body" not in snapshot.reference_snapshot
                assert snapshot.creation_methods
                assert "selected_group_id" not in snapshot.model_dump()
                if industry == "decoration":
                    assert all(item.total is None for item in snapshot.decision.strategy.title_assessments)
                assert duration < 75
                print(
                    f"{industry}: joint decision {duration:.1f}s, "
                    f"{snapshot.title_formula['code']}/{snapshot.body_formula['code']}"
                )
                parent = await db.get(AgentRun, run_id)
                if creation_mode == "viral_rewrite":
                    handler = V3DeterministicNodeHandler()
                    state.update(await handler._compile_runtime_snapshot(db=db, state=state, node_run_id=node_id))
                    state.update(await handler._normalize_evidence(db=db, state=state, node_run_id=node_id))
                    state.update(await handler._merge_research_evidence(db=db, state=state, node_run_id=node_id))
                    state.update(await handler._freeze_evidence_bundle(db=db, state=state, node_run_id=node_id))
                    state.update(await handler._load_formula_lexicons(db=db, state=state, node_run_id=node_id))
                    state["persona_profile"] = {}
                    previous_node = await db.get(ContentNodeRun, node_id)
                    previous_node.status = "completed"
                    node_id = f"cn_{uuid.uuid4().hex}"
                    db.add(
                        ContentNodeRun(
                            id=node_id,
                            task_id=task_id,
                            agent_run_id=run_id,
                            node_id="generate_content",
                            node_type="agent",
                            status="running",
                        )
                    )
                    await db.commit()
                    generation = next(item for item in WORKFLOW_JOINT["nodes"] if item["id"] == "generate_content")
                    state.update(
                        await AgentNodeHandler().execute(db=db, node=generation, state=state, node_run_id=node_id)
                    )
                    assert state["content_draft"] and state["selected_title"]
                    print(f"{industry}: prepared reference consumed by real generation Agent")
                parent.status = "completed"
                await db.commit()
        finally:
            if reference_file_id:
                async with pg_manager.AsyncSession() as db:
                    await db.execute(
                        delete(ContentViralArticleVersion).where(
                            ContentViralArticleVersion.file_id == reference_file_id
                        )
                    )
                    await db.execute(delete(KnowledgeFile).where(KnowledgeFile.file_id == reference_file_id))
                    await db.commit()
            if run_id:
                async with pg_manager.AsyncSession() as db:
                    parent = await db.get(AgentRun, run_id)
                    node_run = await db.get(ContentNodeRun, node_id)
                    if parent and parent.status == "running":
                        parent.status = "failed"
                    if node_run:
                        node_run.status = parent.status if parent else "failed"
                    await db.commit()
            await pg_manager.async_engine.dispose()
            if task_id:
                response = await client.delete(f"/api/content/tasks/{task_id}", headers=headers)
                assert response.status_code == 200, response.text
