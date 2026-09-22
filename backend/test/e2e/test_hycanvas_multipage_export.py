"""Export an existing multi-page design without changing its saved contents."""

import io
import os
from urllib.parse import quote

import httpx
import pytest
from PIL import Image, ImageStat


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_multipage_design_exports_first_and_last_page():
    design_id = os.getenv("HYCANVAS_E2E_DESIGN_ID")
    if not design_id:
        pytest.skip("Set HYCANVAS_E2E_DESIGN_ID to a multi-page test design")
    path = f"/api/v1/designs/{quote(design_id, safe='')}"
    async with httpx.AsyncClient(
        base_url=os.environ["HYCANVAS_BASE_URL"],
        headers={"Authorization": f"Bearer {os.environ['HYCANVAS_API_KEY']}"},
        timeout=30,
    ) as client:
        response = await client.get(f"{path}/file")
        response.raise_for_status()
        pages = response.json()["pages"]
        assert len(pages) > 1, "The configured design must contain multiple pages"

        for index in (0, len(pages) - 1):
            response = await client.get(f"{path}/render.png", params={"page": index})
            response.raise_for_status()
            assert response.headers["content-type"].startswith("image/png")
            with Image.open(io.BytesIO(response.content)) as image:
                assert image.size == (int(pages[index]["width"]), int(pages[index]["height"]))
                assert max(ImageStat.Stat(image.convert("RGB")).stddev) > 1, f"Page {index} is blank"
