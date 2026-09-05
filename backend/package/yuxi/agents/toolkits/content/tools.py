from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field
from sqlalchemy import select

from yuxi.agents.toolkits.registry import tool
from yuxi.content.execution_trace import build_execution_preview
from yuxi.content_cover.schemas import CoverComposeCreate, CoverGenerateCreate, PosterGenerateCreate
from yuxi.content.validators import normalize_manual_evidence, validate_content
from yuxi.repositories.content_repository import ContentRepository
from yuxi.services.content_cover_service import (
    create_cover_compose_job,
    create_cover_generate_job,
    create_poster_billboard_job,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User


_CONTENT_TOOL_RUNTIME: ContextVar[ToolRuntime | None] = ContextVar("content_tool_runtime", default=None)


@contextmanager
def content_tool_runtime(runtime: ToolRuntime):
    token = _CONTENT_TOOL_RUNTIME.set(runtime)
    try:
        yield
    finally:
        _CONTENT_TOOL_RUNTIME.reset(token)


def _effective_runtime(runtime: ToolRuntime | None) -> ToolRuntime | None:
    return runtime or _CONTENT_TOOL_RUNTIME.get()


class RuleBundleInput(BaseModel):
    rule_version_id: str = Field(description="任务锁定的创作规则版本 ID")


def _filter_strategy_rule_bundle(
    bundle: dict[str, Any], *, industry_slug: str, content_type_code: str
) -> dict[str, Any]:
    rules = [
        item
        for item in bundle.get("combination_rules") or []
        if (not item.get("industry_scope") or industry_slug in item["industry_scope"])
        and (not item.get("content_type_codes") or content_type_code in item["content_type_codes"])
    ]
    method_codes = {
        str(member.get("method_code"))
        for rule in rules
        for member in rule.get("method_members") or []
        if member.get("method_code")
    }
    method_codes.update(str(code) for rule in rules for code in rule.get("methods") or [] if code)
    title_codes = {
        str(code)
        for rule in rules
        for key in ("title_formula_candidate_codes", "title_formula_codes")
        for code in rule.get(key) or []
        if code
    }
    body_codes = {
        str(code)
        for rule in rules
        for key in ("body_formula_candidate_codes", "body_pattern_codes")
        for code in rule.get(key) or []
        if code
    }
    body_codes.update(str(rule["content_formula_code"]) for rule in rules if rule.get("content_formula_code"))
    return {
        **bundle,
        "methods": [item for item in bundle.get("methods") or [] if item.get("code") in method_codes],
        "title_formulas": [item for item in bundle.get("title_formulas") or [] if item.get("code") in title_codes],
        "content_formulas": [item for item in bundle.get("content_formulas") or [] if item.get("code") in body_codes],
        "combination_rules": rules,
    }


class TaskFactsInput(BaseModel):
    task_id: str


class TaskOCRInput(BaseModel):
    task_id: str = Field(description="需要读取 OCR 结果的内容任务 ID")


class NormalizeEvidenceInput(BaseModel):
    task_id: str
    brief: dict[str, Any]


class ValidateFactsInput(BaseModel):
    title: str
    body: str
    topics: list[str] = Field(default_factory=list)
    brief: dict[str, Any]
    evidence_bundle: dict[str, Any]
    strategy: dict[str, Any]


class CreateContentCoverJobInput(BaseModel):
    task_id: str = Field(description="当前内容任务 ID；视觉方案由运行时锁定快照提供")


def _wrap_visual_text(value: str, *, max_chars_per_line: int, max_lines: int) -> str:
    if len(value) <= max_chars_per_line or "\n" in value:
        return value
    if max_lines == 2 and len(value) <= max_chars_per_line * 2:
        minimum = len(value) - max_chars_per_line
        candidates = range(minimum, max_chars_per_line + 1)
        preferred_endings = "，。！？；：、,.!?;:㎡%元万亿"
        breakpoint = min(
            candidates,
            key=lambda index: (value[index - 1] not in preferred_endings, abs(index * 2 - len(value))),
        )
        return f"{value[:breakpoint]}\n{value[breakpoint:]}"
    lines = [value[index : index + max_chars_per_line] for index in range(0, len(value), max_chars_per_line)]
    if len(lines) > max_lines:
        raise ValueError(f"文字超过模板限制的 {max_lines} 行")
    return "\n".join(lines)


def _hycanvas_template_fields(
    declarations: list[dict[str, Any]],
    *,
    visual_text: list[str],
    brief: dict[str, Any],
) -> dict[str, str]:
    """Resolve author-declared template semantics from locked content inputs."""
    form_values = brief.get("form_values") or {}
    brand = brief.get("brand") or {}
    sources = {
        "title": visual_text[0] if visual_text else "",
        "subtitle": visual_text[1] if len(visual_text) > 1 else "",
        "body_excerpt": visual_text[1] if len(visual_text) > 1 else (visual_text[0] if visual_text else ""),
        "project_name": form_values.get("project_name") or form_values.get("community_name") or "",
        "project_name_en": form_values.get("project_name_en") or form_values.get("community_name_en") or "",
        "project_area": form_values.get("project_area") or form_values.get("area") or form_values.get("area_sqm") or "",
        "designer": form_values.get("designer") or form_values.get("designer_name") or "",
        "completion_year": form_values.get("completion_year") or form_values.get("year") or "",
        "brand_name": brand.get("name") or form_values.get("brand_name") or "",
    }
    fields: dict[str, str] = {}
    for field in declarations:
        if field.get("kind") != "text" or not field.get("label"):
            continue
        label = str(field["label"])
        role = str(field.get("semanticRole") or "")
        if role == "label":
            continue
        value = str(sources.get(role) or "").strip()
        if not role:
            if "副标题" in label:
                value = sources["subtitle"]
            elif "标题" in label or "语录" in label:
                value = sources["title"]
            else:
                value = sources["body_excerpt"]
        if role == "project_area":
            match = re.search(r"\d+(?:\.\d+)?", value)
            value = match.group(0) if match else ""
        elif role == "completion_year":
            match = re.search(r"(?:19|20)\d{2}", value)
            value = match.group(0) if match else ""
        constraints = field.get("constraints") or {}
        if constraints.get("required") and not value:
            raise ValueError(f"封面模板必填字段“{label}”在事实简报中没有对应内容")
        max_chars = constraints.get("maxChars")
        if isinstance(max_chars, int) and max_chars > 0 and len(value.replace("\n", "")) > max_chars:
            raise ValueError(f"封面字段“{label}”超过模板限制的 {max_chars} 个字符")
        max_chars_per_line = constraints.get("maxCharsPerLine")
        max_lines = constraints.get("maxLines")
        if (
            isinstance(max_chars_per_line, int)
            and max_chars_per_line > 0
            and isinstance(max_lines, int)
            and max_lines > 1
            and "\n" not in value
            and len(value) > max_chars_per_line
        ):
            try:
                value = _wrap_visual_text(
                    value,
                    max_chars_per_line=max_chars_per_line,
                    max_lines=max_lines,
                )
            except ValueError as exc:
                raise ValueError(f"封面字段“{label}”超过模板限制的 {max_lines} 行") from exc
        fields[label] = value
    return fields


def _runtime_uid(runtime: ToolRuntime | None) -> str:
    runtime = _effective_runtime(runtime)
    context = getattr(runtime, "context", None)
    uid = context.get("uid") if isinstance(context, Mapping) else getattr(context, "uid", None)
    if not uid:
        config = getattr(runtime, "config", None)
        configurable = config.get("configurable") if isinstance(config, Mapping) else None
        uid = configurable.get("uid") if isinstance(configurable, Mapping) else None
    if not uid:
        raise ValueError("无法获取当前用户")
    return str(uid)


async def _emit_content_tool_event(runtime: ToolRuntime | None, event_type: str, payload: dict[str, Any]) -> None:
    runtime = _effective_runtime(runtime)
    context = getattr(runtime, "context", None)
    run_id = str(getattr(context, "run_id", "") or "").strip()
    if not run_id or not getattr(context, "_content_node_output_contract", None):
        return
    from yuxi.services.run_queue_service import append_content_runtime_event

    await append_content_runtime_event(context, event_type, payload)


@tool(
    category="buildin",
    tags=["内容生产", "规则"],
    display_name="读取创作规则包",
    args_schema=RuleBundleInput,
)
async def get_creation_rule_bundle(rule_version_id: str, runtime: ToolRuntime = None) -> dict[str, Any]:
    """读取指定版本的创作手法、标题公式、正文公式和组合规则。"""
    _runtime_uid(runtime)
    runtime = _effective_runtime(runtime)
    await _emit_content_tool_event(
        runtime,
        "content.tool.called",
        {"tool_name": "get_creation_rule_bundle", "input_preview": {"rule_version_id": rule_version_id}},
    )
    async with pg_manager.get_async_session_context() as db:
        repository = ContentRepository(db)
        bundle = await repository.get_rule_bundle(rule_version_id)
        if bundle is None:
            raise ValueError("规则版本不存在")
        context = getattr(runtime, "context", None)
        task_id = str(getattr(context, "_content_task_id", "") or "")
        if task_id:
            task = await repository.get_task(task_id)
            pack = await repository.get_industry_pack(task.industry_pack_version_id) if task else None
            if task and pack and task.content_type_code:
                bundle = _filter_strategy_rule_bundle(
                    bundle,
                    industry_slug=pack.slug,
                    content_type_code=task.content_type_code,
                )
                if not bundle["combination_rules"]:
                    raise ValueError("当前行业和内容类型没有可用的创作组合规则")
        await _emit_content_tool_event(
            runtime,
            "content.tool.completed",
            {
                "tool_name": "get_creation_rule_bundle",
                "output_preview": build_execution_preview(
                    {
                        "创作手法": [item.get("name") for item in bundle.get("methods") or [] if item.get("name")],
                        "标题公式": [
                            item.get("name") for item in bundle.get("title_formulas") or [] if item.get("name")
                        ],
                        "正文公式": [
                            {
                                "名称": item.get("name"),
                                "内容结构": item.get("structure_schema") or [],
                            }
                            for item in bundle.get("content_formulas") or []
                            if item.get("name")
                        ],
                        "组合规则": [
                            item.get("scenario_description")
                            for item in bundle.get("combination_rules") or []
                            if item.get("scenario_description")
                        ],
                    }
                ),
            },
        )
        return bundle


@tool(
    category="buildin",
    tags=["内容生产", "业务事实"],
    display_name="读取内容任务业务事实",
    args_schema=TaskFactsInput,
)
async def get_business_facts(task_id: str, runtime: ToolRuntime = None) -> dict[str, Any]:
    """读取当前用户可访问任务中已经冻结的业务事实和证据包。"""
    uid = _runtime_uid(runtime)
    await _emit_content_tool_event(
        runtime,
        "content.tool.called",
        {"tool_name": "get_business_facts", "task_id": task_id},
    )
    async with pg_manager.get_async_session_context() as db:
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one_or_none()
        if user is None:
            raise ValueError("用户不存在")
        task = await ContentRepository(db).get_task_for_user(task_id, user)
        if task is None:
            raise ValueError("内容任务不存在或无权访问")
        brief = task.brief_json or {}
        record_version = task.updated_at.isoformat() if task.updated_at else "unknown"
        fields = [
            {
                "field_path": key,
                "value": value,
                "source": {
                    "record_id": task.id,
                    "record_version": record_version,
                    "field_path": key,
                },
            }
            for key, value in sorted(brief.items())
        ]
        result = {
            "task_id": task.id,
            "brief": brief,
            "evidence_bundle": task.evidence_json or {"items": []},
            "records": [
                {
                    "record_id": task.id,
                    "record_version": record_version,
                    "record_type": "content_task_brief",
                    "fields": fields,
                }
            ],
        }
        await _emit_content_tool_event(
            runtime,
            "content.business_data.retrieved",
            {"source_ids": [task.id], "record_count": 1, "field_count": len(fields)},
        )
        await _emit_content_tool_event(
            runtime,
            "content.tool.completed",
            {"tool_name": "get_business_facts", "task_id": task.id, "record_count": 1},
        )
        return result


@tool(
    category="buildin",
    tags=["内容生产", "OCR", "业务素材"],
    display_name="读取内容任务 OCR 结果",
    args_schema=TaskOCRInput,
)
async def get_content_ocr_results(task_id: str, runtime: ToolRuntime = None) -> dict[str, Any]:
    """读取当前用户可访问内容任务中已持久化的 OCR 原始结果和校对结果。"""
    uid = _runtime_uid(runtime)
    async with pg_manager.get_async_session_context() as db:
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one_or_none()
        if user is None:
            raise ValueError("用户不存在")
        repo = ContentRepository(db)
        task = await repo.get_task_for_user(task_id, user)
        if task is None:
            raise ValueError("内容任务不存在或无权访问")
        items = await repo.list_ocr_results(task.id)
        return {"task_id": task.id, "items": [item.to_dict() for item in items]}


@tool(
    category="buildin",
    tags=["内容生产", "证据"],
    display_name="标准化内容证据",
    args_schema=NormalizeEvidenceInput,
)
async def normalize_content_evidence(
    task_id: str, brief: dict[str, Any], runtime: ToolRuntime = None
) -> dict[str, Any]:
    """把业务简报标准化为带稳定来源的 EvidenceBundle。"""
    _runtime_uid(runtime)
    return normalize_manual_evidence(task_id, brief)


@tool(
    category="buildin",
    tags=["内容生产", "审核"],
    display_name="校验内容事实",
    args_schema=ValidateFactsInput,
)
async def validate_content_facts(
    title: str,
    body: str,
    topics: list[str],
    brief: dict[str, Any],
    evidence_bundle: dict[str, Any],
    strategy: dict[str, Any],
    runtime: ToolRuntime = None,
) -> dict[str, Any]:
    """确定性校验数字来源、禁止表达、必含词和策略快照。"""
    _runtime_uid(runtime)
    return validate_content(
        title=title,
        body=body,
        topics=topics,
        brief=brief,
        evidence_bundle=evidence_bundle,
        strategy=strategy,
    )


@tool(
    category="buildin",
    tags=["内容生产", "封面"],
    display_name="创建内容封面任务",
    args_schema=CreateContentCoverJobInput,
)
async def create_content_cover_job(
    task_id: str,
    runtime: ToolRuntime = None,
) -> dict[str, Any]:
    """根据已锁定 VisualPlan 幂等创建 CoverJob；本工具不等待任务完成。"""

    runtime = _effective_runtime(runtime)
    uid = _runtime_uid(runtime)
    context = getattr(runtime, "context", None)
    if getattr(context, "_content_node_output_contract", None) != "CoverJobSubmissionResultV1":
        raise ValueError("create_content_cover_job 只允许在封面提交节点调用")
    collector = getattr(context, "_content_node_result_collector", None)
    domain = getattr(collector, "domain_context", None)
    node_input = getattr(context, "_content_node_input", None)
    if node_input is None or node_input.task_id != task_id:
        raise ValueError("封面任务与当前内容节点不一致")
    governance = getattr(context, "_content_node_governance", None) or {}
    locked_values = governance.get("locked_values") or {}
    visual_plan_payload = locked_values.get("visual_plan")
    if not isinstance(visual_plan_payload, dict) or not visual_plan_payload:
        raise ValueError("封面提交节点缺少锁定 VisualPlan")

    from yuxi.content.model.contracts.content_nodes import VisualPlanResultV1

    plan_hash = str(locked_values.get("visual_plan_hash") or "")
    visual_plan = VisualPlanResultV1.model_validate(
        {key: value for key, value in visual_plan_payload.items() if key != "plan_hash"}
    )
    if domain is None or domain.visual_plan_hash != plan_hash:
        raise ValueError("plan_hash 与锁定 VisualPlan 不一致")
    if visual_plan_payload.get("plan_hash") != plan_hash:
        raise ValueError("锁定 VisualPlan 快照 hash 不一致")
    source_asset_ids = list(visual_plan.source_asset_ids)
    if not set(source_asset_ids).issubset(domain.allowed_asset_ids):
        raise ValueError("封面任务引用了未授权素材")

    mode = visual_plan.mode
    size = f"{visual_plan.size.width}x{visual_plan.size.height}"
    text = list(visual_plan.text)
    workflow_resume = {
        "parent_run_id": node_input.parent_run_id,
        "node_id": "wait_cover_job",
        "expected_state_version": int(locked_values.get("state_version") or 0),
    }
    await _emit_content_tool_event(
        runtime,
        "content.tool.called",
        {"tool_name": "create_content_cover_job", "task_id": task_id},
    )

    idempotency_key = "content-v3-" + hashlib.sha256(f"{uid}:{task_id}:{plan_hash}".encode()).hexdigest()
    async with pg_manager.get_async_session_context() as db:
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one_or_none()
        if user is None:
            raise ValueError("用户不存在")
        task = await ContentRepository(db).get_task_for_user(task_id, user)
        if task is None:
            raise ValueError("内容任务不存在")
        visual_material = (task.runtime_config_snapshot_json or {}).get("visual_material") or {}
        locked_image_asset_id = visual_material.get("image_asset_id")
        if locked_image_asset_id and source_asset_ids != [locked_image_asset_id]:
            raise ValueError("视觉方案未使用任务锁定的唯一图库图片")
        hycanvas_template_id = visual_material.get("hycanvas_template_id")
        poster_template_id = visual_material.get("poster_template_id")
        if hycanvas_template_id:
            from yuxi.services.content_cover_service import create_hycanvas_cover_job

            fillable_fields = visual_material.get("hycanvas_fillable_fields") or []
            fields = _hycanvas_template_fields(
                fillable_fields,
                visual_text=text,
                brief=task.brief_json or {},
            )
            image_field_label = None
            for field in fillable_fields:
                if not field.get("label"):
                    continue
                label = str(field["label"])
                if field.get("kind") == "image":
                    image_field_label = label
                    continue
            result = await create_hycanvas_cover_job(
                db,
                user,
                content_task_id=task_id,
                source_asset_id=locked_image_asset_id,
                template_id=hycanvas_template_id,
                title=text[0],
                fields=fields,
                image_field_label=image_field_label,
                idempotency_key=idempotency_key,
                parameters={
                    "visual_plan_hash": plan_hash,
                    "workflow_resume": workflow_resume,
                },
            )
        elif poster_template_id:
            from yuxi.repositories.content_cover_repository import ContentCoverRepository

            poster = await ContentCoverRepository(db).get_poster_template_for_user(poster_template_id, str(user.uid))
            if (
                poster is None
                or poster.status != "ready"
                or poster.checksum != visual_material.get("poster_template_checksum")
                or poster.version != visual_material.get("poster_template_version")
            ):
                raise ValueError("任务锁定的封面模板已变更或不可用，请复制任务后重新选择")
            result = await create_poster_billboard_job(
                db,
                user,
                PosterGenerateCreate(
                    poster_template_id=poster_template_id,
                    product_asset_id=locked_image_asset_id,
                    content_task_id=task_id,
                    title=text[0],
                    enhance_with_image2=False,
                    n=1,
                    parameters={
                        "visual_plan_hash": plan_hash,
                        "workflow_resume": workflow_resume,
                    },
                    idempotency_key=idempotency_key,
                ),
            )
        elif mode == "template":
            result = await create_cover_compose_job(
                db,
                user,
                CoverComposeCreate(
                    asset_ids=source_asset_ids,
                    template_id="grid_3x3",
                    theme_id="editorial_ink",
                    size=size,
                    layout={
                        "title": text[0],
                        "subtitle": text[1] if len(text) > 1 else "",
                        "workflow_resume": workflow_resume,
                        "visual_plan_hash": plan_hash,
                    },
                    content_task_id=task_id,
                    idempotency_key=idempotency_key,
                ),
            )
        else:
            if not source_asset_ids:
                resolved_provider_mode = "text_to_image"
            elif len(source_asset_ids) == 1:
                resolved_provider_mode = "image_to_image"
            else:
                resolved_provider_mode = "multi_reference"
            result = await create_cover_generate_job(
                db,
                user,
                CoverGenerateCreate(
                    mode=resolved_provider_mode,
                    content_task_id=task_id,
                    source_asset_ids=source_asset_ids,
                    title=text[0],
                    prompt="；".join(text),
                    size=size,
                    n=1,
                    parameters={
                        "visual_plan_hash": plan_hash,
                        "workflow_resume": workflow_resume,
                    },
                    idempotency_key=idempotency_key,
                ),
            )
    job = result["job"]
    await _emit_content_tool_event(
        runtime,
        "content.cover.started",
        {
            "task_id": task_id,
            "cover_job_id": job["id"],
            "mode": job["mode"],
            "deduplicated": result["deduplicated"],
        },
    )
    await _emit_content_tool_event(
        runtime,
        "content.tool.completed",
        {
            "tool_name": "create_content_cover_job",
            "task_id": task_id,
            "cover_job_id": job["id"],
        },
    )
    submission = {
        "cover_job_id": job["id"],
        "plan_hash": plan_hash,
        "source_asset_ids": source_asset_ids,
    }
    setattr(context, "_content_cover_job_submission", submission)
    return submission
