from __future__ import annotations

import asyncio
import re

from sqlalchemy import select

from yuxi.content.control.visual_template_fields import resolve_hycanvas_template_fields
from yuxi.content.generation import DEFAULT_DIRECT_GENERATION_PROMPT, generate_direct_content
from yuxi.utils.line_breaks import normalize_escaped_newlines
from yuxi.content_cover.ai_cover_prompt import AI_COVER_PROMPT
from yuxi.content_cover.handwritten_quote_prompt import (
    HANDWRITTEN_QUOTE_NEGATIVE_PROMPT,
    build_handwritten_quote_prompt,
)
from yuxi.content_cover.schemas import CoverGenerateCreate
from yuxi.repositories.agent_run_repository import TERMINAL_RUN_STATUSES, AgentRunRepository
from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.repositories.content_repository import ContentRepository
from yuxi.services.content_cover_service import (
    create_cover_generate_job,
    create_hycanvas_cover_job,
    ensure_hycanvas_reference_asset,
    set_current_cover,
)
from yuxi.services.content_forbidden_words_service import load_forbidden_words
from yuxi.services.run_queue_service import append_run_stream_event, clear_cancel_signal, has_cancel_signal
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentArtifact
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger

DIRECT_COVER_TIMEOUT_SECONDS = 900
DIRECT_COVER_POLL_SECONDS = 1


async def process_direct_content_run(ctx, run_id: str):
    del ctx
    async with pg_manager.get_async_session_context() as db:
        run_repo = AgentRunRepository(db)
        run = await run_repo.get_run(run_id)
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            return
        task = await ContentRepository(db).get_task(run.thread_id)
        if task is None:
            await run_repo.set_terminal_status(
                run_id,
                status="failed",
                error_type="content_task_missing",
                error_message="内容任务不存在",
            )
            return

    await _set_run_running(run_id)
    await append_run_stream_event(
        run_id,
        "metadata",
        {"task_id": run.thread_id, "run_type": "content_direct", "review": "not_run"},
        thread_id=run.thread_id,
    )
    payload = run.input_payload or {}
    try:
        if await has_cancel_signal(run_id):
            raise asyncio.CancelledError
        forbidden_snapshot = await load_forbidden_words(str(run.uid), "封禁词库")
        stream_values = {"title": "", "body": "", "topics": []}
        published_values = {"title": "", "body": "", "topics": []}
        last_publish_at = asyncio.get_running_loop().time()

        async def publish_stream_values() -> None:
            nonlocal last_publish_at
            if await has_cancel_signal(run_id):
                raise asyncio.CancelledError
            for field in ("title", "body", "topics"):
                value = stream_values[field]
                previous = published_values[field]
                if value == previous:
                    continue
                if isinstance(value, str) and isinstance(previous, str):
                    delta = value[len(previous) :] if value.startswith(previous) else value
                elif isinstance(value, list) and isinstance(previous, list):
                    delta = value[len(previous) :] if value[: len(previous)] == previous else value
                else:
                    delta = value
                await append_run_stream_event(
                    run_id,
                    "content.direct.delta",
                    {"field": field, "delta": delta, "value": value},
                    thread_id=run.thread_id,
                )
                published_values[field] = value.copy() if isinstance(value, list) else value
            last_publish_at = asyncio.get_running_loop().time()

        async def handle_content_delta(update: dict) -> None:
            field = update.get("field")
            if field not in stream_values:
                return
            stream_values[field] = update.get("value")
            now = asyncio.get_running_loop().time()
            pending_body_length = len(str(stream_values["body"])) - len(str(published_values["body"]))
            if field != "body" or pending_body_length >= 24 or now - last_publish_at >= 0.15:
                await publish_stream_values()

        output = await generate_direct_content(
            model_spec=payload.get("model_spec"),
            creative_style=payload.get("creative_style") or {},
            viral_source=payload.get("viral_source") or {},
            user_request=str(payload.get("user_request") or ""),
            generation_prompt=str(payload.get("generation_prompt", DEFAULT_DIRECT_GENERATION_PROMPT)),
            forbidden_lexicon=forbidden_snapshot["alternatives"],
            on_delta=handle_content_delta,
        )
        await publish_stream_values()
        if await has_cancel_signal(run_id):
            raise asyncio.CancelledError
        async with pg_manager.get_async_session_context() as db:
            repo = ContentRepository(db)
            task = await repo.get_task(run.thread_id, for_update=True)
            if task is None:
                raise RuntimeError("内容任务不存在")
            review = {"status": "not_run", "checks": [], "review_mode": "direct_generation"}
            artifact = await repo.get_artifact_for_task(task.id)
            runtime_snapshot = {
                "creation_mode": "viral_rewrite",
                "generation_mode": "direct",
                "creative_style": payload.get("creative_style") or {},
                "generation_prompt": payload.get("generation_prompt") or "",
                "viral_asset_id": payload.get("viral_asset_id"),
                "visual_material": payload.get("visual_material") or {},
                "forbidden_lexicon": forbidden_snapshot,
            }
            strategy_snapshot = {
                "generation_mode": "direct",
                "creative_style": payload.get("creative_style") or {},
                "viral_asset_id": payload.get("viral_asset_id"),
            }
            evidence_snapshot = {
                "items": [
                    {
                        "id": f"viral:{payload.get('viral_asset_id')}",
                        "source_type": "viral_reference",
                        "metadata": {
                            "selected_reference": True,
                            "asset_id": payload.get("viral_asset_id"),
                        },
                    }
                ],
                "viral_source": payload.get("viral_source") or {},
                "forbidden_lexicon": forbidden_snapshot,
            }
            if artifact is None:
                artifact = ContentArtifact(
                    id=f"ca_{run.id.replace('-', '')}",
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    status="generated",
                    current_version=1,
                    title=output.title,
                    body=output.body,
                    topics=output.topics,
                    strategy_snapshot=strategy_snapshot,
                    evidence_snapshot=evidence_snapshot,
                    review_snapshot=review,
                    content_type_snapshot={"code": task.content_type_code},
                    runtime_config_snapshot=runtime_snapshot,
                    created_by=run.uid,
                )
                db.add(artifact)
                await db.flush()
            else:
                artifact.current_version += 1
                artifact.status = "generated"
                artifact.title = output.title
                artifact.body = output.body
                artifact.topics = output.topics
                artifact.strategy_snapshot = strategy_snapshot
                artifact.evidence_snapshot = evidence_snapshot
                artifact.review_snapshot = review
                artifact.content_type_snapshot = {"code": task.content_type_code}
                artifact.runtime_config_snapshot = runtime_snapshot
                artifact.updated_at = utc_now_naive()
            await repo.save_artifact_version(
                artifact=artifact,
                source_type="generated",
                model_spec=payload.get("model_spec"),
                skill_versions={},
                rule_version_id=task.rule_version_id,
                knowledge_snapshot=evidence_snapshot,
                review_snapshot=review,
                created_by=run.uid,
            )
            task.status = "running"
            task.current_stage = "generation"
            task.review_json = review
            task.selected_title_json = {"text": output.title}
            task.error_json = None
            await db.commit()
            artifact_id = artifact.id
        await append_run_stream_event(
            run_id,
            "content.generated",
            {"task_id": run.thread_id, "artifact_id": artifact_id},
            thread_id=run.thread_id,
        )
        cover_job = await _create_direct_cover_job(run, output)
        await _wait_for_direct_cover(run, cover_job["id"])
        await _set_run_terminal(run_id, "completed")
        await append_run_stream_event(
            run_id,
            "end",
            {"status": "completed", "task_id": run.thread_id, "artifact_id": artifact_id},
            thread_id=run.thread_id,
        )
    except asyncio.CancelledError:
        await _set_run_terminal(run_id, "cancelled", "cancelled", "内容运行已取消")
        await append_run_stream_event(run_id, "end", {"status": "cancelled"}, thread_id=run.thread_id)
    except Exception as exc:
        logger.exception("Direct content generation failed: %s", run_id)
        async with pg_manager.get_async_session_context() as db:
            task = await ContentRepository(db).get_task(run.thread_id, for_update=True)
            if task is not None:
                task.status = "failed"
                task.error_json = {"code": "CONTENT_DIRECT_GENERATION_FAILED", "message": str(exc)}
                await db.commit()
        await _set_run_terminal(run_id, "failed", type(exc).__name__, str(exc))
        await append_run_stream_event(
            run_id,
            "error",
            {"status": "failed", "message": str(exc), "retryable": True},
            thread_id=run.thread_id,
        )
        await append_run_stream_event(run_id, "end", {"status": "failed"}, thread_id=run.thread_id)
    finally:
        await clear_cancel_signal(run_id)


async def _set_run_running(run_id: str) -> None:
    async with pg_manager.get_async_session_context() as db:
        await AgentRunRepository(db).mark_running(run_id)


def _cover_text(output) -> tuple[str, str, list[str]]:
    title = normalize_escaped_newlines(str(output.title)).strip()[:60]
    paragraphs = [item.strip() for item in re.split(r"\n+", str(output.body)) if item.strip()]
    subtitle = (paragraphs[0] if paragraphs else title)[:120]
    topics = [str(item).strip() for item in output.topics if str(item).strip()][:10]
    return title, subtitle, topics


def _fit_template_text(value: str, declarations: list[dict], role: str, default_limit: int) -> str:
    limits = [
        constraints["maxChars"]
        for field in declarations
        if field.get("kind") == "text"
        and field.get("semanticRole") == role
        and isinstance((constraints := field.get("constraints") or {}).get("maxChars"), int)
        and constraints["maxChars"] > 0
    ]
    return value[: min(limits, default=default_limit)]


async def _create_direct_cover_job(run, output) -> dict:
    payload = run.input_payload or {}
    visual_material = payload.get("visual_material") or {}
    content_brief = payload.get("content_brief") or {}
    title, subtitle, topics = _cover_text(output)
    idempotency_key = f"content-direct-cover:{run.id}"

    async with pg_manager.get_async_session_context() as db:
        user = (await db.execute(select(User).where(User.uid == run.uid, User.is_deleted == 0))).scalar_one()
        image_asset_id = str(visual_material.get("image_asset_id") or "")
        template_id = str(visual_material.get("hycanvas_template_id") or "")
        if template_id:
            from yuxi.services.hycanvas_service import HyCanvasClient

            template = await HyCanvasClient.from_env().get_xiaohongshu_template(template_id)
            if template is None or template.get("zone") != "builtin":
                raise ValueError("所选内置封面模板不存在或不可用")
            if template.get("is_handwritten_quote_template"):
                quote_snapshot = payload.get("trusted_external_material_snapshot")
                if not isinstance(quote_snapshot, dict):
                    raise ValueError("手写报价封面需要已确认的结构化报价数据")
                reference_asset = await ensure_hycanvas_reference_asset(db, user, template_id)
                result = await create_cover_generate_job(
                    db,
                    user,
                    CoverGenerateCreate(
                        mode="image_to_image",
                        content_task_id=run.thread_id,
                        source_asset_ids=[reference_asset.id],
                        title="装修人工报价",
                        render_prompt_text=True,
                        prompt=build_handwritten_quote_prompt(quote_snapshot),
                        negative_prompt=HANDWRITTEN_QUOTE_NEGATIVE_PROMPT,
                        size="1080x1440",
                        n=1,
                        parameters={"quality": "high", "output_format": "png"},
                        idempotency_key=idempotency_key,
                    ),
                )
            else:
                if not image_asset_id:
                    raise ValueError("内置封面需要一张图库背景图")
                declarations = list(template.get("fillable_fields") or [])
                visual_title = _fit_template_text(title, declarations, "title", 60)
                visual_subtitle = _fit_template_text(subtitle, declarations, "subtitle", 120)
                fields = resolve_hycanvas_template_fields(
                    declarations,
                    visual_text=[visual_title, visual_subtitle],
                    brief=content_brief,
                )
                image_field_label = next(
                    (
                        str(field["label"])
                        for field in declarations
                        if field.get("kind") == "image" and field.get("label")
                    ),
                    None,
                )
                result = await create_hycanvas_cover_job(
                    db,
                    user,
                    content_task_id=run.thread_id,
                    source_asset_id=image_asset_id,
                    template_id=template_id,
                    title=visual_title,
                    fields=fields,
                    image_field_label=image_field_label,
                    idempotency_key=idempotency_key,
                    parameters={"generation_mode": "direct"},
                )
        elif visual_material.get("cover_mode") == "ai":
            if not image_asset_id:
                raise ValueError("AI 封面需要一张图库背景图")
            result = await create_cover_generate_job(
                db,
                user,
                CoverGenerateCreate(
                    mode="image_to_image",
                    content_task_id=run.thread_id,
                    source_asset_ids=[image_asset_id],
                    title=title,
                    subtitle=subtitle,
                    tags=topics,
                    render_copy_with_image2=True,
                    prompt=AI_COVER_PROMPT,
                    size="1080x1440",
                    n=1,
                    parameters={"quality": "high", "output_format": "png"},
                    idempotency_key=idempotency_key,
                ),
            )
        else:
            raise ValueError("新生成仅支持 AI 封面或内置封面")

    job = result["job"]
    await append_run_stream_event(
        run.id,
        "content.cover.started",
        {"task_id": run.thread_id, "cover_job_id": job["id"], "mode": job["mode"]},
        thread_id=run.thread_id,
    )
    return job


async def _wait_for_direct_cover(run, job_id: str) -> None:
    deadline = asyncio.get_running_loop().time() + DIRECT_COVER_TIMEOUT_SECONDS
    last_progress = None
    while True:
        if await has_cancel_signal(run.id):
            raise asyncio.CancelledError
        async with pg_manager.get_async_session_context() as db:
            job = await ContentCoverRepository(db).get_job(job_id)
            if job is None:
                raise RuntimeError("封面任务不存在")
            progress = (job.status, job.progress)
            if progress != last_progress:
                await append_run_stream_event(
                    run.id,
                    "content.cover.progress",
                    {
                        "task_id": run.thread_id,
                        "cover_job_id": job.id,
                        "status": job.status,
                        "progress": job.progress,
                    },
                    thread_id=run.thread_id,
                )
                last_progress = progress
            if job.status in {"failed", "cancelled"}:
                raise RuntimeError(job.error_message or job.error_code or "封面生成失败")
            if job.status == "succeeded":
                asset_ids = list((job.result_json or {}).get("asset_ids") or [])
                if not asset_ids:
                    raise RuntimeError("封面任务完成但没有返回图片")
                user = (await db.execute(select(User).where(User.uid == run.uid, User.is_deleted == 0))).scalar_one()
                await set_current_cover(db, user, job.id, asset_id=asset_ids[0])
                content_repo = ContentRepository(db)
                task = await content_repo.get_task(run.thread_id, for_update=True)
                task.status = "generated"
                task.current_stage = "generation"
                task.error_json = None
                artifact = await content_repo.get_artifact_for_task(run.thread_id)
                await content_repo.track(
                    "content_direct_run_completed",
                    uid=run.uid,
                    task_id=run.thread_id,
                    run_id=run.id,
                    properties={
                        "artifact_id": artifact.id,
                        "cover_asset_id": asset_ids[0],
                        "review_status": "not_run",
                    },
                )
                await db.commit()
                await append_run_stream_event(
                    run.id,
                    "content.cover.completed",
                    {
                        "task_id": run.thread_id,
                        "cover_job_id": job.id,
                        "cover_asset_id": asset_ids[0],
                    },
                    thread_id=run.thread_id,
                )
                return
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("封面生成等待超时")
        await asyncio.sleep(DIRECT_COVER_POLL_SECONDS)


async def _set_run_terminal(
    run_id: str, status: str, error_type: str | None = None, error_message: str | None = None
) -> None:
    async with pg_manager.get_async_session_context() as db:
        await AgentRunRepository(db).set_terminal_status(
            run_id,
            status=status,
            error_type=error_type,
            error_message=error_message,
        )
