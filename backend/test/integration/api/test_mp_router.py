from __future__ import annotations

import io
import uuid

import pytest
from PIL import Image


pytestmark = pytest.mark.asyncio


def _phone() -> str:
    return f"139{uuid.uuid4().int % 10**8:08d}"


def _employee_payload(phone: str, **overrides):
    suffix = uuid.uuid4().hex[:6].upper()
    data = {
        "employee_code": f"MP{suffix}",
        "name": f"小程序员工_{suffix}",
        "login_account": phone,
        "gender": "male",
        "login_port": ["app"],
        "role": "运营",
        "enabled": True,
    }
    data.update(overrides)
    return data


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color=(180, 74, 58)).save(buffer, format="PNG")
    return buffer.getvalue()


async def test_mp_sms_login_me_schema_and_pc_token_isolation(test_client, admin_headers):
    phone = _phone()
    created = await test_client.post("/api/employees", headers=admin_headers, json=_employee_payload(phone))
    assert created.status_code == 200, created.text
    employee_pk = created.json()["employee"]["id"]
    try:
        sent = await test_client.post("/api/mp/auth/sms/send", json={"phone": phone})
        assert sent.status_code == 200, sent.text
        body = sent.json()
        assert body["sent"] is True
        assert "debug_code" in body

        logged = await test_client.post(
            "/api/mp/auth/sms/login",
            json={"phone": phone, "code": body["debug_code"]},
        )
        assert logged.status_code == 200, logged.text
        token = logged.json()["access_token"]
        mp_headers = {"Authorization": f"Bearer {token}"}
        assert logged.json()["employee"]["login_account"] == phone

        me = await test_client.get("/api/mp/me", headers=mp_headers)
        assert me.status_code == 200, me.text
        assert me.json()["employee"]["login_account"] == phone

        patched = await test_client.patch("/api/mp/me", headers=mp_headers, json={"bio": "整装顾问"})
        assert patched.status_code == 200, patched.text
        assert patched.json()["employee"]["bio"] == "整装顾问"

        schema = await test_client.get(
            "/api/mp/content/form-schema",
            headers=mp_headers,
            params={"service_entry": "装修家居"},
        )
        assert schema.status_code == 200, schema.text
        data = schema.json()
        assert data["service_entry"] == "装修家居"
        assert {item["value"] for item in data["service_entries"]} == {"装修家居", "好评笔记"}
        assert all(item["enabled"] for item in data["content_types"])
        assert all(item["service_entry"] == "装修家居" for item in data["variables"])
        assert all("app" in item["ports"] and "quick" in item["editions"] for item in data["variables"])
        assert data["frame_areas"][0]["value"] == "50-70㎡"
        assert data["frame_areas"][0]["quote_choices"]["基础"] == ["4万", "4.5万", "5万"]
        assert "北欧" in data["design_styles"]
        assert data["regions"][0] == "长沙市"
        zhuzhou = next(item for item in data["region_tree"] if item["city"] == "株洲市")
        assert "荷塘区" in zhuzhou["districts"]
        assert "云龙示范区" in zhuzhou["districts"]
        assert all(item["districts"] for item in data["region_tree"])

        review_schema = await test_client.get(
            "/api/mp/content/form-schema",
            headers=mp_headers,
            params={"service_entry": "好评笔记"},
        )
        assert review_schema.status_code == 200, review_schema.text
        review_data = review_schema.json()
        assert review_data["service_entry"] == "好评笔记"
        assert review_data["regions"][0] == "长沙市"
        assert review_data["region_tree"] == data["region_tree"]

        pricing = await test_client.get(
            "/api/mp/content/pricing",
            headers=mp_headers,
            params={"frame_area": "50-70㎡"},
        )
        assert pricing.status_code == 200, pricing.text
        assert pricing.json()["quotes"]["基础"] == "4-5万"
        assert pricing.json()["quote_choices"]["木制品"] == ["2万", "2.5万", "3万"]

        pc_blocked = await test_client.get("/api/employees", headers=mp_headers)
        assert pc_blocked.status_code == 401, pc_blocked.text

        listed = await test_client.get("/api/mp/contents", headers=mp_headers)
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"] == []
        assert listed.json()["total"] == 0
    finally:
        deleted = await test_client.delete(f"/api/employees/{employee_pk}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text


async def test_mp_compile_brief_requires_cover_and_creates_locked_task(test_client, admin_headers):
    phone = _phone()
    created = await test_client.post("/api/employees", headers=admin_headers, json=_employee_payload(phone))
    assert created.status_code == 200, created.text
    employee_pk = created.json()["employee"]["id"]
    task_id = None
    mp_headers = None
    try:
        sent = await test_client.post("/api/mp/auth/sms/send", json={"phone": phone})
        assert sent.status_code == 200, sent.text
        logged = await test_client.post(
            "/api/mp/auth/sms/login",
            json={"phone": phone, "code": sent.json()["debug_code"]},
        )
        assert logged.status_code == 200, logged.text
        mp_headers = {"Authorization": f"Bearer {logged.json()['access_token']}"}

        missing = await test_client.post(
            "/api/mp/content/compile-brief",
            headers=mp_headers,
            json={"service_entry": "装修家居", "form_values": {}},
        )
        assert missing.status_code == 422, missing.text

        uploaded = await test_client.post(
            "/api/mp/content/uploads/cover",
            headers=mp_headers,
            files={"file": ("cover.png", _png_bytes(), "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        cover_asset_id = uploaded.json()["asset"]["id"]

        schema = await test_client.get(
            "/api/mp/content/form-schema",
            headers=mp_headers,
            params={"service_entry": "装修家居"},
        )
        assert schema.status_code == 200, schema.text
        type_code = next(item["type_code"] for item in schema.json()["content_types"] if item["name"] == "工艺施工展示")
        compiled = await test_client.post(
            "/api/mp/content/compile-brief",
            headers=mp_headers,
            json={
                "service_entry": "装修家居",
                "content_type_code": type_code,
                "cover_asset_id": cover_asset_id,
                "form_values": {
                    "楼盘信息": "星河湾",
                    "外框面积": "50-70㎡",
                    "基础": "4-5万",
                    "木制品": "2-3万",
                    "主材": "2-3万",
                    "设计风格": "北欧",
                    "所在区域": "长沙市 岳麓区",
                },
            },
        )
        assert compiled.status_code == 200, compiled.text
        payload = compiled.json()
        task_id = payload["task_id"]
        assert payload["status"] == "strategy_evidence_locked"
        assert payload["task_status"] == "brief_ready"
        assert payload["content_code"].startswith("NR")

        task = await test_client.get(f"/api/mp/content/tasks/{task_id}", headers=mp_headers)
        assert task.status_code == 200, task.text
        assert task.json()["locked"] is True

        listed = await test_client.get("/api/mp/contents", headers=mp_headers, params={"service_entry": "装修家居"})
        assert listed.status_code == 200, listed.text
        assert listed.json()["total"] >= 1
        item = listed.json()["items"][0]
        assert item["task_id"] == task_id
        assert item["service_entry"] == "装修家居"

        fav = await test_client.post(f"/api/mp/contents/{task_id}/favorite", headers=mp_headers)
        assert fav.status_code == 200, fav.text
        assert fav.json()["favorited"] is True
        unfav = await test_client.delete(f"/api/mp/contents/{task_id}/favorite", headers=mp_headers)
        assert unfav.status_code == 200, unfav.text
        assert unfav.json()["favorited"] is False
    finally:
        if task_id and mp_headers:
            await test_client.delete(f"/api/mp/contents/{task_id}", headers=mp_headers)
        await test_client.delete(f"/api/employees/{employee_pk}", headers=admin_headers)


async def test_mp_wechat_login_requires_enabled_employee_phone(test_client, admin_headers):
    phone = _phone()
    unknown = _phone()
    created = await test_client.post("/api/employees", headers=admin_headers, json=_employee_payload(phone))
    assert created.status_code == 200, created.text
    employee_pk = created.json()["employee"]["id"]
    try:
        started = await test_client.post("/api/mp/auth/wechat/code", json={"code": "dev-code"})
        assert started.status_code == 200, started.text
        session_id = started.json()["session_id"]

        missing = await test_client.post(
            "/api/mp/auth/wechat/phone",
            json={"session_id": session_id, "phone": unknown},
        )
        assert missing.status_code == 404, missing.text
        assert missing.json()["detail"]["error"]["code"] == "EMPLOYEE_NOT_FOUND"

        disabled = await test_client.patch(
            f"/api/employees/{employee_pk}",
            headers=admin_headers,
            json={"enabled": False},
        )
        assert disabled.status_code == 200, disabled.text
        stopped = await test_client.post(
            "/api/mp/auth/wechat/phone",
            json={"session_id": session_id, "phone": phone},
        )
        assert stopped.status_code == 403, stopped.text
        assert stopped.json()["detail"]["error"]["code"] == "EMPLOYEE_DISABLED"

        enabled = await test_client.patch(
            f"/api/employees/{employee_pk}",
            headers=admin_headers,
            json={"enabled": True},
        )
        assert enabled.status_code == 200, enabled.text
        bound = await test_client.post(
            "/api/mp/auth/wechat/phone",
            json={"session_id": session_id, "phone": phone},
        )
        assert bound.status_code == 200, bound.text
        assert bound.json()["phone_masked"] == f"{phone[:3]}****{phone[-4:]}"
        assert "phone" not in bound.json()

        spoofed = await test_client.post(
            "/api/mp/auth/confirm",
            json={"session_id": session_id, "phone": unknown},
        )
        assert spoofed.status_code == 200, spoofed.text
        assert spoofed.json()["employee"]["login_account"] == phone
    finally:
        await test_client.delete(f"/api/employees/{employee_pk}", headers=admin_headers)


async def test_mp_sms_send_validates_employee_before_sending(test_client, admin_headers):
    unknown = await test_client.post("/api/mp/auth/sms/send", json={"phone": _phone()})
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["detail"]["error"]["code"] == "EMPLOYEE_NOT_FOUND"

    disabled_phone = _phone()
    created = await test_client.post(
        "/api/employees", headers=admin_headers, json=_employee_payload(disabled_phone, enabled=False)
    )
    assert created.status_code == 200, created.text
    disabled_id = created.json()["employee"]["id"]
    pc_phone = _phone()
    created_pc = await test_client.post(
        "/api/employees", headers=admin_headers, json=_employee_payload(pc_phone, login_port=["pc"])
    )
    assert created_pc.status_code == 200, created_pc.text
    pc_id = created_pc.json()["employee"]["id"]
    try:
        disabled = await test_client.post("/api/mp/auth/sms/send", json={"phone": disabled_phone})
        assert disabled.status_code == 403, disabled.text
        assert disabled.json()["detail"]["error"]["code"] == "EMPLOYEE_DISABLED"

        forbidden = await test_client.post("/api/mp/auth/sms/send", json={"phone": pc_phone})
        assert forbidden.status_code == 403, forbidden.text
        assert forbidden.json()["detail"]["error"]["code"] == "EMPLOYEE_APP_FORBIDDEN"
    finally:
        await test_client.delete(f"/api/employees/{disabled_id}", headers=admin_headers)
        await test_client.delete(f"/api/employees/{pc_id}", headers=admin_headers)
