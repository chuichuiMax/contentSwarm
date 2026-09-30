from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_remote_material_config_returns_redacted_state(
    test_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
):
    response = await test_client.get("/api/material-library/remote-config", headers=admin_headers)

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "base_url",
        "base_url_editable",
        "configured",
        "source",
        "can_manage",
        "verification_status",
        "verified_at",
    }
    assert "username" not in payload
    assert "password" not in payload
