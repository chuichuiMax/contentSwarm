from unittest.mock import AsyncMock
from urllib.parse import unquote

import pytest

from server.routers import mp_router


@pytest.mark.asyncio
@pytest.mark.parametrize("file_name", ["rough-test.png", "企业毛坯图库_上传测试.png", '客厅 "原图".webp'])
async def test_gallery_file_response_preserves_unicode_filename_without_header_encoding_error(monkeypatch, file_name):
    monkeypatch.setattr(
        mp_router, "read_mp_gallery_item_file", AsyncMock(return_value=(b"image-data", "image/webp", file_name))
    )
    response = await mp_router.mp_gallery_item_file("sample", ctx=object(), db=object())
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("inline; filename*=UTF-8''")
    assert unquote(disposition.split("UTF-8''", 1)[1]) == file_name
    assert response.status_code == 200 and response.body == b"image-data"
    assert response.headers["content-type"] == "image/webp"
