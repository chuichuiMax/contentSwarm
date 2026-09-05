from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.asyncio


def _payload(**overrides):
    suffix = uuid.uuid4().hex[:8]
    data = {
        "employee_code": f"H{suffix[:5].upper()}",
        "name": f"pytest员工_{suffix}",
        "login_account": f"1{suffix[:10]}",
        "gender": "male",
        "login_port": ["pc", "app"],
        "role": "运营",
        "enabled": True,
    }
    data.update(overrides)
    return data


async def test_employee_crud_search_and_toggle(test_client, admin_headers):
    created = await test_client.post("/api/employees", headers=admin_headers, json=_payload())
    assert created.status_code == 200, created.text
    employee = created.json()["employee"]
    employee_pk = employee["id"]
    employee_code = employee["employee_code"]

    try:
        listed = await test_client.get("/api/employees", headers=admin_headers)
        assert listed.status_code == 200, listed.text
        ids = [item["id"] for item in listed.json()["employees"]]
        assert employee_pk in ids
        assert "phone" not in listed.json()["employees"][0]
        assert "mobile" not in listed.json()["employees"][0]

        searched = await test_client.get(
            "/api/employees",
            headers=admin_headers,
            params={"keyword": employee["name"]},
        )
        assert searched.status_code == 200, searched.text
        assert searched.json()["employees"][0]["id"] == employee_pk

        duplicate = await test_client.post(
            "/api/employees",
            headers=admin_headers,
            json=_payload(employee_code=employee_code, login_account=f"2{uuid.uuid4().hex[:10]}"),
        )
        assert duplicate.status_code == 409, duplicate.text
        assert duplicate.json()["detail"]["error"]["code"] == "EMPLOYEE_CODE_EXISTS"

        toggled = await test_client.patch(
            f"/api/employees/{employee_pk}",
            headers=admin_headers,
            json={"enabled": False},
        )
        assert toggled.status_code == 200, toggled.text
        assert toggled.json()["employee"]["enabled"] is False

        updated = await test_client.patch(
            f"/api/employees/{employee_pk}",
            headers=admin_headers,
            json={"role": "网销", "login_port": ["app"]},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["employee"]["role"] == "网销"
        assert updated.json()["employee"]["login_port"] == ["app"]
    finally:
        deleted = await test_client.delete(f"/api/employees/{employee_pk}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text

    gone = await test_client.patch(
        f"/api/employees/{employee_pk}",
        headers=admin_headers,
        json={"enabled": True},
    )
    assert gone.status_code == 404
    assert gone.json()["detail"]["error"]["code"] == "EMPLOYEE_NOT_FOUND"
