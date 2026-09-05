from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.asyncio


def _payload(**overrides):
    suffix = uuid.uuid4().hex[:8]
    data = {
        "name": f"pytest账号_{suffix}",
        "account_id": f"id{suffix}",
        "account_type": "enterprise",
        "following_count": 12,
        "follower_count": 345,
        "likes_count": 67,
        "works_count": 8,
        "enabled": True,
    }
    data.update(overrides)
    return data


async def test_account_crud_search_and_toggle(test_client, admin_headers):
    created = await test_client.post("/api/accounts", headers=admin_headers, json=_payload())
    assert created.status_code == 200, created.text
    account = created.json()["account"]
    account_pk = account["id"]
    account_id = account["account_id"]

    try:
        listed = await test_client.get("/api/accounts", headers=admin_headers)
        assert listed.status_code == 200, listed.text
        ids = [item["id"] for item in listed.json()["accounts"]]
        assert account_pk in ids

        searched = await test_client.get(
            "/api/accounts",
            headers=admin_headers,
            params={"keyword": account["name"]},
        )
        assert searched.status_code == 200, searched.text
        assert searched.json()["accounts"][0]["id"] == account_pk

        duplicate = await test_client.post(
            "/api/accounts",
            headers=admin_headers,
            json=_payload(account_id=account_id, name="重复ID"),
        )
        assert duplicate.status_code == 409, duplicate.text
        assert duplicate.json()["detail"]["error"]["code"] == "ACCOUNT_ID_EXISTS"

        toggled = await test_client.patch(
            f"/api/accounts/{account_pk}",
            headers=admin_headers,
            json={"enabled": False},
        )
        assert toggled.status_code == 200, toggled.text
        assert toggled.json()["account"]["enabled"] is False
        assert toggled.json()["account"]["account_type"] == "enterprise"

        updated = await test_client.patch(
            f"/api/accounts/{account_pk}",
            headers=admin_headers,
            json={"account_type": "personal", "follower_count": 999},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["account"]["account_type"] == "personal"
        assert updated.json()["account"]["follower_count"] == 999
    finally:
        deleted = await test_client.delete(f"/api/accounts/{account_pk}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text

    missing = await test_client.get("/api/accounts", headers=admin_headers)
    assert missing.status_code == 200, missing.text
    assert account_pk not in [item["id"] for item in missing.json()["accounts"]]

    gone = await test_client.patch(
        f"/api/accounts/{account_pk}",
        headers=admin_headers,
        json={"enabled": True},
    )
    assert gone.status_code == 404
    assert gone.json()["detail"]["error"]["code"] == "ACCOUNT_NOT_FOUND"
