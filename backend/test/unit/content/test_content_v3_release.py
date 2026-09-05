from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import yuxi.services.content_service as content_service
from yuxi.agents.middlewares.skills import SkillsMiddleware
from yuxi.agents.middlewares.token_usage import TokenUsageMiddleware
from yuxi.content.catalog import CONTENT_TYPES
from yuxi.content.rules import BODY_FORMULAS, METHODS, TITLE_FORMULAS
from yuxi.content.schemas import ContentBriefPayload, ContentRunCreate, ContentTaskCreate
from yuxi.content.v3.fixtures import load_decoration_matrix
from yuxi.content.v3.workflow import LEGACY_PLATFORM_WORKFLOW_V3_ID, PLATFORM_WORKFLOW_V3_ID


def _v3_bundle() -> dict:
    fixture = load_decoration_matrix()
    groups = [
        {
            **group,
            "schema_version": 3,
            "content_type_codes": [group["content_direction"]["code"]],
        }
        for group in fixture["groups"]
    ]
    return {
        "methods": METHODS,
        "title_formulas": TITLE_FORMULAS,
        "content_formulas": BODY_FORMULAS,
        "content_types": CONTENT_TYPES,
        "combination_rules": groups,
    }


def test_v3_rule_publish_validation_uses_candidate_pools():
    bundle = _v3_bundle()
    assert content_service.validate_rule_bundle_for_publish(bundle)["errors"] == []

    invalid = {
        **bundle,
        "combination_rules": [{**bundle["combination_rules"][0], "title_formula_candidate_codes": ["T99"]}],
    }
    errors = content_service.validate_rule_bundle_for_publish(invalid)["errors"]
    assert [item["code"] for item in errors] == ["V3_TITLE_POOL_INVALID"]


def test_task_create_rejects_legacy_version_pinning_fields():
    with pytest.raises(ValidationError):
        ContentTaskCreate(
            industry_template_id="industry-decoration-v3",
            workflow_version_id="workflow-v2",
        )


@pytest.mark.asyncio
async def test_v34_brief_compiles_without_visual_material(monkeypatch):
    task = SimpleNamespace(
        id="task-v34",
        workflow_version_id=PLATFORM_WORKFLOW_V3_ID,
        industry_template_version_id="industry-decoration-v3",
        current_stage="brief",
        selected_image_item_id=None,
        selected_poster_template_id=None,
        runtime_config_snapshot_json={"schema_version": 3},
        strategy_json={},
        to_dict=lambda: {
            "id": "task-v34",
            "selected_image_item_id": task.selected_image_item_id,
            "runtime_config_snapshot": task.runtime_config_snapshot_json,
        },
    )

    class FakeRepo:
        def __init__(self, db):
            del db

        async def get_task_for_user(self, task_id, user, for_update=False):
            del user, for_update
            return task if task_id == task.id else None

        async def get_template(self, template_id):
            return SimpleNamespace(id=template_id)

        async def track(self, *args, **kwargs):
            del args, kwargs

    class FakeDB:
        async def commit(self):
            return None

    monkeypatch.setattr(content_service, "ContentRepository", FakeRepo)
    monkeypatch.setattr(
        content_service,
        "compile_content_brief",
        lambda **kwargs: ({"form_values": kwargs["brief"].form_values}, []),
    )
    monkeypatch.setattr(content_service, "normalize_manual_evidence", lambda task_id, compiled: {"items": []})

    result = await content_service.save_content_brief(
        FakeDB(),
        SimpleNamespace(uid="user-1"),
        task.id,
        ContentBriefPayload(form_values={"brand_name": "测试品牌"}),
        compile_now=True,
    )

    assert result["compiled"] is True
    assert task.selected_image_item_id is None
    assert task.runtime_config_snapshot_json["visual_material"] is None


@pytest.mark.asyncio
async def test_v37_brief_rejects_hycanvas_template_without_image(monkeypatch):
    task = SimpleNamespace(
        id="task-v34-template-only",
        workflow_version_id=PLATFORM_WORKFLOW_V3_ID,
        industry_template_version_id="industry-decoration-v3",
        current_stage="brief",
        selected_image_item_id=None,
        selected_poster_template_id=None,
        runtime_config_snapshot_json={"schema_version": 3},
        strategy_json={},
        brief_json={},
        to_dict=lambda: {
            "id": "task-v34-template-only",
            "selected_image_item_id": task.selected_image_item_id,
            "runtime_config_snapshot": task.runtime_config_snapshot_json,
        },
    )

    class FakeRepo:
        def __init__(self, db):
            del db

        async def get_task_for_user(self, task_id, user, for_update=False):
            del user, for_update
            return task if task_id == task.id else None

        async def get_template(self, template_id):
            return SimpleNamespace(id=template_id)

        async def track(self, *args, **kwargs):
            del args, kwargs

    class FakeDB:
        async def commit(self):
            return None

    monkeypatch.setattr(content_service, "ContentRepository", FakeRepo)
    monkeypatch.setattr(
        content_service,
        "compile_content_brief",
        lambda **kwargs: ({"form_values": kwargs["brief"].form_values}, []),
    )
    with pytest.raises(HTTPException) as exc_info:
        await content_service.save_content_brief(
            FakeDB(),
            SimpleNamespace(uid="user-1"),
            task.id,
            ContentBriefPayload(
                form_values={"brand_name": "测试品牌"},
                visual_material={"hycanvas_template_id": "xiaohongshu-home-renovation"},
            ),
            compile_now=True,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"]["code"] == "CONTENT_IMAGE_MATERIAL_REQUIRED"


@pytest.mark.asyncio
async def test_v3_run_starts_from_brief_without_legacy_strategy(monkeypatch):
    task = SimpleNamespace(
        id="task-v3",
        workflow_version_id=PLATFORM_WORKFLOW_V3_ID,
        brief_json={"form_values": {"brand_name": "测试品牌"}},
        strategy_json={},
        runtime_config_snapshot_json={"schema_version": 3},
    )

    class FakeRepo:
        def __init__(self, db):
            del db

        async def get_task_for_user(self, task_id, user, for_update=False):
            del user, for_update
            return task if task_id == task.id else None

    async def fake_enqueue(db, **kwargs):
        del db
        assert kwargs["task"] is task
        return {"run_id": "run-v3", "status": "queued"}

    monkeypatch.setattr(content_service, "ContentRepository", FakeRepo)
    monkeypatch.setattr(content_service, "_enqueue_content_run", fake_enqueue)
    result = await content_service.create_content_run(
        object(),
        SimpleNamespace(uid="user-1"),
        task.id,
        ContentRunCreate(request_id="request-v3"),
    )
    assert result["run_id"] == "run-v3"


@pytest.mark.asyncio
async def test_legacy_task_cannot_start_a_new_run(monkeypatch):
    task = SimpleNamespace(
        id="task-v2",
        brief_json={"form_values": {"brand_name": "历史品牌"}},
        runtime_config_snapshot_json={"schema_version": 2},
    )

    class FakeRepo:
        def __init__(self, db):
            del db

        async def get_task_for_user(self, task_id, user, for_update=False):
            del user, for_update
            return task if task_id == task.id else None

    monkeypatch.setattr(content_service, "ContentRepository", FakeRepo)
    with pytest.raises(HTTPException) as exc_info:
        await content_service.create_content_run(
            object(),
            SimpleNamespace(uid="user-1"),
            task.id,
            ContentRunCreate(request_id="request-v2"),
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "CONTENT_LEGACY_TASK_READ_ONLY"


@pytest.mark.asyncio
async def test_previous_v3_checkpoint_is_read_only_after_new_contract_release(monkeypatch):
    task = SimpleNamespace(
        id="task-old-v3",
        workflow_version_id=LEGACY_PLATFORM_WORKFLOW_V3_ID,
        brief_json={"form_values": {"brand_name": "历史品牌"}},
        runtime_config_snapshot_json={"schema_version": 3},
    )

    class FakeRepo:
        def __init__(self, db):
            del db

        async def get_task_for_user(self, task_id, user, for_update=False):
            del user, for_update
            return task if task_id == task.id else None

    monkeypatch.setattr(content_service, "ContentRepository", FakeRepo)
    with pytest.raises(HTTPException) as exc_info:
        await content_service.create_content_run(
            object(),
            SimpleNamespace(uid="user-1"),
            task.id,
            ContentRunCreate(request_id="request-old-v3"),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "CONTENT_WORKFLOW_UPGRADE_REQUIRED"


@pytest.mark.asyncio
async def test_new_tasks_only_lock_v3_rule_pack_and_workflow(monkeypatch):
    template = SimpleNamespace(
        id="industry-decoration-v3",
        slug="decoration",
        status="published",
        default_workflow_version_id="workflow-v3",
        default_goal="brand",
        default_strategy={},
        name="装修与家居",
    )
    workflow = SimpleNamespace(
        id="workflow-v3",
        slug="enterprise-content",
        status="published",
        definition_json={"schema_version": 3, "nodes": [], "edges": []},
        definition_hash="hash-v3",
    )
    created = []

    class FakeRepo:
        def __init__(self, db):
            del db

        async def get_template(self, template_id):
            return template if template_id == template.id else None

        async def get_workflow(self, workflow_id):
            return workflow if workflow_id == workflow.id else None

        async def get_published_rule_version(self, *, schema_version):
            assert schema_version == 3
            return SimpleNamespace(id="rules-v3")

        async def get_rule_bundle(self, version_id):
            assert version_id == "rules-v3"
            return {"content_types": [{"code": "CT05", "supported_goals": ["brand"]}]}

        async def get_published_industry_pack(self, slug, *, schema_version):
            assert (slug, schema_version) == ("decoration", 3)
            return SimpleNamespace(
                id="industry-pack-decoration-v3",
                slug=slug,
                status="published",
                version=3,
                schema_version=3,
            )

        async def create_task(self, **kwargs):
            created.append(kwargs)
            return SimpleNamespace(
                id="task-v3",
                mode=kwargs["mode"],
                to_dict=lambda: {
                    "id": "task-v3",
                    "rule_version_id": kwargs["rule_version_id"],
                    "runtime_config_snapshot": kwargs["runtime_config_snapshot"],
                },
            )

        async def track(self, *args, **kwargs):
            del args, kwargs

    async def commit():
        return None

    monkeypatch.setattr(content_service, "ContentRepository", FakeRepo)
    result = await content_service.create_content_task(
        SimpleNamespace(commit=commit),
        SimpleNamespace(uid="user-1"),
        ContentTaskCreate(industry_template_id=template.id, content_goal="brand"),
    )

    assert result["task"]["runtime_config_snapshot"]["schema_version"] == 3
    assert result["task"]["runtime_config_snapshot"]["creation_mode"] == "original"
    assert created[0]["rule_version_id"] == "rules-v3"
    assert created[0]["workflow_version"].id == "workflow-v3"
    assert created[0]["industry_pack_version_id"] == "industry-pack-decoration-v3"


def test_agent_tool_and_token_limits_fail_explicitly():
    context = SimpleNamespace(_content_node_max_tool_calls=1, _content_node_tool_scope=["allowed-tool"])
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        tool_call={"name": "allowed-tool"},
    )
    SkillsMiddleware._claim_content_tool_call(request)
    with pytest.raises(RuntimeError, match="工具调用超过节点上限"):
        SkillsMiddleware._claim_content_tool_call(request)

    denied_context = SimpleNamespace(_content_node_max_tool_calls=1, _content_node_tool_scope=["allowed-tool"])
    denied_request = SimpleNamespace(
        runtime=SimpleNamespace(context=denied_context),
        tool_call={"name": "other-tool"},
    )
    with pytest.raises(RuntimeError, match="不在当前节点许可范围"):
        SkillsMiddleware._claim_content_tool_call(denied_request)
    assert not hasattr(denied_context, "_content_node_tool_calls_used")

    token_context = SimpleNamespace(_content_node_token_budget=100)
    token_request = SimpleNamespace(runtime=SimpleNamespace(context=token_context))
    with pytest.raises(RuntimeError, match="Token 使用超过节点预算"):
        TokenUsageMiddleware._enforce_content_token_budget(
            token_request,
            {"model_usage": {"output_tokens": 101}},
        )
