import json

import httpx
import pytest
from fastapi import HTTPException

from yuxi.content_cover.schemas import HyCanvasDesignCreate
from yuxi.services.hycanvas_service import HyCanvasClient
from yuxi.services import hycanvas_service


def test_from_env_requires_complete_configuration(monkeypatch):
    for name in (
        "HYCANVAS_BASE_URL",
        "HYCANVAS_DEV_PUBLIC_URL",
        "HYCANVAS_PUBLIC_URL",
        "HYCANVAS_API_KEY",
        "HYCANVAS_WORKSPACE_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(HTTPException) as exc:
        HyCanvasClient.from_env()

    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "hycanvas_not_configured"


def test_from_env_prefers_local_dev_public_url(monkeypatch):
    monkeypatch.setenv("HYCANVAS_BASE_URL", "http://hycanvas-app:8005")
    monkeypatch.setenv("HYCANVAS_PUBLIC_URL", "http://127.0.0.1:8005")
    monkeypatch.setenv("HYCANVAS_DEV_PUBLIC_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("HYCANVAS_API_KEY", "hyk_test")
    monkeypatch.setenv("HYCANVAS_WORKSPACE_ID", "workspace-1")

    assert HyCanvasClient.from_env().public_url == "http://127.0.0.1:3000"


@pytest.mark.asyncio
async def test_lists_three_by_four_fillable_templates_and_keeps_metadata():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer hyk_test"
        assert "q" not in request.url.params
        return httpx.Response(
            200,
            json=[
                {
                    "id": "xiaohongshu-checklist",
                    "title": "小红书干货清单",
                    "tags": ["小红书", "干货"],
                    "format": {"width": 1080, "height": 1440, "unit": "px"},
                    "fillableFields": [{"nodeId": "n1", "kind": "text", "label": "主标题"}],
                    "previewUrls": ["/template-previews/xiaohongshu-checklist-p0.png"],
                },
                {
                    "id": "8bc32ed9-f80a-47e2-8e2b-913e35c125c8",
                    "title": "鸿扬项目案例封面",
                    "tags": ["小红书", "团队模板"],
                    "format": {"width": 1080, "height": 1440, "unit": "px"},
                    "fillableFields": [],
                },
                {
                    "id": "landscape-template",
                    "title": "横版封面",
                    "format": {"width": 1200, "height": 628, "unit": "px"},
                    "fillableFields": [
                        {"nodeId": "title", "kind": "text", "label": "标题"}
                    ],
                },
                {"id": "generic-poster", "title": "普通海报"},
            ],
        )

    client = HyCanvasClient(
        base_url="http://hycanvas",
        public_url="http://canvas.example",
        api_key="hyk_test",
        workspace_id="ws-1",
        transport=httpx.MockTransport(handler),
    )

    result = await client.list_xiaohongshu_templates()

    assert result["total"] == 2
    assert result["templates"][0]["id"] == "xiaohongshu-checklist"
    assert result["templates"][1]["id"] == "8bc32ed9-f80a-47e2-8e2b-913e35c125c8"
    assert result["templates"][0]["fillable_fields"][0]["label"] == "主标题"
    assert result["templates"][0]["preview_urls"] == ["/hycanvas-template-previews/xiaohongshu-checklist-p0.png"]
    assert result["templates"][1]["preview_urls"] == [
        "/api/content/covers/hycanvas/templates/8bc32ed9-f80a-47e2-8e2b-913e35c125c8/render.png"
    ]


@pytest.mark.asyncio
async def test_renders_custom_template_preview_through_hycanvas():
    template_id = "8bc32ed9-f80a-47e2-8e2b-913e35c125c8"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/templates/{template_id}/render.png"
        assert request.headers["Authorization"] == "Bearer hyk_test"
        return httpx.Response(200, content=b"png", headers={"content-type": "image/png"})

    client = HyCanvasClient(
        base_url="http://hycanvas",
        public_url="http://canvas.example",
        api_key="hyk_test",
        workspace_id="ws-1",
        transport=httpx.MockTransport(handler),
    )

    content, content_type = await client.render_template_png(template_id)

    assert content == b"png"
    assert content_type == "image/png"


@pytest.mark.asyncio
async def test_renders_template_preview_with_selected_image_background():
    template_id = "8bc32ed9-f80a-47e2-8e2b-913e35c125c8"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/templates/{template_id}/preview.png"
        assert request.headers["Authorization"] == "Bearer hyk_test"
        assert json.loads(request.content) == {
            "backgroundImage": {
                "filename": "cover.png",
                "contentType": "image/png",
                "dataBase64": "cG5n",
            }
        }
        return httpx.Response(200, content=b"composite", headers={"content-type": "image/png"})

    client = HyCanvasClient(
        base_url="http://hycanvas",
        public_url="http://canvas.example",
        api_key="hyk_test",
        workspace_id="ws-1",
        transport=httpx.MockTransport(handler),
    )

    content, content_type = await client.render_template_with_background_png(
        template_id,
        (b"png", "image/png", "cover.png"),
    )

    assert content == b"composite"
    assert content_type == "image/png"


@pytest.mark.asyncio
async def test_creates_design_in_configured_workspace_and_returns_urls():
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/api/v1/templates/xiaohongshu-checklist/instantiate"
        assert body == {
            "workspaceId": "ws-1",
            "title": "装修清单",
            "fields": {"主标题": "装修前必看清单"},
            "images": {},
            "backgroundImage": None,
        }
        return httpx.Response(201, json={"designId": "design-1"})

    client = HyCanvasClient(
        base_url="http://hycanvas",
        public_url="http://canvas.example",
        api_key="hyk_test",
        workspace_id="ws-1",
        transport=httpx.MockTransport(handler),
    )

    result = await client.create_design(
        HyCanvasDesignCreate(
            artifact_id="artifact-1",
            template_id="xiaohongshu-checklist",
            title="装修清单",
            fields={"主标题": "装修前必看清单"},
        )
    )

    assert result == {
        "design_id": "design-1",
        "editor_url": "http://canvas.example/editor/?id=design-1",
        "render_url": "/api/content/covers/hycanvas/designs/design-1/render.png",
    }


@pytest.mark.asyncio
async def test_creates_design_from_custom_template_without_fillable_fields():
    template_id = "01c7f0bc-3ce5-431b-82e5-7390e9bc246e"

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == f"/api/v1/templates/{template_id}/instantiate"
        assert body["fields"] == {}
        assert body["backgroundImage"]["dataBase64"] == "cG5n"
        return httpx.Response(201, json={"designId": "custom-design"})

    client = HyCanvasClient(
        base_url="http://hycanvas",
        public_url="http://canvas.example",
        api_key="hyk_test",
        workspace_id="ws-1",
        transport=httpx.MockTransport(handler),
    )

    result = await client.create_design(
        HyCanvasDesignCreate(
            artifact_id="artifact-1",
            template_id=template_id,
            title="自定义模板封面",
            fields={},
        ),
        image=(b"png", "image/png", "product.png"),
    )

    assert result["design_id"] == "custom-design"


@pytest.mark.asyncio
async def test_create_design_sends_optional_main_image():
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["images"] == {}
        assert body["backgroundImage"] == {
            "filename": "product.png",
            "contentType": "image/png",
            "dataBase64": "cG5n",
        }
        return httpx.Response(201, json={"designId": "design-image"})

    client = HyCanvasClient(
        base_url="http://hycanvas",
        public_url="http://canvas.example",
        api_key="hyk_test",
        workspace_id="ws-1",
        transport=httpx.MockTransport(handler),
    )
    payload = HyCanvasDesignCreate(
        artifact_id="artifact-1",
        template_id="xiaohongshu-product-review",
        title="产品种草",
        fields={"产品标题": "真实测评"},
    )

    result = await client.create_design(payload, image=(b"png", "image/png", "product.png"))

    assert result["design_id"] == "design-image"


@pytest.mark.asyncio
async def test_create_design_omits_background_when_no_material_selected():
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["images"] == {}
        assert body["backgroundImage"] is None
        return httpx.Response(201, json={"designId": "design-text-only"})

    client = HyCanvasClient(
        base_url="http://hycanvas",
        public_url="http://canvas.example",
        api_key="hyk_test",
        workspace_id="ws-1",
        transport=httpx.MockTransport(handler),
    )
    payload = HyCanvasDesignCreate(
        artifact_id="artifact-1",
        template_id="xiaohongshu-emotion-quote",
        title="情绪语录",
        fields={"语录正文": "允许自己慢一点"},
    )

    result = await client.create_design(payload, image=None, image_field_label=None)

    assert result["design_id"] == "design-text-only"


@pytest.mark.asyncio
async def test_creates_design_bound_editor_session_url(monkeypatch):
    artifact = type(
        "Artifact",
        (),
        {"hycanvas_design_snapshot": {"design_id": "design-1"}},
    )()
    user = type("User", (), {"uid": "user-1"})()

    class FakeContentRepository:
        def __init__(self, db):
            del db

        async def get_artifact_for_user(self, artifact_id, current_user):
            assert artifact_id == "artifact-1" and current_user is user
            return artifact

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/auth/integration-ticket/design-1"
        assert request.headers["Authorization"] == "Bearer hyk_test"
        return httpx.Response(201, json={"ticket": "ticket-1"})

    monkeypatch.setattr(hycanvas_service, "ContentRepository", FakeContentRepository)
    client = HyCanvasClient(
        base_url="http://hycanvas",
        public_url="http://canvas.example",
        api_key="hyk_test",
        workspace_id="ws-1",
        transport=httpx.MockTransport(handler),
    )

    result = await client.create_editor_session(
        object(),
        user,
        "artifact-1",
        "design-1",
        "http://127.0.0.1:5173/content/task-1?resultDetail=1",
        "返回 ContentFlow",
    )

    assert result["editor_url"].startswith("http://canvas.example/api/v1/auth/integration?")
    assert "ticket=ticket-1" in result["editor_url"]
    assert "designId=design-1" in result["editor_url"]


@pytest.mark.asyncio
async def test_creates_workspace_bound_session_url():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/auth/integration-ticket/workspace/ws-1"
        assert request.headers["Authorization"] == "Bearer hyk_test"
        return httpx.Response(201, json={"ticket": "workspace-ticket"})

    client = HyCanvasClient(
        base_url="http://hycanvas",
        public_url="http://canvas.example",
        api_key="hyk_test",
        workspace_id="ws-1",
        transport=httpx.MockTransport(handler),
    )

    result = await client.create_workspace_session()

    assert result["editor_url"].startswith("http://canvas.example/api/v1/auth/integration?")
    assert "ticket=workspace-ticket" in result["editor_url"]
    assert "workspaceId=ws-1" in result["editor_url"]
    assert "next=%2Fdashboard%2F" in result["editor_url"]


@pytest.mark.asyncio
async def test_create_and_bind_persists_snapshot_and_cover(monkeypatch):
    artifact = type(
        "Artifact",
        (),
        {
            "id": "artifact-1",
            "task_id": "task-1",
            "current_version": 3,
            "to_dict": lambda self: {
                "id": self.id,
                "current_version": self.current_version,
                "hycanvas_design_snapshot": getattr(self, "hycanvas_design_snapshot", {}),
            },
        },
    )()
    asset = type("Asset", (), {"id": "cover-1"})()
    user = type("User", (), {"uid": "user-1"})()
    saved = {}

    class FakeContentRepository:
        def __init__(self, db):
            del db

        async def get_artifact_for_user(self, artifact_id, current_user, for_update=False):
            assert (artifact_id, current_user, for_update) == ("artifact-1", user, True)
            return artifact

    class FakeCoverRepository:
        def __init__(self, db):
            del db

        async def get_asset_for_user(self, asset_id, owner_uid):
            assert (asset_id, owner_uid) == ("cover-1", "user-1")
            return asset

        async def bind_hycanvas_design(self, **kwargs):
            saved.update(kwargs["snapshot"])
            artifact.hycanvas_design_snapshot = kwargs["snapshot"]
            artifact.current_version = 4
            return type("Version", (), {"version": 4})()

    async def fake_create_asset(db, current_user, upload, *, role, content_task_id):
        assert db is fake_db and current_user is user
        assert role == "source" and content_task_id == "task-1"
        assert await upload.read() == b"png"
        return {"asset": {"id": "cover-1"}}

    async def fake_get_file(db, current_user, asset_id):
        assert db is fake_db and current_user is user and asset_id == "source-1"
        return b"source", "image/png", "source.png"

    monkeypatch.setattr(hycanvas_service, "ContentRepository", FakeContentRepository)
    monkeypatch.setattr(hycanvas_service, "ContentCoverRepository", FakeCoverRepository)
    monkeypatch.setattr(hycanvas_service, "create_cover_asset", fake_create_asset)
    monkeypatch.setattr(hycanvas_service, "get_cover_asset_file", fake_get_file)

    client = HyCanvasClient(
        base_url="http://hycanvas",
        public_url="http://canvas.example",
        api_key="hyk_test",
        workspace_id="ws-1",
    )

    async def fake_create_design(payload, *, image=None):
        assert image == (b"source", "image/png", "source.png")
        return {"design_id": "design-1", "editor_url": "editor", "render_url": "render"}

    async def fake_render(design_id):
        assert design_id == "design-1"
        return b"png", "image/png"

    monkeypatch.setattr(client, "create_design", fake_create_design)
    monkeypatch.setattr(client, "render_png", fake_render)
    fake_db = type("DB", (), {"commit": lambda self: _async_none()})()
    payload = HyCanvasDesignCreate(
        artifact_id="artifact-1",
        template_id="xiaohongshu-product-review",
        title="种草封面",
        fields={"产品标题": "真实测评"},
        image_asset_id="source-1",
    )

    result = await client.create_and_bind(fake_db, user, payload)

    assert saved["design_id"] == "design-1"
    assert saved["source_image_asset_id"] == "source-1"
    assert saved["cover_asset_id"] == "cover-1"
    assert result["artifact_version"] == 4
    assert result["artifact"]["hycanvas_design_snapshot"]["template_id"] == "xiaohongshu-product-review"


@pytest.mark.asyncio
async def test_sync_rejects_design_not_bound_to_artifact(monkeypatch):
    artifact = type("Artifact", (), {"hycanvas_design_snapshot": {"design_id": "design-bound"}})()
    user = type("User", (), {"uid": "user-1"})()

    class FakeContentRepository:
        def __init__(self, db):
            del db

        async def get_artifact_for_user(self, artifact_id, current_user, for_update=False):
            assert (artifact_id, current_user, for_update) == ("artifact-1", user, True)
            return artifact

    monkeypatch.setattr(hycanvas_service, "ContentRepository", FakeContentRepository)
    client = HyCanvasClient(
        base_url="http://hycanvas",
        public_url="http://canvas.example",
        api_key="hyk_test",
        workspace_id="ws-1",
    )

    with pytest.raises(HTTPException) as exc:
        await client.sync_and_bind(object(), user, "artifact-1", "design-other")

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "hycanvas_design_mismatch"


async def _async_none():
    return None
