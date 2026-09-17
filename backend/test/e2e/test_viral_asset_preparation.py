"""真实 HTTP -> ARQ -> 受管 Agent -> 已核验画像，使用独立合成资料。"""

import asyncio
import hashlib
import os
import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from yuxi.content.model.viral_assets import ViralArticleSource
from yuxi.repositories.viral_asset_repository import ViralAssetRepository
from yuxi.services.content_viral_assets import preparation_skill_hash
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentViralArticleVersion
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeFile
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_full_article_prepared_by_managed_agent_and_reused():
    uid = os.environ.get("RULE_EDITOR_TEST_UID")
    if not uid:
        pytest.skip("未配置独立 E2E 管理员 UID")
    pg_manager.initialize()
    kb_id, file_id = f"kb_test_{uuid.uuid4().hex[:16]}", f"file_test_{uuid.uuid4().hex[:16]}"
    body = (
        "厨房布局先看生活习惯。\n把洗菜、切菜、炒菜按使用顺序安排，减少来回走动。\n"
        "准备方案时，先把常用物品和使用习惯列出来，再确认收纳位置。\n你家厨房最想改善的是哪一处？"
    )
    source = ViralArticleSource(
        kb_id=kb_id,
        file_id=file_id,
        locator="article:1",
        industry_slug="decoration",
        title="厨房动线怎么安排",
        body=body,
        full_source_hash=hashlib.sha256(body.encode()).hexdigest(),
        source_file_version="e2e-v1",
        completeness="complete",
        viral_basis="这是隔离测试中的人工策展参考文章，已由测试管理员审核纳入参考池；不声称真实平台互动量。",
    )
    asset_id = None
    try:
        async with pg_manager.AsyncSession() as db:
            user = (await db.execute(select(User).where(User.uid == uid))).scalar_one()
            assert user.role == "superadmin"
            headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(user.id)})}"}
            db.add(
                KnowledgeBase(
                    kb_id=kb_id,
                    name=f"pytest viral {kb_id}",
                    kb_type="milvus",
                    created_by=uid,
                    embedding_model_spec="alibaba:text-embedding-v4",
                    additional_params={},
                )
            )
            await db.flush()
            db.add(
                KnowledgeFile(
                    file_id=file_id,
                    kb_id=kb_id,
                    filename="完整测试文章.md",
                    status="parsed",
                    content_hash="e2e-v1",
                    created_by=uid,
                )
            )
            await db.flush()
            repo = ViralAssetRepository(db)
            asset = await repo.register(source, skill_hash=preparation_skill_hash(), uid=uid)
            asset_id = asset.id
            asset.status = "failed"
            await db.commit()
            again = await repo.register(source, skill_hash=preparation_skill_hash(), uid=uid)
            assert again.id == asset_id
            await db.commit()
        await pg_manager.async_engine.dispose()
        async with httpx.AsyncClient(
            base_url=os.environ.get("TEST_BASE_URL", "http://localhost:5050"), timeout=30
        ) as client:
            response = await client.post(f"/api/content/viral-assets/{asset_id}/retry", headers=headers)
            assert response.status_code == 200, response.text
            for _ in range(85):
                response = await client.get(f"/api/content/viral-assets/{asset_id}", headers=headers)
                assert response.status_code == 200, response.text
                result = response.json()["asset"]
                if result["status"] not in {"pending", "running"}:
                    break
                await asyncio.sleep(2)
            assert result["status"] == "ready", result.get("error_message") or result["status"]
            assert result["source"]["body"] == body
            assert result["preparation"]["source_hash"] == source.source_hash
            assert result["preparation"]["reference_blueprint"]["content_block_sequence"]
            assert result["agent_run_id"]
            response = await client.post(f"/api/content/viral-assets/{asset_id}/retry", headers=headers)
            assert response.status_code == 409
    finally:
        async with pg_manager.AsyncSession() as db:
            if asset_id:
                await db.execute(delete(ContentViralArticleVersion).where(ContentViralArticleVersion.id == asset_id))
            await db.execute(delete(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
            await db.execute(delete(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
            await db.commit()
        await pg_manager.async_engine.dispose()
