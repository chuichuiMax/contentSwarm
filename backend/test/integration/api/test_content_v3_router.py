from __future__ import annotations

import uuid
import io

import pytest
from PIL import Image


pytestmark = pytest.mark.asyncio


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (48, 36), "#315EFB").save(output, format="PNG")
    return output.getvalue()


async def test_v3_strategy_preview_permissions_and_removed_v2_route(test_client, admin_headers, standard_user):
    hycanvas_template_id = "01c7f0bc-3ce5-431b-82e5-7390e9bc246e"
    material_response = await test_client.post(
        "/api/material-library/images/import",
        headers=admin_headers,
        data={"category": "product"},
        files=[("files", (f"content-v3-{uuid.uuid4().hex}.png", _png(), "image/png"))],
    )
    assert material_response.status_code == 201, material_response.text
    material = material_response.json()["items"][0]
    bootstrap_response = await test_client.get("/api/content/bootstrap", headers=admin_headers)
    assert bootstrap_response.status_code == 200, bootstrap_response.text
    template = next(item for item in bootstrap_response.json()["industry_templates"] if item["slug"] == "decoration")
    create_response = await test_client.post(
        "/api/content/tasks",
        headers=admin_headers,
        json={
            "industry_template_id": template["id"],
            "mode": "quick",
            "content_goal": "brand",
            "content_type_code": "CT05",
            "name": f"pytest_content_v3_{uuid.uuid4().hex[:8]}",
        },
    )
    assert create_response.status_code == 200, create_response.text
    task_id = create_response.json()["task"]["id"]
    assert create_response.json()["task"]["rule_version_id"] == "content-rules-platform-v3"
    assert create_response.json()["task"]["runtime_config_snapshot"]["schema_version"] == 3
    assert create_response.json()["task"]["latest_run_id"] is None

    try:
        compile_response = await test_client.post(
            f"/api/content/tasks/{task_id}/compile-brief",
            headers=admin_headers,
            json={
                "brief": {
                    "visual_material": {
                        "image_item_id": material["id"],
                        "hycanvas_template_id": hycanvas_template_id,
                    },
                    "audience": ["准备装修的业主"],
                    "form_values": {
                        "brand_name": "V3 测试品牌",
                        "process": ["水电定位", "隐蔽验收"],
                        "result": "施工节点可追溯",
                        "advantage": ["标准工序留档"],
                        "pain": ["隐蔽工程难追溯"],
                        "project_type": "三室两厅",
                        "craft_and_materials": "水电施工与隐蔽验收",
                    },
                }
            },
        )
        assert compile_response.status_code == 200, compile_response.text
        assert (
            compile_response.json()["task"]["runtime_config_snapshot"]["visual_material"]["hycanvas_template_id"]
            == hycanvas_template_id
        )

        denied_response = await test_client.post(
            f"/api/content/tasks/{task_id}/strategy/recommend-v3",
            headers=standard_user["headers"],
            json={"content_direction_code": "CT05"},
        )
        assert denied_response.status_code == 404
        assert denied_response.json()["detail"]["error"]["code"] == "CONTENT_TASK_NOT_FOUND"

        preview_response = await test_client.post(
            f"/api/content/tasks/{task_id}/strategy/recommend-v3",
            headers=admin_headers,
            json={"content_direction_code": "CT05"},
        )
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()
        assert preview["preview"] is True
        assert preview["creates_run"] is False
        assert preview["rule_version_id"] == "content-rules-platform-v3"
        assert preview["industry_pack_version_id"] == "industry-pack-decoration-v3"
        assert preview["decision"]["status"] == "matched"
        assert len(preview["decision"]["eligible_groups"]) == 4

        task_response = await test_client.get(f"/api/content/tasks/{task_id}", headers=admin_headers)
        assert task_response.status_code == 200, task_response.text
        assert task_response.json()["task"]["latest_run_id"] is None
        assert task_response.json()["task"]["strategy"] == {}

        removed_response = await test_client.post(
            f"/api/content/tasks/{task_id}/strategy/recommend-v2",
            headers=admin_headers,
            json={"random_seed": 7, "limit": 5},
        )
        assert removed_response.status_code == 404
    finally:
        delete_response = await test_client.delete(f"/api/content/tasks/{task_id}", headers=admin_headers)
        assert delete_response.status_code == 200, delete_response.text
        material_delete = await test_client.delete(
            f"/api/material-library/items/{material['id']}", headers=admin_headers
        )
        assert material_delete.status_code == 200, material_delete.text
