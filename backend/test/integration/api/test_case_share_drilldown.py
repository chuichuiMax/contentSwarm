"""Data-overview drilldowns use exact account identity, dates and existing delete scope."""

import io

import pytest
from openpyxl import load_workbook

from test.integration.api.test_case_share_stats import report as report
from test.integration.api.test_case_visit_router import visit_api as visit_api
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentMaterialShare, ContentRole

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
BASE = "/api/case-shares"
TARGET = {"sharer_uid": "visitor-test-user-1"}


async def test_person_filter_matches_overview_counts_dates_pagination_and_export(report):
    async with report.sessions() as db:
        # A different operator sharing this person's materials must remain excluded.
        other = await db.get(ContentMaterialShare, "share-b")
        other.owner_uid = TARGET["sharer_uid"]
        other.sharer_employee_name = "实际员工"
        await db.commit()
        counts = await MaterialLibraryRepository(db).count_case_shares_by_owner([TARGET["sharer_uid"]])

    headers = report.tokens[5]
    listing = (await report.client.get(BASE, headers=headers, params=TARGET)).json()
    assert listing["total"] == counts[TARGET["sharer_uid"]] == 2
    assert [row["id"] for row in listing["items"]] == ["historical", "share-a"]
    # The legacy counter fallback must not become a fabricated sharer identity.
    assert listing["items"][0]["sharer_name"] is None
    second_page = (await report.client.get(BASE, headers=headers, params={**TARGET, "page_size": 1, "page": 2})).json()
    assert second_page["total"] == 2 and second_page["items"][0]["id"] == "share-a"

    for day, expected in [("2026-10-28", "share-a"), ("2026-10-29", "historical")]:
        params = {**TARGET, "start_date": day, "end_date": day}
        filtered = (await report.client.get(BASE, headers=headers, params=params)).json()
        assert filtered["total"] == 1 and filtered["items"][0]["id"] == expected
        exported = await report.client.get(BASE + "/export", headers=headers, params=params)
        assert exported.status_code == 200
        sheet = load_workbook(io.BytesIO(exported.content)).active
        assert sheet.max_row == 2
        assert sheet["F2"].value.startswith(day)

    params = {**TARGET, "source": "mp", "keyword": "0001"}
    assert (await report.client.get(BASE, headers=headers, params=params)).json()["total"] == 1
    exported = await report.client.get(BASE + "/export", headers=headers, params=TARGET)
    assert load_workbook(io.BytesIO(exported.content)).active.max_row == 3


async def test_admin_drilldown_allows_read_and_export_without_expanding_delete_or_default_scope(report):
    headers = report.tokens[3]
    assert (await report.client.get(BASE, headers=headers)).json()["total"] == 0
    filtered = (await report.client.get(BASE, headers=headers, params=TARGET)).json()
    assert {row["id"] for row in filtered["items"]} == {"historical", "share-a"}
    exported = await report.client.get(BASE + "/export", headers=headers, params=TARGET)
    assert exported.status_code == 200
    assert load_workbook(io.BytesIO(exported.content)).active.max_row == 3
    assert (await report.client.get(BASE + "/summary", headers=headers)).json() == {"total": 0, "today": 0}
    for share_id in ("share-a", "historical"):
        assert (await report.client.delete(BASE + "/" + share_id, headers=headers)).status_code == 404

    # Standard users cannot read another person's rows by supplying a target UID.
    for suffix in ("", "/export"):
        response = await report.client.get(BASE + suffix, headers=report.tokens[2], params=TARGET)
        assert response.status_code == 200
        if suffix:
            assert load_workbook(io.BytesIO(response.content)).active.max_row == 1
        else:
            assert response.json()["items"] == [] and response.json()["total"] == 0
    own = (await report.client.get(BASE, headers=report.tokens[1], params=TARGET)).json()
    assert [row["id"] for row in own["items"]] == ["share-a"]


async def test_drilldown_validates_identity_and_requires_report_permission(report):
    for uid in ("", "x" * 256):
        response = await report.client.get(BASE, headers=report.tokens[5], params={"sharer_uid": uid})
        assert response.status_code == 422
    for suffix in ("", "/export"):
        response = await report.client.get(BASE + suffix, headers=report.tokens[3], params={"sharer_uid": "missing"})
        assert response.status_code == 200
        if suffix:
            assert load_workbook(io.BytesIO(response.content)).active.max_row == 1
        else:
            assert response.json()["total"] == 0

    async with report.sessions() as db:
        db.add(
            ContentRole(
                id="no-drilldown",
                role_code="no-drilldown",
                name="无案例权限",
                created_by="test",
                permissions=["chat.access"],
            )
        )
        (await db.get(User, 1)).role = "无案例权限"
        await db.commit()
    for suffix in ("", "/export"):
        response = await report.client.get(BASE + suffix, headers=report.tokens[1], params=TARGET)
        assert response.status_code == 403
