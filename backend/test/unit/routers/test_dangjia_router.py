from __future__ import annotations

import pytest

from server.routers import dangjia_router


@pytest.mark.asyncio
async def test_media_endpoint_returns_image_with_immutable_cache(monkeypatch):
    async def read_media(asset_id: str):
        assert asset_id == "cca_0123456789abcdef0123456789abcdef"
        return b"png-bytes", "image/png", "1.png"

    monkeypatch.setattr(dangjia_router, "read_dangjia_cover_media", read_media)

    response = await dangjia_router.get_content_media_endpoint(
        "cca_0123456789abcdef0123456789abcdef",
        "1.png",
    )

    assert response.status_code == 200
    assert response.body == b"png-bytes"
    assert response.media_type == "image/png"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
