"""切换计划及回滚使用独立模板，事务最终回滚，不改管理员默认值。"""

from copy import deepcopy
import uuid

import pytest

from scripts.migrate_joint_strategy import migrate
from yuxi.content.v3.joint_workflow import (
    PLATFORM_WORKFLOW_JOINT_ID, PLATFORM_WORKFLOW_BLUEPRINT_FIRST_ID, PLATFORM_WORKFLOW_PRICE_RECOVERY_ID,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import ContentWorkflowVersion, IndustryTemplateVersion


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow_id", [
    PLATFORM_WORKFLOW_JOINT_ID, PLATFORM_WORKFLOW_BLUEPRINT_FIRST_ID, PLATFORM_WORKFLOW_PRICE_RECOVERY_ID,
])
async def test_explicit_template_switch_and_rollback_preserve_history(workflow_id):
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        try:
            original = await db.get(IndustryTemplateVersion, "industry-decoration-v3")
            values = {column.name: deepcopy(getattr(original, column.name)) for column in original.__table__.columns}
            values.update(id=f"industry_test_{uuid.uuid4().hex}", name="pytest 隔离切换模板", status="draft")
            isolated = IndustryTemplateVersion(**values)
            db.add(isolated)
            await db.flush()
            before = original.default_workflow_version_id
            old_workflow = await db.get(ContentWorkflowVersion, before)
            old_definition = deepcopy(old_workflow.definition_json)
            plan = await migrate(db, template_ids=[isolated.id], workflow_id=workflow_id)
            assert isolated.default_workflow_version_id == before
            await migrate(db, plan=plan)
            assert isolated.default_workflow_version_id == workflow_id
            assert original.default_workflow_version_id == before
            assert old_workflow.definition_json == old_definition
            await migrate(db, plan=plan, rollback=True)
            assert isolated.default_workflow_version_id == before
            isolated.default_workflow_version_id = (
                PLATFORM_WORKFLOW_BLUEPRINT_FIRST_ID
                if before != PLATFORM_WORKFLOW_BLUEPRINT_FIRST_ID
                else PLATFORM_WORKFLOW_JOINT_ID
            )
            with pytest.raises(ValueError, match="已变更"):
                await migrate(db, plan=plan)
        finally:
            await db.rollback()
    await pg_manager.async_engine.dispose()
