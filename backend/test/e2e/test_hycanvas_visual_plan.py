"""只有模板字段的视觉方案经过真实队列、HyCanvas 和存储完成封面。"""

import asyncio
import io
import uuid
from types import SimpleNamespace

import pytest
from PIL import Image, ImageStat
from sqlalchemy import delete, select

from test.integration.conftest import test_client as test_client
from test.integration.api.test_material_library_router import material_users as material_users, _png
from yuxi.agents.toolkits.content.tools import create_content_cover_job
from yuxi.content.control.workflow.agent_node import AgentNodeResultMapper
from yuxi.services.hycanvas_service import HyCanvasClient
from yuxi.storage.minio.client import get_minio_client
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import ContentCoverAsset, ContentCoverJob, ContentTask


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_template_fields_only_plan_renders_cover(test_client, material_users):
    headers = material_users["owner"]
    uid = (await test_client.get("/api/auth/me", headers=headers)).json()["uid"]
    hycanvas = HyCanvasClient.from_env()
    templates = (await hycanvas.list_xiaohongshu_templates())["templates"]
    template = next(t for t in templates if t["id"] == "system-cover-personalized-template-1-top-left")
    bootstrap = (await test_client.get("/api/content/bootstrap", headers=headers)).json()
    industry = next(t for t in bootstrap["industry_templates"] if t["slug"] == "decoration")
    created = await test_client.post(
        "/api/content/tasks",
        headers=headers,
        json={
            "industry_template_id": industry["id"],
            "name": f"pytest_hycanvas_fields_{uuid.uuid4().hex[:8]}",
            "mode": "pro",
            "content_goal": industry["default_goal"],
            "content_type_code": "CT05",
        },
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task"]["id"]
    job_id = None
    try:
        uploaded = await test_client.post(
            "/api/content/covers/assets",
            headers=headers,
            data={"role": "source"},
            files={"file": ("background.png", _png(), "image/png")},
        )
        assert uploaded.status_code == 201, uploaded.text
        asset_id = uploaded.json()["asset"]["id"]
        async with pg_manager.get_async_session_context() as db:
            task = await db.get(ContentTask, task_id)
            task.runtime_config_snapshot_json = {
                "visual_material": {
                    "cover_mode": "builtin",
                    "image_asset_id": asset_id,
                    "hycanvas_template_id": template["id"],
                    "hycanvas_fillable_fields": template["fillable_fields"],
                }
            }
        fields = {"主标题": "旧房装修项目明细", "副标题": "材料和施工范围提前讲清楚"}
        plan = AgentNodeResultMapper.to_state(
            "plan_visuals",
            {
                "size": {"width": 1080, "height": 1440},
                "safe_area": {"top": 0, "right": 0, "bottom": 0, "left": 0},
                "text": [],
                "template_fields": fields,
                "source_asset_ids": [asset_id],
                "mode": "template",
                "risks": [],
                "artifact_version_id": "e2e-approved-content",
                "evidence_ids": [],
            },
            {},
        )["visual_plan"]
        context = SimpleNamespace(
            uid=uid,
            _content_node_output_contract="CoverJobSubmissionResultV1",
            _content_node_result_collector=SimpleNamespace(
                domain_context=SimpleNamespace(visual_plan_hash=plan["plan_hash"], allowed_asset_ids={asset_id})
            ),
            _content_node_input=SimpleNamespace(task_id=task_id, parent_run_id=f"e2e-{uuid.uuid4().hex}"),
            _content_node_governance={"locked_values": {"visual_plan_hash": plan["plan_hash"], "visual_plan": plan}},
        )
        result = await create_content_cover_job.coroutine(task_id=task_id, runtime=SimpleNamespace(context=context))
        job_id = result["cover_job_id"]
        duplicate = await create_content_cover_job.coroutine(task_id=task_id, runtime=SimpleNamespace(context=context))
        assert duplicate["cover_job_id"] == job_id
        for _ in range(60):
            response = await test_client.get(f"/api/content/covers/jobs/{job_id}", headers=headers)
            assert response.status_code == 200, response.text
            job = response.json()["job"]
            if job["status"] in {"succeeded", "failed", "cancelled"}:
                break
            await asyncio.sleep(1)
        assert job["status"] == "succeeded", job
        async with pg_manager.get_async_session_context() as db:
            persisted = await db.get(ContentCoverJob, job_id)
            assert persisted.request_json["title"] == fields["主标题"]
            assert all(persisted.request_json["fields"][key] == value for key, value in fields.items())
            design = persisted.result_json["hycanvas_design_snapshot"]
            assert design["title"] == fields["主标题"]
        response = await test_client.get(job["result_assets"][0]["file_url"], headers=headers)
        assert response.status_code == 200, response.text
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.size == (1080, 1440)
            assert max(ImageStat.Stat(image.convert("RGB")).stddev) > 1
        assert plan["text"] == []
        assert plan["template_fields"] == fields
    finally:
        async with pg_manager.get_async_session_context() as db:
            # HyCanvas API Key 不提供删除设计权限，保留设计供成图检查。
            assets = (await db.scalars(select(ContentCoverAsset).where(ContentCoverAsset.owner_uid == uid))).all()
            for asset in assets:
                await get_minio_client().adelete_file(asset.bucket_name, asset.object_name)
            await db.execute(delete(ContentCoverJob).where(ContentCoverJob.owner_uid == uid))
            await db.execute(delete(ContentCoverAsset).where(ContentCoverAsset.owner_uid == uid))
            await db.execute(delete(ContentTask).where(ContentTask.id == task_id))
        await pg_manager.close()
