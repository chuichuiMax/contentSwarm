"""PC share -> anonymous MP entry -> booking, using PostgreSQL and actual HTTP routes."""

import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from server.routers.case_visit_router import case_visits
from test.integration.api import test_material_share_jobs as share_tests
from test.unit.services.test_case_share_entry import EntryRedis
from yuxi.services import case_share_entry_service as entry_service
from yuxi.services import case_visit_service as visits
from yuxi.storage.postgres.models_business import Department, User
from yuxi.storage.postgres.models_content import ContentCaseVisitLead, ContentEmployee, ContentMaterialShare
from yuxi.utils.auth_utils import AuthUtils

pytestmark = pytest.mark.asyncio
share_env = share_tests.env


async def test_pc_share_entry_and_booking_preserve_actor_and_source(share_env, monkeypatch, httpx_mock):
    env = share_env
    env.app.include_router(case_visits, prefix="/api")
    async with env.sessions() as db:
        db.add(Department(id=7, name="测试部门"))
        await db.flush()
        user = await db.get(User, 1)
        user.department_id = 7
        user.phone_number = "19900000001"
        db.add(ContentEmployee(
            id="pc-sender-employee", employee_code="PC-SENDER", name="登录员工", login_account="19900000001",
            gender="unknown", role="user", created_by="sender",
        ))
        await db.commit()
    headers = {"Authorization": "Bearer " + AuthUtils.create_access_token({"sub": "1"})}
    assert (await env.client.post("/api/material-library/shares", json={"item_ids": ["i1"]})).status_code == 401
    created = await env.client.post("/api/material-library/shares", headers=headers, json={
        "item_ids": ["i1"], "contact_name": "对外联系人", "contact_phone": "13800000003",
        "sharer_uid": "not-the-sender",
    })
    assert created.status_code == 201, created.text
    token = created.json()["share"]["id"]
    assert created.json()["share"]["mini_program_url"] == (
        f"https://share.test/api/material-library/shares/{token}/open-mp?booking=false"
    )
    async with env.sessions() as db:
        share = await db.scalar(select(ContentMaterialShare).where(ContentMaterialShare.token == token))
        assert share.source_platform == "pc" and share.sharer_uid == "sender"
        assert (share.sharer_name, share.sharer_phone) == ("对外联系人", "13800000003")
        assert (await db.get(User, 1)).phone_number == "19900000001"
        assert (await db.get(ContentEmployee, "pc-sender-employee")).login_account == "19900000001"
        code = share.mp_entry_code
        assert len(code) == 24
    for path in [f"/share/case/{token}", f"/api/material-library/shares/{token}/page"]:
        page = await env.client.get(path)
        assert page.status_code == 200 and "预约免费参观工地" in page.text and f"{token}/booking" in page.text
        assert 'href="tel:13800000003"' in page.text and "联系人：对外联系人" in page.text
    entry = await env.client.get(f"/api/material-library/shares/{token}/booking")
    assert entry.status_code == 200 and f"{token}/mp-code" in entry.text
    resolved = await env.client.get(f"/api/material-library/share-entries/{code}")
    assert resolved.json() == {"share_id": token, "booking": True}
    assert (await env.client.get("/api/material-library/share-entries/" + "e" * 24)).status_code == 404
    async with env.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(ContentCaseVisitLead)) == 0

    redis = EntryRedis()
    monkeypatch.setattr(entry_service, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(entry_service, "_wechat_credentials", lambda: ("wx-test", "unused"))
    monkeypatch.setattr(entry_service, "_wechat_access_token", AsyncMock(return_value="case-entry-test"))
    httpx_mock.add_response(
        method="POST", url="https://api.weixin.qq.com/wxa/generate_urllink?access_token=case-entry-test",
        json={"url_link": "https://wxmpurl.cn/case-browse-test"},
    )
    opened = await env.client.get(created.json()["share"]["mini_program_url"], follow_redirects=False)
    assert opened.status_code == 302 and opened.headers["location"] == "https://wxmpurl.cn/case-browse-test"
    link_payload = json.loads(httpx_mock.get_requests()[0].content)
    assert link_payload["path"] == "pages/materials/shared-case"
    assert link_payload["query"] == f"entryCode={code}&booking=0"
    assert opened.headers["cache-control"] == "no-store"
    monkeypatch.setattr(entry_service, "_wechat_credentials", lambda: ("", ""))
    failure = await env.client.get(f"/api/material-library/shares/{token}/open-mp")
    assert failure.status_code == 503 and "微信预约服务未配置" in failure.text
    assert "返回预约页" in failure.text and failure.headers["cache-control"] == "no-store"

    monkeypatch.setattr(visits, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(visits, "_wechat_credentials", lambda: ("wx-test", "unused"))
    monkeypatch.setattr(visits, "_wechat_phone_by_code", AsyncMock(return_value="19912345678"))
    redis.values["mp:visit:pc-test-session"] = json.dumps(
        {"appid": "wx-test", "openid": "pc-visitor", "share_token": token}
    )
    for _ in range(2):
        response = await env.client.post(
            f"/api/mp/share/cases/{token}/appointments",
            data={"session_id": "pc-test-session", "phone_code": "verified", "consent": "true"},
        )
        assert response.status_code == 200, response.text
    async with env.sessions() as db:
        leads = list((await db.scalars(select(ContentCaseVisitLead))).all())
        assert len(leads) == 1 and leads[0].sharer_uid == "sender" and leads[0].department_id == 7
        assert leads[0].booking_revision == 1 and leads[0].read_revision == 0
        assert leads[0].follow_status == "pending" and leads[0].share_id == share.id
        assert leads[0].nickname is None and leads[0].avatar_data is None


async def test_legacy_entry_concurrency_does_not_relabel_source(share_env):
    env = share_env
    async with env.sessions() as db:
        share = ContentMaterialShare(
            id="legacy-entry",
            token="legacy-public",
            owner_uid="sender",
            category_id="case",
            title="旧分享",
            sharer_uid="sender",
        )
        db.add(share)
        await db.commit()
    import asyncio

    async def allocate():
        async with env.sessions() as db:
            share = await entry_service.booking_share(db, "legacy-public")
            return await entry_service.ensure_entry_code(db, share)

    codes = await asyncio.gather(allocate(), allocate(), allocate())
    assert len(set(codes)) == 1
    async with env.sessions() as db:
        share = await db.get(ContentMaterialShare, "legacy-entry")
        assert share.token == "legacy-public" and share.source_platform is None
