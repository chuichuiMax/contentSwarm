from types import SimpleNamespace

import pytest

from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler


@pytest.mark.asyncio
async def test_new_decoration_pack_still_requires_formula_lexicons():
    class EmptyRows:
        def all(self):
            return []

    class Database:
        async def execute(self, _query):
            return EmptyRows()

    with pytest.raises(ValueError, match="必需词库不可用"):
        await V3DeterministicNodeHandler._load_formula_lexicons(
            db=Database(),
            node_run_id="test",
            state={
                "industry_pack_version_id": "industry-pack-decoration-v4",
                "strategy_snapshot": {"title_formula": {"code": "T01"}, "body_formula": {"code": "C02"}},
            },
        )


@pytest.mark.asyncio
async def test_runtime_snapshot_keeps_deprecated_pack_for_existing_task(monkeypatch):
    from yuxi.repositories.content_repository import ContentRepository

    pack = {"id": "industry-pack-decoration-v3", "status": "deprecated", "evidence_policy": {"required": True}}
    task = SimpleNamespace(
        workflow_definition_hash="frozen-hash",
        industry_template_version_id="template",
        industry_pack_version_id=pack["id"],
        channel_profile_version_id="channel",
        workflow_version_id="workflow",
        rule_version_id="content-rules-platform-v3",
        persona_profile_version_id=None,
        tenant_id=None,
        mode="quick",
    )

    class Database:
        async def get(self, _model, _id):
            return task

    async def get_template(self, _id):
        return SimpleNamespace(slug="decoration")

    async def list_packs(self, *, published_only=True):
        return [] if published_only else [pack]

    async def empty_list(self):
        return []

    monkeypatch.setattr(ContentRepository, "get_template", get_template)
    monkeypatch.setattr(ContentRepository, "list_industry_packs", list_packs)
    monkeypatch.setattr(ContentRepository, "list_channel_profiles", empty_list)
    monkeypatch.setattr(ContentRepository, "list_compliance_policies", empty_list)
    result = await V3DeterministicNodeHandler._compile_runtime_snapshot(
        db=Database(), state={"task_id": "historical-task"}, node_run_id="test"
    )
    assert result["industry_pack"] == pack
    assert result["runtime_config_snapshot"]["industry_pack_version_id"] == pack["id"]
    assert result["runtime_config_snapshot"]["rule_version_id"] == "content-rules-platform-v3"
