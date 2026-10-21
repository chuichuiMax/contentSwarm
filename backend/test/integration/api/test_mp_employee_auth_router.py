import uuid

import pytest


pytestmark = pytest.mark.asyncio


@pytest.fixture
async def employee_factory(test_client, admin_headers):
    employee_ids = []

    async def create(**overrides):
        suffix = uuid.uuid4().hex[:8].upper()
        payload = {
            "employee_code": f"00{suffix}",
            "name": f"工号登录测试{suffix}",
            "login_account": f"139{uuid.uuid4().int % 10**8:08d}",
            "gender": "male",
            "login_port": ["app"],
            "role": "运营",
            "enabled": True,
            **overrides,
        }
        response = await test_client.post("/api/employees", headers=admin_headers, json=payload)
        assert response.status_code == 200, response.text
        employee = response.json()["employee"]
        employee_ids.append(employee["id"])
        persisted = await test_client.get(
            "/api/employees", headers=admin_headers, params={"keyword": payload["employee_code"]}
        )
        assert persisted.status_code == 200, persisted.text
        saved = next((row for row in persisted.json()["employees"] if row["id"] == employee["id"]), None)
        assert saved is not None and saved["enabled"] is payload["enabled"], persisted.text
        return employee

    yield create

    for employee_id in employee_ids:
        response = await test_client.delete(f"/api/employees/{employee_id}", headers=admin_headers)
        assert response.status_code == 200, response.text


@pytest.fixture
def attempt_headers():
    # 每项测试独立计数，不消耗同机开发人员的失败尝试额度。
    number = uuid.uuid4().int % 65536
    return {"X-Forwarded-For": f"198.18.{number // 256}.{number % 256}"}


async def test_lookup_is_exact_and_read_only_then_login_issues_mp_identity(
    test_client,
    admin_headers,
    employee_factory,
    attempt_headers,
):
    employee = await employee_factory()
    before_login = employee["last_login_at"]
    for invalid in (employee["name"], employee["login_account"], employee["employee_code"][:-1]):
        missing = await test_client.post(
            "/api/mp/auth/employee/lookup",
            headers=attempt_headers,
            json={"employee_code": invalid},
        )
        assert missing.status_code == 404, missing.text
        assert missing.json()["detail"]["error"]["message"] == "未找到对应员工号"

    lookup = await test_client.post(
        "/api/mp/auth/employee/lookup",
        headers=attempt_headers,
        json={"employee_code": f"  {employee['employee_code']}  "},
    )
    assert lookup.status_code == 200, lookup.text
    assert lookup.json() == {
        "employee": {
            "employee_code": employee["employee_code"],
            "name": employee["name"],
            "login_account": employee["login_account"],
        }
    }
    listing = await test_client.get("/api/employees", headers=admin_headers)
    stored = next(row for row in listing.json()["employees"] if row["id"] == employee["id"])
    assert stored["last_login_at"] == before_login

    logged = await test_client.post(
        "/api/mp/auth/employee/login",
        headers=attempt_headers,
        json={"employee_code": employee["employee_code"]},
    )
    assert logged.status_code == 200, logged.text
    body = logged.json()
    assert body["token_type"] == "bearer"
    assert body["employee"]["id"] == employee["id"]
    assert body["employee"]["login_account"] == employee["login_account"]
    assert body["employee"]["last_login_at"] is not None
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    me = await test_client.get("/api/mp/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["employee"]["id"] == employee["id"]
    pc = await test_client.get("/api/employees", headers=headers)
    assert pc.status_code == 401, pc.text


@pytest.mark.parametrize(
    "overrides,error_code",
    [
        ({"enabled": False}, "EMPLOYEE_DISABLED"),
        ({"login_port": ["pc"]}, "EMPLOYEE_APP_FORBIDDEN"),
    ],
)
async def test_lookup_and_login_enforce_employee_status(
    test_client,
    employee_factory,
    attempt_headers,
    overrides,
    error_code,
):
    employee = await employee_factory(**overrides)
    for endpoint in ("lookup", "login"):
        response = await test_client.post(
            f"/api/mp/auth/employee/{endpoint}",
            headers=attempt_headers,
            json={"employee_code": employee["employee_code"]},
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["error"]["code"] == error_code
        assert "access_token" not in response.json()


async def test_login_rechecks_employee_revoked_after_lookup(
    test_client,
    admin_headers,
    employee_factory,
    attempt_headers,
):
    employee = await employee_factory()
    payload = {"employee_code": employee["employee_code"]}
    lookup = await test_client.post("/api/mp/auth/employee/lookup", headers=attempt_headers, json=payload)
    assert lookup.status_code == 200, lookup.text
    changed = await test_client.patch(
        f"/api/employees/{employee['id']}",
        headers=admin_headers,
        json={"enabled": False},
    )
    assert changed.status_code == 200, changed.text
    logged = await test_client.post("/api/mp/auth/employee/login", headers=attempt_headers, json=payload)
    assert logged.status_code == 403, logged.text
    assert logged.json()["detail"]["error"]["code"] == "EMPLOYEE_DISABLED"


@pytest.mark.parametrize("code", ["", "   ", "A" * 65])
async def test_employee_code_input_is_validated(test_client, attempt_headers, code):
    for endpoint in ("lookup", "login"):
        response = await test_client.post(
            f"/api/mp/auth/employee/{endpoint}",
            headers=attempt_headers,
            json={"employee_code": code},
        )
        assert response.status_code == 422, response.text


async def test_unknown_employee_attempts_use_existing_rate_limit(test_client, attempt_headers):
    payload = {"employee_code": f"missing-{uuid.uuid4().hex}"}
    for _ in range(10):
        response = await test_client.post("/api/mp/auth/employee/lookup", headers=attempt_headers, json=payload)
        assert response.status_code == 404, response.text
    limited = await test_client.post("/api/mp/auth/employee/login", headers=attempt_headers, json=payload)
    assert limited.status_code == 429, limited.text
    assert int(limited.headers["Retry-After"]) > 0
