import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from yuxi.services import mp_service as service

pytestmark = pytest.mark.asyncio


@pytest.fixture
def auth(monkeypatch):
    employee = SimpleNamespace(
        id="employee-1",
        employee_code="001A",
        name="测试员工",
        login_account="13900000001",
        enabled=True,
        login_port=["app"],
        last_login_at=None,
    )
    repo = SimpleNamespace(
        get_by_login_account=AsyncMock(return_value=employee), get_by_code=AsyncMock(return_value=employee)
    )
    values = {"mp:wx:session": json.dumps({"openid": "wx-user", "session_key": "wx-key"})}

    async def setex(key, ttl, value):
        values[key] = value

    async def delete(key):
        return values.pop(key, None) is not None

    redis = SimpleNamespace(
        get=AsyncMock(side_effect=values.get), setex=AsyncMock(side_effect=setex), delete=AsyncMock(side_effect=delete)
    )
    issue = AsyncMock(return_value={"access_token": "mp-token", "employee": {"id": employee.id}})
    monkeypatch.setattr(service, "EmployeeRepository", lambda db: repo)
    monkeypatch.setattr(service, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(service, "_wechat_credentials", lambda: ("wx-test", "test-secret"))
    monkeypatch.setattr(service, "_wechat_phone_by_code", AsyncMock(return_value=employee.login_account))
    monkeypatch.setattr(service, "_issue_token", issue)
    return SimpleNamespace(employee=employee, repo=repo, values=values, redis=redis, issue=issue)


@pytest.mark.parametrize("environment", ["development", "production"])
async def test_plaintext_phone_cannot_replace_wechat_authorization(auth, monkeypatch, environment):
    monkeypatch.setenv("YUXI_ENV", environment)
    payload = service.WechatPhonePayload(session_id="session", phone=auth.employee.login_account)
    with pytest.raises(HTTPException) as exc:
        await service.bind_wechat_phone(object(), payload)
    assert exc.value.detail["error"]["code"] == "WECHAT_PHONE_REQUIRED"
    auth.repo.get_by_login_account.assert_not_awaited()
    auth.issue.assert_not_awaited()


@pytest.mark.parametrize("operation", ["code", "phone"])
async def test_missing_wechat_credentials_fail_even_in_development(auth, monkeypatch, operation):
    monkeypatch.setenv("YUXI_ENV", "development")
    monkeypatch.setattr(service, "_wechat_credentials", lambda: ("", ""))
    with pytest.raises(HTTPException) as exc:
        if operation == "code":
            await service.login_by_wechat_code(service.WechatCodePayload(code="not-a-real-code"))
        else:
            await service.bind_wechat_phone(
                object(),
                service.WechatPhonePayload(
                    session_id="session",
                    code="phone-code",
                    phone=auth.employee.login_account,
                ),
            )
    assert exc.value.detail["error"]["code"] == "WECHAT_NOT_CONFIGURED"
    auth.redis.setex.assert_not_awaited()


async def test_invalid_phone_code_never_falls_back_to_supplied_phone(auth, monkeypatch):
    monkeypatch.setenv("YUXI_ENV", "development")
    monkeypatch.setattr(
        service,
        "_wechat_phone_by_code",
        AsyncMock(
            side_effect=service._mp_error(
                422,
                "WECHAT_PHONE_INVALID",
                "授权已失效",
            )
        ),
    )
    with pytest.raises(HTTPException) as exc:
        await service.bind_wechat_phone(
            object(),
            service.WechatPhonePayload(
                session_id="session",
                code="expired",
                phone=auth.employee.login_account,
            ),
        )
    assert exc.value.detail["error"]["code"] == "WECHAT_PHONE_INVALID"
    auth.repo.get_by_login_account.assert_not_awaited()


@pytest.mark.parametrize(
    "state,error",
    [
        ("missing", "EMPLOYEE_NOT_FOUND"),
        ("disabled", "EMPLOYEE_DISABLED"),
        ("pc_only", "EMPLOYEE_APP_FORBIDDEN"),
    ],
)
async def test_authorized_phone_still_requires_eligible_employee(auth, state, error):
    if state == "missing":
        auth.repo.get_by_login_account.return_value = None
    elif state == "disabled":
        auth.employee.enabled = False
    else:
        auth.employee.login_port = ["pc"]
    with pytest.raises(HTTPException) as exc:
        await service.bind_wechat_phone(object(), service.WechatPhonePayload(session_id="session", code="verified"))
    assert exc.value.detail["error"]["code"] == error
    auth.issue.assert_not_awaited()
    assert "phone" not in json.loads(auth.values["mp:wx:session"])


async def test_wechat_and_employee_login_share_issuer_and_confirm_ignores_client_phone(auth):
    db = object()
    bound = await service.bind_wechat_phone(db, service.WechatPhonePayload(session_id="session", code="verified"))
    assert bound["phone_masked"] == "139****0001"
    auth.issue.assert_not_awaited()
    confirmed = await service.confirm_login(db, service.AuthConfirmPayload(session_id="session", phone="13999999999"))
    by_code = await service.login_by_employee_code(db, service.EmployeeCodePayload(employee_code="001A"))
    assert confirmed == by_code
    assert auth.issue.await_args_list[0] == auth.issue.await_args_list[1]
    assert auth.issue.await_args.args == (db, auth.employee)
    assert "mp:wx:session" not in auth.values


@pytest.mark.parametrize("state,error", [("disabled", "EMPLOYEE_DISABLED"), ("pc_only", "EMPLOYEE_APP_FORBIDDEN")])
async def test_confirm_rechecks_access_after_authorization(auth, state, error):
    await service.bind_wechat_phone(object(), service.WechatPhonePayload(session_id="session", code="verified"))
    if state == "disabled":
        auth.employee.enabled = False
    else:
        auth.employee.login_port = ["pc"]
    with pytest.raises(HTTPException) as exc:
        await service.confirm_login(object(), service.AuthConfirmPayload(session_id="session"))
    assert exc.value.detail["error"]["code"] == error
    auth.issue.assert_not_awaited()


async def test_expired_session_and_unbound_confirm_do_not_issue_token(auth):
    with pytest.raises(HTTPException) as exc:
        await service.bind_wechat_phone(object(), service.WechatPhonePayload(session_id="expired", code="verified"))
    assert exc.value.detail["error"]["code"] == "WECHAT_SESSION_EXPIRED"
    with pytest.raises(HTTPException) as exc:
        await service.confirm_login(object(), service.AuthConfirmPayload(session_id="session"))
    assert exc.value.detail["error"]["code"] == "WECHAT_PHONE_REQUIRED"
    auth.issue.assert_not_awaited()


@pytest.mark.parametrize("body", [{"errcode": 40029, "errmsg": "invalid code"}, {}, {"openid": "only-openid"}])
async def test_invalid_wechat_login_response_never_creates_session(auth, httpx_mock, body):
    httpx_mock.add_response(method="GET", json=body)
    with pytest.raises(HTTPException) as exc:
        await service.login_by_wechat_code(service.WechatCodePayload(code="invalid"))
    assert exc.value.detail["error"]["code"] == "WECHAT_CODE_INVALID"
    auth.redis.setex.assert_not_awaited()
