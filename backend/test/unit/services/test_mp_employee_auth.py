from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from yuxi.services import mp_service as service


@pytest.fixture
def employee_repo(monkeypatch):
    employee = SimpleNamespace(
        id="employee-1",
        employee_code="001A",
        name="测试员工",
        login_account="13900000001",
        enabled=True,
        login_port=["app"],
        last_login_at=None,
    )
    repo = SimpleNamespace(get_by_code=AsyncMock(return_value=employee))
    monkeypatch.setattr(service, "EmployeeRepository", lambda db: repo)
    return repo, employee


def test_employee_code_payload_trims_without_losing_leading_zeroes():
    assert service.EmployeeCodePayload(employee_code=" 001A ").employee_code == "001A"
    assert service.EmployeeCodePayload(employee_code="A").employee_code == "A"
    for value in ("", "   ", "A" * 65):
        with pytest.raises(ValidationError):
            service.EmployeeCodePayload(employee_code=value)


@pytest.mark.asyncio
async def test_lookup_returns_only_display_fields_without_login_side_effects(employee_repo, monkeypatch):
    repo, employee = employee_repo
    issue = AsyncMock()
    monkeypatch.setattr(service, "_issue_token", issue)
    result = await service.lookup_employee_by_code(object(), service.EmployeeCodePayload(employee_code="001A"))
    assert result == {"employee": {"employee_code": "001A", "name": "测试员工", "login_account": "13900000001"}}
    repo.get_by_code.assert_awaited_once_with("001A")
    issue.assert_not_awaited()
    assert employee.last_login_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["lookup_employee_by_code", "login_by_employee_code"])
@pytest.mark.parametrize(
    "state,expected_status,expected_code",
    [
        ("missing", 404, "EMPLOYEE_NOT_FOUND"),
        ("disabled", 403, "EMPLOYEE_DISABLED"),
        ("pc_only", 403, "EMPLOYEE_APP_FORBIDDEN"),
    ],
)
async def test_both_endpoints_reject_ineligible_employees(
    employee_repo, monkeypatch, operation, state, expected_status, expected_code
):
    repo, employee = employee_repo
    if state == "missing":
        repo.get_by_code.return_value = None
    elif state == "disabled":
        employee.enabled = False
    else:
        employee.login_port = ["pc"]
    issue = AsyncMock()
    monkeypatch.setattr(service, "_issue_token", issue)
    with pytest.raises(HTTPException) as exc:
        await getattr(service, operation)(object(), service.EmployeeCodePayload(employee_code="001A"))
    assert exc.value.status_code == expected_status
    assert exc.value.detail["error"]["code"] == expected_code
    if state == "missing":
        assert exc.value.detail["error"]["message"] == "未找到对应员工号"
    issue.assert_not_awaited()


@pytest.mark.asyncio
async def test_login_reuses_mp_token_issuer(employee_repo, monkeypatch):
    _, employee = employee_repo
    issue = AsyncMock(return_value={"access_token": "mp-token", "employee": {"id": employee.id}})
    monkeypatch.setattr(service, "_issue_token", issue)
    db = object()
    result = await service.login_by_employee_code(db, service.EmployeeCodePayload(employee_code="001A"))
    issue.assert_awaited_once_with(db, employee)
    assert result["access_token"] == "mp-token"


@pytest.mark.asyncio
async def test_login_rechecks_eligibility_after_lookup(employee_repo, monkeypatch):
    _, employee = employee_repo
    issue = AsyncMock()
    monkeypatch.setattr(service, "_issue_token", issue)
    payload = service.EmployeeCodePayload(employee_code="001A")
    await service.lookup_employee_by_code(object(), payload)
    employee.enabled = False
    with pytest.raises(HTTPException) as exc:
        await service.login_by_employee_code(object(), payload)
    assert exc.value.detail["error"]["code"] == "EMPLOYEE_DISABLED"
    issue.assert_not_awaited()
