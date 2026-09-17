"""显式切换指定行业模板；默认生成可审阅计划，保留历史任务及管理员版本。

uv run python scripts/migrate_joint_strategy.py --template-id industry-decoration-v3 --plan /tmp/joint-plan.json
uv run python scripts/migrate_joint_strategy.py --apply /tmp/joint-plan.json
uv run python scripts/migrate_joint_strategy.py --rollback /tmp/joint-plan.json
"""

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from yuxi.content.model.workflows.definition import WorkflowCatalog, WorkflowDefinitionPolicy, workflow_definition_hash
from yuxi.content.v3.joint_workflow import (
    PLATFORM_WORKFLOW_JOINT_ID,
    WORKFLOW_JOINT,
    PLATFORM_WORKFLOW_BLUEPRINT_FIRST_ID,
    WORKFLOW_BLUEPRINT_FIRST,
    PLATFORM_WORKFLOW_PRICE_RECOVERY_ID, WORKFLOW_PRICE_RECOVERY,
)
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.agents.skills.repository import SkillRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import ContentWorkflowVersion, IndustryTemplateVersion
from yuxi.utils.datetime_utils import utc_now_naive


async def migrate(db, *, template_ids=None, plan=None, rollback=False, workflow_id=PLATFORM_WORKFLOW_JOINT_ID):
    if plan:
        workflow_id = plan["target_id"]
    definition = {
        PLATFORM_WORKFLOW_JOINT_ID: WORKFLOW_JOINT,
        PLATFORM_WORKFLOW_BLUEPRINT_FIRST_ID: WORKFLOW_BLUEPRINT_FIRST,
        PLATFORM_WORKFLOW_PRICE_RECOVERY_ID: WORKFLOW_PRICE_RECOVERY,
    }[workflow_id]
    target = (
        await db.execute(
            select(ContentWorkflowVersion)
            .where(
                ContentWorkflowVersion.id == workflow_id,
            )
            .with_for_update()
        )
    ).scalar_one()
    expected_hash = workflow_definition_hash(definition)
    if target.definition_hash != expected_hash or workflow_definition_hash(target.definition_json) != expected_hash:
        raise ValueError("目标工作流与已验证定义不一致；请先核对版本，禁止覆盖管理员修改")
    if plan:
        if plan["target_id"] != target.id or plan["definition_hash"] != expected_hash:
            raise ValueError("切换计划版本不匹配，请重新生成计划")
        template_ids = [item["id"] for item in plan["templates"]]
    templates = list(
        (
            await db.execute(
                select(IndustryTemplateVersion)
                .where(
                    IndustryTemplateVersion.id.in_(template_ids),
                )
                .order_by(IndustryTemplateVersion.id)
                .with_for_update()
            )
        ).scalars()
    )
    if len(templates) != len(set(template_ids)):
        raise ValueError("指定的行业模板不存在")
    if plan is None:
        return {
            "target_id": target.id,
            "definition_hash": expected_hash,
            "created_at": utc_now_naive().isoformat(),
            "templates": [
                {"id": item.id, "name": item.name, "previous_workflow_id": item.default_workflow_version_id}
                for item in templates
            ],
        }
    previous = {item["id"]: item["previous_workflow_id"] for item in plan["templates"]}
    for template in templates:
        expected = target.id if rollback else previous[template.id]
        if template.default_workflow_version_id != expected:
            raise ValueError(f"模板 {template.id} 在计划后已变更，停止切换")
    if not rollback:
        nodes = target.definition_json["nodes"]
        agents = await AgentRepository(db).list_by_slugs(
            sorted({n["agent_slug"] for n in nodes if n["type"] == "agent"})
        )
        skills = await SkillRepository(db).list_by_slugs(
            sorted({s for n in nodes for s in n.get("required_skills", [])})
        )
        WorkflowDefinitionPolicy.validate(
            target.definition_json,
            catalog=WorkflowCatalog(
                agents=frozenset(item.slug for item in agents if item.enabled),
                skills=frozenset(item.slug for item in skills if item.enabled),
            ),
        )
        target.status = "published"
        target.published_at = target.published_at or utc_now_naive()
    for template in templates:
        template.default_workflow_version_id = previous[template.id] if rollback else target.id
    # 回滚只恢复新任务入口。已创建任务仍需要目标版本，不能删除或改写其定义。
    return {"action": "rollback" if rollback else "apply", "template_ids": template_ids, "target_id": target.id}


async def main(args):
    pg_manager.initialize()
    try:
        async with pg_manager.AsyncSession() as db:
            if args.apply or args.rollback:
                plan = json.loads(Path(args.apply or args.rollback).read_text())
                result = await migrate(db, plan=plan, rollback=bool(args.rollback))
                await db.commit()
            else:
                if not args.template_id or not args.plan:
                    raise ValueError("生成计划需要 --template-id 和 --plan")
                result = await migrate(db, template_ids=args.template_id, workflow_id=args.workflow_id)
                # 排他创建，避免覆盖恢复点；没有修改数据库。
                with Path(args.plan).open("x", encoding="utf-8") as file:
                    json.dump(result, file, ensure_ascii=False, indent=2)
                await db.rollback()
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await pg_manager.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template-id", action="append")
    parser.add_argument("--plan")
    parser.add_argument(
        "--workflow-id",
        default=PLATFORM_WORKFLOW_JOINT_ID,
        choices=[PLATFORM_WORKFLOW_JOINT_ID, PLATFORM_WORKFLOW_BLUEPRINT_FIRST_ID, PLATFORM_WORKFLOW_PRICE_RECOVERY_ID],
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply")
    mode.add_argument("--rollback")
    asyncio.run(main(parser.parse_args()))
