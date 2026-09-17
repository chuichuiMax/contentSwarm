"""真实行业选择、HTTP匹配和数据库公式锁定回归；不启动模型或队列。"""

import json
import os
import uuid

import pytest

from test.integration.api.test_rule_library_lifecycle import rule_editor_headers  # noqa: F401
from yuxi.content.catalog import CONTENT_TYPES, INDUSTRY_CONFIG
from yuxi.content.control.workflow import deterministic_node
from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import AgentRun


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", list(INDUSTRY_CONFIG))
async def test_selected_industry_matches_its_matrix_and_locks_correct_formula(
    test_client,
    rule_editor_headers,  # noqa: F811
    monkeypatch,
    slug,
):
    async def suppress_test_broadcast(*_args, **_kwargs):
        pass

    monkeypatch.setattr(deterministic_node, "append_run_stream_event", suppress_test_broadcast)
    headers = rule_editor_headers
    response = await test_client.get("/api/content/bootstrap", headers=headers)
    assert response.status_code == 200, response.text
    bootstrap = response.json()
    template = next(item for item in bootstrap["industry_templates"] if item["slug"] == slug)
    bundle = bootstrap["rule_bundle"]
    assert len([row for row in bundle["combination_rules"] if row["industry_scope"] == [slug]]) == (
        28 if slug == "decoration" else 14
    )
    response = await test_client.post(
        "/api/content/tasks",
        headers=headers,
        json={
            "industry_template_id": template["id"],
            "mode": "quick",
            "content_goal": "brand",
            "content_type_code": "CT01",
            "name": f"pytest 行业矩阵验证 {slug}",
        },
    )
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["rule_version_id"] == bundle["version"]["id"]
    assert slug in task["industry_pack_version_id"]
    pack = next(item for item in bootstrap["industry_packs"] if item["id"] == task["industry_pack_version_id"])
    assert pack["source_metadata"]["rule_version_id"] == task["rule_version_id"]
    try:
        brief = {"audience": ["测试目标人群"], "form_values": {"brand_name": "pytest", "process": ["测试流程"]}}
        response = await test_client.put(
            f"/api/content/tasks/{task['id']}/brief", headers=headers, json={"brief": brief}
        )
        assert response.status_code == 200, response.text
        groups = {row["id"]: row for row in bundle["combination_rules"]}
        pg_manager.initialize()
        async with pg_manager.AsyncSession() as db:
            run_id = str(uuid.uuid4())
            db.add(
                AgentRun(
                    id=run_id,
                    thread_id=task["id"],
                    agent_id="content-workflow",
                    uid=os.environ["RULE_EDITOR_TEST_UID"],
                    request_id=str(uuid.uuid4()),
                    status="completed",
                )
            )
            await db.flush()
            for direction in CONTENT_TYPES:
                response = await test_client.post(
                    f"/api/content/tasks/{task['id']}/strategy/recommend-v3",
                    headers=headers,
                    json={"content_direction_code": direction["code"]},
                )
                assert response.status_code == 200, response.text
                preview = response.json()
                assert preview["industry_pack_version_id"] == task["industry_pack_version_id"]
                eligible = preview["decision"]["eligible_groups"]
                assert len(eligible) == (4 if slug == "decoration" else 2)
                for item in eligible:
                    group = groups[item["group_code"]]
                    assert group["industry_scope"] == [slug]
                    assert group["content_type_codes"] == [direction["code"]]
                    if slug != "decoration":
                        assert group["source_metadata"]["source"] == "industry-matrix-editorial"
                    selection = {
                        "selected_direction_code": direction["code"],
                        "selected_group_id": group["id"],
                        "creation_method_codes": [m["method_code"] for m in group["method_members"]],
                        "title_formula_code": item["title_formula_candidate_codes"][0],
                        "body_formula_code": item["body_formula_candidate_codes"][0],
                    }
                    locked = await V3DeterministicNodeHandler._lock_creation_strategy(
                        db=db,
                        state={
                            "task_id": task["id"],
                            "run_id": run_id,
                            "uid": os.environ["RULE_EDITOR_TEST_UID"],
                            "strategy_selection": selection,
                            "content_brief": brief,
                        },
                        node_run_id=None,
                    )
                    snapshot = locked["strategy_snapshot"]
                    assert snapshot["selected_group_id"] == group["id"]
                    assert snapshot["title_formula"]["code"] == selection["title_formula_code"]
                    assert snapshot["body_formula"]["code"] == selection["body_formula_code"]
                    assert len(snapshot["body_formula"]["structure_schema"]) == 4
                    if slug != "decoration":
                        assert (
                            snapshot["body_formula"]["source_content"]["scenario_description"]
                            == group["scenario_description"]
                        )
                        assert not any(
                            word in json.dumps(snapshot["body_formula"], ensure_ascii=False)
                            for word in ("工长", "户型", "装修", "施工")
                        )
            await db.rollback()
    finally:
        await pg_manager.async_engine.dispose()
        response = await test_client.delete(f"/api/content/tasks/{task['id']}", headers=headers)
        assert response.status_code == 200, response.text
