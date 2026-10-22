from __future__ import annotations

import pytest

from test.integration.api.test_material_library_router import _png, material_users

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

INTEGRATION_PREFIX = "/api/integrations/material-library"


async def test_integration_material_requires_auth(test_client):
    response = await test_client.get(f"{INTEGRATION_PREFIX}/items/missing-id")
    assert response.status_code == 401


async def test_integration_material_item_with_api_key(test_client, material_users):
    owner_headers = material_users["owner"]
    created = await test_client.post(
        "/api/user/apikey/",
        json={"name": "material-integration"},
        headers=owner_headers,
    )
    assert created.status_code == 200, created.text
    secret = created.json()["secret"]
    api_headers = {"Authorization": f"Bearer {secret}"}

    uploaded = await test_client.post(
        "/api/material-library/images/import",
        headers=owner_headers,
        data={"category": "product"},
        files=[("files", ("integration.png", _png(), "image/png"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    item_id = uploaded.json()["items"][0]["id"]

    detail = await test_client.get(f"{INTEGRATION_PREFIX}/items/{item_id}", headers=api_headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["id"] == item_id
    assert body["api_file_url"].endswith(f"/items/{item_id}/file")
    assert body["api_thumbnail_url"].endswith(f"/items/{item_id}/thumbnail")

    listed = await test_client.get(
        f"{INTEGRATION_PREFIX}/items?material_type=image&category=product",
        headers=api_headers,
    )
    assert listed.status_code == 200, listed.text
    assert item_id in {row["id"] for row in listed.json()["items"]}

    file_response = await test_client.get(body["api_file_url"], headers=api_headers, follow_redirects=False)
    assert file_response.status_code in {200, 302}, file_response.text

    other = await test_client.get(f"{INTEGRATION_PREFIX}/items/{item_id}", headers=material_users["other"])
    assert other.status_code == 404
