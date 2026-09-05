from __future__ import annotations

import asyncio
import io
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.content.schemas import ContentOCRCorrection
from yuxi.knowledge.parser.factory import DocumentProcessorFactory
from yuxi.knowledge.parser.rapid_ocr import RapidOCRParser
from yuxi.repositories.content_repository import ContentRepository
from yuxi.storage.minio.client import StorageError, get_minio_client
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentOCRResult
from yuxi.utils import logger
from yuxi.utils.upload_utils import read_upload_with_limit

CONTENT_OCR_BUCKET = os.getenv("CONTENT_OCR_BUCKET", "content-ocr")
MAX_CONTENT_OCR_IMAGE_BYTES = 50 * 1024 * 1024
SUPPORTED_IMAGE_FORMATS = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
}


def _ocr_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "retryable": False}},
    )


def serialize_ocr_result(item: ContentOCRResult) -> dict[str, Any]:
    result = item.to_dict()
    result["image_url"] = f"/api/content/ocr-results/{item.id}/image"
    return result


async def _require_task(repo: ContentRepository, user: User, task_id: str):
    task = await repo.get_task_for_user(task_id, user)
    if task is None:
        raise _ocr_error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    return task


async def _recognize(item: ContentOCRResult, image_bytes: bytes, db: AsyncSession) -> None:
    item.status = "processing"
    item.error_code = None
    item.error_message = None
    await db.commit()

    try:
        processor = DocumentProcessorFactory.get_processor("rapid_ocr")
        if not isinstance(processor, RapidOCRParser):
            raise RuntimeError("RapidOCR 处理器类型无效")
        result = await asyncio.to_thread(processor.process_image_result, image_bytes)
        item.raw_text = result["text"]
        item.blocks_json = result["blocks"]
        item.processing_ms = result["processing_ms"]
        item.status = "completed"
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"内容 OCR 识别失败: {item.id}: {exc}")
        item.status = "failed"
        item.error_code = "OCR_PROCESSING_FAILED"
        item.error_message = f"RapidOCR 识别失败：{str(exc)[:500]}"
    await db.commit()


async def create_content_ocr_result(
    db: AsyncSession,
    user: User,
    task_id: str,
    file: UploadFile,
) -> dict[str, Any]:
    repo = ContentRepository(db)
    task = await _require_task(repo, user, task_id)
    if not file.filename:
        raise _ocr_error(400, "OCR_FILE_NAME_REQUIRED", "无法识别上传图片的文件名")

    try:
        image_bytes = await read_upload_with_limit(
            file,
            max_size_bytes=MAX_CONTENT_OCR_IMAGE_BYTES,
            too_large_message="图片过大，当前仅支持 50 MB 以内的文件",
        )
    except ValueError as exc:
        raise _ocr_error(400, "OCR_FILE_TOO_LARGE", str(exc)) from exc
    if not image_bytes:
        raise _ocr_error(400, "OCR_FILE_EMPTY", "上传图片不能为空")

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image_format = image.format
            width, height = image.size
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise _ocr_error(400, "OCR_IMAGE_INVALID", "上传文件不是有效图片") from exc
    if image_format not in SUPPORTED_IMAGE_FORMATS:
        raise _ocr_error(415, "OCR_IMAGE_UNSUPPORTED", "仅支持 JPG、PNG、WebP、BMP 和 TIFF 图片")

    result_id = f"cor_{uuid.uuid4().hex}"
    suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp", "TIFF": ".tiff"}[image_format]
    object_name = f"content-ocr/{user.uid}/{task.id}/{result_id}/original{suffix}"
    minio_client = get_minio_client()
    try:
        upload_result = await minio_client.aupload_file(
            bucket_name=CONTENT_OCR_BUCKET,
            object_name=object_name,
            data=image_bytes,
            content_type=SUPPORTED_IMAGE_FORMATS[image_format],
        )
    except StorageError as exc:
        raise _ocr_error(500, "OCR_IMAGE_STORAGE_FAILED", "原始图片保存失败") from exc

    safe_file_name = Path(file.filename.replace("\\", "/")).name
    try:
        item = await repo.create_ocr_result(
            result_id=result_id,
            task=task,
            original_file_name=safe_file_name,
            content_type=SUPPORTED_IMAGE_FORMATS[image_format],
            file_size=len(image_bytes),
            image_width=width,
            image_height=height,
            bucket_name=upload_result.bucket_name,
            object_name=upload_result.object_name,
            created_by=str(user.uid),
        )
        await db.commit()
    except Exception:
        await minio_client.adelete_file(upload_result.bucket_name, upload_result.object_name)
        raise

    await _recognize(item, image_bytes, db)
    return {"item": serialize_ocr_result(item)}


async def list_content_ocr_results(db: AsyncSession, user: User, task_id: str) -> dict[str, Any]:
    repo = ContentRepository(db)
    await _require_task(repo, user, task_id)
    items = await repo.list_ocr_results(task_id)
    return {"items": [serialize_ocr_result(item) for item in items]}


async def get_content_ocr_result(db: AsyncSession, user: User, result_id: str) -> dict[str, Any]:
    item = await ContentRepository(db).get_ocr_result_for_user(result_id, user)
    if item is None:
        raise _ocr_error(404, "CONTENT_OCR_RESULT_NOT_FOUND", "OCR 识别记录不存在")
    return {"item": serialize_ocr_result(item)}


async def update_content_ocr_result(
    db: AsyncSession,
    user: User,
    result_id: str,
    payload: ContentOCRCorrection,
) -> dict[str, Any]:
    item = await ContentRepository(db).get_ocr_result_for_user(result_id, user, for_update=True)
    if item is None:
        raise _ocr_error(404, "CONTENT_OCR_RESULT_NOT_FOUND", "OCR 识别记录不存在")
    if item.status != "completed":
        raise _ocr_error(409, "CONTENT_OCR_RESULT_NOT_READY", "OCR 识别完成后才能保存校对结果")
    item.corrected_text = payload.corrected_text
    await db.commit()
    return {"item": serialize_ocr_result(item)}


async def retry_content_ocr_result(db: AsyncSession, user: User, result_id: str) -> dict[str, Any]:
    item = await ContentRepository(db).get_ocr_result_for_user(result_id, user, for_update=True)
    if item is None:
        raise _ocr_error(404, "CONTENT_OCR_RESULT_NOT_FOUND", "OCR 识别记录不存在")
    try:
        image_bytes = await get_minio_client().adownload_file(item.bucket_name, item.object_name)
    except StorageError as exc:
        raise _ocr_error(500, "OCR_IMAGE_STORAGE_FAILED", "无法读取已保存的原始图片") from exc
    item.corrected_text = None
    await _recognize(item, image_bytes, db)
    return {"item": serialize_ocr_result(item)}


async def get_content_ocr_image(db: AsyncSession, user: User, result_id: str) -> tuple[bytes, str, str]:
    item = await ContentRepository(db).get_ocr_result_for_user(result_id, user)
    if item is None:
        raise _ocr_error(404, "CONTENT_OCR_RESULT_NOT_FOUND", "OCR 识别记录不存在")
    try:
        data = await get_minio_client().adownload_file(item.bucket_name, item.object_name)
    except StorageError as exc:
        raise _ocr_error(500, "OCR_IMAGE_STORAGE_FAILED", "无法读取已保存的原始图片") from exc
    return data, item.content_type, item.original_file_name
