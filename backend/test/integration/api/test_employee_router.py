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
        "current_branch": "郴州鸿扬",
        "current_department": "工程交付部",
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
        created_row = next(item for item in listed.json()["employees"] if item["id"] == employee_pk)
        assert created_row["source"] == "employee"
        assert created_row["current_branch"] == "郴州鸿扬"
        assert created_row["current_department"] == "工程交付部"
        assert any(item.get("source") == "user" for item in listed.json()["employees"])
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
            json={"role": "网销", "login_port": ["app"], "age": 32},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["employee"]["role"] == "网销"
        assert updated.json()["employee"]["login_port"] == ["app"]
        assert updated.json()["employee"]["age"] == 32
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


async def test_create_employee_provisions_platform_login_with_default_password(test_client, admin_headers):
    payload = _payload(role="管理员", login_account=f"139{uuid.uuid4().int % 10**8:08d}")
    created = await test_client.post("/api/employees", headers=admin_headers, json=payload)
    assert created.status_code == 200, created.text
    employee = created.json()["employee"]
    employee_pk = employee["id"]
    phone = employee["login_account"]

    try:
        logged = await test_client.post(
            "/api/auth/token",
            data={"username": phone, "password": "123456"},
        )
        assert logged.status_code == 200, logged.text
        body = logged.json()
        assert body["phone_number"] == phone
        assert body["role"] == "admin"
        assert body["access_token"]

        mine = await test_client.get(
            "/api/employees/me",
            headers={"Authorization": f"Bearer {body['access_token']}"},
        )
        assert mine.status_code == 200, mine.text
        assert mine.json()["employee"]["id"] == employee_pk
        assert mine.json()["employee"]["current_branch"] == "郴州鸿扬"

        rejected = await test_client.post(
            "/api/auth/token",
            data={"username": phone, "password": "wrong-password"},
        )
        assert rejected.status_code == 401, rejected.text
    finally:
        deleted = await test_client.delete(f"/api/employees/{employee_pk}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text

    after_delete = await test_client.post(
        "/api/auth/token",
        data={"username": phone, "password": "123456"},
    )
    assert after_delete.status_code == 401, after_delete.text


async def test_employee_login_uses_default_password_when_phone_already_used(test_client, admin_headers):
    phone = f"136{uuid.uuid4().int % 10**8:08d}"
    suffix = uuid.uuid4().hex[:8]
    existing = await test_client.post(
        "/api/auth/users",
        headers=admin_headers,
        json={
            "username": f"exist_{suffix}",
            "password": "OldPass!123",
            "role": "user",
            "phone_number": phone,
        },
    )
    assert existing.status_code == 200, existing.text
    existing_uid = existing.json()["uid"]
    created = await test_client.post(
        "/api/employees",
        headers=admin_headers,
        json=_payload(login_account=phone, role="运营"),
    )
    assert created.status_code == 200, created.text
    employee_pk = created.json()["employee"]["id"]
    try:
        logged = await test_client.post(
            "/api/auth/token",
            data={"username": phone, "password": "123456"},
        )
        assert logged.status_code == 200, logged.text
        body = logged.json()
        assert body["uid"].startswith("mp_")
        assert body["uid"] != existing_uid
        assert body["role"] == "运营"
    finally:
        deleted = await test_client.delete(f"/api/employees/{employee_pk}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text


async def test_pc_login_requires_enabled_employee_with_pc_port(test_client, admin_headers):
    payload = _payload(login_account=f"138{uuid.uuid4().int % 10**8:08d}")
    created = await test_client.post("/api/employees", headers=admin_headers, json=payload)
    assert created.status_code == 200, created.text
    employee = created.json()["employee"]
    employee_pk = employee["id"]
    phone = employee["login_account"]
    employee_code = employee["employee_code"]

    try:
        by_code = await test_client.post(
            "/api/auth/token",
            data={"username": employee_code, "password": "123456"},
        )
        assert by_code.status_code == 200, by_code.text
        assert by_code.json()["phone_number"] == phone

        app_only = await test_client.patch(
            f"/api/employees/{employee_pk}",
            headers=admin_headers,
            json={"login_port": ["app"]},
        )
        assert app_only.status_code == 200, app_only.text
        blocked_port = await test_client.post(
            "/api/auth/token",
            data={"username": phone, "password": "123456"},
        )
        assert blocked_port.status_code == 403, blocked_port.text
        assert blocked_port.json()["detail"] == "该员工未开通 PC 登录"

        restored = await test_client.patch(
            f"/api/employees/{employee_pk}",
            headers=admin_headers,
            json={"login_port": ["pc", "app"], "enabled": False},
        )
        assert restored.status_code == 200, restored.text
        blocked_disabled = await test_client.post(
            "/api/auth/token",
            data={"username": phone, "password": "123456"},
        )
        assert blocked_disabled.status_code == 403, blocked_disabled.text
        assert blocked_disabled.json()["detail"] == "员工账号已禁用"
    finally:
        deleted = await test_client.delete(f"/api/employees/{employee_pk}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text
