from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.asyncio


def _employee_payload(**overrides):
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


async def test_roles_seed_and_link_employee_role(test_client, admin_headers):
    listed = await test_client.get("/api/roles", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    names = [item["name"] for item in listed.json()["roles"]]
    assert "运营" in names
    assert "网销" in names

    role_name = f"测试角色_{uuid.uuid4().hex[:6]}"
    created_role = await test_client.post(
        "/api/roles",
        headers=admin_headers,
        json={"name": role_name, "enabled": True},
    )
    assert created_role.status_code == 200, created_role.text
    role = created_role.json()["role"]
    role_pk = role["id"]
    assert role["role_code"].startswith("JS")
    assert role["member_count"] == 0

    created_employee = await test_client.post(
        "/api/employees",
        headers=admin_headers,
        json=_employee_payload(role=role_name),
    )
    assert created_employee.status_code == 200, created_employee.text
    employee_pk = created_employee.json()["employee"]["id"]

    try:
        refreshed = await test_client.get("/api/roles", headers=admin_headers)
        linked = next(item for item in refreshed.json()["roles"] if item["id"] == role_pk)
        assert linked["member_count"] == 1

        members = await test_client.get(f"/api/roles/{role_pk}/employees", headers=admin_headers)
        assert members.status_code == 200, members.text
        assert members.json()["employees"][0]["id"] == employee_pk

        employee = created_employee.json()["employee"]
        by_name = await test_client.get(
            f"/api/roles/{role_pk}/employees",
            headers=admin_headers,
            params={"keyword": employee["name"][2:6]},
        )
        assert by_name.status_code == 200, by_name.text
        assert employee_pk in [item["id"] for item in by_name.json()["employees"]]

        by_code = await test_client.get(
            f"/api/roles/{role_pk}/employees",
            headers=admin_headers,
            params={"keyword": employee["employee_code"][1:4]},
        )
        assert by_code.status_code == 200, by_code.text
        assert employee_pk in [item["id"] for item in by_code.json()["employees"]]

        missed_members = await test_client.get(
            f"/api/roles/{role_pk}/employees",
            headers=admin_headers,
            params={"keyword": "不是这个员工"},
        )
        assert missed_members.status_code == 200, missed_members.text
        assert employee_pk not in [item["id"] for item in missed_members.json()["employees"]]

        searched = await test_client.get(
            "/api/roles",
            headers=admin_headers,
            params={"keyword": role_name[1:6]},
        )
        assert searched.status_code == 200, searched.text
        assert role_pk in [item["id"] for item in searched.json()["roles"]]

        blocked = await test_client.delete(f"/api/roles/{role_pk}", headers=admin_headers)
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["error"]["code"] == "ROLE_IN_USE"

        unknown = await test_client.post(
            "/api/employees",
            headers=admin_headers,
            json=_employee_payload(role="不存在的角色"),
        )
        assert unknown.status_code == 422, unknown.text
        assert unknown.json()["detail"]["error"]["code"] == "ROLE_NOT_FOUND"
    finally:
        await test_client.delete(f"/api/employees/{employee_pk}", headers=admin_headers)
        deleted = await test_client.delete(f"/api/roles/{role_pk}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text


async def test_role_permissions_get_put_and_reject_unknown(test_client, admin_headers):
    role_name = f"授权角色_{uuid.uuid4().hex[:6]}"
    created = await test_client.post(
        "/api/roles",
        headers=admin_headers,
        json={"name": role_name, "enabled": True},
    )
    assert created.status_code == 200, created.text
    role_pk = created.json()["role"]["id"]

    try:
        loaded = await test_client.get(f"/api/roles/{role_pk}/permissions", headers=admin_headers)
        assert loaded.status_code == 200, loaded.text
        body = loaded.json()
        assert body["grants"] == []
        assert any(item["module"] == "员工管理" for item in body["catalog"])
        assert any(item["module"] == "配置管理" for item in body["catalog"])

        saved = await test_client.put(
            f"/api/roles/{role_pk}/permissions",
            headers=admin_headers,
            json={"grants": ["employee.view_list", "chat.access", "employee.view_list"]},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["grants"] == ["employee.view_list", "chat.access"]

        reloaded = await test_client.get(f"/api/roles/{role_pk}/permissions", headers=admin_headers)
        assert reloaded.status_code == 200, reloaded.text
        assert reloaded.json()["grants"] == ["employee.view_list", "chat.access"]

        invalid = await test_client.put(
            f"/api/roles/{role_pk}/permissions",
            headers=admin_headers,
            json={"grants": ["not.a.permission"]},
        )
        assert invalid.status_code == 422, invalid.text
        assert invalid.json()["detail"]["error"]["code"] == "ROLE_PERMISSION_INVALID"
    finally:
        deleted = await test_client.delete(f"/api/roles/{role_pk}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text


async def test_roles_include_system_and_link_users(test_client, admin_headers):
    listed = await test_client.get("/api/roles", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    by_code = {item["role_code"]: item for item in listed.json()["roles"]}
    assert by_code["user"]["name"] == "普通用户"
    assert by_code["user"]["is_system"] is True
    assert by_code["admin"]["is_system"] is True
    assert by_code["superadmin"]["is_system"] is True

    locked = await test_client.delete(f"/api/roles/{by_code['user']['id']}", headers=admin_headers)
    assert locked.status_code == 409, locked.text
    assert locked.json()["detail"]["error"]["code"] == "ROLE_SYSTEM_LOCKED"

    role_name = f"联动角色_{uuid.uuid4().hex[:6]}"
    created_role = await test_client.post(
        "/api/roles",
        headers=admin_headers,
        json={"name": role_name, "enabled": True},
    )
    assert created_role.status_code == 200, created_role.text
    role = created_role.json()["role"]
    role_pk = role["id"]
    user_id = None

    try:
        suffix = uuid.uuid4().hex[:8]
        created_user = await test_client.post(
            "/api/auth/users",
            headers=admin_headers,
            json={"username": f"u_link_{suffix}", "password": f"Pw!{suffix}", "role": role_name},
        )
        assert created_user.status_code == 200, created_user.text
        user = created_user.json()
        user_id = user["id"]
        assert user["role"] == role_name

        refreshed = await test_client.get("/api/roles", headers=admin_headers)
        linked = next(item for item in refreshed.json()["roles"] if item["id"] == role_pk)
        assert linked["member_count"] >= 1

        members = await test_client.get(f"/api/roles/{role_pk}/employees", headers=admin_headers)
        assert members.status_code == 200, members.text
        assert f"user:{user['uid']}" in [item["id"] for item in members.json()["employees"]]

        blocked = await test_client.delete(f"/api/roles/{role_pk}", headers=admin_headers)
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["error"]["code"] == "ROLE_IN_USE"
    finally:
        if user_id is not None:
            await test_client.delete(f"/api/auth/users/{user_id}", headers=admin_headers)
        deleted = await test_client.delete(f"/api/roles/{role_pk}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text
