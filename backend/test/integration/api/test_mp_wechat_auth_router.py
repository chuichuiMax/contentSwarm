"""微信认证 HTTP 链路：隔离 PostgreSQL schema，只模拟微信和 Redis 边界。"""

import os
import re
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.main import LoginRateLimitMiddleware
from server.routers.mp_router import mp
from server.utils.auth_middleware import get_db
from yuxi.services import mp_service as service
from yuxi.storage.postgres.models_business import Base, Department, User
from yuxi.storage.postgres.models_content import ContentEmployee

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def auth_env(monkeypatch, httpx_mock):
    schema = f"test_wechat_auth_{uuid.uuid4().hex}"
    url = os.environ["POSTGRES_URL"].replace("postgresql://", "postgresql+asyncpg://", 1)
    setup = create_async_engine(url)
    async with setup.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    tables = {model.__table__ for model in (Department, User, ContentEmployee)}
    while True:
        dependencies = {key.column.table for table in tables for key in table.foreign_keys}
        if dependencies <= tables:
            break
        tables |= dependencies
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=list(tables)))
    async with sessions() as db:
        db.add(Department(id=1, name="微信登录测试部门"))
        await db.flush()
        db.add(
            User(
                id=1,
                uid="same-user",
                username="员工",
                phone_number="13900000001",
                password_hash="unused",
                role="user",
                department_id=1,
            )
        )
        db.add(
            ContentEmployee(
                id="same-employee",
                employee_code="001A",
                name="员工",
                login_account="13900000001",
                gender="unknown",
                role="user",
                login_port=["app"],
                enabled=True,
                created_by="same-user",
            )
        )
        await db.commit()
    values = {}

    async def setex(key, ttl, value):
        values[key] = value

    async def delete(key):
        return values.pop(key, None) is not None

    redis = SimpleNamespace(
        get=AsyncMock(side_effect=values.get), setex=AsyncMock(side_effect=setex), delete=AsyncMock(side_effect=delete)
    )
    monkeypatch.setattr(service, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(service, "_wechat_credentials", lambda: ("wx-test", "test-secret"))
    monkeypatch.setattr(service, "_wechat_access_token", AsyncMock(return_value="test-wx-token"))
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"https://api\.weixin\.qq\.com/sns/jscode2session\?.*"),
        json={"openid": "verified-openid", "session_key": "verified-key"},
        is_reusable=True,
        is_optional=True,
    )
    app = FastAPI()
    app.include_router(mp, prefix="/api")
    app.add_middleware(LoginRateLimitMiddleware)

    async def db_dependency():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = db_dependency
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://login.test") as client:
            yield SimpleNamespace(client=client, sessions=sessions, values=values, httpx_mock=httpx_mock)
    finally:
        await engine.dispose()
        async with setup.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await setup.dispose()


async def authorize(env, phone="13900000001"):
    started = await env.client.post("/api/mp/auth/wechat/code", json={"code": "wx-login-code"})
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]
    env.httpx_mock.add_response(
        method="POST",
        url="https://api.weixin.qq.com/wxa/business/getuserphonenumber?access_token=test-wx-token",
        json={"errcode": 0, "phone_info": {"purePhoneNumber": phone}},
    )
    bound = await env.client.post("/api/mp/auth/wechat/phone", json={"session_id": session_id, "code": "phone-code"})
    return session_id, bound


async def test_wechat_and_code_login_return_same_employee_and_user(auth_env):
    env = auth_env
    session_id, bound = await authorize(env)
    assert bound.status_code == 200, bound.text
    assert bound.json()["phone_masked"] == "139****0001"
    assert "access_token" not in bound.json()
    confirmed = await env.client.post("/api/mp/auth/confirm", json={"session_id": session_id, "phone": "13999999999"})
    assert confirmed.status_code == 200, confirmed.text
    code = await env.client.post("/api/mp/auth/employee/login", json={"employee_code": "001A"})
    assert code.status_code == 200, code.text
    identities = []
    for response in (confirmed, code):
        assert response.json()["employee"]["id"] == "same-employee"
        me = await env.client.get("/api/mp/me", headers={"Authorization": "Bearer " + response.json()["access_token"]})
        assert me.status_code == 200, me.text
        identities.append((me.json()["employee"]["id"], me.json()["user_uid"]))
    assert identities == [("same-employee", "same-user")] * 2
    replay = await env.client.post("/api/mp/auth/confirm", json={"session_id": session_id})
    assert replay.status_code == 422
    async with env.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(ContentEmployee)) == 1
        assert await db.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.parametrize(
    "state,error",
    [
        ("unknown", "EMPLOYEE_NOT_FOUND"),
        ("disabled", "EMPLOYEE_DISABLED"),
        ("pc_only", "EMPLOYEE_APP_FORBIDDEN"),
    ],
)
async def test_mp_wechat_login_requires_eligible_employee_phone(auth_env, state, error):
    env = auth_env
    if state != "unknown":
        async with env.sessions() as db:
            employee = await db.get(ContentEmployee, "same-employee")
            if state == "disabled":
                employee.enabled = False
            else:
                employee.login_port = ["pc"]
            await db.commit()
    _, bound = await authorize(env, "13900000002" if state == "unknown" else "13900000001")
    assert bound.status_code == (404 if state == "unknown" else 403), bound.text
    assert bound.json()["detail"]["error"]["code"] == error
    assert "access_token" not in bound.json()


@pytest.mark.parametrize("state,error", [("disabled", "EMPLOYEE_DISABLED"), ("pc_only", "EMPLOYEE_APP_FORBIDDEN")])
async def test_confirm_rechecks_revoked_access(auth_env, state, error):
    env = auth_env
    session_id, bound = await authorize(env)
    assert bound.status_code == 200, bound.text
    async with env.sessions() as db:
        employee = await db.get(ContentEmployee, "same-employee")
        if state == "disabled":
            employee.enabled = False
        else:
            employee.login_port = ["pc"]
        await db.commit()
    rejected = await env.client.post("/api/mp/auth/confirm", json={"session_id": session_id})
    assert rejected.status_code == 403, rejected.text
    assert rejected.json()["detail"]["error"]["code"] == error
    assert "access_token" not in rejected.json()


@pytest.mark.parametrize("path", ["wechat/code", "wechat/phone", "confirm"])
async def test_wechat_auth_failures_use_existing_rate_limit(auth_env, path):
    env = auth_env
    payload = {"code": "", "session_id": "missing"}
    headers = {"X-Forwarded-For": f"test-wechat-{uuid.uuid4().hex}"}
    for _ in range(10):
        rejected = await env.client.post(f"/api/mp/auth/{path}", json=payload, headers=headers)
        assert rejected.status_code == 422, rejected.text
    limited = await env.client.post(f"/api/mp/auth/{path}", json=payload, headers=headers)
    assert limited.status_code == 429, limited.text
    assert int(limited.headers["Retry-After"]) > 0
