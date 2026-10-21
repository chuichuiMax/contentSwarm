import asyncio
import io
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import httpx
from fastapi import HTTPException
from PIL import Image
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from yuxi.services import case_visit_service as service


def avatar_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (640, 480), "blue").save(stream, "PNG")
    return stream.getvalue()


class FakeRedis:
    def __init__(self, session=None):
        self.value = json.dumps(session) if session else None
        self.mutex = asyncio.Lock()

    async def get(self, _key):
        return self.value

    async def setex(self, _key, _ttl, value):
        self.value = value

    @asynccontextmanager
    async def lock(self, *_args, **_kwargs):
        async with self.mutex:
            yield


@pytest.fixture
def booking(monkeypatch):
    redis = FakeRedis({"appid": "wx_test", "openid": "verified-openid", "share_token": "share-a"})
    share = SimpleNamespace(id="snapshot-a", sharer_uid="employee-a", sharer_department_id=7)
    repo = SimpleNamespace(
        share=AsyncMock(return_value=share),
        upsert=AsyncMock(return_value="lead-a"),
        submission=AsyncMock(return_value=None),
        record_submission=Mock(),
    )
    phone = AsyncMock(return_value="19900000001")
    monkeypatch.setattr(service, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(service, "CaseVisitRepository", lambda _db: repo)
    monkeypatch.setattr(service, "_wechat_credentials", lambda: ("wx_test", "test-secret"))
    monkeypatch.setattr(service, "_wechat_phone_by_code", phone)
    return SimpleNamespace(redis=redis, repo=repo, phone=phone, db=SimpleNamespace(commit=AsyncMock()))


def test_avatar_is_validated_resized_and_persistable():
    result = service.encode_booking_avatar(avatar_bytes())
    with Image.open(io.BytesIO(result)) as image:
        assert image.format == "WEBP"
        assert image.size == (256, 192)


@pytest.mark.parametrize("data", [b"not-an-image", b"", b"a" * (service.MAX_AVATAR_BYTES + 1)])
def test_invalid_avatar_rejected(data):
    with pytest.raises(HTTPException) as error:
        service.encode_booking_avatar(data)
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_booking_uses_server_identity_phone_and_share_attribution(booking):
    result = await service.submit_booking(booking.db, "share-a", "session-a", " 客户甲 ", "code-a", avatar_bytes())
    assert result == {"success": True, "id": "lead-a"}
    values = booking.repo.upsert.call_args.args[0]
    assert values["openid"] == "verified-openid"
    assert values["phone"] == "19900000001"
    assert values["nickname"] == "客户甲"
    assert values["share_id"] == "snapshot-a"
    assert values["sharer_uid"] == "employee-a" and values["department_id"] == 7
    assert values["avatar_data"][8:12] == b"WEBP"
    booking.db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_phone_only_booking_does_not_fabricate_profile_fields(booking):
    result = await service.submit_booking(booking.db, "share-a", "session-a", None, "code-a", None)
    assert result == {"success": True, "id": "lead-a"}
    values = booking.repo.upsert.call_args.args[0]
    assert values["nickname"] is None and values["avatar_data"] is None
    assert values["openid"] == "verified-openid" and values["phone"] == "19900000001"
    assert values["share_id"] == "snapshot-a" and values["sharer_uid"] == "employee-a"


@pytest.mark.asyncio
async def test_parallel_callbacks_consume_phone_code_once(booking):
    await asyncio.gather(
        *[
            service.submit_booking(booking.db, "share-a", "session-a", "客户", "code-a", avatar_bytes())
            for _ in range(2)
        ]
    )
    booking.phone.assert_awaited_once()


@pytest.mark.asyncio
async def test_committed_submission_retry_does_not_reset_lead(booking):
    booking.repo.submission.return_value = "already-saved"
    result = await service.submit_booking(booking.db, "share-a", "session-a", "客户", "code-a", avatar_bytes())
    assert result == {"success": True, "id": "already-saved"}
    booking.repo.upsert.assert_not_called()
    booking.phone.assert_not_called()
    booking.repo.record_submission.assert_not_called()


@pytest.mark.asyncio
async def test_retry_after_database_failure_keeps_verified_phone(booking):
    booking.repo.upsert.side_effect = [RuntimeError("database unavailable"), "lead-a"]
    with pytest.raises(RuntimeError):
        await service.submit_booking(booking.db, "share-a", "session-a", "客户", "code-a", avatar_bytes())
    await service.submit_booking(booking.db, "share-a", "session-a", "客户", "code-a", avatar_bytes())
    booking.phone.assert_awaited_once()


@pytest.mark.parametrize(
    "token,session,status",
    [
        ("share-a", None, 401),
        ("share-b", {"appid": "wx_test", "openid": "x", "share_token": "share-a"}, 403),
        ("share-a", {"appid": "another-app", "openid": "x", "share_token": "share-a"}, 403),
    ],
)
@pytest.mark.asyncio
async def test_expired_or_cross_case_session_cannot_submit(booking, token, session, status):
    booking.redis.value = json.dumps(session) if session else None
    with pytest.raises(HTTPException) as error:
        await service.submit_booking(booking.db, token, "session-a", "客户", "code-a", avatar_bytes())
    assert error.value.status_code == status
    booking.repo.upsert.assert_not_called()
    booking.phone.assert_not_called()


@pytest.mark.asyncio
async def test_phone_failure_does_not_save_customer(booking):
    booking.phone.side_effect = HTTPException(422, "denied")
    with pytest.raises(HTTPException):
        await service.submit_booking(booking.db, "share-a", "session-a", "客户", "bad-code", avatar_bytes())
    booking.repo.upsert.assert_not_called()
    booking.db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_malformed_nickname_does_not_consume_phone_code(booking):
    with pytest.raises(HTTPException):
        await service.submit_booking(booking.db, "share-a", "session-a", " ", "code", avatar_bytes())
    booking.phone.assert_not_called()


@pytest.mark.parametrize(
    "role,department,expected",
    [
        ("user", 7, "sharer_uid = 'employee-a'"),
        ("admin", 7, "department_id = 7"),
        ("superadmin", 7, None),
    ],
)
@pytest.mark.asyncio
async def test_scope_is_explicit_for_each_role(monkeypatch, role, department, expected):
    monkeypatch.setattr(service, "effective_permission_grants", AsyncMock(return_value=None))
    predicates = await service.lead_scope(
        None, SimpleNamespace(role=role, uid="employee-a", department_id=department), "view_list"
    )
    if expected is None:
        assert predicates == []
    else:
        assert expected in str(
            predicates[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        )


@pytest.mark.asyncio
async def test_missing_grant_and_unbound_admin_are_forbidden(monkeypatch):
    user = SimpleNamespace(role="user", uid="a", department_id=1)
    monkeypatch.setattr(service, "effective_permission_grants", AsyncMock(return_value=["chat.access"]))
    with pytest.raises(HTTPException) as error:
        await service.lead_scope(None, user, "delete")
    assert error.value.status_code == 403
    user.role = "admin"
    user.department_id = None
    monkeypatch.setattr(service, "effective_permission_grants", AsyncMock(return_value=None))
    with pytest.raises(HTTPException):
        await service.lead_scope(None, user, "view_list")


@pytest.mark.asyncio
async def test_avatar_and_delete_reject_out_of_scope_record(monkeypatch):
    monkeypatch.setattr(service, "effective_permission_grants", AsyncMock(return_value=None))
    repo = SimpleNamespace(avatar=AsyncMock(return_value=None), delete=AsyncMock(return_value=False))
    monkeypatch.setattr(service, "CaseVisitRepository", lambda _db: repo)
    user = SimpleNamespace(role="user", uid="employee-a", department_id=1)
    db = SimpleNamespace(commit=AsyncMock())
    for operation in [service.read_lead_avatar, service.delete_lead]:
        with pytest.raises(HTTPException) as error:
            await operation(db, user, "other-lead")
        assert error.value.status_code == 404
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_visitor_session_uses_verified_wechat_identity_and_does_not_issue_employee_token(booking, monkeypatch):
    original_client = httpx.AsyncClient

    def wechat(request):
        assert request.url.params["js_code"] == "login-code"
        return httpx.Response(200, json={"openid": "provider-openid", "session_key": "not-exposed"})

    monkeypatch.setattr(
        service.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(wechat),
            **kwargs,
        ),
    )
    result = await service.create_visitor_session(
        booking.db,
        service.VisitorSessionCreate(
            code="login-code",
            share_token="share-a",
        ),
    )
    assert set(result) == {"session_id", "expires_in"}
    stored = json.loads(booking.redis.value)
    assert stored == {"appid": "wx_test", "openid": "provider-openid", "share_token": "share-a"}


def test_visitor_cannot_supply_openid_or_phone_as_identity():
    with pytest.raises(ValidationError):
        service.VisitorSessionCreate(code="login-code", share_token="share-a", openid="forged", phone="19900000001")


@pytest.mark.asyncio
async def test_visitor_session_requires_real_provider_configuration(booking, monkeypatch):
    monkeypatch.setattr(service, "_wechat_credentials", lambda: ("", ""))
    with pytest.raises(HTTPException) as error:
        await service.create_visitor_session(
            booking.db, service.VisitorSessionCreate(code="code", share_token="share-a")
        )
    assert error.value.status_code == 503
