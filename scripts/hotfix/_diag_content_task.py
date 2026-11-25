"""Inspect a content task and related runs (run inside api-dev)."""
from __future__ import annotations

import asyncio
import json
import sys

from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import AgentRun
from yuxi.storage.postgres.models_content import ContentArtifact, ContentTask


async def main(task_id: str) -> None:
    pg_manager.initialize()
    async with pg_manager.get_async_session_context() as db:
        task = await db.get(ContentTask, task_id)
        if task is None:
            print("task_not_found", task_id)
            return
        brief = task.brief_json or {}
        review = task.review_json or {}
        selected = task.selected_title_json or {}
        print(
            "task",
            json.dumps(
                {
                    "id": task.id,
                    "name": task.name,
                    "status": task.status,
                    "stage": task.current_stage,
                    "content_type_code": task.content_type_code,
                    "mode": task.mode,
                    "latest_run_id": task.latest_run_id,
                    "brief_keys": list(brief.keys())[:25],
                    "brief_body_len": len(str(brief.get("body") or brief.get("content_body") or "")),
                    "brief_body_preview": str(brief.get("body") or brief.get("content_body") or "")[:600],
                    "selected_title": selected,
                    "review_keys": list(review.keys())[:20],
                    "error": task.error_json,
                    "runtime": task.runtime_config_snapshot_json,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        artifact = await db.scalar(select(ContentArtifact).where(ContentArtifact.task_id == task_id))
        if artifact:
            print(
                "artifact",
                json.dumps(
                    {
                        "title_len": len(artifact.title or ""),
                        "body_len": len(artifact.body or ""),
                        "title": (artifact.title or "")[:200],
                        "body": (artifact.body or "")[:2000],
                        "topics": artifact.topics,
                        "review_snapshot": artifact.review_snapshot,
                        "strategy_snapshot_keys": list((artifact.strategy_snapshot or {}).keys())[:20],
                        "compliance_snapshot": artifact.compliance_snapshot,
                        "runtime_config_snapshot": artifact.runtime_config_snapshot,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        runs = (
            await db.scalars(
                select(AgentRun)
                .where(AgentRun.thread_id == task_id)
                .order_by(AgentRun.created_at.desc())
                .limit(8)
            )
        ).all()
        print("runs", len(runs))
        for run in runs:
            payload = run.input_payload or {}
            print(
                "run",
                run.id,
                run.status,
                run.agent_id,
                run.error_type,
                (run.error_message or "")[:300],
                "payload_keys",
                list(payload.keys())[:20],
            )
            for key in ("body", "title", "content_type_code", "instruction", "brief"):
                if key in payload:
                    val = payload[key]
                    if isinstance(val, str):
                        print(f" payload.{key}_len", len(val), "preview", val[:400])


if __name__ == "__main__":
    tid = sys.argv[1] if len(sys.argv) > 1 else ""
    if not tid:
        raise SystemExit("usage: task_id")
    asyncio.run(main(tid))