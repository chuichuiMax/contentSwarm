"""真实数据库事务验证默认入口升级，结束时回滚所有测试变更。"""

from sqlalchemy import select
import pytest

from yuxi.content.v3.joint_workflow import PLATFORM_WORKFLOW_PRICE_RECOVERY_ID
from yuxi.content.v3.seed import _activate_v3_seed_data
from yuxi.content.v3.workflow import PLATFORM_WORKFLOW_V3_ID
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import ContentTask, ContentWorkflowVersion, IndustryTemplateVersion


@pytest.mark.asyncio
async def test_existing_default_upgrade_preserves_historical_task_versions():
    pg_manager.initialize()
    try:
        async with pg_manager.AsyncSession() as db:
            try:
                template = await db.get(IndustryTemplateVersion, "industry-decoration-v3")
                template.default_workflow_version_id = PLATFORM_WORKFLOW_V3_ID
                target = await db.get(ContentWorkflowVersion, PLATFORM_WORKFLOW_PRICE_RECOVERY_ID)
                target.status = "draft"
                history_query = select(
                    ContentTask.id,
                    ContentTask.workflow_version_id,
                    ContentTask.workflow_definition_hash,
                    ContentTask.runtime_config_snapshot_json,
                ).order_by(ContentTask.id)
                history = (await db.execute(history_query)).all()
                for _ in range(2):
                    await _activate_v3_seed_data(db)
                    await db.flush()
                    assert template.default_workflow_version_id == PLATFORM_WORKFLOW_PRICE_RECOVERY_ID
                    assert target.status == "published"
                    assert (await db.execute(history_query)).all() == history
            finally:
                await db.rollback()
    finally:
        await pg_manager.async_engine.dispose()
