import httpx
import pytest

from yuxi.services.hycanvas_service import HyCanvasClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("template_id", "path"),
    [
        (
            "system-cover-personalized-template-1-top-left",
            "/api/v1/templates/system-cover-personalized-template-1-top-left/render.png",
        ),
        (
            "8bc32ed9-f80a-47e2-8e2b-913e35c125c8",
            "/api/v1/templates/8bc32ed9-f80a-47e2-8e2b-913e35c125c8/render.png",
        ),
        ("xiaohongshu-checklist", "/template-previews/xiaohongshu-checklist-p0.png"),
    ],
)
async def test_template_preview_uses_existing_image_endpoint(template_id, path):
    async def handler(request):
        assert request.url.path == path
        assert request.headers["Authorization"] == "Bearer hyk_test"
        return httpx.Response(200, content=b"preview-png", headers={"content-type": "image/png"})

    client = HyCanvasClient(
        base_url="http://hycanvas",
        public_url="http://canvas.example",
        api_key="hyk_test",
        workspace_id="ws-1",
        transport=httpx.MockTransport(handler),
    )

    content, content_type = await client.fetch_template_preview(template_id)

    assert content == b"preview-png"
    assert content_type == "image/png"
