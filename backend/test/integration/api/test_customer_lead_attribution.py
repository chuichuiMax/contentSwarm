"""Actual sharing accounts determine self-booking exclusion and PC attribution."""

import asyncio
import io
import json
from unittest.mock import AsyncMock

import pytest
from openpyxl import load_workbook
from sqlalchemy import func, select

from test.integration.api.test_case_visit_router import visit_api as visit_api
from test.unit.services.test_case_share_entry import EntryRedis
from yuxi.services import case_visit_service as service
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import (
    ContentCaseVisitLead,
    ContentCaseVisitSubmission,
    ContentEmployee,
    ContentMaterialShare,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["user", "admin", "superadmin"])
async def test_independent_account_name_is_searchable_and_exported_without_using_public_contact(visit_api, role):
    async with visit_api.sessions() as db:
        user = await db.get(User, 1)
        user.username, user.role, user.phone_number = "独立分享账号", role, None
        share = await db.get(ContentMaterialShare, "share-a")
        share.sharer_uid, share.sharer_name = user.uid, "手填联系人"
        share.source_platform = "pc"
        await db.commit()
    base, headers = "/api/customer-leads", visit_api.tokens[1]
    response = await visit_api.client.get(base, headers=headers, params={"keyword": "独立分享账号"})
    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert rows and all(row["sharer_name"] == "独立分享账号" for row in rows)
    assert all(row["employee_code"] is None and row["branch"] is None for row in rows)
    assert (await visit_api.client.get(base, headers=headers, params={"keyword": "手填联系人"})).json()["total"] == 0
    exported = await visit_api.client.get(base + "/export", headers=headers, params={"keyword": "独立分享账号"})
    sheet = load_workbook(io.BytesIO(exported.content)).active
    assert sheet.max_row == len(rows) + 1 and sheet["D2"].value == "独立分享账号"
    assert sheet["E2"].value is None and sheet["F2"].value is None
    # Sharing identity must not grant access to another account's leads.
    assert (await visit_api.client.get(base, headers=visit_api.tokens[3])).json()["total"] == 0


@pytest.mark.asyncio
async def test_legacy_employee_link_uses_actual_account_and_current_employee_fields(visit_api):
    async with visit_api.sessions() as db:
        employee = await db.get(ContentEmployee, "emp-1")
        employee.name, employee.employee_code, employee.current_branch = "当前员工", "000123", "当前门店"
        share = await db.get(ContentMaterialShare, "share-a")
        share.sharer_uid = "visitor-test-user-1"
        share.sharer_employee_name, share.sharer_branch = "旧员工姓名", "旧门店"
        await db.commit()
    base, headers = "/api/customer-leads", visit_api.tokens[1]
    response = await visit_api.client.get(base, headers=headers, params={"keyword": "当前员工", "branch": "当前门店"})
    assert response.json()["total"] == 1
    row = response.json()["items"][0]
    assert (row["sharer_name"], row["employee_code"], row["branch"]) == ("当前员工", "000123", "当前门店")
    assert (await visit_api.client.get(base, headers=headers, params={"keyword": "旧员工姓名"})).json()["total"] == 0


@pytest.mark.asyncio
async def test_deleted_employee_uses_operator_snapshot_and_never_material_owner_or_public_contact(visit_api):
    async with visit_api.sessions() as db:
        share = await db.get(ContentMaterialShare, "share-a")
        share.sharer_uid, share.sharer_employee_id = "visitor-test-user-1", "emp-1"
        share.sharer_employee_name, share.sharer_employee_code, share.sharer_branch = "原分享员工", "000099", "原门店"
        await db.delete(await db.get(ContentEmployee, "emp-1"))
        await db.commit()
    base, headers = "/api/customer-leads", visit_api.tokens[1]
    params = {"keyword": "原分享员工", "branch": "原门店"}
    response = await visit_api.client.get(base, headers=headers, params=params)
    assert response.json()["total"] == 1
    row = response.json()["items"][0]
    assert (row["sharer_name"], row["employee_code"], row["branch"]) == ("原分享员工", "000099", "原门店")
    exported = await visit_api.client.get(base + "/export", headers=headers, params=params)
    assert load_workbook(io.BytesIO(exported.content)).active["D2"].value == "原分享员工"
    async with visit_api.sessions() as db:
        share = await db.get(ContentMaterialShare, "share-a")
        share.sharer_uid = share.sharer_employee_id = None
        share.sharer_employee_name = share.sharer_employee_code = share.sharer_branch = None
        share.owner_uid, share.sharer_name = "visitor-test-user-5", "不可信联系人"
        await db.commit()
    rows = (await visit_api.client.get(base, headers=visit_api.tokens[5])).json()["items"]
    unknown = next(row for row in rows if row["id"] == "lead-4")
    assert unknown["sharer_name"] is None and unknown["employee_code"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["pc", "mp"])
async def test_account_phone_exclusion_does_not_count_or_reset_existing_leads(visit_api, monkeypatch, platform):
    redis = EntryRedis()
    phone = AsyncMock(return_value="18800000001")
    monkeypatch.setattr(service, "get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr(service, "_wechat_credentials", lambda: ("wx-test", "unused"))
    monkeypatch.setattr(service, "_wechat_phone_by_code", phone)
    async with visit_api.sessions() as db:
        share = await db.get(ContentMaterialShare, "share-a")
        share.sharer_uid, share.sharer_phone, share.source_platform = "visitor-test-user-1", "19900000002", platform
        lead = await db.get(ContentCaseVisitLead, "lead-1")
        lead.follow_status, lead.read_revision = "followed", 1
        await db.commit()
    redis.values["mp:visit:self"] = json.dumps(
        {
            "appid": "wx-test",
            "openid": "openid-1",
            "share_token": "token-a",
        }
    )
    redis.values[service.mp_lead_unread_cache_key("visitor-test-user-1")] = "0"

    async def book():
        response = await visit_api.client.post(
            "/api/mp/share/cases/token-a/appointments",
            data={
                "session_id": "self",
                "phone_code": "verified",
                "consent": "true",
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    expected = {"success": True, "id": None, "recorded": False, "reason": "self_booking"}
    assert await asyncio.gather(book(), book()) == [expected, expected]
    assert await book() == expected
    phone.assert_awaited_once()
    async with visit_api.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(ContentCaseVisitLead)) == 4
        assert await db.scalar(select(func.count()).select_from(ContentCaseVisitSubmission)) == 0
        lead = await db.get(ContentCaseVisitLead, "lead-1")
        assert (lead.phone, lead.booking_revision, lead.read_revision, lead.follow_status) == (
            "19900000001",
            1,
            1,
            "followed",
        )
    assert redis.values[service.mp_lead_unread_cache_key("visitor-test-user-1")] == "0"
    summary = (await visit_api.client.get("/api/customer-leads/summary", headers=visit_api.tokens[5])).json()
    assert summary["total"] == 4
    # A third party whose phone matches only the editable public contact still becomes a lead.
    phone.return_value = "19900000002"
    redis.values["mp:visit:self"] = json.dumps(
        {
            "appid": "wx-test",
            "openid": "another-customer",
            "share_token": "token-a",
        }
    )
    assert (await book())["id"]
    async with visit_api.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(ContentCaseVisitLead)) == 5
