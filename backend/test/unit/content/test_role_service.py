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
    assert "variable.view_list" in keys
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
