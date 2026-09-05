from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


async def test_rule_draft_editing_isolated_from_published_version(test_client, admin_headers):
    versions_response = await test_client.get("/api/content/admin/rules", headers=admin_headers)
    assert versions_response.status_code == 200, versions_response.text
    versions = versions_response.json()["items"]
    assert not [item for item in versions if item["status"] == "draft"], (
        "Integration environment already contains a platform rule draft. "
        "Discard or publish it before running this test."
    )
    published = next(item for item in versions if item["status"] == "published")

    immutable_response = await test_client.put(
        f"/api/content/admin/rules/{published['id']}/bundle",
        headers=admin_headers,
        json={
            "changelog": "不应保存",
            "methods": [],
            "title_formulas": [],
            "content_formulas": [],
            "combination_rules": [],
        },
    )
    assert immutable_response.status_code == 409
    assert immutable_response.json()["detail"]["error"]["code"] == "CONTENT_RULE_VERSION_IMMUTABLE"

    draft_id = None
    try:
        create_response = await test_client.post(
            "/api/content/admin/rules/drafts",
            headers=admin_headers,
            json={"source_version_id": published["id"], "changelog": "pytest 规则编辑草稿"},
        )
        assert create_response.status_code == 200, create_response.text
        draft = create_response.json()["bundle"]
        draft_id = draft["version"]["id"]
        draft_bundle_response = await test_client.get(
            f"/api/content/admin/rules/{draft_id}/bundle",
            headers=admin_headers,
        )
        assert draft_bundle_response.status_code == 200, draft_bundle_response.text
        assert draft_bundle_response.json()["validation"]["errors"] == []
        draft["methods"].append(
            {
                "code": "M99",
                "name": "Pytest 手法",
                "method_type": "core",
                "principle": "验证草稿编辑与线上版本隔离。",
                "suitable_scenes": ["集成测试"],
                "sentence_patterns": [],
                "tag_schema": {},
                "variable_schema": [],
                "risk_rules": [],
                "enabled": True,
                "sort_order": len(draft["methods"]),
            }
        )
        save_response = await test_client.put(
            f"/api/content/admin/rules/{draft_id}/bundle",
            headers=admin_headers,
            json={
                "changelog": "pytest 新增 M99",
                "methods": draft["methods"],
                "title_formulas": draft["title_formulas"],
                "content_formulas": draft["content_formulas"],
                "combination_rules": draft["combination_rules"],
            },
        )
        assert save_response.status_code == 200, save_response.text
        saved = save_response.json()
        assert any(item["code"] == "M99" for item in saved["bundle"]["methods"])
        assert saved["validation"]["errors"] == []

        published_bundle_response = await test_client.get(
            f"/api/content/rule-versions/{published['id']}/bundle",
            headers=admin_headers,
        )
        assert published_bundle_response.status_code == 200
        published_methods = published_bundle_response.json()["bundle"]["methods"]
        assert not any(item["code"] == "M99" for item in published_methods)
    finally:
        if draft_id:
            discard_response = await test_client.delete(
                f"/api/content/admin/rules/{draft_id}", headers=admin_headers
            )
            assert discard_response.status_code == 200, discard_response.text
