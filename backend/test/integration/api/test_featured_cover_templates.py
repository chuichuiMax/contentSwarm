"""精选封面专区模板进入 contentSwarm 封面模板列表的端到端验证。

HyCanvas 的 API Key 只允许读/生成路径（上传素材与保存模板是会话功能），
因此测试先走集成票据换取浏览器同款 cookie 会话，再走与前端批量上传同一
契约：上传图片素材 → 保存公开的 1080x1440 单图铺满模板（精选封面 tag）→
contentSwarm 封面模板列表可见。
"""

from __future__ import annotations

import base64
import io
import os
import uuid

import httpx
import pytest
from PIL import Image

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _hycanvas_config() -> tuple[str, str, str] | None:
    base_url = (os.getenv("HYCANVAS_BASE_URL") or "").strip()
    api_key = (os.getenv("HYCANVAS_API_KEY") or "").strip()
    workspace_id = (os.getenv("HYCANVAS_WORKSPACE_ID") or "").strip()
    if not all((base_url, api_key, workspace_id)):
        return None
    return base_url.rstrip("/"), api_key, workspace_id


def _cover_png(width: int = 270, height: int = 360) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), "#3b5bdb").save(output, format="PNG")
    return output.getvalue()


def _cover_design_file(title: str, asset_url: str, mime: str, width: int, height: int) -> dict:
    """与前端 featuredCovers.buildCoverDesign 同构的 1080x1440 单图铺满模板文件。"""
    asset_id = uuid.uuid4().hex
    return {
        "format": "hycanvas.design",
        "schemaVersion": 24,
        "id": uuid.uuid4().hex,
        "title": title,
        "unit": "px",
        "dpi": 96,
        "pages": [
            {
                "id": uuid.uuid4().hex,
                "name": "Page 1",
                "width": 1080,
                "height": 1440,
                "background": {"type": "solid", "color": {"srgb": {"r": 1, "g": 1, "b": 1, "a": 1}}},
                "children": [
                    {
                        "id": uuid.uuid4().hex,
                        "type": "image",
                        "transform": {"x": 0, "y": 0, "scaleX": 1, "scaleY": 1, "rotation": 0},
                        "size": {"width": 1080, "height": 1440},
                        "opacity": 1,
                        "blendMode": "normal",
                        "source": {"assetId": asset_id, "naturalWidth": width, "naturalHeight": height},
                        "fit": "cover",
                    }
                ],
            }
        ],
        "assets": [
            {"id": asset_id, "kind": "image", "url": asset_url, "mime": mime, "width": width, "height": height}
        ],
        "fonts": [],
        "meta": {},
    }


async def _integration_session(client: httpx.AsyncClient, workspace_id: str) -> None:
    """用集成票据换取浏览器会话 cookie（写入 client 的 cookie jar）。"""
    issued = await client.post(f"/api/v1/auth/integration-ticket/workspace/{workspace_id}")
    assert issued.status_code == 201, issued.text
    ticket = issued.json()["ticket"]
    redeemed = await client.get(
        "/api/v1/auth/integration",
        params={"ticket": ticket, "workspaceId": workspace_id, "next": "/dashboard/"},
    )
    assert redeemed.status_code == 302, redeemed.text


async def test_featured_cover_template_enters_cover_catalog(test_client, admin_headers):
    config = _hycanvas_config()
    if config is None:
        pytest.skip("HyCanvas 未配置")
    base_url, api_key, workspace_id = config
    title = f"pytest-精选封面-{uuid.uuid4().hex[:8]}"

    asset_id = None
    template_id = None
    async with httpx.AsyncClient(base_url=base_url, timeout=30, follow_redirects=False) as hycanvas:
        hycanvas.headers["Authorization"] = f"Bearer {api_key}"
        try:
            await _integration_session(hycanvas, workspace_id)
            # 会话已建立；后续走浏览器同款 cookie，API key 头不再参与。
            hycanvas.headers.pop("Authorization")

            uploaded = await hycanvas.post(
                f"/api/v1/workspaces/{workspace_id}/assets",
                json={"filename": f"{title}.png", "dataBase64": base64.b64encode(_cover_png()).decode()},
            )
            assert uploaded.status_code in (200, 201), uploaded.text
            asset = uploaded.json()
            asset_id = asset["id"]

            saved = await hycanvas.post(
                "/api/v1/templates",
                json={
                    "workspaceId": workspace_id,
                    "file": _cover_design_file(title, asset["url"], asset.get("mimeType") or "image/png", 270, 360),
                    "title": title,
                    "category": "精选封面",
                    "tags": ["精选封面"],
                    "visibility": "public",
                },
            )
            assert saved.status_code in (200, 201), saved.text
            template_id = saved.json()["id"]

            catalog = await test_client.get("/api/content/covers/hycanvas/templates", headers=admin_headers)
            assert catalog.status_code == 200, catalog.text
            match = [item for item in catalog.json()["templates"] if item["id"] == template_id]
            assert match, "精选封面模板未进入 contentSwarm 封面模板列表"
            assert match[0]["title"] == title
            assert match[0]["zone"] == "featured"
        finally:
            if template_id:
                await hycanvas.delete(f"/api/v1/templates/{template_id}")
            if asset_id:
                await hycanvas.delete(f"/api/v1/assets/{asset_id}")


async def test_featured_cover_template_compile_brief(test_client, admin_headers):
    """精选封面进入业务简报：与内置封面互斥，编译后冻结到视觉快照。"""
    config = _hycanvas_config()
    if config is None:
        pytest.skip("HyCanvas 未配置")
    base_url, api_key, workspace_id = config
    title = f"pytest-精选封面-{uuid.uuid4().hex[:8]}"
    builtin_template_id = "01c7f0bc-3ce5-431b-82e5-7390e9bc246e"

    material_response = await test_client.post(
        "/api/material-library/images/import",
        headers=admin_headers,
        data={"category": "product"},
        files=[("files", (f"featured-{uuid.uuid4().hex}.png", _cover_png(), "image/png"))],
    )
    assert material_response.status_code == 201, material_response.text
    material = material_response.json()["items"][0]

    bootstrap_response = await test_client.get("/api/content/bootstrap", headers=admin_headers)
    assert bootstrap_response.status_code == 200, bootstrap_response.text
    industry = next(item for item in bootstrap_response.json()["industry_templates"] if item["slug"] == "decoration")
    create_response = await test_client.post(
        "/api/content/tasks",
        headers=admin_headers,
        json={
            "industry_template_id": industry["id"],
            "mode": "quick",
            "content_goal": industry["default_goal"],
            "name": f"pytest_featured_brief_{uuid.uuid4().hex[:8]}",
        },
    )
    assert create_response.status_code == 200, create_response.text
    task_id = create_response.json()["task"]["id"]

    form_values = {
        "brand_name": "精选封面测试品牌",
        "process": ["水电定位", "隐蔽验收"],
        "result": "施工节点可追溯",
        "advantage": ["标准工序留档"],
        "pain": ["隐蔽工程难追溯"],
        "project_type": "三室两厅",
        "craft_and_materials": "水电施工与隐蔽验收",
    }

    asset_id = None
    template_id = None
    async with httpx.AsyncClient(base_url=base_url, timeout=30, follow_redirects=False) as hycanvas:
        hycanvas.headers["Authorization"] = f"Bearer {api_key}"
        try:
            await _integration_session(hycanvas, workspace_id)
            hycanvas.headers.pop("Authorization")

            uploaded = await hycanvas.post(
                f"/api/v1/workspaces/{workspace_id}/assets",
                json={"filename": f"{title}.png", "dataBase64": base64.b64encode(_cover_png()).decode()},
            )
            assert uploaded.status_code in (200, 201), uploaded.text
            asset = uploaded.json()
            asset_id = asset["id"]

            saved = await hycanvas.post(
                "/api/v1/templates",
                json={
                    "workspaceId": workspace_id,
                    "file": _cover_design_file(title, asset["url"], asset.get("mimeType") or "image/png", 270, 360),
                    "title": title,
                    "category": "精选封面",
                    "tags": ["精选封面"],
                    "visibility": "public",
                },
            )
            assert saved.status_code in (200, 201), saved.text
            template_id = saved.json()["id"]

            conflict_response = await test_client.post(
                f"/api/content/tasks/{task_id}/compile-brief",
                headers=admin_headers,
                json={
                    "brief": {
                        "visual_material": {
                            "image_item_id": material["id"],
                            "hycanvas_template_id": builtin_template_id,
                            "featured_cover_template_id": template_id,
                        },
                        "audience": ["准备装修的业主"],
                        "form_values": form_values,
                    }
                },
            )
            assert conflict_response.status_code == 422, conflict_response.text
            assert conflict_response.json()["detail"]["error"]["code"] == "CONTENT_COVER_TEMPLATE_CONFLICT"

            compile_response = await test_client.post(
                f"/api/content/tasks/{task_id}/compile-brief",
                headers=admin_headers,
                json={
                    "brief": {
                        "visual_material": {
                            "image_item_id": material["id"],
                            "featured_cover_template_id": template_id,
                        },
                        "audience": ["准备装修的业主"],
                        "form_values": form_values,
                    }
                },
            )
            assert compile_response.status_code == 200, compile_response.text
            visual = compile_response.json()["task"]["runtime_config_snapshot"]["visual_material"]
            assert visual["featured_cover_template_id"] == template_id
            assert visual["featured_cover_template_title"] == title
            assert visual.get("hycanvas_template_id") is None
        finally:
            if template_id:
                await hycanvas.delete(f"/api/v1/templates/{template_id}")
            if asset_id:
                await hycanvas.delete(f"/api/v1/assets/{asset_id}")
            await test_client.delete(f"/api/content/tasks/{task_id}", headers=admin_headers)
            await test_client.delete(f"/api/material-library/items/{material['id']}", headers=admin_headers)
