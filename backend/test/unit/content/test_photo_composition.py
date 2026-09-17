import json

import httpx
import pytest
from pydantic import ValidationError

from yuxi.content_cover.photo_composition import PHOTO_LAYOUTS, PhotoComposition
from yuxi.content_cover.schemas import HyCanvasDesignCreate
from yuxi.services.hycanvas_service import HyCanvasClient


@pytest.mark.parametrize("layout", PHOTO_LAYOUTS)
def test_layout_has_full_nonoverlapping_cells(layout):
    coords = [
        (r, c)
        for cell in layout["cells"]
        for r in range(cell["row"], cell["row"] + cell["rowSpan"])
        for c in range(cell["col"], cell["col"] + cell["colSpan"])
    ]
    assert len(set(coords)) == len(coords) == layout["rows"] * layout["cols"]
    composition = PhotoComposition(
        layout_id=layout["id"], slots=[{"image_item_id": str(i)} for i in range(len(layout["cells"]))]
    )
    composition.require_complete("0")


def test_incomplete_draft_allowed_but_cannot_generate():
    value = PhotoComposition(layout_id="grid-2", slots=[{"image_item_id": "main"}, {}])
    with pytest.raises(ValueError, match="填满"):
        value.require_complete("main")
    with pytest.raises(ValidationError):
        PhotoComposition(layout_id="grid-9", slots=[{}, {}])
    with pytest.raises(ValidationError):
        PhotoComposition(layout_id="grid-2", slots=[{"focal_x": 2}, {}])


@pytest.mark.asyncio
async def test_template_overlay_preview_and_generation_use_original_cover_image():
    bodies = []

    async def handler(request):
        bodies.append(json.loads(request.content))
        if request.url.path.endswith("preview.png"):
            return httpx.Response(200, content=b"png")
        return httpx.Response(201, json={"designId": "new-design"})

    client = HyCanvasClient(
        base_url="http://canvas",
        public_url="http://canvas",
        api_key="test",
        workspace_id="workspace",
        transport=httpx.MockTransport(handler),
    )
    image = (b"cover", "image/png", "cover.png")
    await client.render_template_with_background_png("template", image)
    await client.create_design(
        HyCanvasDesignCreate(artifact_id="task", template_id="xiaohongshu-grid", title="案例", fields={}),
        image=image,
    )
    assert all("photoComposition" not in body for body in bodies)
    assert all(body["backgroundImage"]["filename"] == "cover.png" for body in bodies)
    assert all(body["backgroundImage"]["dataBase64"] == "Y292ZXI=" for body in bodies)
