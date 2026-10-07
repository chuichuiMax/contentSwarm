"""显式补齐旧系统内容创作 Agent 的大纲 Skill；默认检查，--apply 才提交。"""

import argparse
import asyncio
from copy import deepcopy

from sqlalchemy import select

from yuxi.content.v3.agents import CONTENT_AGENT_SPECS, validate_existing_content_agent
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Agent


async def repair(db, *, apply=False):
    spec = next(item for item in CONTENT_AGENT_SPECS if item.slug == "content-generation-agent")
    agent = (await db.execute(select(Agent).where(Agent.slug == spec.slug).with_for_update())).scalar_one()
    context = (agent.config_json or {}).get("context")
    if agent.created_by != "system" or not isinstance(context, dict):
        raise ValueError("仅允许修复系统内容创作 Agent，请核对配置来源")
    missing = set(spec.skills) - set(context.get("skills") or [])
    if not missing:
        validate_existing_content_agent(agent, spec)
        return []
    if missing != {"content-outline-builder"}:
        raise ValueError("缺项超出大纲 Skill 迁移范围，请核对授权配置")
    candidate = deepcopy(agent.config_json)
    candidate["context"]["skills"] = [*context.get("skills", []), "content-outline-builder"]
    previous = agent.config_json
    agent.config_json = candidate
    try:
        validate_existing_content_agent(agent, spec)
    finally:
        if not apply:
            agent.config_json = previous
    if apply:
        await db.flush()
    return ["content-outline-builder"]


async def main(apply):
    pg_manager.initialize()
    try:
        async with pg_manager.AsyncSession() as db:
            added = await repair(db, apply=apply)
            print(f"content-generation-agent: 新增 Skill {added}")
            if apply:
                await db.commit()
                print("授权已补齐；其他配置保持不变")
            else:
                await db.rollback()
                print("仅检查，未提交变更")
    finally:
        await pg_manager.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args().apply))
