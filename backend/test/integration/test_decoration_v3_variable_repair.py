"""在事务内的临时配置表复现旧版缺项，验证显式修复和已发布配置保护。"""

from copy import deepcopy

import pytest
import pytest_asyncio
from sqlalchemy import text

from scripts.repair_decoration_v3_variables import PACK_ID, RULE_ID, repair
from scripts.repair_content_generation_authorization import repair as repair_authorization
from yuxi.content.v3.seed import _activate_v3_seed_data, _ensure_all_industry_packs_v3, _ensure_workflow_v3
from yuxi.content.v3.joint_workflow import (
    BLUEPRINT_FIRST_WORKFLOW_IDS,
    PLATFORM_WORKFLOW_EXPRESSION_GUIDANCE_ID,
    WORKFLOW_EXPRESSION_GUIDANCE,
    WORKFLOW_PRICE_RECOVERY,
)
from yuxi.content.v3.modular_rules import LEGACY_EXPRESSION_GUIDANCE_WORKFLOW_ID, MODULAR_WORKFLOW_IDS
from yuxi.content.model.workflows.definition import workflow_definition_hash
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import ContentTask, ContentWorkflowVersion
from yuxi.storage.postgres.models_business import Agent

TABLES = (
    "content_rule_versions",
    "content_industry_pack_versions",
    "content_variable_definitions",
    "content_industry_variable_mappings",
    "content_workflow_versions",
    "content_industry_template_versions",
    "content_tasks",
    "agents",
)


@pytest_asyncio.fixture
async def isolated_config():
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        try:
            for table in TABLES:
                await db.execute(text(f"CREATE TEMP TABLE {table} (LIKE public.{table} INCLUDING ALL) ON COMMIT DROP"))
                await db.execute(text(f"INSERT INTO {table} SELECT * FROM public.{table}"))
            # Prepare the published legacy seed state only in these temporary tables.
            await db.execute(
                text("UPDATE content_rule_versions SET status='published', tenant_id=NULL WHERE id=:rules"),
                {"rules": RULE_ID},
            )
            await db.execute(
                text(
                    "UPDATE content_industry_pack_versions SET status='published', tenant_id=NULL, "
                    "source_metadata=jsonb_set(COALESCE(source_metadata::jsonb, '{}'::jsonb), "
                    "'{source}', '\"platform-v3-seed\"')::json WHERE id=:pack"
                ),
                {"pack": PACK_ID},
            )
            await db.execute(
                text(
                    "DELETE FROM content_industry_variable_mappings WHERE industry_pack_version_id = :pack "
                    "AND field_key IN ('renovation_scene', 'quote_type')"
                ),
                {"pack": PACK_ID},
            )
            await db.execute(
                text(
                    "DELETE FROM content_variable_definitions WHERE rule_version_id = :rules "
                    "AND code IN ('quote_type', 'title_price', 'title_price_label', 'quote_block')"
                ),
                {"rules": RULE_ID},
            )
            yield db
        finally:
            await db.rollback()
    await pg_manager.close()


@pytest.mark.asyncio
async def test_repair_unblocks_seed_preserves_existing_config_and_is_idempotent(isolated_config):
    db = isolated_config
    before = {}
    for table in TABLES:
        before[table] = dict((await db.execute(text(f"SELECT id, row_to_json(t) FROM {table} t"))).all())
    with pytest.raises(RuntimeError, match="mapping-v3-decoration-renovation_scene"):
        await _ensure_all_industry_packs_v3(db)

    plan = await repair(db)
    assert {item["field_key"] for item in plan["variable_mappings"]} == {"renovation_scene", "quote_type"}
    assert {item["code"] for item in plan["variable_definitions"]} == {
        "quote_type",
        "title_price",
        "title_price_label",
        "quote_block",
    }
    for table in TABLES:
        assert dict((await db.execute(text(f"SELECT id, row_to_json(t) FROM {table} t"))).all()) == before[table]

    assert await repair(db, apply=True) == plan
    await _ensure_all_industry_packs_v3(db)
    assert await repair(db, apply=True) == {"variable_definitions": [], "variable_mappings": []}
    for table in TABLES:
        after = dict((await db.execute(text(f"SELECT id, row_to_json(t) FROM {table} t"))).all())
        assert all(after[row_id] == row for row_id, row in before[table].items())
    quote = (
        await db.execute(
            text(
                "SELECT evidence_policy, sensitivity FROM content_variable_definitions "
                "WHERE id='variable-quote_type-v3'"
            )
        )
    ).one()
    assert quote.evidence_policy == {"required": True}
    assert quote.sensitivity == "high_risk"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["tenant_id='pytest-custom'", "status='draft'", "source_metadata='{}'"])
async def test_repair_rejects_non_system_or_unpublished_pack(isolated_config, change):
    await isolated_config.execute(
        text(f"UPDATE content_industry_pack_versions SET {change} WHERE id=:pack"), {"pack": PACK_ID}
    )
    with pytest.raises(ValueError, match="仅允许修复"):
        await repair(isolated_config, apply=True)


@pytest.mark.asyncio
async def test_seed_publishes_distinct_expression_version_and_preserves_legacy_tasks(isolated_config):
    db = isolated_config
    legacy_id = LEGACY_EXPRESSION_GUIDANCE_WORKFLOW_ID
    assert legacy_id in MODULAR_WORKFLOW_IDS
    assert legacy_id in BLUEPRINT_FIRST_WORKFLOW_IDS
    previous = await db.get(ContentWorkflowVersion, legacy_id)
    if previous is None:
        previous = ContentWorkflowVersion(id=legacy_id, slug="enterprise-content", version=19, created_by="system")
        db.add(previous)
    previous.status = "published"
    previous.definition_json = deepcopy(WORKFLOW_PRICE_RECOVERY)
    previous.definition_hash = workflow_definition_hash(WORKFLOW_PRICE_RECOVERY)
    db.add(
        ContentTask(
            id="pytest-seed-history",
            name="历史版本保护测试",
            industry_template_version_id="industry-decoration-v3",
            workflow_version_id=legacy_id,
            workflow_definition_hash=previous.definition_hash,
            rule_version_id=RULE_ID,
            created_by="pytest",
            updated_by="pytest",
        )
    )
    await db.flush()
    await db.execute(
        text("DELETE FROM content_workflow_versions WHERE id=:id"),
        {"id": PLATFORM_WORKFLOW_EXPRESSION_GUIDANCE_ID},
    )
    legacy = (
        await db.execute(text("SELECT row_to_json(t) FROM content_workflow_versions t WHERE id=:id"), {"id": legacy_id})
    ).scalar_one()
    task_refs = dict((await db.execute(text("SELECT id, row_to_json(t) FROM content_tasks t"))).all())
    await repair(db, apply=True)
    await _ensure_workflow_v3(db)
    await db.flush()
    await _ensure_all_industry_packs_v3(db)
    await _activate_v3_seed_data(db)
    await db.flush()
    active = (
        await db.execute(
            text("SELECT status, definition_hash, definition_json FROM content_workflow_versions WHERE id=:id"),
            {"id": PLATFORM_WORKFLOW_EXPRESSION_GUIDANCE_ID},
        )
    ).one()
    assert active.status == "published"
    assert active.definition_json == WORKFLOW_EXPRESSION_GUIDANCE
    assert active.definition_hash == workflow_definition_hash(WORKFLOW_EXPRESSION_GUIDANCE)
    assert (
        await db.execute(text("SELECT row_to_json(t) FROM content_workflow_versions t WHERE id=:id"), {"id": legacy_id})
    ).scalar_one() == legacy
    assert dict((await db.execute(text("SELECT id, row_to_json(t) FROM content_tasks t"))).all()) == task_refs


@pytest.mark.asyncio
async def test_outline_authorization_repair_preserves_custom_context_and_higher_version(isolated_config):
    db = isolated_config
    from yuxi.content.v3.agents import CONTENT_AGENT_SPECS
    from sqlalchemy import select

    agent = (await db.execute(select(Agent).where(Agent.slug == "content-generation-agent"))).scalar_one()
    spec = next(item for item in CONTENT_AGENT_SPECS if item.slug == agent.slug)
    config = deepcopy(agent.config_json)
    config["context"]["skills"] = [skill for skill in spec.skills if skill != "content-outline-builder"]
    config["context"].update(
        model="pytest-custom-model", knowledges=["pytest-kb"], system_prompt="pytest-custom-prompt"
    )
    agent.config_json = config
    agent.config_version = 11
    await db.flush()
    before = deepcopy(agent.config_json)
    assert await repair_authorization(db) == ["content-outline-builder"]
    assert agent.config_json == before
    assert await repair_authorization(db, apply=True) == ["content-outline-builder"]
    expected = deepcopy(before)
    expected["context"]["skills"].append("content-outline-builder")
    assert agent.config_json == expected
    assert agent.config_version == 11
    assert await repair_authorization(db, apply=True) == []
