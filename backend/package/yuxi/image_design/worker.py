from __future__ import annotations

import asyncio
import hashlib
import io
import os
import uuid
from typing import Any

from PIL import Image, ImageOps
from sqlalchemy import select

from yuxi.content_cover.image2_client import Image2Client, Image2Error
from yuxi.content_cover.image2_settings import resolve_image2_config
from yuxi.content_cover.schemas import Image2Input, Image2Request, Image2Submission
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.run_queue_service import clear_cancel_signal
from yuxi.storage.minio import get_minio_client
from yuxi.storage.postgres.models_content import ContentCoverAsset, ImageDesignJob
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
    repo = MaterialLibraryRepository(db, include_shared=True)
    item = await repo.get_item_for_user(material_id, owner_uid)
    if item is None or item.material_type != "image" or item.status != "enabled":
        raise Image2Error("IMAGE_DESIGN_MATERIAL_NOT_FOUND", "图片设计引用的素材不存在或已下架")
    asset = await repo.get_asset(item.asset_id, item.owner_uid)
    if asset is None:
        raise Image2Error("IMAGE_DESIGN_MATERIAL_FILE_MISSING", "图片设计引用的素材文件不存在")
    data = await get_minio_client().adownload_file(asset.bucket_name, asset.object_name)
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


async def process_image_design_job(ctx: dict[str, Any], job_id: str) -> None:
    del ctx
    async with pg_manager.get_async_session_context() as db:
        job = await db.scalar(select(ImageDesignJob).where(ImageDesignJob.id == job_id).with_for_update())
        if job is None:
            logger.warning("Image design job not found: %s", job_id)
            return
        request = dict(job.request_json or {})
        job.status = "running"
        job.progress = 5
        job.started_at = utc_now_naive()
        await db.commit()

    try:
        async with pg_manager.get_async_session_context() as db:
            material_ids = list(request.get("material_ids") or [])
            inputs = [await _load_material_input(db, job.owner_uid, material_id) for material_id in material_ids]
            image2_config = await resolve_image2_config(db, owner_uid=job.owner_uid)
        if not inputs:
            raise Image2Error("IMAGE_DESIGN_MATERIAL_REQUIRED", "图片设计任务缺少参考素材")
        image2_request = _build_image2_request(request, inputs)
        requested_count = int(request.get("gen_count") or 1)
        asset_ids: list[str] = []
        provider_task_ids: list[str] = []
        async with Image2Client(image2_config) as client:
            for index in range(requested_count):
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
                object_name = f"image-design/{job.owner_uid}/{job.id}/result-{index + 1}.png"
                uploaded = await get_minio_client().aupload_file(
                    bucket_name=RESULT_BUCKET,
                    object_name=object_name,
                    data=normalized,
                    content_type="image/png",
                )
                async with pg_manager.get_async_session_context() as db:
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
                    db.add(asset)
                    await db.commit()
                asset_ids.append(asset_id)
                await _set_job(
                    job_id,
                    progress=20 + int((index + 1) * 70 / requested_count),
                    result_json={"asset_ids": asset_ids},
                )
        await _set_job(
            job_id,
            status="succeeded",
            progress=100,
            result_json={"asset_ids": asset_ids},
            completed_at=utc_now_naive(),
        )
    except Image2Error as exc:
        await _set_job(
            job_id,
            status="failed",
            error_code=exc.code,
            error_message=str(exc),
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
