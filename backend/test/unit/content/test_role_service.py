from pydantic import ValidationError

import pytest

from yuxi.services.role_permissions import all_permission_keys, normalize_grants
from yuxi.services.role_service import (
    RoleCreate,
    RolePermissionsUpdate,
    RoleUpdate,
    is_system_role,
    next_role_code,
    stored_user_role,
)


def test_role_create_schema_requires_name():
    with pytest.raises(ValidationError):
        RoleCreate(name="")


def test_role_update_schema_allows_partial_enabled():
    payload = RoleUpdate(enabled=False)
    assert payload.model_dump(exclude_unset=True) == {"enabled": False}


def test_next_role_code_increments_js_sequence():
    assert next_role_code([]) == "JS0001"
    assert next_role_code(["JS0001", "JS0005", "OPS01"]) == "JS0006"
    assert next_role_code(["user", "admin", "superadmin", "JS0002"]) == "JS0003"


def test_system_roles_store_platform_codes():
    role = type("Role", (), {"role_type": "系统", "role_code": "user", "name": "普通用户"})()
    assert is_system_role(role)
    assert stored_user_role(role) == "user"


def test_content_roles_store_display_name_for_users():
    role = type("Role", (), {"role_type": "新增", "role_code": "JS0001", "name": "运营"})()
    assert not is_system_role(role)
    assert stored_user_role(role) == "运营"


def test_permission_catalog_keys_are_unique():
    keys = list(all_permission_keys())
    assert "employee.view_list" in keys
    assert "permission.create" in keys
    assert "content_type.view_list" in keys
    assert "target_audience.view_list" in keys
    assert "resident_population.view_list" in keys
    assert "variable.view_list" in keys
    assert "business_variable.view_list" in keys
    assert "process_standard.view_list" in keys
    assert not any(key.startswith("cover.") for key in keys)
    assert len(keys) == len(set(keys))


def test_normalize_grants_keeps_known_keys_and_skips_duplicates():
    grants = normalize_grants(["employee.view_list", "employee.view_list", "chat.access"])
    assert grants == ["employee.view_list", "chat.access"]


def test_normalize_grants_rejects_unknown_keys_in_strict_mode():
    with pytest.raises(ValueError):
        normalize_grants(["employee.view_list", "unknown.action"])
    assert normalize_grants(["employee.view_list", "unknown.action"], strict=False) == ["employee.view_list"]


def test_role_permissions_update_schema_defaults_empty_grants():
    payload = RolePermissionsUpdate()
    assert payload.grants == []


@pytest.mark.asyncio
async def test_member_count_skips_mp_prefixed_users(monkeypatch):
    from types import SimpleNamespace

    from yuxi.services import role_service as service

    role = SimpleNamespace(
        id="r1",
        name="家装顾问",
        role_code="JS0002",
        role_type="新增",
        to_dict=lambda member_count=0: {"id": "r1", "name": "家装顾问", "member_count": member_count},
    )

    class FakeRoleRepo:
        async def list_roles(self, *, keyword=None, enabled=None):
            return [role]

    class FakeEmployeeRepo:
        async def count_by_role(self):
            return {"家装顾问": 2}

    class FakeUserRepo:
        async def count_by_role_with_db(self, _db):
            return {"家装顾问": 3}

    async def fake_ensure(_db):
        return None

    monkeypatch.setattr(service, "ensure_default_roles", fake_ensure)
    monkeypatch.setattr(service, "RoleRepository", lambda _db: FakeRoleRepo())
    monkeypatch.setattr(service, "EmployeeRepository", lambda _db: FakeEmployeeRepo())
    monkeypatch.setattr(service, "UserRepository", lambda: FakeUserRepo())

    result = await service.list_roles(object())
    assert result["roles"][0]["member_count"] == 5


@pytest.mark.asyncio
async def test_list_role_members_uses_filtered_user_repo(monkeypatch):
    from types import SimpleNamespace

    from yuxi.services import role_service as service

    role = SimpleNamespace(id="r1", name="运营", role_code="JS0001", role_type="新增")
    employee = SimpleNamespace(to_dict=lambda: {"id": "e1", "employee_code": "YG001", "name": "张三"})
    user = SimpleNamespace(uid="u_pc_1", username="李四")

    class FakeRoleRepo:
        async def get(self, _role_pk):
            return role

    class FakeEmployeeRepo:
        async def list_by_role(self, _name, *, keyword=None):
            return [employee]

    class FakeUserRepo:
        async def list_by_roles_with_db(self, _db, roles, *, keyword=None):
            assert "运营" in roles
            return [user]

    monkeypatch.setattr(service, "RoleRepository", lambda _db: FakeRoleRepo())
    monkeypatch.setattr(service, "EmployeeRepository", lambda _db: FakeEmployeeRepo())
    monkeypatch.setattr(service, "UserRepository", lambda: FakeUserRepo())

    result = await service.list_role_members(object(), "r1")
    codes = [item["employee_code"] for item in result["employees"]]
    assert codes == ["YG001", "u_pc_1"]
    assert result["total"] == 2
