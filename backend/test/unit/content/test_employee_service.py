from __future__ import annotations

import pytest
from pydantic import ValidationError

from yuxi.services.employee_service import EmployeeCreate, EmployeeUpdate


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
