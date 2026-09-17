import os
from copy import deepcopy

import pytest
from sqlalchemy import select

from test.integration.api.test_rule_library_lifecycle import rule_editor_headers  # noqa: F401
from yuxi.content.schemas import RuleDraftCreate
from yuxi.content.v3.seed import ensure_content_v3_seed_data
from yuxi.repositories.content_repository import ContentRepository
from yuxi.services.content_industry_sync import sync_industry_pack_bindings
from yuxi.services.content_service import (
    activate_content_rule_version,
    create_content_rule_draft,
    validate_content_industry_pack,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import IndustryContentPackVersion


@pytest.mark.asyncio
async def test_pack_binding_matches_published_matrix_and_retains_business_config(
    test_client,
    rule_editor_headers,  # noqa: F811
):
    response = await test_client.get("/api/content/bootstrap", headers=rule_editor_headers)
    assert response.status_code == 200, response.text
    bundle = response.json()["rule_bundle"]
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        repo = ContentRepository(db)
        user = (await db.execute(select(User).where(User.uid == os.environ["RULE_EDITOR_TEST_UID"]))).scalar_one()
        ids = await sync_industry_pack_bindings(db, bundle=bundle, uid=str(user.uid))
        assert len(ids) == 6
        assert await sync_industry_pack_bindings(db, bundle=bundle, uid=str(user.uid)) == ids
        for pack_id in ids:
            pack = await repo.get_industry_pack(pack_id)
            parent = await repo.get_industry_pack(pack.source_metadata["derived_from_pack_id"])
            snapshot = deepcopy(parent.golden_samples)
            expected = {row["id"] for row in bundle["combination_rules"] if row["industry_scope"] == [pack.slug]}
            assert len(expected) == (28 if pack.slug == "decoration" else 14)
            assert set(pack.combination_overrides) == expected
            assert {row["expected_group_id"] for row in pack.golden_samples} == expected
            assert pack.source_metadata["rule_version_id"] == bundle["version"]["id"]
            for field in (
                "lexicon_version_ids",
                "persona_templates",
                "knowledge_scope",
                "evidence_policy",
                "review_policy",
                "compliance_policy",
                "visual_policy",
                "variable_schema",
                "negative_examples",
            ):
                assert getattr(pack, field) == getattr(parent, field)
            assert parent.golden_samples == snapshot
            assert parent.status == "deprecated"
            mappings = await repo.list_industry_variable_mappings(pack.id)
            previous = await repo.list_industry_variable_mappings(parent.id)
            assert [(m.field_key, m.variable_code) for m in mappings] == [
                (m.field_key, m.variable_code) for m in previous
            ]
            report = await validate_content_industry_pack(db, user, pack.id, commit=False)
            assert report["validation"]["valid"]
            assert report["evaluation"]["metrics"]["group_coverage"] == 1
            assert not report.get("regression", {}).get("passed"), "结构同步不能冒充完整生成回归"
        await ensure_content_v3_seed_data(db)
        # 种子入口会提交，当前绑定应已由正式同步完成；此处只验证幂等重启不恢复旧版。
        active = list(
            (
                await db.execute(
                    select(IndustryContentPackVersion).where(
                        IndustryContentPackVersion.schema_version == 3, IndustryContentPackVersion.status == "published"
                    )
                )
            ).scalars()
        )
        assert {item.id for item in active} == set(ids)
    await pg_manager.async_engine.dispose()


@pytest.mark.asyncio
async def test_rule_publication_syncs_packs_and_rollback_reuses_previous_versions(monkeypatch):
    pg_manager.initialize()
    try:
        async with pg_manager.AsyncSession() as db:
            # 全程单事务；服务的提交只 flush，测试结束统一回滚，线上规则始终保持原版本。
            monkeypatch.setattr(db, "commit", db.flush)
            repo = ContentRepository(db)
            user = (await db.execute(select(User).where(User.uid == os.environ["RULE_EDITOR_TEST_UID"]))).scalar_one()
            original = await repo.get_published_rule_version_for_update(schema_version=3)
            original_id = original.id
            previous = {item["id"] for item in await repo.list_industry_packs() if item["schema_version"] == 3}
            draft = await create_content_rule_draft(
                db, user, RuleDraftCreate(source_version_id=original_id, changelog="pytest transaction only")
            )
            draft_id = draft["bundle"]["version"]["id"]
            await activate_content_rule_version(db, user, draft_id, rollback=False, note="pytest")
            current = [item for item in await repo.list_industry_packs() if item["schema_version"] == 3]
            assert len(current) == 6
            assert not previous.intersection(item["id"] for item in current)
            assert all(item["source_metadata"]["rule_version_id"] == draft_id for item in current)
            await activate_content_rule_version(db, user, original_id, rollback=True, note="pytest rollback")
            restored = {item["id"] for item in await repo.list_industry_packs() if item["schema_version"] == 3}
            assert restored == previous
            await db.rollback()
    finally:
        await pg_manager.async_engine.dispose()
