"""Sync generate_content token_budget in published workflow definitions to match code."""

from __future__ import annotations

import asyncio
from copy import deepcopy

from sqlalchemy import select

from yuxi.content.model.workflows.definition import workflow_definition_hash
from yuxi.content.v3.workflow import WORKFLOW_V3
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import ContentWorkflowVersion

TARGET_BUDGET = 48000
NODE_ID = "generate_content"


def _expected_budget_from_code() -> int:
    node = next(item for item in WORKFLOW_V3["nodes"] if item["id"] == NODE_ID)
    return int(node["token_budget"])


async def main() -> None:
    expected = _expected_budget_from_code()
    if expected != TARGET_BUDGET:
        raise RuntimeError(f"code budget is {expected}, expected {TARGET_BUDGET}")
    pg_manager.initialize()
    updated = []
    async with pg_manager.get_async_session_context() as db:
        rows = list((await db.execute(select(ContentWorkflowVersion))).scalars())
        for workflow in rows:
            definition = deepcopy(workflow.definition_json or {})
            nodes = definition.get("nodes") or []
            changed = False
            for node in nodes:
                if node.get("id") != NODE_ID:
                    continue
                if int(node.get("token_budget") or 0) == expected:
                    continue
                node["token_budget"] = expected
                changed = True
            if not changed:
                continue
            workflow.definition_json = definition
            workflow.definition_hash = workflow_definition_hash(definition)
            updated.append(f"{workflow.id}:{workflow.status}")
        await db.commit()
    print("updated", len(updated))
    for item in updated:
        print(item)


asyncio.run(main())
