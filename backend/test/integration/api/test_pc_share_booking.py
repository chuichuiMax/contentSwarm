"""PC share -> anonymous MP entry -> booking, using PostgreSQL and actual HTTP routes."""

import io
import json
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from sqlalchemy import func, select

from server.routers.case_visit_router import case_visits
from test.integration.api import test_material_share_jobs as share_tests
from test.unit.services.test_case_share_entry import EntryRedis
from yuxi.services import case_share_entry_service as entry_service
from yuxi.services import case_visit_service as visits
from yuxi.storage.postgres.models_business import Department, User
from yuxi.storage.postgres.models_content import ContentCaseVisitLead, ContentMaterialShare
from yuxi.utils.auth_utils import AuthUtils

pytestmark = pytest.mark.asyncio
share_env = share_tests.env


async def test_pc_share_entry_and_booking_preserve_actor_and_source(share_env, monkeypatch):
    env = share_env
    env.app.include_router(case_visits, prefix="/api")
    async with env.sessions() as db:
        db.add(Department(id=7, name="测试部门"))
        await db.flush()
        user = await db.get(User, 1)
        user.department_id = 7
        await db.commit()
    headers = {"Authorization": "Bearer " + AuthUtils.create_access_token({"sub": "1"})}
    assert (await env.client.post("/api/material-library/shares", json={"item_ids": ["i1"]})).status_code == 401
    created = await env.client.post("/api/material-library/shares", headers=headers, json={"item_ids": ["i1"]})
    assert created.status_code == 201, created.text
    token = created.json()["share"]["id"]
    async with env.sessions() as db:
        share = await db.scalar(select(ContentMaterialShare).where(ContentMaterialShare.token == token))
        assert share.source_platform == "pc" and share.sharer_uid == "sender"
        code = share.mp_entry_code
        assert len(code) == 24
    for path in [f"/share/case/{token}", f"/api/material-library/shares/{token}/page"]:
        page = await env.client.get(path)
        assert page.status_code == 200 and "免费预约参观工地" in page.text and f"{token}/booking" in page.text
    entry = await env.client.get(f"/api/material-library/shares/{token}/booking")
    assert entry.status_code == 200 and f"{token}/mp-code" in entry.text
    resolved = await env.client.get(f"/api/material-library/share-entries/{code}")
    assert resolved.json() == {"share_id": token, "booking": True}
    assert (await env.client.get("/api/material-library/share-entries/" + "e" * 24)).status_code == 404
    async with env.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(ContentCaseVisitLead)) == 0

    redis = EntryRedis()
    monkeypatch.setattr(entry_service, "get_redis_client", AsyncMock(return_value=redis))
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
    image = io.BytesIO()
    Image.new("RGB", (32, 32), "blue").save(image, "PNG")
    for _ in range(2):
        response = await env.client.post(
            f"/api/mp/share/cases/{token}/appointments",
            data={"session_id": "pc-test-session", "nickname": "测试业主", "phone_code": "verified", "consent": "true"},
            files={"avatar": ("avatar.png", image.getvalue(), "image/png")},
        )
        assert response.status_code == 200, response.text
    async with env.sessions() as db:
        leads = list((await db.scalars(select(ContentCaseVisitLead))).all())
        assert len(leads) == 1 and leads[0].sharer_uid == "sender" and leads[0].department_id == 7
        assert leads[0].booking_revision == 1 and leads[0].read_revision == 0
        assert leads[0].follow_status == "pending" and leads[0].share_id == share.id


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
