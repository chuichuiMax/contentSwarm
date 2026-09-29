from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.run_queue_service import get_arq_pool
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentMaterialLibraryItem,
    ImageDesignLibraryItem,
    ImageDesignMpDraft,
    ImageDesignJob,
    ImageDesignRefinement,
    ImageDesignAnalysis,
)
from yuxi.utils.datetime_utils import format_utc_datetime, utc_now_naive
from yuxi.utils.upload_utils import read_upload_with_limit

from .mp_schemas import MpDrafts, MpDraftsUpdate, MpLibraryCreate, MpPolishCreate, MpTaskCreate, SaveTarget
from .save_targets import validate_mp_save_target
from .schemas import (
    ANALYSIS_SCHEMA_VERSION,
    CrossSpaceRefinementCreate,
    ImageDesignGenerateCreate,
    RoomAdaptRefinementCreate,
    StyleTransferRefinementCreate,
)
from .workflow_profiles import SPACE_LABELS

MP_SPACE_IDS = {label: space_id for space_id, label in SPACE_LABELS.items()}
MP_LAYOUT_IDS = {
    "一字型沙发墙": "sofa_wall",
    "L 型沙发 + 单人椅": "l_sofa",
    "沙发对坐式": "face_to_face",
    "无主沙发自由布局": "free_layout",
}
MP_ADDON_IDS = {
    "落地窗旁休闲躺椅": "lounge_chair",
    "沙发后长条书桌/吧台": "bar_table",
    "电视墙满墙收纳柜": "storage_wall",
    "开放式层板展示架": "shelf",
    "地毯划分沙发区": "rug_zone",
    "壁炉居中": "fireplace",
}


def _error(code: str, message: str, status_code: int = 404) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


async def get_drafts(db: AsyncSession, user: User) -> dict[str, Any]:
    row = await db.scalar(select(ImageDesignMpDraft).where(ImageDesignMpDraft.owner_uid == str(user.uid)))
    stored = row.drafts_json if row else {}
    # Only the three public workflows are returned, including for older stored drafts.
    return {
        "drafts": MpDrafts.model_validate(
            {key: stored[key] for key in MpDrafts.model_fields if key in stored}
        ).model_dump()
    }


async def save_drafts(db: AsyncSession, user: User, payload: MpDraftsUpdate) -> dict[str, Any]:
    owner_uid = str(user.uid)
    row = await db.scalar(select(ImageDesignMpDraft).where(ImageDesignMpDraft.owner_uid == owner_uid))
    if row is None:
        try:
            async with db.begin_nested():
                row = ImageDesignMpDraft(
                    owner_uid=owner_uid,
                    tenant_id=str(user.department_id) if user.department_id is not None else None,
                    drafts_json=payload.drafts.model_dump(),
                )
                db.add(row)
                await db.flush()
        except IntegrityError:
            row = await db.scalar(select(ImageDesignMpDraft).where(ImageDesignMpDraft.owner_uid == owner_uid))
            if row is None:
                raise
    row.drafts_json = payload.drafts.model_dump()
    await db.commit()
    return {"drafts": payload.drafts.model_dump()}


def serialize_mp_library_item(row: ImageDesignLibraryItem, asset: ContentCoverAsset) -> dict[str, Any]:
    return {
        "id": row.id,
        "source_item_id": row.source_material_item_id,
        "asset_id": row.asset_id,
        "file_url": f"/api/mp/image-design/library/{row.id}/file",
        "thumbnail_file_url": f"/api/mp/image-design/library/{row.id}/thumbnail",
        "file_name": asset.original_file_name,
        "source_role": row.source_role,
        "created_at": format_utc_datetime(row.created_at),
    }


def _library_query(db: AsyncSession, user: User):
    owner_uid = str(user.uid)
    material = ContentMaterialLibraryItem
    asset = ContentCoverAsset
    reference = ImageDesignLibraryItem
    source_access = (
        select(material.id)
        .where(
            material.id == reference.source_material_item_id,
            material.asset_id == asset.id,
            material.owner_uid == asset.owner_uid,
            material.material_type == "image",
            material.status == "enabled",
            material.deleted_at.is_(None),
            MaterialLibraryRepository(db, include_shared=True).item_access(owner_uid),
        )
        .correlate(reference, asset)
        .exists()
    )
    return (
        select(reference, asset)
        .join(asset, asset.id == reference.asset_id)
        .where(
            reference.owner_uid == owner_uid,
            asset.deleted_at.is_(None),
            or_(
                and_(
                    reference.source_material_item_id.is_(None),
                    asset.owner_uid == owner_uid,
                    asset.role == "image_design_input",
                ),
                source_access,
            ),
        )
    )


async def get_library_item_and_asset(db: AsyncSession, user: User, item_id: str):
    row = (await db.execute(_library_query(db, user).where(ImageDesignLibraryItem.id == item_id))).one_or_none()
    if row is None:
        raise _error("IMAGE_DESIGN_MATERIAL_NOT_FOUND", "图片不存在、已下架或当前用户无权使用")
    return row


async def list_library(db: AsyncSession, user: User, *, page: int, page_size: int) -> dict[str, Any]:
    query = _library_query(db, user).where(ImageDesignLibraryItem.hidden_at.is_(None))
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await db.execute(
            query.order_by(ImageDesignLibraryItem.created_at.desc(), ImageDesignLibraryItem.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    hashes = {asset.sha256 for _, asset in rows if asset.sha256}
    analyses = (
        (
            await db.execute(
                select(
                    ImageDesignAnalysis.asset_sha256,
                    ImageDesignAnalysis.analysis_role,
                ).where(
                    ImageDesignAnalysis.owner_uid == str(user.uid),
                    ImageDesignAnalysis.asset_sha256.in_(hashes),
                    ImageDesignAnalysis.schema_version == ANALYSIS_SCHEMA_VERSION,
                    ImageDesignAnalysis.status == "completed",
                )
            )
        ).all()
        if hashes
        else []
    )
    roles_by_hash: dict[str, set[str]] = {}
    for sha256, role in analyses:
        roles_by_hash.setdefault(sha256, set()).add(role)
    return {
        "items": [
            {
                **serialize_mp_library_item(row, asset),
                "recognized_roles": sorted(roles_by_hash.get(asset.sha256, set())),
            }
            for row, asset in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def remove_library_item(db: AsyncSession, user: User, item_id: str) -> dict:
    # Hide only this account's reference; existing draft/task inputs and PC assets remain readable.
    row = await db.scalar(
        select(ImageDesignLibraryItem)
        .where(
            ImageDesignLibraryItem.id == item_id,
            ImageDesignLibraryItem.owner_uid == str(user.uid),
        )
        .with_for_update()
    )
    if row is None:
        raise _error("IMAGE_DESIGN_MATERIAL_NOT_FOUND", "图库图片不存在")
    if row.hidden_at is None:
        row.hidden_at = utc_now_naive()
    await db.commit()
    return {"success": True, "id": item_id}


async def add_library_item(db: AsyncSession, user: User, payload: MpLibraryCreate) -> dict[str, Any]:
    owner_uid = str(user.uid)
    repo = MaterialLibraryRepository(db, include_shared=True)
    source = await repo.get_item_for_user(payload.source_library_item_id, owner_uid)
    if source is None or source.material_type != "image" or source.status != "enabled":
        raise _error("IMAGE_DESIGN_MATERIAL_NOT_FOUND", "素材不存在、已下架或当前用户无权使用")
    if payload.source_gallery_id is not None and payload.source_gallery_id != source.category:
        raise _error("IMAGE_DESIGN_SOURCE_GALLERY_INVALID", "来源图库与素材不匹配", 422)
    asset = await repo.get_asset(source.asset_id, source.owner_uid)
    if asset is None:
        raise _error("IMAGE_DESIGN_MATERIAL_FILE_MISSING", "素材文件不存在")
    query = select(ImageDesignLibraryItem).where(
        ImageDesignLibraryItem.owner_uid == owner_uid,
        ImageDesignLibraryItem.asset_id == asset.id,
    )
    row = await db.scalar(query)
    if row is None:
        try:
            async with db.begin_nested():
                row = ImageDesignLibraryItem(
                    id=f"idli_{uuid.uuid4().hex}",
                    owner_uid=owner_uid,
                    tenant_id=str(user.department_id) if user.department_id is not None else None,
                    asset_id=asset.id,
                    source_material_item_id=source.id,
                    source_gallery_id=source.category,
                    source_role=payload.source_role,
                )
                db.add(row)
                await db.flush()
        except IntegrityError:
            row = await db.scalar(query)
            if row is None:
                raise
    # A material may be moved/re-added while its underlying asset remains the same.
    row.source_material_item_id = source.id
    row.source_gallery_id = source.category
    row.source_role = payload.source_role
    row.hidden_at = None
    await db.commit()
    return {"item": serialize_mp_library_item(row, asset)}


async def upload_image(db: AsyncSession, user: User, file: UploadFile, *, role: str) -> dict[str, Any]:
    from yuxi.services.material_library_service import MAX_MATERIAL_BYTES, MATERIAL_LIBRARY_BUCKET, _normalize_image
    from yuxi.storage.minio import get_minio_client

    if not file.filename:
        raise _error("MATERIAL_FILE_NAME_REQUIRED", "无法识别上传文件名", 400)
    try:
        raw = await read_upload_with_limit(
            file, max_size_bytes=MAX_MATERIAL_BYTES, too_large_message="图片过大，当前仅支持 100 MB 以内的文件"
        )
    except ValueError as exc:
        raise _error("MATERIAL_IMAGE_TOO_LARGE", str(exc), 400) from exc
    normalized, width, height, content_type = _normalize_image(raw)
    if len(normalized) > MAX_MATERIAL_BYTES:
        raise _error("MATERIAL_IMAGE_TOO_LARGE", "图片规范化后超过 100 MB", 400)
    owner_uid = str(user.uid)
    tenant_id = str(user.department_id) if user.department_id is not None else None
    asset_id = f"cca_{uuid.uuid4().hex}"
    storage = get_minio_client()
    uploaded = await storage.aupload_file(
        bucket_name=MATERIAL_LIBRARY_BUCKET,
        object_name=f"image-design/{owner_uid}/inputs/{asset_id}.webp",
        data=normalized,
        content_type=content_type,
    )
    try:
        asset = ContentCoverAsset(
            id=asset_id,
            owner_uid=owner_uid,
            tenant_id=tenant_id,
            role="image_design_input",
            original_file_name=Path(file.filename.replace("\\", "/")).name,
            content_type=content_type,
            file_size=len(normalized),
            image_width=width,
            image_height=height,
            sha256=hashlib.sha256(normalized).hexdigest(),
            bucket_name=uploaded.bucket_name,
            object_name=uploaded.object_name,
            metadata_json={"input_role": role, "source_channel": "mp", "source_folder": "uploads"},
        )
        db.add(asset)
        await db.flush()
        from yuxi.services.material_library_service import create_library_item_for_asset
        from yuxi.services.personal_materials import upload_category

        upload_folder = await upload_category(db, user, "uploads", "mp")
        material_item = await create_library_item_for_asset(
            db, asset=asset, material_type="image", name=Path(file.filename).stem,
            category=upload_folder.id, category_owner_uid=upload_folder.owner_uid,
            metadata={"source_channel": "mp", "source_folder": "uploads"},
        )
        # Removing it from 我的上传 must not destroy the separate 生图 input history.
        material_item.metadata_json = {**(material_item.metadata_json or {}), "retain_asset_on_delete": True}
        row = ImageDesignLibraryItem(
            id=f"idli_{uuid.uuid4().hex}",
            owner_uid=owner_uid,
            tenant_id=tenant_id,
            asset_id=asset.id,
            source_role="upload",
        )
        db.add(row)
        await db.flush()
        await db.commit()
    except Exception:
        await db.rollback()
        await storage.adelete_file(uploaded.bucket_name, uploaded.object_name)
        raise
    return {"item": serialize_mp_library_item(row, asset)}


async def read_library_file(db: AsyncSession, user: User, item_id: str, *, thumbnail: bool = False):
    row, asset = await get_library_item_and_asset(db, user, item_id)
    if row.source_material_item_id:
        from yuxi.services.material_upload_queue import read_material_bytes

        data = await read_material_bytes(asset)
    else:
        from yuxi.storage.minio import get_minio_client

        data = await get_minio_client().adownload_file(asset.bucket_name, asset.object_name)
    if thumbnail:
        from yuxi.services.material_library_service import _make_image_thumbnail

        return _make_image_thumbnail(data), "image/jpeg"
    return data, asset.content_type


def map_ratio(value: str) -> str:
    return {"portrait": "3:4", "landscape": "4:3", "square": "1:1"}[value]


def map_quality(value: str) -> str:
    return {"1k": "1K", "2k": "2K"}[value]


def refinement_payload(payload: MpPolishCreate):
    materials = {image.role: image.library_item_id for image in payload.images}
    common = {"user_prompt": payload.description or "按所选图片要求生成"}
    if payload.workflow == "redesign":
        return StyleTransferRefinementCreate(
            workflow="style_transfer",
            source_material_id=materials["source"],
            style_label=payload.style or None,
            use_prompt_as_style=not bool(payload.style),
            **common,
        )
    if payload.workflow == "adapt":
        return RoomAdaptRefinementCreate(
            workflow="room_adapt",
            style_reference_material_id=materials["reference"],
            raw_structure_material_id=materials["rough"],
            **common,
        )
    space = MP_SPACE_IDS.get(payload.target_space)
    layout = MP_LAYOUT_IDS.get(payload.layout_type)
    if not space or not layout or any(element not in MP_ADDON_IDS for element in payload.extra_element):
        raise _error("IMAGE_DESIGN_TRANSFER_OPTION_INVALID", "目标空间、布局或附加元素不在小程序支持的选项中", 422)
    # Shared MP choices retain their meaning in every space, using internal core
    # IDs instead of substituting an unrelated space-specific layout or element.
    prefix = "" if space == "living_room" else "mp_"
    return CrossSpaceRefinementCreate(
        workflow="cross_space",
        style_reference_material_id=materials["reference"],
        target_space=space,
        layout=f"{prefix}{layout}",
        addons=[f"{prefix}{MP_ADDON_IDS[element]}" for element in payload.extra_element],
        **common,
    )


async def polish(db: AsyncSession, user: User, payload: MpPolishCreate) -> dict[str, Any]:
    from .service import create_refinement

    result = await create_refinement(db, user, refinement_payload(payload))
    refinement = result["refinement"]
    return {"polished_prompt": refinement["compiled_prompt"], "refinement_id": refinement["id"]}


def serialize_task(job: dict[str, Any]) -> dict[str, Any]:
    request, result = job.get("request") or {}, job.get("result") or {}
    status = {"succeeded": "completed", "polling": "running"}.get(job["status"], job["status"])
    completed = len(result.get("asset_ids") or [])
    missing = max(0, int(request.get("gen_count") or 1) - completed)
    return {
        "id": job["id"],
        "task_id": job["id"],
        "workflow": {"style_transfer": "redesign", "room_adapt": "adapt", "cross_space": "transfer"}[job["workflow"]],
        "status": status,
        "status_text": {
            "queued": "正在排队…",
            "running": "正在生成图片…",
            "completed": "生成完成",
            "failed": "生成失败",
            "cancelled": "已取消",
        }.get(status, status),
        "progress": job.get("progress", 0),
        "created_at": job.get("created_at"),
        "error_code": job.get("error_code"),
        "error_message": job.get("error_message"),
        "completed_count": completed,
        "failed_count": missing if status == "failed" else 0,
        "can_retry": status == "failed" and missing > 0,
        "requested_save_target": request.get("requested_save_target"),
        "resolved_save_target": result.get("resolved_save_target"),
        "save_warning": result.get("save_warning"),
        "result_assets": [
            {"id": asset_id, "file_url": f"/api/mp/image-design/results/{asset_id}/file"}
            for asset_id in result.get("asset_ids") or []
        ],
    }


async def create_task(db: AsyncSession, user: User, payload: MpTaskCreate) -> dict[str, Any]:
    row = await db.scalar(
        select(ImageDesignRefinement).where(
            ImageDesignRefinement.id == payload.refinement_id,
            ImageDesignRefinement.owner_uid == str(user.uid),
        )
    )
    if row is None:
        raise _error("IMAGE_DESIGN_REFINEMENT_INVALID", "提示词优化记录不存在")
    converted = refinement_payload(payload)
    semantic = converted.model_dump(mode="json", exclude={"parent_refinement_id", "edited_prompt"})
    semantic["effective_prompt"] = converted.user_prompt
    if (
        row.status != "completed"
        or row.workflow != converted.workflow
        or semantic != (row.request_json or {}).get("semantic")
    ):
        raise _error("IMAGE_DESIGN_REFINEMENT_STALE", "输入已变化，请重新进行 AI 深度优化", 409)
    from .service import create_generate_job

    # The core rechecks current input access, hashes, plan integrity and target permissions.
    await validate_mp_save_target(db, user, payload.save_target)
    result = await create_generate_job(
        db,
        user,
        ImageDesignGenerateCreate(
            refinement_id=row.id,
            aspect_ratio=map_ratio(payload.ratio),
            gen_count=payload.count,
            clarity=map_quality(payload.quality),
            save_target=payload.save_target,
            idempotency_key=payload.idempotency_key,
        ),
        mp_fixed_target=True,
    )
    return {"task": serialize_task(result["job"]), "reused": result["reused"]}


async def get_task(db: AsyncSession, user: User, job_id: str) -> dict[str, Any]:
    job = await db.scalar(
        select(ImageDesignJob).where(
            ImageDesignJob.id == job_id,
            ImageDesignJob.owner_uid == str(user.uid),
        )
    )
    if job is None:
        raise _error("IMAGE_DESIGN_JOB_NOT_FOUND", "图片设计任务不存在")
    return {"task": serialize_task(job.to_dict())}


async def retry_task(db: AsyncSession, user: User, job_id: str) -> dict[str, Any]:
    job = await db.scalar(
        select(ImageDesignJob)
        .where(
            ImageDesignJob.id == job_id,
            ImageDesignJob.owner_uid == str(user.uid),
        )
        .with_for_update()
    )
    if job is None:
        raise _error("IMAGE_DESIGN_JOB_NOT_FOUND", "图片设计任务不存在")
    completed = len((job.result_json or {}).get("asset_ids") or [])
    requested = int((job.request_json or {}).get("gen_count") or 1)
    if job.status != "failed" or completed >= requested:
        raise _error("IMAGE_DESIGN_RETRY_INVALID", "仅可补生成失败任务中尚未完成的图片", 409)
    target = (job.request_json or {}).get("requested_save_target")
    if not target:
        raise _error("IMAGE_DESIGN_SAVE_TARGET_INVALID", "旧任务未指定保存位置，请重新生成", 422)
    await validate_mp_save_target(db, user, SaveTarget.model_validate(target))
    job.request_json = {**job.request_json, "mp_fixed_target": True}
    job.status = "queued"
    job.error_code = job.error_message = None
    job.completed_at = job.started_at = None
    job.progress = int(completed * 90 / requested)
    job.updated_at = utc_now_naive()
    await db.commit()
    try:
        queue = await get_arq_pool()
        # ARQ retains completed job keys; each explicit retry needs a new delivery ID.
        queued = await queue.enqueue_job(
            "process_image_design_job",
            job.id,
            _job_id=f"image-design:{job.id}:retry:{uuid.uuid4().hex}",
        )
        if queued is None:
            raise _error("IMAGE_DESIGN_QUEUE_REJECTED", "图片设计任务未能进入执行队列", 503)
    except Exception as exc:
        job.status = "failed"
        job.error_code = (
            "IMAGE_DESIGN_QUEUE_REJECTED" if isinstance(exc, HTTPException) else "IMAGE_DESIGN_QUEUE_UNAVAILABLE"
        )
        job.error_message = "图片设计生成队列暂不可用"
        job.completed_at = utc_now_naive()
        await db.commit()
        raise _error(job.error_code, job.error_message, 503) from exc
    return {"task": serialize_task(job.to_dict())}


async def list_results(db: AsyncSession, user: User, *, page: int, page_size: int) -> dict[str, Any]:
    query = (
        select(ContentCoverAsset, ImageDesignJob)
        .join(
            ImageDesignJob,
            ContentCoverAsset.metadata_json["image_design_job_id"].as_string() == ImageDesignJob.id,
        )
        .where(
            ContentCoverAsset.owner_uid == str(user.uid),
            ImageDesignJob.owner_uid == str(user.uid),
            ContentCoverAsset.role == "output",
            ContentCoverAsset.deleted_at.is_(None),
            ContentCoverAsset.metadata_json["domain"].as_string() == "image_design",
        )
    )
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await db.execute(
            query.order_by(ContentCoverAsset.created_at.desc(), ContentCoverAsset.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = []
    for asset, job in rows:
        task = serialize_task(job.to_dict())
        item = {
            **task,
            "id": asset.id,
            "file_url": f"/api/mp/image-design/results/{asset.id}/file",
            "image_url": f"/api/mp/image-design/results/{asset.id}/file",
            "original_url": f"/api/mp/image-design/results/{asset.id}/file",
            "file_name": asset.original_file_name,
            "created_at": format_utc_datetime(asset.created_at),
        }
        for image in (job.request_json or {}).get("image_roles") or []:
            role = (
                "source"
                if job.workflow == "style_transfer"
                else "rough"
                if image["role"] == "structure_source"
                else "reference"
            )
            item[f"{role}_image"] = {
                "library_item_id": image["material_id"],
                "file_url": f"/api/mp/image-design/tasks/{job.id}/inputs/{image['role']}/file",
            }
        items.append(item)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def read_task_input(db: AsyncSession, user: User, job_id: str, role: str):
    job = await db.scalar(
        select(ImageDesignJob).where(
            ImageDesignJob.id == job_id,
            ImageDesignJob.owner_uid == str(user.uid),
        )
    )
    if job is None:
        raise _error("IMAGE_DESIGN_JOB_NOT_FOUND", "图片设计任务不存在")
    snapshot = next(
        (image for image in (job.request_json or {}).get("image_roles") or [] if image["role"] == role), None
    )
    if snapshot is None:
        raise _error("IMAGE_DESIGN_MATERIAL_NOT_FOUND", "任务输入图片不存在")
    from .service import resolve_image_input
    from yuxi.services.material_upload_queue import read_material_bytes

    image = await resolve_image_input(db, str(user.uid), snapshot["material_id"])
    pinned = next(
        (item for item in (job.request_json or {}).get("input_snapshots") or [] if item["role"] == role), None
    )
    if pinned and (pinned["asset_id"] != image.asset.id or pinned["asset_sha256"] != image.asset_sha256):
        raise _error("IMAGE_DESIGN_REFINEMENT_STALE", "任务输入图片已变化", 409)
    return await read_material_bytes(image.asset), image.asset.content_type
