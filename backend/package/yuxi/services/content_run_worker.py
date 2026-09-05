from __future__ import annotations

import asyncio
import json
from typing import Any

from langgraph.types import Command
from langgraph.errors import InvalidUpdateError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from yuxi.agents.buildin import agent_manager
from yuxi.agents.buildin.content_workflow.context import ContentWorkflowContext
from yuxi.content_cover.schemas import CoverRetryCreate
from yuxi.content.v3.workflow import LEGACY_PLATFORM_WORKFLOW_V3_IDS
from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.repositories.content_repository import ContentRepository
from yuxi.repositories.agent_run_repository import TERMINAL_RUN_STATUSES, AgentRunRepository
from yuxi.services.run_queue_service import append_run_stream_event, clear_cancel_signal, has_cancel_signal
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.utils.logging_config import logger


async def _load_content_run(run_id: str):
    async with pg_manager.get_async_session_context() as db:
        run = await AgentRunRepository(db).get_run(run_id)
        if run is None:
            return None, None, None, None
        task = await ContentRepository(db).get_task(run.thread_id)
        if task is None:
            return run, None, None, None
        repo = ContentRepository(db)
        workflow = await repo.get_workflow(task.workflow_version_id)
        rule_bundle = await repo.get_rule_bundle(task.rule_version_id)
        return run, task, workflow, rule_bundle


async def _set_content_run_status(
    run_id: str,
    *,
    status: str,
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    async with pg_manager.get_async_session_context() as db:
        repo = AgentRunRepository(db)
        if status == "running":
            await repo.mark_running(run_id)
        else:
            await repo.set_terminal_status(
                run_id,
                status=status,
                error_type=error_type,
                error_message=error_message,
            )


async def _retry_failed_cover_job(run, state: dict[str, Any]) -> dict[str, Any] | None:
    cover_job_id = str((state.get("cover_job") or {}).get("cover_job_id") or "")
    if not cover_job_id:
        raise RuntimeError("封面等待节点缺少可重试的 CoverJob")
    async with pg_manager.get_async_session_context() as db:
        cover_job = await ContentCoverRepository(db).get_job_for_user(cover_job_id, run.uid)
        if cover_job is None or cover_job.content_task_id != run.thread_id:
            return None
        if cover_job.status not in {"failed", "cancelled"}:
            return cover_job.to_dict()
        user = (await db.execute(select(User).where(User.uid == run.uid, User.is_deleted == 0))).scalar_one_or_none()
        if user is None:
            raise RuntimeError("内容任务所属用户不存在")
        from yuxi.services.content_cover_service import retry_cover_job

        result = await retry_cover_job(
            db,
            user,
            cover_job.id,
            CoverRetryCreate(idempotency_key=f"content-workflow-cover:{run.id}"),
            workflow_resume={
                "parent_run_id": run.id,
                "node_id": "wait_cover_job",
                "expected_state_version": int(state.get("state_version") or 0),
            },
        )
        return result["job"]


def _visual_plan_exceeds_template_limits(state: dict[str, Any]) -> bool:
    visual_plan_text = (state.get("visual_plan") or {}).get("text") or []
    visual_material = (state.get("runtime_config_snapshot") or {}).get("visual_material") or {}
    role_indexes = {"title": 0, "subtitle": 1, "body_excerpt": 1}
    for field in visual_material.get("hycanvas_fillable_fields") or []:
        role = str(field.get("semanticRole") or "")
        text_index = role_indexes.get(role)
        max_chars = (field.get("constraints") or {}).get("maxChars")
        if text_index is None or not isinstance(max_chars, int) or max_chars <= 0:
            continue
        if text_index < len(visual_plan_text) and len(str(visual_plan_text[text_index])) > max_chars:
            return True
    return False


def _retry_predecessor(workflow_definition: dict[str, Any], pending_node: str) -> str | None:
    for source, target in reversed(workflow_definition.get("edges") or []):
        if target == pending_node:
            return str(source)
    return None


async def process_content_run(ctx, run_id: str):
    run, task, workflow, rule_bundle = await _load_content_run(run_id)
    if run is None:
        logger.warning(f"Content run not found: {run_id}")
        return
    if run.status in TERMINAL_RUN_STATUSES:
        return
    if task is None or workflow is None or rule_bundle is None:
        await _set_content_run_status(
            run_id,
            status="failed",
            error_type="content_configuration_missing",
            error_message="内容任务、工作流或规则版本不存在",
        )
        return
    task_schema_version = int((task.runtime_config_snapshot_json or {}).get("schema_version") or 1)
    workflow_schema_version = int((workflow.definition_json or {}).get("schema_version") or 1)
    if task_schema_version != 3 or workflow_schema_version != 3:
        await _set_content_run_status(
            run_id,
            status="failed",
            error_type="content_legacy_task_read_only",
            error_message="旧版内容任务仅保留历史查询，Worker 只执行 V3 工作流",
        )
        return
    if task.workflow_version_id in LEGACY_PLATFORM_WORKFLOW_V3_IDS:
        await _set_content_run_status(
            run_id,
            status="failed",
            error_type="content_workflow_upgrade_required",
            error_message="旧版 V3 checkpoint 不会套用新版节点输入契约；请新建任务后生产",
        )
        return

    payload = run.input_payload or {}
    action = payload.get("action") or "start"
    job_try = int((ctx or {}).get("job_try") or 1)
    await _set_content_run_status(run_id, status="running")
    await append_run_stream_event(
        run_id,
        "metadata",
        {
            "task_id": task.id,
            "workflow_version_id": task.workflow_version_id,
            "rule_version_id": task.rule_version_id,
            "action": action,
        },
        thread_id=task.id,
    )

    try:
        agent = agent_manager.get_agent("ContentWorkflowAgent")
        context = ContentWorkflowContext(
            uid=run.uid,
            thread_id=run.checkpoint_thread_id or f"content:{task.id}",
            run_id=run.id,
            request_id=run.request_id,
            task_id=task.id,
            workflow_definition=workflow.definition_json or {},
            rule_bundle=rule_bundle,
            model_spec=payload.get("model_spec"),
        )
        graph = await agent.get_graph(context=context)
        runtime_limits = (workflow.definition_json or {}).get("runtime_limits") or {}
        max_steps = int(runtime_limits.get("max_steps") or 100)
        config = {
            "configurable": {"thread_id": context.thread_id, "uid": run.uid},
            "recursion_limit": min(max(max_steps, 31), 200),
        }
        if action == "resume":
            await graph.aupdate_state(
                config,
                {
                    "run_id": run.id,
                    "resume_parent_run_id": payload.get("parent_run_id"),
                    "uid": run.uid,
                    "model_spec": payload.get("model_spec"),
                },
            )
            graph_input = Command(resume=payload.get("resume") or {})
        elif action == "retry" or job_try > 1:
            snapshot = await graph.aget_state(config)
            pending_nodes = set(snapshot.next or ())
            requested_node = payload.get("node_id")
            if not pending_nodes:
                raise RuntimeError("工作流 checkpoint 中没有可重试的失败节点")
            if requested_node and requested_node not in pending_nodes:
                raise RuntimeError(f"节点 {requested_node} 不是当前可重试节点")
            state_values = getattr(snapshot, "values", {}) or {}
            state_update = {
                "run_id": run.id,
                "uid": run.uid,
                "model_spec": payload.get("model_spec"),
                "resume_parent_run_id": None,
            }
            retry_from_node = None
            if requested_node == "submit_cover_job" and _visual_plan_exceeds_template_limits(state_values):
                state_update["visual_plan"] = None
                state_update["cover_job"] = None
                retry_from_node = "human_content_approval"
            elif requested_node == "wait_cover_job":
                retried_cover = await _retry_failed_cover_job(run, state_values)
                if retried_cover is None:
                    state_update["cover_job"] = None
                    retry_from_node = "plan_visuals"
                else:
                    state_update["cover_job"] = {
                        **(state_values.get("cover_job") or {}),
                        "cover_job_id": retried_cover["id"],
                        "status": retried_cover["status"],
                    }
            if retry_from_node:
                await graph.aupdate_state(config, state_update, as_node=retry_from_node)
            else:
                try:
                    await graph.aupdate_state(config, state_update)
                except InvalidUpdateError as exc:
                    if "Ambiguous update" not in str(exc) or len(pending_nodes) != 1:
                        raise
                    pending_node = next(iter(pending_nodes))
                    predecessor = _retry_predecessor(workflow.definition_json or {}, pending_node)
                    if predecessor is None:
                        raise
                    await graph.aupdate_state(config, state_update, as_node=predecessor)
            graph_input = None
        else:
            visual_material = (task.runtime_config_snapshot_json or {}).get("visual_material") or {}
            selected_media = []
            if visual_material.get("image_asset_id"):
                selected_media.append(
                    {
                        "id": visual_material["image_asset_id"],
                        "attachment_id": visual_material.get("image_item_id"),
                        "verified_status": "user_confirmed",
                        "privacy_status": "approved",
                        "allowed_usage": ["visual"],
                        "source_hash": visual_material.get("image_sha256"),
                        "width": visual_material.get("image_width"),
                        "height": visual_material.get("image_height"),
                        "selected_for_cover": True,
                    }
                )
            graph_input = {
                "task_id": task.id,
                "run_id": run.id,
                "uid": run.uid,
                "model_spec": payload.get("model_spec"),
                "workflow_version_id": task.workflow_version_id,
                "rule_version_id": task.rule_version_id,
                "industry_template_version_id": task.industry_template_version_id,
                "schema_version": 3,
                "runtime_config_snapshot": task.runtime_config_snapshot_json or {},
                "content_type": {},
                "industry_pack": {},
                "persona_profile": {},
                "channel_profile": {},
                "compliance_policies": [],
                "lexicon_entries": [],
                "media_evidence_items": selected_media,
                "content_brief": task.brief_json or {},
                "content_angles": [],
                "selected_angle": None,
                "content_outline": {},
                "evidence_bundle": task.evidence_json or {"items": []},
                "title_candidates": [],
                "selected_title": None,
                "content_draft": None,
                "validation_report": None,
                "title_validation_report": None,
                "review_report": None,
                "strategy_snapshot": {},
                "product_material_requirements": {},
                "product_evidence_collection": {},
                "product_evidence_pack": {},
                "persona_diff": None,
                "channel_result": None,
                "approval_result": None,
                "artifact_id": None,
                "current_node": "queued",
                "retry_counts": {},
                "state_version": 0,
                "task_mode": getattr(task, "mode", "quick"),
                "resume_parent_run_id": None,
            }

        await graph.ainvoke(graph_input, config=config, context=context)
        snapshot = await graph.aget_state(config)
        if snapshot.next:
            interrupts = list(getattr(snapshot, "interrupts", ()) or ())
            interrupt_payload = interrupts[0].value if interrupts else {"interrupt_type": "human_review"}
            async with pg_manager.get_async_session_context() as db:
                persisted_task = await ContentRepository(db).get_task(task.id, for_update=True)
                persisted_task.status = (
                    "waiting_external"
                    if interrupt_payload.get("interrupt_type") == "external_wait"
                    else "waiting_human"
                )
                persisted_task.current_stage = "generation"
                persisted_task.latest_run_id = run.id
                await ContentRepository(db).track(
                    "content_run_interrupted",
                    uid=run.uid,
                    task_id=task.id,
                    run_id=run.id,
                    properties={"interrupt_type": interrupt_payload.get("interrupt_type")},
                )
            await append_run_stream_event(
                run_id,
                "interrupt",
                interrupt_payload,
                thread_id=task.id,
            )
            await _set_content_run_status(run_id, status="interrupted")
            await append_run_stream_event(
                run_id,
                "end",
                {"status": "interrupted", "interrupt": interrupt_payload},
                thread_id=task.id,
            )
            if interrupt_payload.get("interrupt_type") == "external_wait" and interrupt_payload.get("cover_job_id"):
                await resume_content_run_from_cover({}, interrupt_payload["cover_job_id"])
            return

        if await has_cancel_signal(run_id):
            raise asyncio.CancelledError
        await _set_content_run_status(run_id, status="completed")
        await append_run_stream_event(
            run_id,
            "end",
            {"status": "completed", "task_id": task.id},
            thread_id=task.id,
        )
    except (asyncio.CancelledError, InterruptedError):
        explicitly_cancelled = await has_cancel_signal(run_id)
        if not explicitly_cancelled:
            async with pg_manager.get_async_session_context() as db:
                persisted_task = await ContentRepository(db).get_task(task.id, for_update=True)
                persisted_task.status = "failed"
                persisted_task.error_json = {
                    "code": "CONTENT_RUN_WORKER_INTERRUPTED",
                    "message": "执行进程发生重载或重启，请从当前节点重试",
                    "retryable": True,
                }
                await ContentRepository(db).track(
                    "content_run_interrupted_unexpectedly",
                    uid=run.uid,
                    task_id=task.id,
                    run_id=run.id,
                    properties={"retryable": True},
                )
            await _set_content_run_status(
                run_id,
                status="failed",
                error_type="worker_interrupted",
                error_message="执行进程发生重载或重启，请从当前节点重试",
            )
            await append_run_stream_event(
                run_id,
                "error",
                {
                    "status": "failed",
                    "message": "执行进程发生重载或重启，请从当前节点重试",
                    "retryable": True,
                },
                thread_id=task.id,
            )
            await append_run_stream_event(run_id, "end", {"status": "failed"}, thread_id=task.id)
            return
        async with pg_manager.get_async_session_context() as db:
            persisted_task = await ContentRepository(db).get_task(task.id, for_update=True)
            persisted_task.status = "cancelled"
            persisted_task.error_json = {"code": "CONTENT_RUN_CANCELLED", "message": "内容运行已取消"}
        await _set_content_run_status(
            run_id,
            status="cancelled",
            error_type="cancelled",
            error_message="内容运行已取消",
        )
        await append_run_stream_event(run_id, "end", {"status": "cancelled"}, thread_id=task.id)
    except Exception as exc:
        retryable = isinstance(
            exc,
            (
                asyncio.TimeoutError,
                ConnectionError,
                TimeoutError,
                OperationalError,
                json.JSONDecodeError,
                ValidationError,
            ),
        )
        if retryable and job_try < 2:
            await append_run_stream_event(
                run_id,
                "error",
                {"status": "retrying", "message": str(exc), "job_try": job_try},
                thread_id=task.id,
            )
            from yuxi.services.run_worker import RetryableRunError

            raise RetryableRunError(str(exc)) from exc
        async with pg_manager.get_async_session_context() as db:
            persisted_task = await ContentRepository(db).get_task(task.id, for_update=True)
            persisted_task.status = "failed"
            persisted_task.error_json = {
                "code": "CONTENT_WORKFLOW_FAILED",
                "message": str(exc),
                "retryable": True,
            }
            await ContentRepository(db).track(
                "content_run_failed",
                uid=run.uid,
                task_id=task.id,
                run_id=run.id,
                properties={"error_type": type(exc).__name__},
            )
        await _set_content_run_status(
            run_id,
            status="failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        await append_run_stream_event(
            run_id,
            "error",
            {"status": "failed", "message": str(exc), "retryable": True},
            thread_id=task.id,
        )
        await append_run_stream_event(run_id, "end", {"status": "failed"}, thread_id=task.id)
    finally:
        await clear_cancel_signal(run_id)


async def resume_content_run_from_cover(ctx, job_id: str) -> dict[str, Any]:
    """Cover Worker 终态事件触发的幂等恢复入口。"""

    del ctx
    async with pg_manager.get_async_session_context() as db:
        job = await ContentCoverRepository(db).get_job(job_id)
        if job is None or job.status not in {"succeeded", "failed", "cancelled"}:
            return {"scheduled": False, "reason": "cover_not_terminal"}
        request = job.request_json or {}
        workflow_resume = (request.get("parameters") or {}).get("workflow_resume") or (request.get("layout") or {}).get(
            "workflow_resume"
        )
        if not workflow_resume:
            return {"scheduled": False, "reason": "workflow_not_linked"}
        parent_run_id = str(workflow_resume.get("parent_run_id") or "")
        parent = await AgentRunRepository(db).get_run(parent_run_id)
        if (
            parent is None
            or parent.status != "interrupted"
            or parent.uid != job.owner_uid
            or parent.thread_id != job.content_task_id
        ):
            return {"scheduled": False, "reason": "parent_not_waiting"}
        task = await ContentRepository(db).get_task(parent.thread_id, for_update=True)
        user = (await db.execute(select(User).where(User.uid == parent.uid, User.is_deleted == 0))).scalar_one_or_none()
        if task is None or user is None:
            return {"scheduled": False, "reason": "owner_or_task_missing"}

        from yuxi.services.content_service import _enqueue_content_run

        result = await _enqueue_content_run(
            db,
            user=user,
            task=task,
            request_id=f"cover-resume:{job.id}:{job.status}",
            action="resume",
            model_spec=(parent.input_payload or {}).get("model_spec"),
            parent_run_id=parent.id,
            resume={
                "run_id": parent.id,
                "node_id": str(workflow_resume.get("node_id") or "wait_cover_job"),
                "expected_state_version": int(workflow_resume.get("expected_state_version") or 0),
                "cover_job_id": job.id,
            },
        )
        return {"scheduled": True, **result}
