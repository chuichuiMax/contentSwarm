from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.asyncio


async def test_resident_population_crud_search_and_toggle(test_client, admin_headers):
    listed = await test_client.get("/api/content-resident-populations", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    names = [item["name"] for item in listed.json()["resident_populations"]]
    assert "三口之家" in names
    assert "人宠友好家" in names

    item_name = f"测试居住人口_{uuid.uuid4().hex[:6]}"
    created = await test_client.post(
        "/api/content-resident-populations",
        headers=admin_headers,
        json={"name": item_name, "enabled": True},
    )
    assert created.status_code == 200, created.text
    item = created.json()["resident_population"]
    item_pk = item["id"]
    assert item["enabled"] is True

    try:
        searched = await test_client.get(
            "/api/content-resident-populations",
            headers=admin_headers,
            params={"keyword": item_name[2:6]},
        )
        assert searched.status_code == 200, searched.text
        assert item_pk in [row["id"] for row in searched.json()["resident_populations"]]

        duplicate = await test_client.post(
            "/api/content-resident-populations",
            headers=admin_headers,
            json={"name": item_name, "enabled": True},
        )
        assert duplicate.status_code == 409, duplicate.text
        assert duplicate.json()["detail"]["error"]["code"] == "RESIDENT_POPULATION_EXISTS"

        toggled = await test_client.patch(
            f"/api/content-resident-populations/{item_pk}",
            headers=admin_headers,
            json={"enabled": False},
        )
        assert toggled.status_code == 200, toggled.text
        assert toggled.json()["resident_population"]["enabled"] is False

        renamed = await test_client.patch(
            f"/api/content-resident-populations/{item_pk}",
            headers=admin_headers,
            json={"name": f"{item_name}_改"},
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["resident_population"]["name"] == f"{item_name}_改"
    finally:
        deleted = await test_client.delete(
            f"/api/content-resident-populations/{item_pk}", headers=admin_headers
        )
        assert deleted.status_code == 200, deleted.text

    gone = await test_client.patch(
        f"/api/content-resident-populations/{item_pk}",
        headers=admin_headers,
        json={"enabled": True},
    )
    assert gone.status_code == 404, gone.text
