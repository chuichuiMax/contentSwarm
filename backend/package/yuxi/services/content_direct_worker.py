from __future__ import annotations

import asyncio
import re
import time

from sqlalchemy import select

from yuxi.content.control.visual_template_fields import resolve_hycanvas_template_fields
from yuxi.content.generation import (
    DEFAULT_DIRECT_GENERATION_PROMPT,
    DIRECT_TOPIC_LIMIT,
    _direct_request_content_type,
    append_topics_within_limit,
    avoid_recent_image_topics,
    generate_direct_content,
)
from yuxi.utils.line_breaks import normalize_escaped_newlines
from yuxi.content_cover.ai_cover_prompt import (
    ai_cover_prompt_controls,
    normalize_cover_form_values,
)
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
    get_cover_asset_file,
    set_current_cover,
)
from yuxi.services.material_library_service import get_material_file
from yuxi.services.content_forbidden_words_service import (
    load_knowledge_base_text,
    load_optional_forbidden_words,
    lookup_knowledge_base_text,
)
from yuxi.services.run_queue_service import append_run_stream_event, clear_cancel_signal, has_cancel_signal
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentArtifact, ContentTask
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger

DIRECT_COVER_TIMEOUT_SECONDS = 900
DIRECT_COVER_POLL_SECONDS = 1
GENERIC_AUDIENCE_TAG_KB_NAME = "目标人群-通用-标签"
_COPY_KB_EXCLUDED = frozenset({"封禁词库", "创作提示词", "好评笔记知识库"})


def _viral_content_type(value) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


async def recent_image_topics(uid: str) -> list[str]:
    """读取这个账号最近几篇发布标签，供看图时避开重复。"""

    async with pg_manager.get_async_session_context() as db:
        rows = (
            await db.execute(
                select(ContentArtifact.topics)
                .join(ContentTask, ContentTask.id == ContentArtifact.task_id)
                .where(ContentTask.created_by == uid)
                .order_by(ContentArtifact.updated_at.desc())
                .limit(8)
            )
        ).scalars()
    collected: list[str] = []
    for topics in rows:
        if isinstance(topics, list):
            collected.extend(str(item) for item in topics)
    return avoid_recent_image_topics(collected)


def other_copy_knowledge_names(content_type_name: str, libraries: list[dict]) -> list[str]:
    """内容类型知识库未命中时，只用没有绑定内容类型的知识库写文案。"""

    excluded = set(content_type_knowledge_names(content_type_name))
    excluded.update(_COPY_KB_EXCLUDED)
    selected: list[str] = []
    for item in libraries:
        text = str(item.get("name") or "").strip()
        if not text or text in excluded or text in selected:
            continue
        if text.startswith("目标人群-") and text.endswith("-标签"):
            continue
        if _viral_content_type(item.get("viral_content_type")):
            continue
        selected.append(text)
    return selected


def content_type_knowledge_names(content_type_name: str) -> list[str]:
    name = str(content_type_name or "").strip()
    if not name:
        return []
    names = [name]
    if not name.endswith("知识库"):
        names.append(f"{name}知识库")
    return names


async def accessible_knowledge_libraries(uid: str) -> list[dict]:
    from yuxi import knowledge_base
    from yuxi.knowledge.manager import DEFAULT_SHARE_CONFIG
    from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
    from yuxi.repositories.user_repository import UserRepository

    user = await UserRepository().get_by_uid(uid)
    if not user:
        return []
    user_info = {"uid": user.uid, "role": user.role, "department_id": user.department_id}
    rows = await KnowledgeBaseRepository().get_all()
    libraries: list[dict] = []
    for row in sorted(rows, key=lambda item: str(item.name or "")):
        if not knowledge_base._database_info_accessible(
            user_info,
            {
                "created_by": row.created_by,
                "share_config": row.share_config or DEFAULT_SHARE_CONFIG.copy(),
            },
        ):
            continue
        name = str(row.name or "").strip()
        if not name:
            continue
        params = row.additional_params if isinstance(row.additional_params, dict) else {}
        libraries.append({"name": name, "viral_content_type": params.get("viral_content_type")})
    return libraries


async def lookup_content_knowledge(uid: str, name: str, *, limit: int = 12000) -> dict | None:
    try:
        return await lookup_knowledge_base_text(uid, name, limit=limit)
    except Exception:
        logger.warning("知识库查找失败 name=%s", name, exc_info=True)
        return None


def content_type_name_from_payload(payload: dict) -> str:
    brief = payload.get("content_brief") if isinstance(payload.get("content_brief"), dict) else {}
    form = brief.get("form_values") if isinstance(brief.get("form_values"), dict) else {}
    name = str(form.get("mp_content_type_name") or "").strip()
    if name:
        return name
    _code, type_name = _direct_request_content_type(str(payload.get("user_request") or ""))
    return type_name


def first_reference_image_locator(payload: dict) -> tuple[str, str]:
    """返回用户选择或上传的第一张图：图库条目优先，其次封面素材。"""

    visual = payload.get("visual_material") or {}
    if not isinstance(visual, dict):
        visual = {}
    composition = visual.get("photo_composition") or {}
    slots = composition.get("slots") if isinstance(composition, dict) else None
    if isinstance(slots, list):
        for slot in slots:
            item_id = str(slot.get("image_item_id") or "").strip() if isinstance(slot, dict) else ""
            if item_id:
                return "item", item_id
    item_id = str(visual.get("image_item_id") or "").strip()
    if item_id:
        return "item", item_id
    asset_id = str(visual.get("image_asset_id") or "").strip()
    if asset_id:
        return "asset", asset_id
    brief = payload.get("content_brief") if isinstance(payload.get("content_brief"), dict) else {}
    form = brief.get("form_values") if isinstance(brief.get("form_values"), dict) else {}
    asset_ids = form.get("cover_asset_ids")
    if isinstance(asset_ids, list):
        for item in asset_ids:
            asset_id = str(item or "").strip()
            if asset_id:
                return "asset", asset_id
    asset_id = str(form.get("cover_asset_id") or "").strip()
    if asset_id:
        return "asset", asset_id
    attachments = brief.get("attachments")
    if isinstance(attachments, list):
        for item in attachments:
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get("asset_id") or "").strip()
            if asset_id:
                return "asset", asset_id
    return "", ""


async def _load_first_reference_image(uid: str, payload: dict) -> tuple[bytes, str] | None:
    if payload.get("skip_cover"):
        return None
    kind, image_id = first_reference_image_locator(payload)
    if not image_id:
        return None
    async with pg_manager.get_async_session_context() as db:
        user = (await db.execute(select(User).where(User.uid == uid, User.is_deleted == 0))).scalar_one()
        if kind == "item":
            data, content_type, _name = await get_material_file(db, user, image_id, allow_case_browse=True)
            return data, content_type
        data, content_type, _name = await get_cover_asset_file(db, user, image_id)
        return data, content_type


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
        forbidden_snapshot = await load_optional_forbidden_words(str(run.uid), "封禁词库")
        knowledge_context = ""
        if payload.get("skip_cover"):
            knowledge_context = await load_knowledge_base_text(str(run.uid), "好评笔记知识库")
        reference_image = await _load_first_reference_image(str(run.uid), payload)
        knowledge_hits = [
            {
                "name": "封禁词库",
                "hit": bool(forbidden_snapshot.get("knowledge_base_id")),
                "kb_id": forbidden_snapshot.get("knowledge_base_id") or "",
                "chars": len(forbidden_snapshot.get("alternatives") or {}),
                "role": "forbidden",
            }
        ]
        reference_knowledge = ""
        if payload.get("skip_cover"):
            knowledge_hits.append(
                {"name": "好评笔记知识库", "hit": bool(knowledge_context), "kb_id": "", "chars": len(knowledge_context)}
            )
        else:
            type_name = content_type_name_from_payload(payload)
            for kb_name in content_type_knowledge_names(type_name):
                found = await lookup_content_knowledge(str(run.uid), kb_name)
                if found is None:
                    continue
                reference_knowledge = found["text"]
                knowledge_hits.append(
                    {
                        "name": found["name"],
                        "hit": True,
                        "kb_id": found["kb_id"],
                        "chars": len(found["text"]),
                        "role": "content_type",
                    }
                )
                break
            else:
                queried = content_type_knowledge_names(type_name)
                if queried:
                    knowledge_hits.append(
                        {"name": queried[0], "hit": False, "kb_id": "", "chars": 0, "role": "content_type"}
                    )
                parts: list[str] = []
                budget = 12000
                for kb_name in other_copy_knowledge_names(
                    type_name, await accessible_knowledge_libraries(str(run.uid))
                ):
                    if budget <= 0:
                        break
                    found = await lookup_content_knowledge(str(run.uid), kb_name, limit=budget)
                    if found is None or not found["text"]:
                        continue
                    text = found["text"][:budget]
                    parts.append(f"【{found['name']}】\n{text}")
                    budget -= len(text)
                    knowledge_hits.append(
                        {
                            "name": found["name"],
                            "hit": True,
                            "kb_id": found["kb_id"],
                            "chars": len(text),
                            "role": "other",
                        }
                    )
                reference_knowledge = "\n\n".join(parts)
        usage_log: dict = {}
        generation = asyncio.create_task(
            generate_direct_content(
                model_spec=payload.get("model_spec"),
                creative_style=payload.get("creative_style") or {},
                viral_source=payload.get("viral_source") or {},
                user_request=str(payload.get("user_request") or ""),
                generation_prompt=str(payload.get("generation_prompt", DEFAULT_DIRECT_GENERATION_PROMPT)),
                forbidden_lexicon=forbidden_snapshot["alternatives"],
                knowledge_context=knowledge_context,
                reference_knowledge=reference_knowledge,
                reference_image=reference_image,
                recent_topics=await recent_image_topics(str(run.uid)) if reference_image else [],
                usage_out=usage_log,
            )
        )
        generation_started_at = time.monotonic()
        while not generation.done():
            if await has_cancel_signal(run_id):
                raise asyncio.CancelledError
            await append_run_stream_event(
                run_id,
                "content.direct.progress",
                {
                    "phase": "generating",
                    "elapsed_seconds": int(time.monotonic() - generation_started_at),
                },
                thread_id=run.thread_id,
            )
            done, _ = await asyncio.wait({generation}, timeout=8)
            if done:
                break
        output = await generation
        if len(output.topics) < DIRECT_TOPIC_LIMIT:
            from yuxi.services.target_audience_tag_service import parse_audience_tag_lines

            audience_hit = await lookup_content_knowledge(
                str(run.uid), GENERIC_AUDIENCE_TAG_KB_NAME, limit=8000
            )
            audience_tags = parse_audience_tag_lines((audience_hit or {}).get("text") or "")
            output.topics = append_topics_within_limit(list(output.topics), audience_tags)
            knowledge_hits.append(
                {
                    "name": GENERIC_AUDIENCE_TAG_KB_NAME,
                    "hit": bool(audience_tags),
                    "kb_id": (audience_hit or {}).get("kb_id") or "",
                    "chars": len(audience_tags),
                    "role": "audience_tags",
                }
            )
        for field in ("title", "body", "topics"):
            value = getattr(output, field)
            await append_run_stream_event(
                run_id,
                "content.direct.delta",
                {"field": field, "delta": value, "value": value},
                thread_id=run.thread_id,
            )
        if await has_cancel_signal(run_id):
            raise asyncio.CancelledError
        async with pg_manager.get_async_session_context() as db:
            repo = ContentRepository(db)
            task = await repo.get_task(run.thread_id, for_update=True)
            if task is None:
                raise RuntimeError("内容任务不存在")
            review = {"status": "not_run", "checks": [], "review_mode": "direct_generation"}
            artifact = await repo.get_artifact_for_task(task.id)
            image_kind, image_id = first_reference_image_locator(payload)
            runtime_snapshot = {
                "creation_mode": "viral_rewrite",
                "generation_mode": "direct",
                "creative_style": payload.get("creative_style") or {},
                "generation_prompt": payload.get("generation_prompt") or "",
                "viral_asset_id": payload.get("viral_asset_id"),
                "visual_material": payload.get("visual_material") or {},
                "forbidden_lexicon": forbidden_snapshot,
                "generation_log": {
                    "model": usage_log.get("model") or payload.get("model_spec") or "",
                    "prompt": usage_log.get("prompt") or payload.get("generation_prompt") or "",
                    "temperature": usage_log.get("temperature"),
                    "top_p": usage_log.get("top_p"),
                    "seed": usage_log.get("seed"),
                    "image_attached": bool(usage_log.get("image_attached")),
                    "image_omitted_reason": usage_log.get("image_omitted_reason") or "",
                    "image_item_id": image_id if image_kind == "item" else "",
                    "image_asset_id": image_id if image_kind == "asset" else "",
                    "elapsed_seconds": usage_log.get("elapsed_seconds"),
                    "input_tokens": usage_log.get("input_tokens"),
                    "output_tokens": usage_log.get("output_tokens"),
                    "total_tokens": usage_log.get("total_tokens"),
                    "knowledge_hits": knowledge_hits,
                },
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
            skip_cover = bool(payload.get("skip_cover"))
            task.status = "generated" if skip_cover else "running"
            task.current_stage = "generation"
            task.review_json = review
            task.selected_title_json = {"text": output.title}
            task.error_json = None
            if skip_cover:
                await repo.track(
                    "content_direct_run_completed",
                    uid=run.uid,
                    task_id=task.id,
                    run_id=run.id,
                    properties={"artifact_id": artifact.id, "review_status": "not_run"},
                )
            await db.commit()
            artifact_id = artifact.id
        await append_run_stream_event(
            run_id,
            "content.generated",
            {"task_id": run.thread_id, "artifact_id": artifact_id},
            thread_id=run.thread_id,
        )
        if payload.get("skip_cover"):
            await _set_run_terminal(run_id, "completed")
            await append_run_stream_event(
                run_id,
                "end",
                {"status": "completed", "task_id": run.thread_id, "artifact_id": artifact_id},
                thread_id=run.thread_id,
            )
            return
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


def _cover_text(output) -> tuple[str, str]:
    title = normalize_escaped_newlines(str(output.title)).strip()[:60]
    paragraphs = [item.strip() for item in re.split(r"\n+", str(output.body)) if item.strip()]
    subtitle = (paragraphs[0] if paragraphs else title)[:120]
    return title, subtitle


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
    title, subtitle = _cover_text(output)
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
            form_values = normalize_cover_form_values(
                {
                    **(content_brief.get("business_variables") or {}),
                    **(content_brief.get("form_values") or {}),
                }
            )
            task_row = await ContentRepository(db).get_task_for_user(run.thread_id, user)
            content_type_code = task_row.content_type_code if task_row else ""
            content_type_name = str(
                content_brief.get("typeName") or content_brief.get("content_type_name") or ""
            )
            prompt_controls = ai_cover_prompt_controls(visual_material)
            from yuxi.services.ai_cover_template_service import cover_generation_selection

            ai_selection = await cover_generation_selection(
                db,
                title=title,
                subtitle=subtitle,
                form_values=form_values,
                content_type_code=content_type_code,
                content_type_name=content_type_name,
                prompt_controls=prompt_controls,
            )
            result = await create_cover_generate_job(
                db,
                user,
                CoverGenerateCreate(
                    mode="image_to_image",
                    content_task_id=run.thread_id,
                    source_asset_ids=[image_asset_id],
                    title=title,
                    subtitle=subtitle,
                    render_copy_with_image2=ai_selection.render_copy_with_image2,
                    prompt=ai_selection.prompt,
                    size="1080x1440",
                    n=1,
                    parameters={
                        "quality": "high",
                        "output_format": "png",
                        "ai_cover_prompt_variant": ai_selection.variant,
                        **ai_selection.extra_parameters,
                    },
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


async def _finalize_direct_cover(run, job_id: str) -> None:
    async with pg_manager.get_async_session_context() as db:
        job = await ContentCoverRepository(db).get_job(job_id)
        if job is None:
            raise RuntimeError("封面任务不存在")
        if job.status in {"failed", "cancelled"}:
            raise RuntimeError(job.error_message or job.error_code or "封面生成失败")
        if job.status != "succeeded":
            return
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
            "cover_job_id": job_id,
            "cover_asset_id": asset_ids[0],
        },
        thread_id=run.thread_id,
    )


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
                await _finalize_direct_cover(run, job_id)
                return
        if asyncio.get_running_loop().time() >= deadline:
            await _finalize_direct_cover(run, job_id)
            async with pg_manager.get_async_session_context() as db:
                job = await ContentCoverRepository(db).get_job(job_id)
                if job is not None and job.status == "succeeded":
                    return
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
