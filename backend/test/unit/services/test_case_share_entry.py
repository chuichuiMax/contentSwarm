import asyncio
import io
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image

from yuxi.services import case_share_entry_service as service


class EntryRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}
        self.locks = {}

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl

    async def delete(self, key):
        self.ttls.pop(key, None)
        return int(self.values.pop(key, None) is not None)

    @asynccontextmanager
    async def lock(self, key, **kwargs):
        async with self.locks.setdefault(key, asyncio.Lock()):
            yield


@pytest.fixture
def entry(monkeypatch):
    redis = EntryRedis()
    share = SimpleNamespace(id="snapshot-a", token="a" * 64, title="案例<&", mp_entry_code="c" * 24)
    repo = SimpleNamespace(
        share_by_token=AsyncMock(return_value=share),
        share_by_entry_code=AsyncMock(return_value=share),
        ensure_share_entry_code=AsyncMock(return_value="d" * 24),
    )
    monkeypatch.setattr(service, "MaterialLibraryRepository", lambda db: repo)
    monkeypatch.setattr(service, "_wechat_credentials", lambda: ("wx-test", "test-secret"))
    monkeypatch.setattr(service, "_wechat_access_token", AsyncMock(return_value="test-access-token"))
    monkeypatch.setattr(service, "_invalidate_wechat_access_token", AsyncMock())
    monkeypatch.setattr(service, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setenv("WECHAT_MP_ENTRY_ENV_VERSION", "release")
    stream = io.BytesIO()
    Image.new("RGB", (32, 32), "green").save(stream, "PNG")
    return SimpleNamespace(
        redis=redis, share=share, repo=repo, db=SimpleNamespace(commit=AsyncMock()), png=stream.getvalue()
    )


@pytest.mark.asyncio
async def test_qrcode_uses_short_code_and_fixed_case_page_and_concurrent_cache(entry, httpx_mock):
    def respond(request):
        payload = json.loads(request.content)
        assert payload == {
            "scene": "c" * 24,
            "page": "pages/materials/shared-case",
            "env_version": "release",
            "width": 430,
            "check_path": True,
        }
        return httpx.Response(200, content=entry.png, headers={"Content-Type": "image/png"})

    httpx_mock.add_callback(respond)
    first, second = await asyncio.gather(
        *[service.wechat_entry_asset(entry.db, entry.share.token, "qrcode") for _ in range(2)]
    )
    assert first == second == (entry.png, "image/png")
    assert len(httpx_mock.get_requests()) == 1
    assert entry.share.token not in json.dumps(json.loads(httpx_mock.get_requests()[0].content))


@pytest.mark.asyncio
async def test_url_link_query_env_and_expiry_cache(entry, httpx_mock, monkeypatch):
    monkeypatch.setenv("WECHAT_MP_ENTRY_ENV_VERSION", "trial")
    httpx_mock.add_response(json={"url_link": "https://wxmpurl.cn/test-entry"})
    for _ in range(2):
        assert await service.wechat_entry_asset(entry.db, entry.share.token, "link") == "https://wxmpurl.cn/test-entry"
    payload = json.loads(httpx_mock.get_requests()[0].content)
    assert payload["query"] == "entryCode=" + "c" * 24 + "&booking=1"
    assert payload["env_version"] == "trial" and payload["expire_interval"] == 7
    assert list(entry.redis.ttls.values()) == [6 * 86400]


@pytest.mark.asyncio
async def test_case_link_stays_separate_from_booking_and_renews_after_cache_expiry(entry, httpx_mock):
    httpx_mock.add_response(json={"url_link": "https://wxmpurl.cn/case-first"})
    httpx_mock.add_response(json={"url_link": "https://wxmpurl.cn/booking"})
    httpx_mock.add_response(json={"url_link": "https://wxmpurl.cn/case-renewed"})
    assert (
        await service.wechat_entry_asset(entry.db, entry.share.token, "link", booking=False)
        == "https://wxmpurl.cn/case-first"
    )
    assert await service.wechat_entry_asset(entry.db, entry.share.token, "link") == "https://wxmpurl.cn/booking"
    assert (
        await service.wechat_entry_asset(entry.db, entry.share.token, "link", booking=False)
        == "https://wxmpurl.cn/case-first"
    )
    first, booking = [json.loads(request.content) for request in httpx_mock.get_requests()]
    assert first["query"] == "entryCode=" + "c" * 24 + "&booking=0"
    assert booking["query"].endswith("booking=1")
    entry.redis.values.pop(next(key for key in entry.redis.values if key.endswith(":case")))
    assert (
        await service.wechat_entry_asset(entry.db, entry.share.token, "link", booking=False)
        == "https://wxmpurl.cn/case-renewed"
    )
    assert len(httpx_mock.get_requests()) == 3


@pytest.mark.asyncio
async def test_invalid_token_refresh_once_and_failed_generation_is_retryable(entry, httpx_mock):
    httpx_mock.add_response(json={"errcode": 40001, "errmsg": "expired"})
    httpx_mock.add_response(json={"errcode": 45009, "errmsg": "quota"})
    httpx_mock.add_response(json={"url_link": "https://wxaurl.cn/retry"})
    with pytest.raises(HTTPException) as error:
        await service.wechat_entry_asset(entry.db, entry.share.token, "link")
    assert error.value.status_code == 503 and not entry.redis.values
    service._invalidate_wechat_access_token.assert_awaited_once()
    assert await service.wechat_entry_asset(entry.db, entry.share.token, "link") == "https://wxaurl.cn/retry"


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["not-json", '{"url_link":"https://attacker.example/"}', '{"errcode":0}'])
async def test_bad_wechat_response_never_becomes_redirect_or_cached_success(entry, httpx_mock, response):
    httpx_mock.add_response(text=response)
    with pytest.raises(HTTPException):
        await service.wechat_entry_asset(entry.db, entry.share.token, "link")
    assert not entry.redis.values


@pytest.mark.asyncio
async def test_missing_credentials_and_unknown_entry_are_explicit_errors(entry, monkeypatch):
    monkeypatch.setattr(service, "_wechat_credentials", lambda: ("", ""))
    with pytest.raises(HTTPException) as error:
        await service.wechat_entry_asset(entry.db, entry.share.token, "qrcode")
    assert error.value.status_code == 503
    entry.repo.share_by_entry_code.return_value = None
    for code, status in [("bad-code", 422), ("e" * 24, 404)]:
        with pytest.raises(HTTPException) as error:
            await service.resolve_entry(entry.db, code)
        assert error.value.status_code == status


@pytest.mark.asyncio
async def test_legacy_short_code_allocation_and_resolution_keep_original_share(entry):
    entry.share.mp_entry_code = None
    assert await service.ensure_entry_code(entry.db, entry.share) == "d" * 24
    entry.db.commit.assert_awaited_once()
    assert await service.resolve_entry(entry.db, "d" * 24) == {"share_id": entry.share.token, "booking": True}


def test_booking_page_uses_proxy_prefix_escapes_title_and_has_explicit_image_failure(entry):
    page = service.render_booking_entry(entry.share, "https://cases.test/boyun")
    assert "案例&lt;&amp;" in page and "案例<&" not in page
    assert f"/boyun/api/material-library/shares/{entry.share.token}/mp-code" in page
    assert f"/boyun/api/material-library/shares/{entry.share.token}/open-mp" in page
    assert "image.onerror" in page and "重新加载小程序码" in page
    assert "授权手机号" in page and "localStorage" not in page
