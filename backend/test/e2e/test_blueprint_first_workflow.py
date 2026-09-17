"""真实 HTTP/Worker：错误方向自动调整，选择匹配参考并生成正文。"""

import asyncio
import json
import os
import socket
import time
import uuid

import httpx
import pytest
from patchright.async_api import async_playwright, expect
from sqlalchemy import delete, select

from test.e2e.test_joint_strategy_agent import make_brief
from test.unit.content.test_viral_asset_preparation import prepared, source
from yuxi.content.model.contracts.joint_strategy import StrategySnapshotV2
from yuxi.content.model.strategy import load_selection_policy
from yuxi.content.model.viral_assets import validate_prepared_asset
from yuxi.content.model.workflows.definition import workflow_definition_hash
from yuxi.content.v3.joint_workflow import PLATFORM_WORKFLOW_BLUEPRINT_FIRST_ID, WORKFLOW_BLUEPRINT_FIRST
from yuxi.repositories.viral_asset_repository import ViralAssetRepository
from yuxi.services.agent_runtime_service import resolve_agent_runtime_context
from yuxi.services.content_viral_assets import preparation_skill_hash
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentNodeRun, ContentTask, ContentViralArticleVersion
from yuxi.storage.postgres.models_knowledge import KnowledgeFile
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("industry", ["decoration", "education"])
async def test_blueprint_first_worker_selects_supported_direction_and_reference(industry):
    uid = os.getenv("RULE_EDITOR_TEST_UID")
    if not uid:
        pytest.skip("需要隔离测试管理员")
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one()
        token = AuthUtils.create_access_token({"sub": str(user.id)})
    await pg_manager.async_engine.dispose()
    task_id = run_id = file_id = None
    terminal = False
    async with httpx.AsyncClient(
        base_url=os.getenv("TEST_BASE_URL", "http://localhost:5050"),
        timeout=30,
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        try:
            response = await client.post(
                "/api/content/tasks",
                json={
                    "industry_template_id": f"industry-{industry}-v3",
                    "content_goal": "acquire" if industry == "decoration" else "educate",
                    "content_type_code": "CT02" if industry == "decoration" else None,
                    "creation_mode": "viral_rewrite" if industry == "decoration" else "original",
                    "name": f"pytest 资料优先 {industry}",
                },
            )
            assert response.status_code == 200, response.text
            task_id = response.json()["task"]["id"]
            brief = make_brief(industry)
            if industry == "decoration":
                brief.update(topic="89㎡三居玄关收纳改造真实案例", content_goal="acquire")
                brief["form_values"].update(
                    product="89㎡三居收纳改造",
                    project_type="三室两厅",
                    area="89㎡",
                    owner_pain="玄关杂物堆积，儿童房学习和储物互相挤占",
                    process="实测家庭物品后，重做玄关柜、餐边柜和儿童房组合柜",
                    craft_and_materials="按每日动线规划柜体分区，完成后现场验收",
                    project_result="已完工并由业主验收，增加12㎡收纳空间，儿童房书桌与储物分区独立",
                    result="已完工验收，增加12㎡收纳空间",
                    proof="合成案例的已确认完工记录；没有分项报价和师傅从业年限",
                    budget="硬装总预算18万元",
                    price="仅有总预算18万元，不是分项报价",
                )
            response = await client.put(f"/api/content/tasks/{task_id}/brief", json={"brief": brief})
            assert response.status_code == 200, response.text
            async with pg_manager.AsyncSession() as db:
                task = await db.get(ContentTask, task_id)
                task.content_type_code = None
                task.workflow_version_id = PLATFORM_WORKFLOW_BLUEPRINT_FIRST_ID
                task.workflow_definition_hash = workflow_definition_hash(WORKFLOW_BLUEPRINT_FIRST)
                task.runtime_config_snapshot_json = {
                    **task.runtime_config_snapshot_json,
                    "workflow_version_id": task.workflow_version_id,
                    "workflow_definition_hash": task.workflow_definition_hash,
                    "selection_policy_snapshot": load_selection_policy(),
                    "strategy_mode": "direction_scoped" if industry == "decoration" else "scored",
                }
                if industry == "decoration":
                    managed = await resolve_agent_runtime_context(
                        db=db, user=user, bound_agent_id="content-joint-strategy-agent"
                    )
                    assert managed.knowledges
                    file_id = f"file_test_{uuid.uuid4().hex[:16]}"
                    db.add(
                        KnowledgeFile(
                            file_id=file_id,
                            kb_id=managed.knowledges[0],
                            filename="pytest 收纳改造案例.md",
                            status="parsed",
                            content_hash="file-version-1",
                            created_by=uid,
                        )
                    )
                    await db.flush()
                    article = source(
                        kb_id=managed.knowledges[0],
                        file_id=file_id,
                        title="89㎡玄关与儿童房收纳改造案例",
                        body="玄关杂物多，儿童房没有独立学习区。\n按家庭物品和动线规划玄关柜、餐边柜和儿童房组合柜。\n完工验收后，收纳增加，学习区独立。\n你家哪一处最需要整理？",
                    )
                    preparation = prepared(article)
                    preparation["reference_card"].update(
                        audience="改善型装修家庭",
                        scene="三居玄关与儿童房收纳改造真实案例",
                        goal="案例展示与咨询获客",
                        channel="小红书",
                        summary="通过真实痛点、收纳改造过程和完工结果展示空间设计能力",
                        required_slots=[
                            {
                                "name": "改造过程",
                                "description": "当前项目实施的收纳布局措施",
                                "required": True,
                                "anchor": {
                                    "section": "body",
                                    "quote": "按家庭物品和动线规划玄关柜、餐边柜和儿童房组合柜。",
                                },
                            },
                            {
                                "name": "完工结果",
                                "description": "当前项目已发生的收纳改造效果",
                                "required": True,
                                "anchor": {"section": "body", "quote": "完工验收后，收纳增加，学习区独立。"},
                            },
                        ],
                    )
                    preparation["reference_blueprint"].update(
                        content_block_sequence=["收纳痛点", "改造过程", "完工结果", "互动"],
                        narrative_structure="痛点到过程再到结果的案例展示",
                    )
                    asset = await ViralAssetRepository(db).register(
                        article, skill_hash=preparation_skill_hash(), uid=uid
                    )
                    asset.prepared_json = validate_prepared_asset(preparation, article).model_dump(mode="json")
                    asset.status = "ready"
                    expected_asset_id = asset.id
                await db.commit()
            await pg_manager.async_engine.dispose()
            started = time.monotonic()
            response = await client.post(f"/api/content/tasks/{task_id}/runs", json={"request_id": uuid.uuid4().hex})
            assert response.status_code == 200, response.text
            run_id = response.json()["run_id"]
            print(f"task={task_id} run={run_id}", flush=True)
            while time.monotonic() - started < 360:
                result = (await client.get(f"/api/content/runs/{run_id}")).json()
                completed = {node["node_id"] for node in result["nodes"] if node["status"] == "completed"}
                if "generate_content" in completed or result["run"]["status"] in {
                    "failed",
                    "cancelled",
                    "interrupted",
                    "completed",
                }:
                    terminal = result["run"]["status"] in {"failed", "cancelled", "completed", "interrupted"}
                    break
                await asyncio.sleep(3)
            assert "generate_content" in completed, result["run"].get("error_message") or result["nodes"]
            decision = (await client.get(f"/api/content/tasks/{task_id}/strategy/decision")).json()
            snapshot = StrategySnapshotV2.model_validate(decision["snapshot"])
            if industry == "decoration":
                assert decision["automatic_direction"] is True
                assert snapshot.content_direction == "CT01"
                assert snapshot.decision.reference.selected_asset_id == expected_asset_id
                assert snapshot.decision.reference.slot_mapping
            else:
                assert snapshot.strategy_mode == "scored" and snapshot.content_direction is None
                assert snapshot.decision.reference.status == "not_requested"
            task_data = (await client.get(f"/api/content/tasks/{task_id}")).json()["task"]
            assert task_data["content_type_code"] is None
            async with pg_manager.AsyncSession() as db:
                output = (
                    await db.execute(
                        select(ContentNodeRun.output_snapshot).where(
                            ContentNodeRun.task_id == task_id,
                            ContentNodeRun.node_id == "generate_content",
                            ContentNodeRun.status == "completed",
                        )
                    )
                ).scalar_one()
                assert "content_draft" in output["updated_fields"]
            await pg_manager.async_engine.dispose()
            print(
                f"{industry}: blueprint/strategy/real generation passed {time.monotonic() - started:.1f}s", flush=True
            )
            if industry == "decoration":
                async with async_playwright() as pw:
                    browser = await pw.chromium.launch(
                        headless=True,
                        args=["--no-sandbox", f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}"],
                    )
                    try:
                        context = await browser.new_context(viewport={"width": 1440, "height": 1100})
                        await context.add_init_script(f"localStorage.setItem('user_token', {json.dumps(token)})")
                        page = await context.new_page()
                        await page.goto(f"http://localhost:5173/content/tasks/{task_id}")
                        await expect(page.locator(".automatic-direction")).to_be_visible(timeout=20000)
                        await expect(page.locator(".automatic-direction")).to_contain_text("案例/成果展示")
                        await page.screenshot(path="/tmp/blueprint-first-direction.png", full_page=True)
                    finally:
                        await browser.close()
        finally:
            if run_id and not terminal:
                await client.post(f"/api/content/runs/{run_id}/cancel")
                for _ in range(15):
                    current = (await client.get(f"/api/content/runs/{run_id}")).json()["run"]["status"]
                    if current in {"failed", "cancelled", "completed", "interrupted"}:
                        break
                    await asyncio.sleep(1)
            if file_id:
                async with pg_manager.AsyncSession() as db:
                    await db.execute(
                        delete(ContentViralArticleVersion).where(ContentViralArticleVersion.file_id == file_id)
                    )
                    await db.execute(delete(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
                    await db.commit()
                await pg_manager.async_engine.dispose()
            if task_id:
                response = await client.delete(f"/api/content/tasks/{task_id}")
                assert response.status_code == 200, response.text


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="module")
async def test_blueprint_first_entry_needs_no_user_direction():
    import re

    uid = os.getenv("RULE_EDITOR_TEST_UID")
    if not uid:
        pytest.skip("需要隔离测试管理员")
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one()
        token = AuthUtils.create_access_token({"sub": str(user.id)})
    await pg_manager.async_engine.dispose()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}"],
        )
        try:
            context = await browser.new_context(viewport={"width": 1440, "height": 1050})
            await context.add_init_script(f"localStorage.setItem('user_token', {json.dumps(token)})")
            page = await context.new_page()
            await page.goto("http://localhost:5173/content/new")
            for industry in ("装修", "教育"):
                await page.get_by_role("button", name=re.compile(industry)).first.click()
                await expect(page.locator(".auto-strategy-hint")).to_be_visible()
                await expect(page.get_by_text("一级内容方向", exact=True)).to_have_count(0)
            await page.get_by_role("button", name=re.compile("装修")).first.click()
            await expect(page.get_by_text("根据本次资料评分选择该行业的公式和创作手法。", exact=True)).to_have_count(0)
            await page.screenshot(path="/tmp/blueprint-first-entry.png", full_page=True)
        finally:
            await browser.close()
