"""CT01～CT07 标准化内容工厂真实 HTTP/Worker canary；止于内容审核。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from yuxi.content.control.workflow.deterministic_node import _trusted_quote_evidence_items
from yuxi.content.model.locked_blocks import render_semicolon_lines
from yuxi.content.v3.joint_workflow import PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID
from yuxi.services.dangjia_service import TRUSTED_QUOTE_SNAPSHOT_KEY
from yuxi.services.run_queue_service import list_run_stream_events
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentNodeRun, ContentTask
from yuxi.utils.auth_utils import AuthUtils


EXPECTED_FORMULAS = {
    "CT01": ("FRT12", "FRB05"),
    "CT02": ("FRT07", "FRB06"),
    "CT03": ("FRT01", "FRB07"),
    "CT04": ("FRT01", "FRB08"),
    "CT05": ("FRT01", "FRB09"),
    "CT06": ("FRT05", "FRB04"),
    "CT07": ("FRT12", "FRB10"),
}

QUOTE_CASES = {
    "CT02": ("项目单价", "standard_unit_price", "水电开槽：35元/米；强弱电布线：120元/㎡", "1.2w"),
    "CT03": ("单价面积", "standard_unit_price", "水电改造：120元/㎡×89㎡=10680元；人工合计：10680元", "1.068w"),
    "CT04": ("工种总价", "project_quote", "拆除：1954元；泥工：9205元；水电：7886元；人工合计：19045元", "1.9045w"),
    "CT05": ("人工辅材", "project_quote", "人工：12800元；辅材：9000元；整套合计：21800元", "2.18w"),
}

CASE_VALUES = {
    "CT01": {
        "product": "老房翻新",
        "price": "33341元",
        "quote_type": "项目报价",
        "process": ["先现场勘察", "再按节点验收"],
        "result": "项目已经省心完工并由业主现场验收",
        "scene": "长沙老房翻新施工现场",
    },
    "CT02": {
        "product": "长沙同城装修水电改造人工",
        "price": "120元/㎡",
        "quote_type": "标准单价",
        "process": ["按实际施工面积核算", "进场前确认报价口径"],
        "result": "报价范围已经向业主逐项说明",
    },
    "CT03": {
        "product": "水电",
        "price": "120元/㎡",
        "quote_type": "项目报价",
        "process": ["按89㎡施工面积核算", "完工后按节点验收"],
        "result": "本次水电改造已按约定范围完工",
        "scene": "长沙同城装修小户型水电改造现场",
    },
    "CT04": {
        "product": "泥瓦",
        "price": "28600元",
        "quote_type": "项目报价",
        "process": ["砌筑找平", "防水施工", "铺砖人工", "泥瓦辅材"],
        "trade_breakdown": [
            {"trade": "砌筑找平", "amount": 6800, "unit": "元", "included_items": ["墙地面找平"]},
            {"trade": "防水施工", "amount": 5200, "unit": "元", "included_items": ["厨卫防水"]},
            {"trade": "铺砖人工", "amount": 9600, "unit": "元", "included_items": ["墙地砖铺贴"]},
            {"trade": "泥瓦辅材", "amount": 7000, "unit": "元", "included_items": ["水泥砂浆辅材"]},
        ],
        "result": "泥瓦阶段已经完成节点验收",
        "scene": "长沙同城装修小户型泥瓦施工现场",
    },
    "CT05": {
        "product": "墙面",
        "price": "21800元",
        "quote_type": "项目报价",
        "process": ["基层处理", "墙面找平", "底漆施工", "面漆施工"],
        "labor_aux_breakdown": {
            "labor_total": 12800,
            "auxiliary_total": 9000,
            "unit": "元",
            "trades": [
                {
                    "trade": "基层处理",
                    "labor_amount": 3600,
                    "auxiliary_amount": 2200,
                    "included_items": ["铲除修补"],
                },
                {
                    "trade": "墙面找平",
                    "labor_amount": 3200,
                    "auxiliary_amount": 2600,
                    "included_items": ["腻子找平"],
                },
                {
                    "trade": "底漆施工",
                    "labor_amount": 2600,
                    "auxiliary_amount": 1700,
                    "included_items": ["底漆涂刷"],
                },
                {
                    "trade": "面漆施工",
                    "labor_amount": 3400,
                    "auxiliary_amount": 2500,
                    "included_items": ["面漆涂刷"],
                },
            ],
        },
        "advantages": ["报价逐项列明", "材料透明"],
        "result": "墙面施工已完成并通过现场验收",
        "scene": "长沙同城装修小户型墙面施工现场",
    },
    "CT06": {
        "product": "卫生间防水施工",
        "price": "3200元",
        "quote_type": "项目报价",
        "process": ["基层清理", "阴阳角加强", "闭水试验", "节点验收"],
        "result": "闭水试验和防水节点验收已经完成",
    },
    "CT07": {
        "product": "巡检",
        "price": "6800元",
        "quote_type": "项目报价",
        "process": ["上午检查水电定位", "下午核对材料并记录整改项"],
        "result": "当天问题已经登记并安排整改复查，让后续装修更安心入住",
        "pain": ["水电老化", "担心施工节点无法验收"],
        "advantages": ["功能分区明确", "节点透明"],
        "scene": "长沙同城装修本地实景工地巡检现场",
    },
}

CONTENT_NODES = {
    "build_creation_plan",
    "validate_material_gate",
    "freeze_production_pack",
    "generate_content",
    "deterministic_validate",
    "compose_locked_quote_block",
    "validate_composed_content",
    "semantic_review",
    "human_content_approval",
}
IMAGE_NODES = {"submit_cover_job", "wait_cover_job", "visual_review", "select_cover"}


def _brief(content_type_code: str) -> dict[str, Any]:
    values = {
        "location": "长沙",
        "quantity": "89㎡",
        "pain": ["担心报价口径不清", "担心施工节点无法验收"],
        "advantages": ["节点透明", "材料与费用边界提前说明"],
        "persona_fact": "我在长沙做装修施工管理5年，坚持每天到现场核对关键节点",
        "scene": "长沙同城装修施工现场",
        "brand_name": "长沙当家工长",
        "call_to_action": "需要同类项目清单可以留言咨询",
        **CASE_VALUES[content_type_code],
    }
    values.update(
        {
            "advantage": values["advantages"],
            "renovation_scene": values["scene"],
            "project_type": values["product"],
            "area": values["quantity"],
            "budget": values["price"],
            "craft_and_materials": values["process"],
            "owner_pain": values["pain"],
            "project_result": values["result"],
        }
    )
    if content_type_code in QUOTE_CASES:
        for key in ("price", "budget", "quote_type", "trade_breakdown", "labor_aux_breakdown"):
            values.pop(key, None)
    return {
        "brand": {"name": values["brand_name"]},
        "audience": ["长沙准备装修的业主"],
        "business_variables": {},
        "persona": {"identity": "长沙装修工长", "tone": "专业、直接、口语化"},
        "required_terms": [],
        "forbidden_terms": [],
        "attachments": [],
        "locked_fields": list(values),
        "form_values": values,
        "material_confirmations": [],
    }


def _trusted_quote_snapshot(content_type_code: str, serial_no: str) -> dict[str, Any]:
    quote_format, quote_type, original_content, title_price = QUOTE_CASES[content_type_code]
    return {
        "schema_version": 1,
        "source": "dangjia",
        "serial_no": serial_no,
        "content_type_code": content_type_code,
        "quote_format": quote_format,
        "quote_type": quote_type,
        "title_price": {"label": "整套人工合计", "display_text": title_price},
        "quote_block": {
            "original_content": original_content,
            "content_hash": hashlib.sha256(original_content.encode("utf-8")).hexdigest(),
            "render_policy": "semicolon-lines-v1",
            "insertion_policy": "after-opening-paragraph-v1",
        },
    }


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def canary_token():
    if os.getenv("STANDARDIZED_FACTORY_CANARY") != "1":
        pytest.skip("需显式启用标准化内容工厂 canary")
    uid = os.getenv("RULE_EDITOR_TEST_UID")
    if not uid:
        pytest.skip("需配置隔离测试管理员")
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one()
        token = AuthUtils.create_access_token({"sub": str(user.id)})
    yield token
    from yuxi.services.run_queue_service import close_queue_clients

    await close_queue_clients()
    await pg_manager.async_engine.dispose()


async def _resume_confirmation(client: httpx.AsyncClient, run_id: str) -> str:
    events = await list_run_stream_events(run_id, limit=500)
    interrupt = next(event["payload"]["payload"] for event in reversed(events) if event["event_type"] == "interrupt")
    assert interrupt["node_id"] in {"confirm_strategy_prices", "confirm_high_risk_facts"}, interrupt
    response = await client.post(
        f"/api/content/runs/{run_id}/resume",
        json={
            "request_id": uuid.uuid4().hex,
            "resume": {
                **interrupt,
                "confirmed_evidence_ids": interrupt.get("evidence_ids") or [],
            },
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


@pytest.mark.e2e
@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("content_type_code", list(EXPECTED_FORMULAS))
async def test_standardized_factory_content_canary(content_type_code: str, canary_token: str):
    """结构化物料通过质量门后只验证正文和语义审核，不进入图片生产。"""

    task_id = run_id = None
    run_stopped = False
    async with httpx.AsyncClient(
        base_url=os.getenv("TEST_BASE_URL", "http://localhost:5050"),
        timeout=30,
        headers={"Authorization": f"Bearer {canary_token}"},
    ) as client:
        try:
            bootstrap = await client.get("/api/content/bootstrap")
            assert bootstrap.status_code == 200, bootstrap.text
            template = next(item for item in bootstrap.json()["industry_templates"] if item["slug"] == "decoration")
            assert template["default_workflow_version_id"] == PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID

            response = await client.post(
                "/api/content/tasks",
                json={
                    "industry_template_id": template["id"],
                    "content_goal": template["default_goal"],
                    "content_type_code": content_type_code,
                    "creation_mode": "viral_rewrite",
                    "name": f"pytest 标准化内容 canary {content_type_code}",
                },
            )
            assert response.status_code == 200, response.text
            task = response.json()["task"]
            task_id = task["id"]
            assert task["workflow_version_id"] == PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID

            brief = _brief(content_type_code)
            response = await client.post(f"/api/content/tasks/{task_id}/compile-brief", json={"brief": brief})
            assert response.status_code == 200, response.text

            if content_type_code in QUOTE_CASES:
                snapshot = _trusted_quote_snapshot(content_type_code, f"canary-{task_id}")
                trusted_items = [
                    item.model_dump(mode="json")
                    for item in _trusted_quote_evidence_items(snapshot, content_type_code=content_type_code)
                ]
                async with pg_manager.AsyncSession() as db:
                    persisted_task = await db.get(ContentTask, task_id)
                    assert persisted_task is not None
                    persisted_task.runtime_config_snapshot_json = {
                        **(persisted_task.runtime_config_snapshot_json or {}),
                        TRUSTED_QUOTE_SNAPSHOT_KEY: snapshot,
                    }
                    evidence = dict(persisted_task.evidence_json or {})
                    evidence["items"] = [
                        item
                        for item in evidence.get("items") or []
                        if not set(item.get("variable_codes") or []).intersection(
                            {"title_price", "title_price_label", "quote_type", "quote_block"}
                        )
                    ] + trusted_items
                    persisted_task.evidence_json = evidence
                    await db.commit()

            response = await client.get(f"/api/content/tasks/{task_id}/creation-plan/preview")
            assert response.status_code == 200, response.text
            preview = response.json()
            expected_title, expected_body = EXPECTED_FORMULAS[content_type_code]
            assert preview["can_generate"] is True, preview
            assert preview["gaps"]["missing_variable_codes"] == []
            assert preview["plan"]["content_type_code"] == content_type_code
            assert preview["plan"]["title_formula"]["code"] == expected_title
            assert preview["plan"]["body_formula"]["code"] == expected_body
            assert preview["plan"]["reference"] is not None
            assert preview["production_order"]["title_formula_code"] == expected_title
            assert preview["production_order"]["body_formula_code"] == expected_body

            response = await client.post(
                f"/api/content/tasks/{task_id}/runs",
                json={"request_id": uuid.uuid4().hex},
            )
            assert response.status_code == 200, response.text
            run_id = response.json()["run_id"]
            print(f"{content_type_code}: task={task_id} run={run_id}", flush=True)

            deadline = time.monotonic() + 900
            result: dict[str, Any] = {}
            while time.monotonic() < deadline:
                response = await client.get(f"/api/content/runs/{run_id}")
                assert response.status_code == 200, response.text
                result = response.json()
                status = result["run"]["status"]
                if status == "interrupted":
                    run_id = await _resume_confirmation(client, run_id)
                    continue
                if status in {"failed", "cancelled", "completed"}:
                    async with pg_manager.AsyncSession() as db:
                        latest_generation = (
                            (
                                await db.execute(
                                    select(ContentNodeRun)
                                    .where(
                                        ContentNodeRun.task_id == task_id,
                                        ContentNodeRun.node_id == "generate_content",
                                    )
                                    .order_by(ContentNodeRun.attempt.desc(), ContentNodeRun.started_at.desc())
                                )
                            )
                            .scalars()
                            .first()
                        )
                    model_input = (
                        (latest_generation.input_snapshot or {}).get("model_visible_payload") or {}
                        if latest_generation
                        else {}
                    )
                    diagnostic = {
                        "validation_report": model_input.get("validation_report"),
                        "review_report": model_input.get("review_report"),
                    }
                    pytest.fail(f"{result['run'].get('error_message') or result}; diagnostic={diagnostic}")

                completed_nodes = {item["node_id"] for item in result["nodes"] if item["status"] == "completed"}
                if "human_content_approval" in completed_nodes:
                    response = await client.post(f"/api/content/runs/{run_id}/cancel")
                    assert response.status_code == 200, response.text
                    run_stopped = True
                    break
                await asyncio.sleep(0.5)
            else:
                pytest.fail(f"{content_type_code} 内容 canary 超时")

            # 取消信号在节点边界生效；若人工审核完成时下一节点已经开始，
            # 允许当前模型调用收尾后再观察到 cancelled。
            for _ in range(240):
                response = await client.get(f"/api/content/runs/{run_id}")
                assert response.status_code == 200, response.text
                result = response.json()
                if result["run"]["status"] in {"cancelled", "failed", "completed", "interrupted"}:
                    break
                await asyncio.sleep(0.5)
            else:
                pytest.fail(f"{content_type_code} 运行未能在内容审核后停止")

            async with pg_manager.AsyncSession() as db:
                node_runs = (
                    (
                        await db.execute(
                            select(ContentNodeRun)
                            .where(ContentNodeRun.task_id == task_id)
                            .order_by(ContentNodeRun.started_at, ContentNodeRun.attempt)
                        )
                    )
                    .scalars()
                    .all()
                )
            completed = {item.node_id for item in node_runs if item.status == "completed"}
            expected_nodes = set(CONTENT_NODES)
            assert expected_nodes <= completed
            assert not (
                {"select_creation_strategy", "reselect_creation_strategy"} & {item.node_id for item in node_runs}
            )
            assert not (IMAGE_NODES & {item.node_id for item in node_runs})

            generation = next(
                item
                for item in reversed(node_runs)
                if item.node_id == "generate_content" and item.status == "completed"
            )
            model_input = generation.input_snapshot["model_visible_payload"]
            assert "production_pack" in model_input
            assert set(model_input) <= {
                "production_pack",
                "validation_report",
                "review_report",
                "selected_title",
                "content_outline",
                "content_draft",
                "repair_constraints",
                "lexicon_constraints",
            }
            assert "content_brief" not in model_input and "evidence_bundle" not in model_input
            assert generation.input_snapshot["runtime_config_snapshot"]["model_input_contract"] == (
                "StandardizedGenerateContentPromptV1"
            )
            pack = model_input["production_pack"]
            assert pack["production_order"]["content_type_code"] == content_type_code
            assert pack["production_order"]["title_formula_code"] == expected_title
            assert pack["production_order"]["body_formula_code"] == expected_body
            assert pack["material_quality_report"]["status"] == "passed"
            assert pack["expression_policy"]["emoji_allowed"] is True
            assert pack["expression_policy"]["minimum_semantic_categories"] == 3
            required_requirement_ids = {
                item["requirement_id"] for item in pack["material_manifest"]["requirements"] if item["required"]
            }
            binding_ids = {item["requirement_id"] for item in pack["material_quality_report"]["bindings"]}
            assert required_requirement_ids <= binding_ids
            assert {"variable:persona_fact", "variable:process"} <= binding_ids
            assert len(pack["material_quality_report"]["materials_hash"]) == 64
            assert "production_pack_hash" not in pack
            assert len([item for item in pack["materials"] if item["material_type"] == "viral_reference"]) == 1
            if content_type_code in QUOTE_CASES:
                original_content = QUOTE_CASES[content_type_code][2]
                assert original_content not in json.dumps(model_input, ensure_ascii=False)
                assert pack["locked_blocks"][0]["block_id"] == "quote_block"
                quote_material = next(
                    item for item in pack["materials"] if "quote_block" in item.get("variable_codes", [])
                )
                assert quote_material["payload"]["value"]["locked"] is True

            review = next(
                item for item in reversed(node_runs) if item.node_id == "semantic_review" and item.status == "completed"
            )
            review_input = (
                review.input_snapshot.get("model_visible_payload") or review.input_snapshot["visible_payload"]
            )
            draft = review_input["content_draft"]
            assert str(draft.get("body") or "").strip()
            assert review_input["expression_policy"] == pack["expression_policy"]
            if content_type_code in QUOTE_CASES:
                assert render_semicolon_lines(QUOTE_CASES[content_type_code][2]) in draft["body"]
            print(
                f"{content_type_code}: {expected_title}/{expected_body}，"
                f"标准物料 {len(pack['materials'])} 条，内容审核通过",
                flush=True,
            )
        finally:
            if run_id and not run_stopped:
                await client.post(f"/api/content/runs/{run_id}/cancel")
            if task_id:
                for _ in range(60):
                    response = await client.delete(f"/api/content/tasks/{task_id}")
                    if response.status_code == 200:
                        break
                    await asyncio.sleep(0.5)
                assert response.status_code == 200, response.text
