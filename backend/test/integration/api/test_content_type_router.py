from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.asyncio


async def test_content_type_crud_search_and_toggle(test_client, admin_headers):
    listed = await test_client.get("/api/content-types", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    names = [item["name"] for item in listed.json()["content_types"]]
    assert "工艺施工展示" in names
    assert "人设自荐" in names

    type_name = f"测试类型_{uuid.uuid4().hex[:6]}"
    created = await test_client.post(
        "/api/content-types",
        headers=admin_headers,
        json={"name": type_name, "enabled": True},
    )
    assert created.status_code == 200, created.text
    item = created.json()["content_type"]
    type_pk = item["id"]
    assert item["type_code"].startswith("NRLX")

    try:
        searched = await test_client.get(
            "/api/content-types",
            headers=admin_headers,
            params={"keyword": type_name[2:6]},
        )
        assert searched.status_code == 200, searched.text
        assert type_pk in [row["id"] for row in searched.json()["content_types"]]

        by_code = await test_client.get(
            "/api/content-types",
            headers=admin_headers,
            params={"keyword": item["type_code"][2:6]},
        )
        assert by_code.status_code == 200, by_code.text
        assert type_pk in [row["id"] for row in by_code.json()["content_types"]]

        duplicate = await test_client.post(
            "/api/content-types",
            headers=admin_headers,
            json={"name": type_name, "enabled": True},
        )
        assert duplicate.status_code == 409, duplicate.text
        assert duplicate.json()["detail"]["error"]["code"] == "CONTENT_TYPE_NAME_EXISTS"

        toggled = await test_client.patch(
            f"/api/content-types/{type_pk}",
            headers=admin_headers,
            json={"enabled": False},
        )
        assert toggled.status_code == 200, toggled.text
        assert toggled.json()["content_type"]["enabled"] is False

        renamed = await test_client.patch(
            f"/api/content-types/{type_pk}",
            headers=admin_headers,
            json={"name": f"{type_name}_改"},
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["content_type"]["name"] == f"{type_name}_改"
    finally:
        deleted = await test_client.delete(f"/api/content-types/{type_pk}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text

    gone = await test_client.patch(
        f"/api/content-types/{type_pk}",
        headers=admin_headers,
        json={"enabled": True},
    )
    assert gone.status_code == 404, gone.text
