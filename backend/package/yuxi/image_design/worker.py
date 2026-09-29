from __future__ import annotations

import asyncio
import hashlib
import io
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from PIL import Image, ImageOps
from sqlalchemy import select

from yuxi.content_cover.image2_client import Image2Client, Image2Error
from yuxi.content_cover.image2_settings import resolve_image2_config
from yuxi.content_cover.schemas import Image2Input, Image2Request, Image2Submission
from yuxi.image_design.save_targets import (
    ResolvedSaveTarget,
    resolve_mp_save_target,
    resolve_writable_save_target,
    validate_mp_save_target,
)
from yuxi.image_design.schemas import ImageDesignSaveTarget
from yuxi.image_design.service import resolve_image_input
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.material_library_service import create_library_item_for_asset
from yuxi.services.material_upload_queue import read_material_bytes
from yuxi.services.run_queue_service import clear_cancel_signal
from yuxi.storage.minio import get_minio_client
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentMaterialCategory,
    ImageDesignJob,
    ImageDesignLibraryItem,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger

POLL_INTERVAL_SECONDS = float(os.getenv("IMAGE2_POLL_INTERVAL_SECONDS", "2"))
POLL_TIMEOUT_SECONDS = float(os.getenv("IMAGE2_POLL_TIMEOUT_SECONDS", "900"))
RESULT_BUCKET = os.getenv("CONTENT_COVER_BUCKET", "content-covers")


async def _set_job(job_id: str, **values: Any) -> ImageDesignJob | None:
    async with pg_manager.get_async_session_context() as db:
        job = await db.scalar(select(ImageDesignJob).where(ImageDesignJob.id == job_id).with_for_update())
        if job is None:
            return None
        for key, value in values.items():
            setattr(job, key, value)
        job.updated_at = utc_now_naive()
        await db.commit()
        return job


async def _load_material_input(db, owner_uid: str, material_id: str) -> Image2Input:
    image = await resolve_image_input(db, owner_uid, material_id)
    asset = image.asset
    data = await read_material_bytes(asset)
    return Image2Input(data=data, content_type=asset.content_type, file_name=asset.original_file_name)


def _build_prompt(request: dict[str, Any]) -> str:
    compiled_prompt = str(request.get("compiled_prompt") or "").strip()
    if compiled_prompt:
        return compiled_prompt
    if request.get("prompt_contract_version") or request.get("refinement_id"):
        raise Image2Error("IMAGE_DESIGN_PROMPT_MISSING", "已验证的图片设计任务缺少编译后提示词")

    # Compatibility path for jobs queued before the refinement contract was introduced.
    workflow = request.get("workflow")
    user_prompt = str(request.get("prompt") or request.get("user_prompt") or "").strip()
    parts = [user_prompt]
    if workflow == "style_transfer":
        style_label = str(request.get("style_label") or "").strip()
        style_details = str(request.get("style_details") or "").strip()
        if style_label:
            parts.append(f"装修风格：{style_label}。")
        if style_details:
            parts.append(f"风格细节：{style_details}")
        parts.append("保留原房的户型结构、镜头位置和主要空间关系，只进行真实自然的装修风格换装。")
    elif workflow == "room_adapt":
        parts.append("根据参考效果图改造毛坯实拍图，严格保留毛坯图的户型框架、门窗位置和空间比例。")
    elif workflow == "cross_space":
        space = str(request.get("target_space_label") or request.get("target_space") or "目标空间")
        layout = str(request.get("space_layout_desc") or request.get("space_layout") or "").strip()
        addons = str(request.get("space_addons_desc") or "").strip()
        parts.append(f"目标空间：{space}。")
        if layout:
            parts.append(f"布局要求：{layout}")
        if addons:
            parts.append(f"附加元素：{addons}")
    parts.append("生成真实室内设计案例图，材质、光影、比例和透视自然，不要生成文字、Logo、水印或界面元素。")
    return "\n".join(item for item in parts if item)


def _build_image2_request(request: dict[str, Any], inputs: list[Image2Input]) -> Image2Request:
    return Image2Request(
        mode="multi_reference" if len(inputs) > 1 else "image_to_image",
        prompt=_build_prompt(request),
        size=str(request.get("size") or "1152x1536"),
        n=1,
        source_images=inputs,
    )


def _normalize_output(raw: bytes, expected_size: str) -> tuple[bytes, int, int]:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.load()
            width, height = image.size
            expected_width, expected_height = (int(value) for value in expected_size.split("x", 1))
            actual_ratio = width / height
            expected_ratio = expected_width / expected_height
            if abs(actual_ratio - expected_ratio) > 0.01:
                raise Image2Error(
                    "IMAGE_DESIGN_OUTPUT_SIZE_MISMATCH",
                    f"image2 返回尺寸为 {width}x{height}，与请求的 {expected_size} 不一致",
                )
            if (width, height) != (expected_width, expected_height):
                image = image.resize((expected_width, expected_height), Image.Resampling.LANCZOS)
                width, height = image.size
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=True)
            return output.getvalue(), width, height
    except Image2Error:
        raise
    except Exception as exc:
        raise Image2Error("IMAGE_DESIGN_INVALID_OUTPUT", "image2 返回的图片不是有效图片") from exc


async def _poll(client: Image2Client, result: Image2Submission) -> Image2Submission:
    if result.status != "pending":
        return result
    if not result.provider_task_id:
        raise Image2Error("IMAGE2_INVALID_RESPONSE", "image2 异步响应缺少任务 ID")
    deadline = asyncio.get_running_loop().time() + POLL_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        result = await client.poll(result.provider_task_id)
        if result.status == "completed":
            return result
        if result.status == "failed":
            raise Image2Error("IMAGE2_GENERATION_FAILED", result.error_message or "image2 生成失败")
    raise Image2Error("IMAGE2_POLL_TIMEOUT", "image2 异步任务等待超时", retryable=True)


async def _ensure_generated_library_item(db, *, user, asset, material_item):
    owner_uid = str(user.uid)
    existing = await db.scalar(
        select(ImageDesignLibraryItem).where(
            ImageDesignLibraryItem.owner_uid == owner_uid,
            ImageDesignLibraryItem.asset_id == asset.id,
        )
    )
    if existing is not None:
        return existing
    entry = ImageDesignLibraryItem(
        id=f"idl_{uuid.uuid4().hex}",
        owner_uid=owner_uid,
        tenant_id=str(user.department_id) if user.department_id is not None else None,
        asset_id=asset.id,
        source_material_item_id=material_item.id,
        source_gallery_id=material_item.category,
        source_role="generated",
    )
    db.add(entry)
    await db.flush()
    return entry


async def attach_generated_asset(db, *, user, asset, requested, job_id, workflow, mp_fixed_target=False):
    """Attach in the caller's transaction, retaining a completed attachment on retry."""
    existing = await MaterialLibraryRepository(db, include_shared=True).get_item_by_asset(asset.id)
    if existing is not None:
        metadata = existing.metadata_json or {}
        saved = metadata["resolved_save_target"]
        resolved = ResolvedSaveTarget(
            saved["scope"],
            saved["gallery_id"],
            existing.category,
            existing.category_owner_uid,
            metadata.get("save_warning"),
        )
        await _ensure_generated_library_item(db, user=user, asset=asset, material_item=existing)
        return resolved, existing

    # An inaccessible or re-scoped folder is not a deleted folder. Do not hide
    # a permission change behind the resolver's missing-folder fallback.
    if requested.gallery_id is not None:
        category = await db.scalar(
            select(ContentMaterialCategory)
            .where(
                ContentMaterialCategory.id == requested.gallery_id,
                ContentMaterialCategory.material_type == "image",
                ContentMaterialCategory.deleted_at.is_(None),
            )
            .with_for_update()
        )
        allow_fallback = category is None
    else:
        allow_fallback = False
    if mp_fixed_target:
        await validate_mp_save_target(db, user, requested)
    resolved = (
        await resolve_mp_save_target(db, user, requested)
        if mp_fixed_target
        else await resolve_writable_save_target(
            db, user, requested, fallback_invalid_folder=allow_fallback,
        )
    )
    metadata = {
        "source": "image_design",
        "save_target_version": 2,
        "image_design_job_id": job_id,
        "workflow": workflow,
        "requested_save_target": requested.model_dump(mode="json"),
        "resolved_save_target": resolved.public_target,
        "save_warning": resolved.warning,
    }
    item = await create_library_item_for_asset(
        db,
        asset=asset,
        material_type="image",
        name=Path(asset.original_file_name).stem,
        category=resolved.category_id,
        category_owner_uid=resolved.category_owner_uid,
        metadata=metadata,
    )
    asset.metadata_json = {**(asset.metadata_json or {}), **metadata}
    await _ensure_generated_library_item(db, user=user, asset=asset, material_item=item)
    return resolved, item


async def process_image_design_job(ctx: dict[str, Any], job_id: str) -> None:
    del ctx
    async with pg_manager.get_async_session_context() as db:
        job = await db.scalar(select(ImageDesignJob).where(ImageDesignJob.id == job_id).with_for_update())
        if job is None:
            logger.warning("Image design job not found: %s", job_id)
            return
        request = dict(job.request_json or {})
        saved_result = dict(job.result_json or {})
        saved_asset_ids = list(saved_result.get("asset_ids") or [])
        job.status = "running"
        job.progress = 5
        job.started_at = utc_now_naive()
        await db.commit()

    try:
        requested = (
            ImageDesignSaveTarget.model_validate(request["requested_save_target"])
            if request.get("requested_save_target") is not None
            else None
        )
        async with pg_manager.get_async_session_context() as db:
            if requested is not None:
                user = await db.scalar(
                    select(User).where(
                        User.uid == job.owner_uid,
                        User.is_deleted == 0,
                        User.deleted_at.is_(None),
                    )
                )
                if user is None:
                    raise Image2Error("IMAGE_DESIGN_OWNER_NOT_FOUND", "图片设计任务所属用户不存在")
                if request.get("mp_fixed_target"):
                    await validate_mp_save_target(db, user, requested)
            material_ids = list(request.get("material_ids") or [])
            inputs = [await _load_material_input(db, job.owner_uid, material_id) for material_id in material_ids]
            image2_config = await resolve_image2_config(db, owner_uid=job.owner_uid)
        if not inputs:
            raise Image2Error("IMAGE_DESIGN_MATERIAL_REQUIRED", "图片设计任务缺少参考素材")
        image2_request = _build_image2_request(request, inputs)
        requested_count = int(request.get("gen_count") or 1)
        asset_ids: list[str] = list(saved_asset_ids)
        library_item_ids: list[str] = list(saved_result.get("library_item_ids") or [])
        result_payload: dict[str, Any] = {**saved_result, "asset_ids": asset_ids}
        save_warning = saved_result.get("save_warning")
        provider_task_ids: list[str] = list(job.provider_task_ids_json or [])
        async with Image2Client(image2_config) as client:
            for index in range(len(saved_asset_ids), requested_count):
                resume_asset_id = None
                asset = None
                if resume_asset_id is None:
                    await _set_job(job_id, progress=10 + int(index * 70 / requested_count))
                    result = await client.submit(image2_request, idempotency_key=f"{job_id}:{index}")
                    if result.provider_task_id:
                        provider_task_ids.append(result.provider_task_id)
                        await _set_job(job_id, provider_task_ids_json=provider_task_ids, status="polling")
                    result = await _poll(client, result)
                    if not result.images:
                        raise Image2Error("IMAGE2_RESULT_EMPTY", "image2 任务完成但没有返回图片")
                    raw, _ = await client.read_output(result.images[0])
                    normalized, width, height = _normalize_output(raw, str(request.get("size")))
                    asset_id = f"cca_{uuid.uuid4().hex}"
                    object_name = f"image-design/{job.owner_uid}/{job.id}/{asset_id}.png"
                    uploaded = await get_minio_client().aupload_file(
                        bucket_name=RESULT_BUCKET,
                        object_name=object_name,
                        data=normalized,
                        content_type="image/png",
                    )
                    asset = ContentCoverAsset(
                        id=asset_id,
                        owner_uid=job.owner_uid,
                        tenant_id=job.tenant_id,
                        role="output",
                        original_file_name=f"image-design-{index + 1}.png",
                        content_type="image/png",
                        file_size=len(normalized),
                        image_width=width,
                        image_height=height,
                        sha256=hashlib.sha256(normalized).hexdigest(),
                        bucket_name=uploaded.bucket_name,
                        object_name=uploaded.object_name,
                        metadata_json={
                            "domain": "image_design",
                            "image_design_job_id": job.id,
                            "workflow": job.workflow,
                            "client_id": job.client_id,
                            "reference_material_id": request.get("reference_material_id"),
                            "raw_room_material_id": request.get("raw_room_material_id"),
                            "refinement_id": request.get("refinement_id"),
                            "analysis_ids": request.get("analysis_ids") or [],
                            "plan_version": request.get("plan_version"),
                            "image_roles": request.get("image_roles") or [],
                            "prompt": request.get("compiled_prompt") or request.get("prompt") or "",
                            "size": request.get("size"),
                            "clarity": request.get("clarity"),
                        },
                    )
                async with pg_manager.get_async_session_context() as db:
                    if requested is not None:
                        # Serialize checkpoints as well as attachment so a repeated
                        # delivery cannot allocate a second asset for this output.
                        current_job = await db.scalar(
                            select(ImageDesignJob).where(ImageDesignJob.id == job_id).with_for_update()
                        )
                        current_ids = list((current_job.result_json or {}).get("asset_ids") or [])
                        if index < len(current_ids):
                            resume_asset_id = current_ids[index]
                        if resume_asset_id is not None:
                            asset = await db.scalar(
                                select(ContentCoverAsset).where(
                                    ContentCoverAsset.id == resume_asset_id,
                                    ContentCoverAsset.owner_uid == job.owner_uid,
                                    ContentCoverAsset.deleted_at.is_(None),
                                )
                            )
                            if asset is None:
                                raise Image2Error("IMAGE_DESIGN_RESULT_NOT_FOUND", "已生成的图片不存在")
                        else:
                            db.add(asset)
                            await db.flush()
                        user = await db.scalar(
                            select(User).where(
                                User.uid == job.owner_uid,
                                User.is_deleted == 0,
                                User.deleted_at.is_(None),
                            )
                        )
                        if user is None:
                            raise Image2Error("IMAGE_DESIGN_OWNER_NOT_FOUND", "图片设计任务所属用户不存在")
                        resolved, item = await attach_generated_asset(
                            db,
                            user=user,
                            asset=asset,
                            requested=requested,
                            job_id=job_id,
                            workflow=job.workflow,
                            mp_fixed_target=request.get("mp_fixed_target", False),
                        )
                        asset_ids.append(asset.id)
                        library_item_ids.append(item.id)
                        save_warning = resolved.warning or save_warning
                        current_item_ids = list((current_job.result_json or {}).get("library_item_ids") or [])
                        result_payload = {
                            **(current_job.result_json or {}),
                            "asset_ids": current_ids if len(current_ids) > len(asset_ids) else list(asset_ids),
                            "library_item_ids": (
                                current_item_ids
                                if len(current_item_ids) > len(library_item_ids)
                                else list(library_item_ids)
                            ),
                            "requested_save_target": requested.model_dump(mode="json"),
                            "resolved_save_target": resolved.public_target,
                            "save_warning": save_warning,
                        }
                        current_job.result_json = result_payload
                        current_job.progress = 20 + int((index + 1) * 70 / requested_count)
                        current_job.updated_at = utc_now_naive()
                    else:
                        db.add(asset)
                    await db.commit()
                if requested is None:
                    asset_ids.append(asset.id)
                    result_payload = {"asset_ids": list(asset_ids)}
                    await _set_job(
                        job_id,
                        progress=20 + int((index + 1) * 70 / requested_count),
                        result_json=result_payload,
                    )
        await _set_job(
            job_id,
            status="succeeded",
            progress=100,
            result_json=result_payload,
            completed_at=utc_now_naive(),
            **({"error_code": None, "error_message": None} if requested is not None else {}),
        )
    except (Image2Error, HTTPException) as exc:
        error = exc.detail.get("error", {}) if isinstance(exc, HTTPException) and isinstance(exc.detail, dict) else {}
        await _set_job(
            job_id,
            status="failed",
            error_code=exc.code if isinstance(exc, Image2Error) else error.get("code", "IMAGE_DESIGN_SAVE_FAILED"),
            error_message=error.get("message") or str(exc),
            completed_at=utc_now_naive(),
        )
        logger.warning("Image design job failed: {} {}", job_id, exc)
    except Exception:
        await _set_job(
            job_id,
            status="failed",
            error_code="IMAGE_DESIGN_WORKER_FAILED",
            error_message="图片设计任务执行失败，请稍后重试",
            completed_at=utc_now_naive(),
        )
        logger.exception("Image design job failed: {}", job_id)
    finally:
        await clear_cancel_signal(job_id)
