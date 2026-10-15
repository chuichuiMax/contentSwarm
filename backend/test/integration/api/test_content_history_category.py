from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.asyncio


async def _create_task(test_client, admin_headers, name: str) -> str:
    bootstrap = (await test_client.get("/api/content/bootstrap", headers=admin_headers)).json()
    template = next(item for item in bootstrap["industry_templates"] if item["slug"] == "decoration")
    created = await test_client.post(
        "/api/content/tasks",
        headers=admin_headers,
        json={
            "industry_template_id": template["id"],
            "mode": "quick",
            "content_goal": template["default_goal"],
            "name": name,
        },
    )
    assert created.status_code == 200, created.text
    return created.json()["task"]["id"]


async def _save_entry(test_client, admin_headers, task_id: str, service_entry: str) -> None:
    saved = await test_client.put(
        f"/api/content/tasks/{task_id}/brief",
        headers=admin_headers,
        json={
            "brief": {
                "user_request": f"{service_entry}分类",
                "form_values": {"mp_service_entry": service_entry, "user_request": f"{service_entry}分类"},
            }
        },
    )
    assert saved.status_code == 200, saved.text


async def test_content_history_filters_review_notes_and_decoration(test_client, admin_headers):
    suffix = uuid.uuid4().hex[:8]
    review_id = await _create_task(test_client, admin_headers, f"pytest_review_{suffix}")
    decoration_id = await _create_task(test_client, admin_headers, f"pytest_decoration_{suffix}")
    try:
        await _save_entry(test_client, admin_headers, review_id, "好评笔记")
        await _save_entry(test_client, admin_headers, decoration_id, "装修家居")

        review_list = await test_client.get(
            "/api/content/tasks",
            headers=admin_headers,
            params={"service_entry": "好评笔记", "page_size": 50},
        )
        assert review_list.status_code == 200, review_list.text
        review_items = review_list.json()["items"]
        assert review_id in {item["id"] for item in review_items}
        assert decoration_id not in {item["id"] for item in review_items}
        assert all(item["service_entry"] == "好评笔记" for item in review_items)

        decoration_list = await test_client.get(
            "/api/content/tasks",
            headers=admin_headers,
            params={"service_entry": "装修家居", "page_size": 50},
        )
        assert decoration_list.status_code == 200, decoration_list.text
        decoration_items = decoration_list.json()["items"]
        assert decoration_id in {item["id"] for item in decoration_items}
        assert review_id not in {item["id"] for item in decoration_items}
        assert all(item["service_entry"] == "装修家居" for item in decoration_items)

        invalid = await test_client.get(
            "/api/content/tasks", headers=admin_headers, params={"service_entry": "其他"}
        )
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["error"]["code"] == "CONTENT_SERVICE_ENTRY_INVALID"
    finally:
        for task_id in (review_id, decoration_id):
            deleted = await test_client.delete(f"/api/content/tasks/{task_id}", headers=admin_headers)
            assert deleted.status_code == 200, deleted.text
