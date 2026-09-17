"""真实 PostgreSQL 验证文章身份、权限、行业、版本及有界卡片检索。"""

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from test.unit.content.test_viral_asset_preparation import prepared, source
from yuxi.repositories.viral_asset_repository import ViralAssetRepository
from yuxi.services import content_viral_assets as service
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import ContentViralArticleVersion
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeFile


@pytest.mark.asyncio
async def test_article_card_scope_freshness_and_history(monkeypatch):
    uid = os.environ.get("RULE_EDITOR_TEST_UID")
    if not uid:
        pytest.skip("需要隔离测试管理员")
    pg_manager.initialize()
    kb_id, file_id = f"kb_test_{uuid.uuid4().hex[:16]}", f"file_test_{uuid.uuid4().hex[:16]}"
    user = SimpleNamespace(uid=uid)
    monkeypatch.setattr(service, "accessible_asset_kbs", AsyncMock(return_value=[kb_id]))
    try:
        async with pg_manager.AsyncSession() as db:
            db.add(
                KnowledgeBase(
                    kb_id=kb_id,
                    name="pytest viral retrieval",
                    kb_type="milvus",
                    created_by=uid,
                    embedding_model_spec="alibaba:text-embedding-v4",
                    additional_params={},
                )
            )
            await db.flush()
            file = KnowledgeFile(
                file_id=file_id,
                kb_id=kb_id,
                filename="多篇参考.md",
                status="parsed",
                content_hash="file-version-1",
                created_by=uid,
            )
            db.add(file)
            await db.flush()
            repo = ViralAssetRepository(db)
            articles = [source(kb_id=kb_id, file_id=file_id, locator=f"row:{index}") for index in range(3)]
            assets = []
            for article in articles:
                asset = await repo.register(article, skill_hash=service.preparation_skill_hash(), uid=uid)
                asset.status, asset.prepared_json = "ready", prepared(article)
                assets.append(asset)
            await db.commit()
            same = await repo.register(articles[0], skill_hash=service.preparation_skill_hash(), uid=uid)
            assert same.id == assets[0].id
            assert len({item.article_id for item in assets}) == 3
            cards = await service.search_ready_viral_assets(
                db, user, industry_slug="decoration", query="厨房布局", kb_ids=[kb_id], limit=2
            )
            assert len(cards) == 2
            assert all("body" not in card and "reference_blueprint" not in card for card in cards)
            assert all("anchors" not in card["reference_card"] for card in cards)
            assert await service.search_ready_viral_assets(
                db, user, industry_slug="decoration", query="互动", kb_ids=[kb_id], limit=2
            ) == []
            structural = await service.search_ready_viral_assets(
                db, user, industry_slug="decoration", query="互动", kb_ids=[kb_id], limit=2,
                include_structure=True,
            )
            assert len(structural) == 2
            assert structural[0]["structure_preview"]["content_block_sequence"] == ["习惯", "动线", "互动"]
            assert "reference_blueprint" not in structural[0] and "body" not in structural[0]
            assert (
                await service.search_ready_viral_assets(
                    db, user, industry_slug="education", query="厨房", kb_ids=[kb_id], limit=2
                )
                == []
            )
            assert (
                await service.search_ready_viral_assets(
                    db, user, industry_slug="decoration", query="厨房", kb_ids=["forbidden"], limit=2
                )
                == []
            )
            monkeypatch.setattr(service, "accessible_asset_kbs", AsyncMock(return_value=[]))
            with pytest.raises(HTTPException) as denied:
                await service.require_asset(db, user, assets[0].id)
            assert denied.value.status_code == 404
            monkeypatch.setattr(service, "accessible_asset_kbs", AsyncMock(return_value=[kb_id]))
            file.content_hash = "updated-file-version"
            await db.commit()
            assert (
                await service.search_ready_viral_assets(
                    db, user, industry_slug="decoration", query="厨房", kb_ids=[kb_id], limit=2
                )
                == []
            )
            result = await service.list_viral_assets(db, user)
            assert all(item["status"] == "invalidated" for item in result["items"])
            await db.delete(file)
            await db.commit()
            await db.refresh(assets[0])
            assert assets[0].source_json["body"] == articles[0].body
    finally:
        async with pg_manager.AsyncSession() as db:
            await db.execute(delete(ContentViralArticleVersion).where(ContentViralArticleVersion.kb_id == kb_id))
            await db.execute(delete(KnowledgeFile).where(KnowledgeFile.kb_id == kb_id))
            await db.execute(delete(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
            await db.commit()
        await pg_manager.async_engine.dispose()
