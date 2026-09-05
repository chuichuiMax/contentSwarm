from __future__ import annotations

from datetime import timedelta
from typing import Any

from langgraph.types import interrupt
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.services.run_queue_service import append_run_stream_event
from yuxi.utils.datetime_utils import utc_now_naive


class ExternalWaitNodeHandler:
    """把外部 CoverJob 转成可恢复的工作流等待，不在 Agent 节点轮询。"""

    async def execute(
        self,
        *,
        db: AsyncSession,
        node: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        if node.get("external_job_type") != "content_cover":
            raise ValueError(f"不支持的外部任务类型: {node.get('external_job_type')}")
        job_id = str((state.get("cover_job") or {}).get("cover_job_id") or "")
        if not job_id:
            raise ValueError("外部等待节点缺少 cover_job_id")
        repo = ContentCoverRepository(db)
        job = await repo.get_job(job_id)
        if job is None or job.content_task_id != state["task_id"] or job.owner_uid != state["uid"]:
            raise ValueError("CoverJob 不存在或不属于当前内容任务")

        timeout_seconds = int(node["timeout_seconds"])
        if (
            job.status not in {"succeeded", "failed", "cancelled"}
            and job.created_at
            and (utc_now_naive() - job.created_at > timedelta(seconds=timeout_seconds))
        ):
            raise TimeoutError("CoverJob 等待超时")

        while job.status not in {"succeeded", "failed", "cancelled"}:
            expected_run_id = state.get("resume_parent_run_id") or state["run_id"]
            answer = interrupt(
                {
                    "interrupt_type": "external_wait",
                    "task_id": state["task_id"],
                    "run_id": expected_run_id,
                    "node_id": node["id"],
                    "expected_state_version": int(state.get("state_version") or 0),
                    "cover_job_id": job.id,
                    "status": job.status,
                    "progress": job.progress,
                }
            )
            expected = {
                "run_id": expected_run_id,
                "node_id": node["id"],
                "expected_state_version": int(state.get("state_version") or 0),
                "cover_job_id": job.id,
            }
            if not isinstance(answer, dict) or any(answer.get(key) != value for key, value in expected.items()):
                raise ValueError("CoverJob 恢复请求已过期或目标不匹配")
            await db.refresh(job)

        if job.status != "succeeded":
            await append_run_stream_event(
                state["run_id"],
                "content.cover.failed",
                {
                    "task_id": state["task_id"],
                    "parent_run_id": state["run_id"],
                    "node_id": node["id"],
                    "cover_job_id": job.id,
                    "status": job.status,
                    "error_code": job.error_code,
                },
                thread_id=state["task_id"],
            )
            failure_detail = job.error_message or job.error_code or "COVER_JOB_FAILED"
            raise RuntimeError(f"CoverJob {job.status}: {failure_detail}")
        asset_ids = list((job.result_json or {}).get("asset_ids") or [])
        if not asset_ids:
            raise RuntimeError("CoverJob 成功但没有返回资产")
        assets = await repo.get_assets_for_user(asset_ids, state["uid"])
        if len(assets) != len(asset_ids) or any(item.role != "output" for item in assets):
            raise RuntimeError("CoverJob 返回了无效资产")
        await append_run_stream_event(
            state["run_id"],
            "content.cover.completed",
            {
                "task_id": state["task_id"],
                "parent_run_id": state["run_id"],
                "node_id": node["id"],
                "cover_job_id": job.id,
                "asset_count": len(asset_ids),
                "provider": job.model,
            },
            thread_id=state["task_id"],
        )
        return {
            "cover_job": {
                **(state.get("cover_job") or {}),
                "cover_job_id": job.id,
                "status": job.status,
                "provider": job.model,
                "provider_task_id": job.provider_task_id,
                "asset_ids": asset_ids,
            },
            "cover_assets": [item.to_dict() for item in assets],
            "resume_parent_run_id": None,
        }


__all__ = ["ExternalWaitNodeHandler"]
