"""发布隔离候选；验收后可显式切换默认，或按发布记录回退。"""

import argparse
import asyncio
from copy import deepcopy
import json

from yuxi.content.model.workflows.definition import WorkflowDefinitionPolicy, workflow_definition_hash
from yuxi.content.v3.agents import ensure_content_v3_agents
from yuxi.content.v3.joint_workflow import WORKFLOW_SINGLE_BLUEPRINT
from yuxi.content.v3.modular_rules import SINGLE_BLUEPRINT_WORKFLOW_ID
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import ContentWorkflowVersion, IndustryTemplateVersion
from yuxi.utils.datetime_utils import utc_now_naive


async def publish(default_target: str | None = None):
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        await ensure_content_v3_agents(db)
        WorkflowDefinitionPolicy.validate(WORKFLOW_SINGLE_BLUEPRINT)
        workflow = await db.get(ContentWorkflowVersion, SINGLE_BLUEPRINT_WORKFLOW_ID)
        expected_hash = workflow_definition_hash(WORKFLOW_SINGLE_BLUEPRINT)
        if workflow and workflow.definition_hash != expected_hash:
            raise ValueError("已发布候选结构已改变，请使用新版本 ID")
        if workflow is None:
            db.add(
                ContentWorkflowVersion(
                    id=SINGLE_BLUEPRINT_WORKFLOW_ID,
                    slug="single-blueprint",
                    version=4,
                    schema_version=3,
                    status="published",
                    definition_json=WORKFLOW_SINGLE_BLUEPRINT,
                    definition_hash=expected_hash,
                    input_schema={"type": "ContentBrief", "version": 3},
                    output_schema={"type": "ContentArtifact", "version": 3},
                    created_by="system",
                    published_at=utc_now_naive(),
                )
            )
            await db.flush()
        template_id = "industry-decoration-single-blueprint-candidate"
        candidate = await db.get(IndustryTemplateVersion, template_id)
        if candidate is None:
            source = await db.get(IndustryTemplateVersion, "industry-decoration-v3")
            values = {
                column.name: deepcopy(getattr(source, column.name))
                for column in IndustryTemplateVersion.__table__.columns
                if column.name not in {"id", "created_at", "published_at"}
            }
            values.update(
                version=100,
                name="装修单蓝图候选验证",
                created_by="candidate-test",
                default_workflow_version_id=SINGLE_BLUEPRINT_WORKFLOW_ID,
            )
            db.add(IndustryTemplateVersion(id=template_id, **values))
        else:
            candidate.status = "published"
            candidate.default_workflow_version_id = SINGLE_BLUEPRINT_WORKFLOW_ID
        result = {"candidate_template_id": template_id, "workflow_hash": expected_hash}
        if default_target:
            target = await db.get(ContentWorkflowVersion, default_target)
            if target is None or target.status != "published":
                raise ValueError("默认工作流必须是已发布版本")
            default_template = await db.get(IndustryTemplateVersion, "industry-decoration-v3")
            result["previous_default"] = default_template.default_workflow_version_id
            default_template.default_workflow_version_id = default_target
            result["current_default"] = default_target
        await db.commit()
        print(json.dumps(result, ensure_ascii=False))
    await pg_manager.async_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--activate", action="store_true", help="验收后切换默认模板到候选工作流")
    actions.add_argument("--rollback-to", help="回退到发布记录中的 previous_default 工作流 ID")
    args = parser.parse_args()
    asyncio.run(publish(SINGLE_BLUEPRINT_WORKFLOW_ID if args.activate else args.rollback_to))
