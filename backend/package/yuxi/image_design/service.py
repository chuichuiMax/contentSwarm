from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content_cover.image2_settings import get_image2_config_state
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.material_upload_queue import read_material_bytes
from yuxi.services.run_queue_service import get_arq_pool
from yuxi.storage.minio import get_minio_client
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ImageDesignAnalysis,
    ImageDesignClient,
    ImageDesignJob,
    ImageDesignRefinement,
    ImageDesignShowcase,
)
from yuxi.utils.datetime_utils import utc_now_naive

from .analysis import (
    VisualModelUnavailableError,
    analysis_cache_key,
    resolve_visual_model_spec,
    run_visual_analysis,
)
from .prompt_compiler import apply_edited_prompt, build_prompt_plan, compile_prompt, validate_plan_coverage
from .save_targets import resolve_writable_save_target
from .schemas import (
    ANALYSIS_SCHEMA_VERSION,
    ASPECT_SIZES,
    DEFAULT_VISION_MODEL_SPEC,
    PROMPT_COMPILER_SPEC,
    WORKFLOW_PROFILE_VERSION,
    CrossSpaceRefinementCreate,
    ImageDesignAnalysisCreate,
    ImageDesignClientCreate,
    ImageDesignGenerateCreate,
    ImageDesignRefinementCreate,
    ImageDesignShowcaseCreate,
    PromptPlan,
    RoomAdaptRefinementCreate,
    StyleTransferRefinementCreate,
)
from .workflow_profiles import public_profiles

ANALYSIS_RUNNING_TTL = timedelta(minutes=5)


def _owner_uid(user: User) -> str:
    return str(user.uid)


def _tenant_id(user: User) -> str | None:
    return str(user.department_id) if user.department_id is not None else None


def _error(code: str, message: str, code_status: int = 400, *, retryable: bool = False) -> HTTPException:
    return HTTPException(
        status_code=code_status,
        detail={"error": {"code": code, "message": message, "retryable": retryable}},
    )


def _serialize_job(job: ImageDesignJob) -> dict[str, Any]:
    data = job.to_dict()
    asset_ids = list((job.result_json or {}).get("asset_ids") or [])
    data["result_assets"] = [
        {"id": asset_id, "file_url": f"/api/image-design/results/{asset_id}/file"} for asset_id in asset_ids
    ]
    return data


def _serialize_result(asset: ContentCoverAsset) -> dict[str, Any]:
    metadata = asset.metadata_json or {}
    return {
        "id": asset.id,
        "file_url": f"/api/image-design/results/{asset.id}/file",
        "file_name": asset.original_file_name,
        "content_type": asset.content_type,
        "width": asset.image_width,
        "height": asset.image_height,
        "file_size": asset.file_size,
        "workflow": metadata.get("workflow"),
        "job_id": metadata.get("image_design_job_id"),
        "client_id": metadata.get("client_id"),
        "reference_material_id": metadata.get("reference_material_id"),
        "raw_room_material_id": metadata.get("raw_room_material_id"),
        "prompt": metadata.get("prompt", ""),
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
    }


def _serialize_refinement(row: ImageDesignRefinement) -> dict[str, Any]:
    data = row.to_dict()
    plan = PromptPlan.model_validate(row.plan_json or {})
    data["compiled_prompts"] = {aspect_ratio: compile_prompt(plan, aspect_ratio) for aspect_ratio in ASPECT_SIZES}
    return data


async def _get_material_item_and_asset(db: AsyncSession, owner_uid: str, material_id: str):
    repo = MaterialLibraryRepository(db, include_shared=True)
    item = await repo.get_item_for_user(material_id, owner_uid)
    if item is None:
        # Dedicated uploads/references are private to their owner, even when
        # their original material is shared. Recheck that source's live access.
        from .mp_service import get_library_item_and_asset

        return await get_library_item_and_asset(db, User(uid=owner_uid), material_id)
    if item.material_type != "image" or item.status != "enabled":
        raise _error("IMAGE_DESIGN_MATERIAL_NOT_FOUND", "素材不存在、已下架或当前用户无权使用", 404)
    asset = await repo.get_asset(item.asset_id, item.owner_uid)
    if asset is None:
        raise _error("IMAGE_DESIGN_MATERIAL_FILE_MISSING", "素材文件不存在", 404)
    return item, asset


@dataclass(frozen=True)
class ImageDesignInput:
    input_id: str
    asset: ContentCoverAsset
    asset_sha256: str


async def resolve_image_input(db: AsyncSession, owner_uid: str, input_id: str) -> ImageDesignInput:
    _, asset = await _get_material_item_and_asset(db, owner_uid, input_id)
    sha256 = asset.sha256 or hashlib.sha256(await read_material_bytes(asset)).hexdigest()
    return ImageDesignInput(input_id, asset, sha256)


async def list_clients(db: AsyncSession, user: User) -> dict[str, Any]:
    owner_uid = _owner_uid(user)
    clients = list(
        (
            await db.execute(
                select(ImageDesignClient)
                .where(ImageDesignClient.owner_uid == owner_uid, ImageDesignClient.deleted_at.is_(None))
                .order_by(ImageDesignClient.created_at.asc())
            )
        ).scalars()
    )
    return {"clients": [client.to_dict() for client in clients], "general": {"id": None, "name": "通用素材库"}}


async def list_showcase(db: AsyncSession, user: User, category: str | None = None) -> dict[str, Any]:
    filters = [ImageDesignShowcase.enabled.is_(True)]
    if category:
        filters.append(ImageDesignShowcase.category == category)
    rows = list(
        (
            await db.execute(
                select(ImageDesignShowcase).where(*filters).order_by(desc(ImageDesignShowcase.created_at)).limit(100)
            )
        ).scalars()
    )
    # 查询时只返回当前用户有权读取的素材；失效案例不阻塞整页展示。
    items = []
    for row in rows:
        try:
            await _get_material_item_and_asset(db, _owner_uid(user), row.image_material_id)
        except HTTPException:
            continue
        item = row.to_dict()
        item["thumbnail_url"] = f"/api/material-library/items/{row.image_material_id}/thumbnail"
        items.append(item)
    categories = sorted({str(item["category"]) for item in items})
    return {"items": items, "categories": categories}


async def create_showcase(db: AsyncSession, user: User, payload: ImageDesignShowcaseCreate) -> dict[str, Any]:
    await _get_material_item_and_asset(db, _owner_uid(user), payload.image_material_id)
    row = ImageDesignShowcase(
        id=f"ids_{uuid.uuid4().hex}",
        owner_uid=_owner_uid(user),
        title=payload.title,
        category=payload.category,
        style_text=payload.style_text,
        image_material_id=payload.image_material_id,
    )
    db.add(row)
    await db.commit()
    return {"item": row.to_dict()}


async def delete_showcase(db: AsyncSession, showcase_id: str) -> dict[str, Any]:
    row = await db.scalar(select(ImageDesignShowcase).where(ImageDesignShowcase.id == showcase_id))
    if row is None:
        raise _error("IMAGE_DESIGN_SHOWCASE_NOT_FOUND", "精选案例不存在", 404)
    row.enabled = False
    await db.commit()
    return {"success": True, "id": showcase_id}


async def create_client(db: AsyncSession, user: User, payload: ImageDesignClientCreate) -> dict[str, Any]:
    owner_uid = _owner_uid(user)
    client = ImageDesignClient(
        id=f"idc_{uuid.uuid4().hex}",
        owner_uid=owner_uid,
        tenant_id=_tenant_id(user),
        name=payload.name,
    )
    db.add(client)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _error("IMAGE_DESIGN_CLIENT_EXISTS", "客户名称已存在", 409) from exc
    return {"client": client.to_dict()}


async def get_bootstrap(db: AsyncSession, user: User) -> dict[str, Any]:
    image2 = await get_image2_config_state(db, owner_uid=_owner_uid(user))
    return {
        "image2": image2,
        "aspect_sizes": ASPECT_SIZES,
        "prompt_compiler_spec": PROMPT_COMPILER_SPEC,
        "vision_model_spec": os.getenv("IMAGE_DESIGN_VISION_MODEL_SPEC", DEFAULT_VISION_MODEL_SPEC),
        "profiles": public_profiles(),
    }


async def create_analysis(
    db: AsyncSession,
    user: User,
    payload: ImageDesignAnalysisCreate,
) -> dict[str, Any]:
    owner_uid = _owner_uid(user)
    _, asset = await _get_material_item_and_asset(db, owner_uid, payload.material_item_id)
    configured_model = os.getenv("IMAGE_DESIGN_VISION_MODEL_SPEC", DEFAULT_VISION_MODEL_SPEC).strip()
    try:
        model_spec = resolve_visual_model_spec(configured_model)
    except Exception as exc:
        raise _error(
            "IMAGE_DESIGN_ANALYSIS_MODEL_UNAVAILABLE",
            "图片视觉分析模型未配置或当前不可用",
            503,
            retryable=True,
        ) from exc
    asset_sha256 = asset.sha256
    if not asset_sha256:
        data = await read_material_bytes(asset)
        asset_sha256 = hashlib.sha256(data).hexdigest()
    else:
        data = None
    cache_key = analysis_cache_key(asset_sha256, payload.role, model_spec, ANALYSIS_SCHEMA_VERSION)
    row = await db.scalar(
        select(ImageDesignAnalysis).where(
            ImageDesignAnalysis.owner_uid == owner_uid,
            ImageDesignAnalysis.cache_key == cache_key,
        )
    )
    if row is not None and row.status == "completed":
        return {"analysis": row.to_dict(), "reused": True}
    now = utc_now_naive()
    if (
        row is not None
        and row.status == "running"
        and row.updated_at is not None
        and row.updated_at > now - ANALYSIS_RUNNING_TTL
    ):
        return {"analysis": row.to_dict(), "reused": True}
    if row is None:
        row = ImageDesignAnalysis(
            id=f"ida_{uuid.uuid4().hex}",
            owner_uid=owner_uid,
            material_item_id=payload.material_item_id,
            asset_sha256=asset_sha256,
            analysis_role=payload.role,
            schema_version=ANALYSIS_SCHEMA_VERSION,
            model_spec=model_spec,
            cache_key=cache_key,
            status="running",
        )
        db.add(row)
    else:
        row.status = "running"
        row.error_code = None
        row.error_message = None
        row.updated_at = now
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await db.scalar(
            select(ImageDesignAnalysis).where(
                ImageDesignAnalysis.owner_uid == owner_uid,
                ImageDesignAnalysis.cache_key == cache_key,
            )
        )
        if existing is not None:
            return {"analysis": existing.to_dict(), "reused": True}
        raise
    try:
        if data is None:
            data = await read_material_bytes(asset)
        result, resolved_model = await run_visual_analysis(data, payload.role, model_spec=model_spec)
        row.result_json = result.model_dump(mode="json")
        row.model_spec = resolved_model
        row.status = "completed"
        row.updated_at = utc_now_naive()
        await db.commit()
    except VisualModelUnavailableError as exc:
        row.status = "failed"
        row.error_code = "IMAGE_DESIGN_ANALYSIS_MODEL_UNAVAILABLE"
        row.error_message = "图片视觉分析模型未配置或当前不可用"
        row.updated_at = utc_now_naive()
        await db.commit()
        raise _error(row.error_code, row.error_message, 503, retryable=True) from exc
    except Exception as exc:
        row.status = "failed"
        row.error_code = "IMAGE_DESIGN_ANALYSIS_FAILED"
        row.error_message = "图片视觉分析失败，请确认视觉模型可用后重试"
        row.updated_at = utc_now_naive()
        await db.commit()
        raise _error(row.error_code, row.error_message, 503, retryable=True) from exc
    return {"analysis": row.to_dict(), "reused": False}


async def get_analysis(db: AsyncSession, user: User, analysis_id: str) -> dict[str, Any]:
    row = await db.scalar(
        select(ImageDesignAnalysis).where(
            ImageDesignAnalysis.id == analysis_id,
            ImageDesignAnalysis.owner_uid == _owner_uid(user),
        )
    )
    if row is None:
        raise _error("IMAGE_DESIGN_ANALYSIS_NOT_FOUND", "图片分析记录不存在", 404)
    return {"analysis": row.to_dict()}


def _material_roles(payload: ImageDesignRefinementCreate) -> list[tuple[str, str]]:
    if isinstance(payload, StyleTransferRefinementCreate):
        return [("structure_source", payload.source_material_id)]
    if isinstance(payload, RoomAdaptRefinementCreate):
        return [
            ("style_reference", payload.style_reference_material_id),
            ("structure_source", payload.raw_structure_material_id),
        ]
    if isinstance(payload, CrossSpaceRefinementCreate):
        return [("cross_space_style", payload.style_reference_material_id)]
    raise TypeError("unsupported image design workflow")


def refinement_fingerprint(semantic_request: dict[str, Any], materials: list[dict[str, str]]) -> str:
    source = {
        "workflow_version": WORKFLOW_PROFILE_VERSION,
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "request": semantic_request,
        "materials": materials,
    }
    return hashlib.sha256(
        json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def validate_refinement_integrity(
    refinement: ImageDesignRefinement,
    current_materials: list[dict[str, str]],
) -> PromptPlan:
    semantic_request = dict((refinement.request_json or {}).get("semantic") or {})
    current_fingerprint = refinement_fingerprint(semantic_request, current_materials)
    if (
        refinement.workflow_version != WORKFLOW_PROFILE_VERSION
        or current_fingerprint != refinement.input_fingerprint
    ):
        raise _error("IMAGE_DESIGN_REFINEMENT_STALE", "优化记录与当前语义输入不一致，请重新进行 AI 深度优化", 409)
    plan = PromptPlan.model_validate(refinement.plan_json or {})
    coverage = validate_plan_coverage(plan)
    if coverage["missing"] or coverage["unexpected"]:
        raise _error("IMAGE_DESIGN_REFINEMENT_STALE", "优化记录的生成计划已失效，请重新进行 AI 深度优化", 409)
    planned_materials = [{"role": item["role"], "material_id": item["material_id"]} for item in plan.image_roles]
    snapshot_materials = [
        {"role": item["role"], "material_id": item["material_id"]} for item in current_materials
    ]
    if planned_materials != snapshot_materials:
        raise _error("IMAGE_DESIGN_REFINEMENT_STALE", "优化记录的图片角色已失效，请重新进行 AI 深度优化", 409)
    return plan


async def create_refinement(
    db: AsyncSession,
    user: User,
    payload: ImageDesignRefinementCreate,
) -> dict[str, Any]:
    owner_uid = _owner_uid(user)
    parent: ImageDesignRefinement | None = None
    if payload.parent_refinement_id:
        parent = await db.scalar(
            select(ImageDesignRefinement).where(
                ImageDesignRefinement.id == payload.parent_refinement_id,
                ImageDesignRefinement.owner_uid == owner_uid,
            )
        )
        if parent is None or parent.workflow != payload.workflow or parent.status != "completed":
            raise _error("IMAGE_DESIGN_REFINEMENT_INVALID", "父级优化记录不存在或工作流不匹配", 409)

    analyses: dict[str, dict[str, Any]] = {}
    analysis_ids: list[str] = []
    materials: list[dict[str, str]] = []
    for role, material_id in _material_roles(payload):
        _, asset = await _get_material_item_and_asset(db, owner_uid, material_id)
        sha256 = asset.sha256
        if not sha256:
            data = await read_material_bytes(asset)
            sha256 = hashlib.sha256(data).hexdigest()
        materials.append({"role": role, "material_id": material_id, "asset_sha256": sha256})
        response = await create_analysis(
            db,
            user,
            ImageDesignAnalysisCreate(material_item_id=material_id, role=role),
        )
        analysis = response["analysis"]
        if analysis["status"] != "completed":
            raise _error("IMAGE_DESIGN_ANALYSIS_PENDING", "图片正在分析，请稍后重试", 409, retryable=True)
        analyses[role] = analysis["result"]
        analysis_ids.append(analysis["id"])

    semantic_request = payload.model_dump(
        mode="json",
        exclude={"parent_refinement_id", "edited_prompt"},
    )
    semantic_request["effective_prompt"] = payload.edited_prompt or payload.user_prompt
    if parent is not None:
        parent_request = parent.request_json or {}
        parent_semantic = dict(parent_request.get("semantic") or {})
        current_base = {key: value for key, value in semantic_request.items() if key != "effective_prompt"}
        parent_base = {key: value for key, value in parent_semantic.items() if key != "effective_prompt"}
        if current_base != parent_base or materials != list(parent_request.get("materials") or []):
            raise _error(
                "IMAGE_DESIGN_REFINEMENT_INVALID",
                "编辑版本只能修改优化结果；图片、工作流或语义选项变化后请重新优化",
                409,
            )
    try:
        if parent is not None:
            parent_plan = validate_refinement_integrity(parent, materials)
            plan = apply_edited_prompt(parent_plan, payload.edited_prompt or "")
            semantic_request["effective_prompt"] = plan.edited_prompt
        else:
            plan = build_prompt_plan(payload, analyses)
    except ValueError as exc:
        raise _error("IMAGE_DESIGN_REFINEMENT_INVALID", str(exc), 422) from exc
    fingerprint = refinement_fingerprint(semantic_request, materials)
    coverage = validate_plan_coverage(plan)
    if coverage["missing"] or coverage["unexpected"]:
        raise _error("IMAGE_DESIGN_PROMPT_COVERAGE_FAILED", "生成计划未覆盖全部有效选项", 422)
    compiled_prompt = compile_prompt(plan)
    request_snapshot = {
        "semantic": semantic_request,
        "materials": materials,
        "parent_refinement_id": payload.parent_refinement_id,
        "edited_prompt": payload.edited_prompt,
    }
    row = ImageDesignRefinement(
        id=f"idr_{uuid.uuid4().hex}",
        owner_uid=owner_uid,
        workflow=payload.workflow,
        workflow_version=WORKFLOW_PROFILE_VERSION,
        input_fingerprint=fingerprint,
        request_json=request_snapshot,
        analysis_ids_json=analysis_ids,
        plan_json=plan.model_dump(mode="json"),
        compiled_prompt=compiled_prompt,
        coverage_json=coverage,
        conflicts_json=plan.conflicts,
        model_spec=PROMPT_COMPILER_SPEC,
        status="completed",
    )
    db.add(row)
    await db.commit()
    return {"refinement": _serialize_refinement(row)}


async def get_refinement(db: AsyncSession, user: User, refinement_id: str) -> dict[str, Any]:
    row = await db.scalar(
        select(ImageDesignRefinement).where(
            ImageDesignRefinement.id == refinement_id,
            ImageDesignRefinement.owner_uid == _owner_uid(user),
        )
    )
    if row is None:
        raise _error("IMAGE_DESIGN_REFINEMENT_INVALID", "提示词优化记录不存在", 404)
    return {"refinement": _serialize_refinement(row)}


async def create_generate_job(
    db: AsyncSession, user: User, payload: ImageDesignGenerateCreate, *, mp_fixed_target: bool = False,
) -> dict[str, Any]:
    owner_uid = _owner_uid(user)
    image2 = await get_image2_config_state(db, owner_uid=owner_uid)
    if not image2.get("configured"):
        raise _error("IMAGE_DESIGN_IMAGE2_NOT_CONFIGURED", "请先配置并验证 image2 中转站", 503, retryable=True)
    refinement = await db.scalar(
        select(ImageDesignRefinement).where(
            ImageDesignRefinement.id == payload.refinement_id,
            ImageDesignRefinement.owner_uid == owner_uid,
        )
    )
    if refinement is None or refinement.status != "completed":
        raise _error("IMAGE_DESIGN_REFINEMENT_INVALID", "请先完成有效的 AI 深度优化", 409)
    material_snapshots = list((refinement.request_json or {}).get("materials") or [])
    current_materials: list[dict[str, str]] = []
    input_snapshots: list[dict[str, str]] = []
    for snapshot in material_snapshots:
        image = await resolve_image_input(db, owner_uid, snapshot["material_id"])
        current_sha = image.asset_sha256
        if current_sha != snapshot["asset_sha256"]:
            raise _error("IMAGE_DESIGN_REFINEMENT_STALE", "输入图片已变化，请重新进行 AI 深度优化", 409)
        current_materials.append(
            {
                "role": snapshot["role"],
                "material_id": snapshot["material_id"],
                "asset_sha256": current_sha,
            }
        )
        input_snapshots.append(
            {
                "role": snapshot["role"],
                "input_id": image.input_id,
                "asset_id": image.asset.id,
                "asset_sha256": image.asset_sha256,
            }
        )
    validated_plan = validate_refinement_integrity(refinement, current_materials)
    if payload.save_target is not None:
        await resolve_writable_save_target(db, user, payload.save_target)
    compiled_prompt = compile_prompt(validated_plan, payload.aspect_ratio)
    material_ids = [item["material_id"] for item in validated_plan.image_roles]
    request = {
        **payload.model_dump(mode="json", exclude={"save_target"}),
        "requested_save_target": payload.save_target.model_dump(mode="json") if payload.save_target else None,
        "mp_fixed_target": mp_fixed_target,
        "workflow": refinement.workflow,
        "prompt_contract_version": 2,
        "plan_version": validated_plan.plan_version,
        "input_fingerprint": refinement.input_fingerprint,
        "compiled_prompt": compiled_prompt,
        "size": ASPECT_SIZES[payload.aspect_ratio][payload.clarity],
        "material_ids": material_ids,
        "image_roles": validated_plan.image_roles,
        "input_snapshots": input_snapshots,
        "analysis_ids": refinement.analysis_ids_json or [],
        "reference_material_id": material_ids[0],
        "raw_room_material_id": material_ids[1] if refinement.workflow == "room_adapt" else None,
    }
    idempotency_key = payload.idempotency_key or hashlib.sha256(f"{owner_uid}:{uuid.uuid4().hex}".encode()).hexdigest()
    existing = await db.scalar(
        select(ImageDesignJob).where(
            ImageDesignJob.owner_uid == owner_uid,
            ImageDesignJob.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return {"job": _serialize_job(existing), "reused": True}
    job = ImageDesignJob(
        id=f"idj_{uuid.uuid4().hex}",
        owner_uid=owner_uid,
        tenant_id=_tenant_id(user),
        client_id=None,
        workflow=refinement.workflow,
        status="queued",
        request_json=request,
        result_json={"asset_ids": []},
        idempotency_key=idempotency_key,
        progress=0,
    )
    db.add(job)
    await db.flush()
    await db.commit()
    try:
        queue = await get_arq_pool()
        queued = await queue.enqueue_job("process_image_design_job", job.id, _job_id=f"image-design:{job.id}")
    except Exception as exc:
        job.status = "failed"
        job.error_code = "IMAGE_DESIGN_QUEUE_UNAVAILABLE"
        job.error_message = "图片设计生成队列暂不可用"
        job.completed_at = utc_now_naive()
        await db.commit()
        raise _error(job.error_code, job.error_message, 503, retryable=True) from exc
    if queued is None:
        job.status = "failed"
        job.error_code = "IMAGE_DESIGN_QUEUE_REJECTED"
        job.error_message = "图片设计任务未能进入执行队列"
        job.completed_at = utc_now_naive()
        await db.commit()
        raise _error(job.error_code, job.error_message, 503, retryable=True)
    return {"job": _serialize_job(job), "reused": False}


async def list_jobs(db: AsyncSession, user: User, *, client_id: str | None = None) -> dict[str, Any]:
    filters = [ImageDesignJob.owner_uid == _owner_uid(user)]
    if client_id:
        filters.append(ImageDesignJob.client_id == client_id)
    jobs = list(
        (
            await db.execute(select(ImageDesignJob).where(*filters).order_by(desc(ImageDesignJob.created_at)).limit(60))
        ).scalars()
    )
    return {"jobs": [_serialize_job(job) for job in jobs], "total": len(jobs)}


async def get_job(db: AsyncSession, user: User, job_id: str) -> dict[str, Any]:
    job = await db.scalar(
        select(ImageDesignJob).where(ImageDesignJob.id == job_id, ImageDesignJob.owner_uid == _owner_uid(user))
    )
    if job is None:
        raise _error("IMAGE_DESIGN_JOB_NOT_FOUND", "图片设计任务不存在", 404)
    return {"job": _serialize_job(job)}


async def list_results(db: AsyncSession, user: User, *, client_id: str | None = None) -> dict[str, Any]:
    assets = list(
        (
            await db.execute(
                select(ContentCoverAsset)
                .where(
                    ContentCoverAsset.owner_uid == _owner_uid(user),
                    ContentCoverAsset.role == "output",
                    ContentCoverAsset.deleted_at.is_(None),
                )
                .order_by(desc(ContentCoverAsset.created_at))
                .limit(200)
            )
        ).scalars()
    )
    results = []
    for asset in assets:
        metadata = asset.metadata_json or {}
        if metadata.get("domain") != "image_design":
            continue
        if client_id and metadata.get("client_id") != client_id:
            continue
        results.append(_serialize_result(asset))
    return {"results": results[:60], "total": len(results)}


async def get_result_file(db: AsyncSession, user: User, asset_id: str) -> tuple[bytes, str, str]:
    asset = await db.scalar(
        select(ContentCoverAsset).where(
            ContentCoverAsset.id == asset_id,
            ContentCoverAsset.owner_uid == _owner_uid(user),
            ContentCoverAsset.role == "output",
            ContentCoverAsset.deleted_at.is_(None),
        )
    )
    if asset is None or (asset.metadata_json or {}).get("domain") != "image_design":
        raise _error("IMAGE_DESIGN_RESULT_NOT_FOUND", "图片设计结果不存在", 404)
    try:
        data = await read_material_bytes(asset)
    except Exception as exc:
        raise _error("IMAGE_DESIGN_STORAGE_FAILED", "图片设计结果读取失败", 500, retryable=True) from exc
    return data, asset.content_type, asset.original_file_name


async def delete_result(db: AsyncSession, user: User, asset_id: str) -> dict[str, Any]:
    asset = await db.scalar(
        select(ContentCoverAsset).where(
            ContentCoverAsset.id == asset_id,
            ContentCoverAsset.owner_uid == _owner_uid(user),
            ContentCoverAsset.role == "output",
            ContentCoverAsset.deleted_at.is_(None),
        )
    )
    if asset is None or (asset.metadata_json or {}).get("domain") != "image_design":
        raise _error("IMAGE_DESIGN_RESULT_NOT_FOUND", "图片设计结果不存在", 404)
    await get_minio_client().adelete_file(asset.bucket_name, asset.object_name)
    asset.deleted_at = utc_now_naive()
    await db.commit()
    return {"success": True, "id": asset_id}
