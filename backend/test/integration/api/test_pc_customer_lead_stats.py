"""PC summary boundaries and operation permissions through real HTTP."""

from datetime import datetime, timedelta

import pytest

from test.integration.api.test_case_visit_router import visit_api as visit_api
from yuxi.services import case_visit_service
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentCaseVisitLead, ContentRole
from yuxi.utils.datetime_utils import SHANGHAI_TZ


@pytest.mark.asyncio
async def test_summary_counts_first_creation_with_shanghai_day_boundaries(visit_api, monkeypatch):
    monkeypatch.setattr(case_visit_service, "shanghai_now", lambda: datetime(2026, 10, 24, 12, tzinfo=SHANGHAI_TZ))
    start = datetime(2026, 10, 23, 16)
    async with visit_api.sessions() as db:
        for index, created in enumerate([start, start - timedelta(microseconds=1), start + timedelta(days=1)], 1):
            lead = await db.get(ContentCaseVisitLead, f"lead-{index}")
            lead.created_at = created
            lead.acquired_at = start + timedelta(hours=3)
        await db.commit()
    client, base = visit_api.client, "/api/customer-leads"
    for user, today in [(1, 1), (2, 0), (4, 0)]:
        response = await client.get(base + "/summary", headers=visit_api.tokens[user])
        assert response.json()["today"] == today
    # Rebooking updates acquisition time without changing a lead's creation date.
    response = await client.get(base, headers=visit_api.tokens[2], params={"start_date": "2026-10-24"})
    assert response.json()["total"] == 1
    response = await client.get(base + "/summary", headers=visit_api.tokens[2], params={"keyword": "not-found"})
    assert response.json()["today"] == 0 and response.json()["total"] == 1


@pytest.mark.asyncio
async def test_custom_role_without_list_permission_cannot_read_stats_phone_or_export(visit_api):
    async with visit_api.sessions() as db:
        db.add(
            ContentRole(
                id="no-lead-role",
                role_code="no_lead",
                name="无客资权限岗位",
                created_by="test",
                permissions=["chat.access"],
            )
        )
        user = await db.get(User, 1)
        user.role = "无客资权限岗位"
        await db.commit()
    for suffix in ["", "/summary", "/export", "/lead-1/phone"]:
        response = await visit_api.client.get("/api/customer-leads" + suffix, headers=visit_api.tokens[1])
        assert response.status_code == 403, response.text
