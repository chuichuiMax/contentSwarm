from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.asyncio


async def test_variable_crud_search_and_toggle(test_client, admin_headers):
    listed = await test_client.get("/api/content-variables", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    names = [item["name"] for item in listed.json()["variables"]]
    assert "设计师" in names
    assert "工匠" in names
    assert "楼盘信息" in names
    assert "主材" in names

    service_entry = "装修家居"
    variable_name = f"测试变量_{uuid.uuid4().hex[:6]}"
    created = await test_client.post(
        "/api/content-variables",
        headers=admin_headers,
        json={"name": variable_name, "service_entry": service_entry, "enabled": True},
    )
    assert created.status_code == 200, created.text
    item = created.json()["variable"]
    variable_pk = item["id"]
    other_pk = None
    assert item["variable_code"].startswith("FWTD")
    assert item["service_entry"] == service_entry
    assert item["ports"] == ["pc", "app"]
    assert item["editions"] == ["quick", "pro"]

    try:
        searched = await test_client.get(
            "/api/content-variables",
            headers=admin_headers,
            params={"keyword": variable_name[2:6]},
        )
        assert searched.status_code == 200, searched.text
        assert variable_pk in [row["id"] for row in searched.json()["variables"]]

        by_code = await test_client.get(
            "/api/content-variables",
            headers=admin_headers,
            params={"keyword": item["variable_code"][2:6]},
        )
        assert by_code.status_code == 200, by_code.text
        assert variable_pk in [row["id"] for row in by_code.json()["variables"]]

        unknown = await test_client.post(
            "/api/content-variables",
            headers=admin_headers,
            json={"name": f"{variable_name}_x", "service_entry": "不存在的入口", "enabled": True},
        )
        assert unknown.status_code == 422, unknown.text
        assert unknown.json()["detail"]["error"]["code"] == "VARIABLE_SERVICE_ENTRY_NOT_FOUND"

        duplicate = await test_client.post(
            "/api/content-variables",
            headers=admin_headers,
            json={"name": variable_name, "service_entry": service_entry, "enabled": True},
        )
        assert duplicate.status_code == 409, duplicate.text
        assert duplicate.json()["detail"]["error"]["code"] == "VARIABLE_NAME_EXISTS"

        other_entry = await test_client.post(
            "/api/content-variables",
            headers=admin_headers,
            json={"name": variable_name, "service_entry": "好评笔记", "enabled": True},
        )
        assert other_entry.status_code == 200, other_entry.text
        other_pk = other_entry.json()["variable"]["id"]
        assert other_entry.json()["variable"]["service_entry"] == "好评笔记"
        assert other_entry.json()["variable"]["name"] == variable_name

        toggled = await test_client.patch(
            f"/api/content-variables/{variable_pk}",
            headers=admin_headers,
            json={"enabled": False, "ports": ["app"], "editions": ["quick"]},
        )
        assert toggled.status_code == 200, toggled.text
        assert toggled.json()["variable"]["enabled"] is False
        assert toggled.json()["variable"]["ports"] == ["app"]
        assert toggled.json()["variable"]["editions"] == ["quick"]
    finally:
        deleted = await test_client.delete(f"/api/content-variables/{variable_pk}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text
        if other_pk:
            other_deleted = await test_client.delete(f"/api/content-variables/{other_pk}", headers=admin_headers)
            assert other_deleted.status_code == 200, other_deleted.text
