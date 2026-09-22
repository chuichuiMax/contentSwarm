from __future__ import annotations

import pytest
from pydantic import ValidationError

from yuxi.services.employee_service import DEFAULT_EMPLOYEE_PASSWORD, EmployeeCreate, EmployeeUpdate


def test_employee_create_schema_requires_core_fields():
    with pytest.raises(ValidationError):
        EmployeeCreate(
            employee_code="",
            name="张三",
            login_account="13510874227",
            gender="male",
            login_port=["pc", "app"],
            role="运营",
        )


def test_employee_create_schema_rejects_unknown_gender():
    with pytest.raises(ValidationError):
        EmployeeCreate(
            employee_code="H04596",
            name="张三",
            login_account="13510874227",
            gender="unknown",
            login_port=["pc", "app"],
            role="运营",
        )


def test_employee_create_schema_requires_login_port():
    with pytest.raises(ValidationError):
        EmployeeCreate(
            employee_code="H04596",
            name="张三",
            login_account="13510874227",
            gender="male",
            login_port=[],
            role="运营",
        )


def test_employee_update_schema_allows_partial_enabled():
    payload = EmployeeUpdate(enabled=False)
    assert payload.model_dump(exclude_unset=True) == {"enabled": False}


def test_employee_create_schema_accepts_age():
    payload = EmployeeCreate(
        employee_code="H04596",
        name="张三",
        login_account="13510874227",
        gender="male",
        age=28,
        login_port=["pc", "app"],
        role="运营",
    )
    assert payload.age == 28


def test_employee_create_schema_accepts_branch_and_department():
    payload = EmployeeCreate(
        employee_code="H04596",
        name="张三",
        login_account="13510874227",
        current_branch="郴州鸿扬",
        current_department="工程交付部",
        gender="male",
        login_port=["app"],
        role="项目经理",
    )
    assert payload.current_branch == "郴州鸿扬"
    assert payload.current_department == "工程交付部"
    assert payload.login_port == ["app"]


def test_employee_create_schema_rejects_invalid_age():
    with pytest.raises(ValidationError):
        EmployeeCreate(
            employee_code="H04596",
            name="张三",
            login_account="13510874227",
            gender="male",
            age=0,
            login_port=["pc", "app"],
            role="运营",
        )


def test_list_roles_signature_supports_skipping_member_counts():
    import inspect

    from yuxi.services.role_service import list_roles

    assert "include_member_counts" in inspect.signature(list_roles).parameters


def test_default_employee_password_is_fixed():
    assert DEFAULT_EMPLOYEE_PASSWORD == "123456"
