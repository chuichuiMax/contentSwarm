from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import uuid
from collections.abc import AsyncIterator
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content_cover import COVER_PROCESSING_VERSION, COVER_SIZES, COVER_TEMPLATES, COVER_THEMES
from yuxi.content_cover.image2_client import Image2Error
from yuxi.content_cover.image2_settings import (
    get_image2_config_state,
    resolve_image2_config,
    save_image2_config,
    verify_image2_config,
)
from yuxi.content_cover.poster_billboard import (
    POSTER_PROCESSING_VERSION,
    PosterBillboardError,
    analyze_poster_template,
    build_poster_copy_plan,
    evaluate_poster_quality,
    normalize_poster_text_slots,
    render_poster_billboard,
)
from yuxi.content_cover.schemas import (
    CoverComposeCreate,
    CoverEditorProjectCreate,
    CoverEditorRenderCreate,
    CoverEditorScene,
    CoverEditorSceneUpdate,
    CoverGenerateCreate,
    CoverRetryCreate,
    Image2ConfigTestRequest,
    Image2GlobalConfigUpdate,
    PosterGenerateCreate,
    PosterPreviewCreate,
    PosterTemplateReviewUpdate,
    PosterTemplateUpdate,
    TemplateReplicatePlanCreate,
)
from yuxi.content_cover.template_replication import (
    TemplateReplicationError,
    apply_layout_overrides,
    analyze_template,
    build_copy_plan,
    build_render_plan,
    ensure_clean_source,
)
from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.repositories.content_repository import ContentRepository
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.material_library_service import (
    MATERIAL_LIBRARY_BUCKET,
    create_library_item_for_asset,
    ensure_material_categories,
    resolve_material_category,
)
from yuxi.services.run_queue_service import (
    get_arq_pool,
    get_last_run_stream_seq,
    list_run_stream_events,
    normalize_after_seq,
    publish_cancel_signal,
)
from yuxi.storage.minio.client import StorageError, get_minio_client
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import (
    ContentArtifact,
    ContentCoverAsset,
    ContentCoverEditProject,
    ContentCoverJob,
    ContentCoverPosterTemplate,
    ContentMaterialLibraryItem,
)
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger
from yuxi.utils.upload_utils import read_upload_with_limit

COVER_BUCKET = os.getenv("CONTENT_COVER_BUCKET", "content-covers")
MAX_COVER_IMAGE_BYTES = 20 * 1024 * 1024
MAX_COVER_DIMENSION = 8192
MAX_COVER_PIXELS = 40_000_000
SUPPORTED_ROLES = {"source", "template", "mask"}
TERMINAL_COVER_STATUSES = {"succeeded", "failed", "cancelled"}


def _error(status_code: int, code: str, message: str, *, retryable: bool = False) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "retryable": retryable}},
    )


def _owner_uid(user: User) -> str:
    return str(user.uid)


def _compact_cover_copy(value: str, *, limit: int) -> str:
    normalized = re.sub(r"https?://\S+", "", value or "")
    normalized = re.sub(r"[`#>*_\[\](){}]+", " ", normalized)
    normalized = re.sub(r"^[\s\-—–•·\d.、]+", "", normalized)
    normalized = re.sub(r"\s+", "", normalized).strip("，。！？；：,.!?;:—- ")
    if len(normalized) <= limit:
        return normalized
    clauses = [
        item.strip("，。！？；：,.!?;:—- ")
        for item in re.split(r"[，。！？；：,.!?;:—|｜]+", normalized)
        if item.strip()
    ]
    suitable = [item for item in clauses if 4 <= len(item) <= limit]
    return (suitable[0] if suitable else normalized[:limit]).strip("，。！？；：,.!?;:—- ")


def _template_texts(
    artifact: ContentArtifact | None,
    title: str,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    summarized_title = _compact_cover_copy(title, limit=14)
    subtitle = ""
    tags: list[str] = []
    if artifact is not None:
        body = re.sub(r"[`#>*_\[\](){}]+", " ", artifact.body or "")
        sentences = [_compact_cover_copy(item, limit=24) for item in re.split(r"[。！？!?\n]+", body)]
        sentences = [item for item in sentences if 6 <= len(item) <= 24 and item != summarized_title]
        preferred = [
            item
            for item in sentences
            if any(keyword in item for keyword in ("帮助", "实现", "提升", "解决", "通过", "让", "适合"))
        ]
        subtitle = (preferred or sentences or [""])[0]
        tags = [_compact_cover_copy(str(topic).lstrip("#"), limit=10) for topic in (artifact.topics or [])]
        tags = [tag for tag in tags if tag][:3]
    return {
        "title": summarized_title,
        "subtitle": subtitle,
        "tags": tags,
        "preserve_fixed_copy": True,
        "source": source or ("content_asset" if artifact is not None else ("manual" if title.strip() else "template")),
    }


def _linked_content_title(artifact: ContentArtifact | None, task: Any | None) -> str:
    artifact_title = str(getattr(artifact, "title", "") or "").strip()
    if artifact_title:
        return artifact_title
    selected = (getattr(task, "selected_title_json", None) or {}).get("title")
    return str(selected or getattr(task, "name", "") or "").strip()


def _tenant_id(user: User) -> str | None:
    return str(user.department_id) if user.department_id is not None else None


def serialize_asset(item: ContentCoverAsset) -> dict[str, Any]:
    data = item.to_dict()
    data["file_url"] = f"/api/content/covers/assets/{item.id}/file"
    return data


def serialize_job(item: ContentCoverJob) -> dict[str, Any]:
    data = item.to_dict()
    asset_ids = (item.result_json or {}).get("asset_ids") or []
    data["result_assets"] = [
        {"id": asset_id, "file_url": f"/api/content/covers/assets/{asset_id}/file"} for asset_id in asset_ids
    ]
    return data


def serialize_editor_project(item: ContentCoverEditProject) -> dict[str, Any]:
    data = item.to_dict()
    data["background_file_url"] = f"/api/content/covers/assets/{item.base_asset_id}/file"
    data["warnings"] = (
        []
        if item.editability == "structured"
        else ["原封面没有可恢复的图层数据，原图文字已合并；你仍可新增文字和调整样式。"]
    )
    return data


def _editor_color(value: Any, default: str | None) -> str | None:
    candidate = str(value or "")
    return candidate if re.fullmatch(r"#[0-9A-Fa-f]{6}", candidate) else default


def _editor_color_metrics(value: str | None) -> tuple[float, int]:
    color = _editor_color(value, "#FFFFFF") or "#FFFFFF"
    red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue, max(red, green, blue) - min(red, green, blue)


def _editor_font_family(value: Any) -> str:
    family = str(value or "").lower()
    is_serif = any(token in family for token in ("serif", "宋", "simsun", "georgia"))
    return "Noto Serif CJK SC" if is_serif else "Noto Sans CJK SC"


def resolve_cover_editor_font(font_key: str) -> Path:
    paths = {
        "noto-sans-cjk-regular": Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        "noto-sans-cjk-bold": Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        "noto-serif-cjk-regular": Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"),
        "noto-serif-cjk-bold": Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"),
    }
    path = paths.get(font_key)
    if path is None:
        raise _error(404, "COVER_EDITOR_FONT_NOT_FOUND", "画板字体不存在")
    if not path.is_file():
        raise _error(503, "COVER_EDITOR_FONT_UNAVAILABLE", "画板字体尚未安装", retryable=True)
    return path


def _poster_editor_scene(
    job: ContentCoverJob,
    base_asset_id: str,
    *,
    snapshot_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request = job.request_json or {}
    snapshot = snapshot_override or request.get("poster_template_snapshot") or {}
    current_slots = {
        str(item.get("slot_id") or ""): item for item in (request.get("copy_plan") or {}).get("slots") or []
    }
    layers: list[dict[str, Any]] = []
    for order, slot in enumerate(snapshot.get("text_slots") or []):
        box = slot.get("box") or {}
        style = slot.get("style") or {}
        plan = current_slots.get(str(slot.get("id") or "")) or {}
        layer_id = re.sub(r"[^A-Za-z0-9_-]+", "_", str(slot.get("id") or order)).strip("_") or str(order)
        font_size = min(512, max(12, round(float(style.get("font_size_ratio") or 0.05) * 1440)))
        fill = _editor_color(style.get("fill"), "#FFFFFF")
        bright_fill = _editor_color_metrics(fill)[0] >= 220
        has_stroke = bool(style.get("stroke")) and float(style.get("stroke_width_ratio") or 0) > 0
        layer_text = str(plan.get("text") if plan.get("changed") else slot.get("source_text") or "")
        shadow = bool(style.get("shadow")) if style.get("shadow") is not None else bright_fill and not has_stroke
        layers.append(
            {
                "id": f"text_{layer_id}_{order}",
                "layer_type": "text",
                "name": layer_text[:80] or "文字",
                "text": layer_text,
                "x": float(style.get("editor_x") or round(float(box.get("x") or 0) * 1080, 3)),
                "y": float(style.get("editor_y") or round(float(box.get("y") or 0) * 1440, 3)),
                "width": float(style.get("editor_width") or round(float(box.get("width") or 0.3) * 1080, 3)),
                "height": float(style.get("editor_height") or round(float(box.get("height") or 0.08) * 1440, 3)),
                "rotation": 0,
                "opacity": 1,
                "visible": True,
                "locked": False,
                "order": order,
                "font_family": _editor_font_family(style.get("font_family")),
                "font_size": float(style.get("font_size_px") or font_size),
                "font_weight": int(style.get("font_weight") or (700 if style.get("bold") else 400)),
                "font_style": "normal",
                "fill": fill,
                "fill_runs": style.get("fill_runs") or [],
                "align": str(style.get("align") or "center"),
                "line_height": 1.2,
                "letter_spacing": float(style.get("letter_spacing") or 0),
                "stroke": has_stroke,
                "stroke_color": _editor_color(style.get("stroke"), "#000000"),
                "stroke_width": min(
                    40,
                    round(float(style.get("stroke_width_ratio") or 0) * font_size, 3),
                ),
                "shadow": shadow,
                "shadow_color": _editor_color(style.get("shadow_color"), "#000000"),
                "shadow_blur": (
                    float(style.get("shadow_blur") or 0)
                    if style.get("shadow") is not None
                    else min(24, max(4, round(font_size * 0.12)))
                    if shadow
                    else 0
                ),
                "shadow_offset_x": float(style.get("shadow_offset_x") or 0),
                "shadow_offset_y": (
                    float(style.get("shadow_offset_y") or 0)
                    if style.get("shadow") is not None
                    else min(12, max(2, round(font_size * 0.08)))
                ),
                "background_fill": _editor_color(style.get("panel_fill"), None),
                "background_opacity": float(
                    style.get("panel_opacity") if style.get("panel_opacity") is not None else 1
                ),
                "background_radius": min(
                    200,
                    round(
                        float(style.get("panel_radius_ratio") or 0) * float(box.get("height") or 0.08) * 1440,
                        3,
                    ),
                ),
                "background_padding": (
                    min(80, round(float(box.get("height") or 0.08) * 1440 * 0.12, 3)) if style.get("panel_fill") else 0
                ),
            }
        )
    return CoverEditorScene.model_validate(
        {
            "version": 1,
            "canvas": {
                "width": 1080,
                "height": 1440,
                "background_asset_id": base_asset_id,
                "safe_area": snapshot.get("safe_area") or {},
            },
            "layers": layers,
        }
    ).model_dump(mode="json")


def _build_editor_background_copy_plan(snapshot: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a text-free background without changing the saved template scene."""
    background_snapshot = deepcopy(snapshot)
    clean_slots: list[dict[str, Any]] = []
    for slot in background_snapshot.get("text_slots") or []:
        slot.setdefault("style", {})["panel_fill"] = None
        clean_slots.append(
            {
                "slot_id": slot.get("id"),
                "role": slot.get("role") or "other",
                "source_text": slot.get("source_text") or "",
                "text": "",
                "max_chars": slot.get("max_chars") or 120,
                "max_lines": slot.get("max_lines") or 4,
                "changed": True,
            }
        )
    return background_snapshot, {
        "processing_version": POSTER_PROCESSING_VERSION,
        "source": "editor",
        "slots": clean_slots,
    }


def _flattened_editor_scene(asset: ContentCoverAsset) -> dict[str, Any]:
    return CoverEditorScene.model_validate(
        {
            "version": 1,
            "canvas": {
                "width": asset.image_width,
                "height": asset.image_height,
                "background_asset_id": asset.id,
                "safe_area": {"x": 0.05, "y": 0.05, "width": 0.9, "height": 0.9},
            },
            "layers": [],
        }
    ).model_dump(mode="json")


def _editor_layer_key(layer: dict[str, Any]) -> str:
    layer_id = str(layer.get("id") or "")
    generated = re.fullmatch(r"(text_.+)_\d+", layer_id)
    return generated.group(1) if generated else layer_id


def _merge_editor_scenes(
    base_scene: dict[str, Any],
    current_scene: dict[str, Any],
    *,
    replace_generated: bool = False,
) -> dict[str, Any]:
    """Add newly recoverable mask layers without overwriting existing edits."""
    base = CoverEditorScene.model_validate(base_scene).model_dump(mode="json")
    current = CoverEditorScene.model_validate(current_scene).model_dump(mode="json")
    current_by_key = {_editor_layer_key(item): item for item in current["layers"]}
    base_keys = {_editor_layer_key(item) for item in base["layers"]}
    if not replace_generated:
        base["layers"] = [current_by_key.get(_editor_layer_key(item), item) for item in base["layers"]]
    base["layers"].extend(
        item
        for item in current["layers"]
        if _editor_layer_key(item) not in base_keys
        and not (replace_generated and re.fullmatch(r"text_slot-\d+(?:_\d+)?", str(item.get("id") or "")))
    )
    return CoverEditorScene.model_validate(base).model_dump(mode="json")


async def _find_poster_ancestor(
    repo: ContentCoverRepository,
    source_job: ContentCoverJob | None,
    owner_uid: str,
) -> ContentCoverJob | None:
    current = source_job
    visited: set[str] = set()
    while current is not None and current.id not in visited:
        visited.add(current.id)
        if current.mode == "poster_billboard":
            return current
        if not current.parent_job_id:
            return None
        current = await repo.get_job_for_user(current.parent_job_id, owner_uid)
    return None


async def _create_poster_editor_background(
    db: AsyncSession,
    user: User,
    source_asset: ContentCoverAsset,
    source_job: ContentCoverJob,
) -> tuple[ContentCoverAsset, dict[str, Any]]:
    request = source_job.request_json or {}
    snapshot = deepcopy(request.get("poster_template_snapshot") or {})
    template_asset_id = str(snapshot.get("asset_id") or "")
    product_asset_id = str(request.get("product_asset_id") or "")
    repo = ContentCoverRepository(db)
    template_asset = await repo.get_asset_for_user(template_asset_id, _owner_uid(user))
    product_asset = await repo.get_asset_for_user(product_asset_id, _owner_uid(user))
    if template_asset is None or product_asset is None:
        raise _error(409, "COVER_EDITOR_SOURCE_MISSING", "结构化封面的模板或产品素材已不存在")

    try:
        template_data, product_data = await asyncio.gather(
            get_minio_client().adownload_file(template_asset.bucket_name, template_asset.object_name),
            get_minio_client().adownload_file(product_asset.bucket_name, product_asset.object_name),
        )
        template_image = _open_cover_image(template_data, error_message="画板模板不是有效图片")
        background_snapshot, clean_plan = _build_editor_background_copy_plan(snapshot)
        rendered, _ = await asyncio.to_thread(
            render_poster_billboard,
            template_image,
            _open_cover_image(product_data, error_message="画板产品素材不是有效图片"),
            background_snapshot,
            clean_plan,
            transform=request.get("transform") or {},
        )
        background_id = f"cca_{uuid.uuid4().hex}"
        object_name = f"content-covers/{_owner_uid(user)}/editor-backgrounds/{background_id}.png"
        uploaded = await get_minio_client().aupload_file(
            bucket_name=COVER_BUCKET,
            object_name=object_name,
            data=rendered,
            content_type="image/png",
        )
    except (StorageError, PosterBillboardError, TemplateReplicationError) as exc:
        raise _error(500, "COVER_EDITOR_BACKGROUND_FAILED", "无法准备可编辑封面底图", retryable=True) from exc
    try:
        background_asset = await repo.create_asset(
            id=background_id,
            owner_uid=_owner_uid(user),
            tenant_id=_tenant_id(user),
            content_task_id=source_asset.content_task_id,
            role="editor_background",
            original_file_name="editor-background.png",
            content_type="image/png",
            file_size=len(rendered),
            image_width=1080,
            image_height=1440,
            sha256=hashlib.sha256(rendered).hexdigest(),
            bucket_name=uploaded.bucket_name,
            object_name=uploaded.object_name,
            metadata_json={"source_asset_id": source_asset.id, "source_job_id": source_job.id},
        )
    except Exception:
        await get_minio_client().adelete_file(uploaded.bucket_name, uploaded.object_name)
        raise
    return background_asset, snapshot


def serialize_poster_template(
    item: ContentCoverPosterTemplate,
    category_name: str | None = None,
    library_item: ContentMaterialLibraryItem | None = None,
) -> dict[str, Any]:
    data = item.to_dict()
    if library_item is not None:
        data["name"] = library_item.display_name
    data["category"] = library_item.category if library_item is not None else item.category
    data["category_name"] = category_name or "未分类"
    data["text_slots"] = normalize_poster_text_slots(data.get("text_slots") or [])
    analysis = data.get("analysis") or {}
    item_status = getattr(item, "status", "ready")
    review_status = analysis.get("review_status") or (
        "confirmed" if item_status == "ready" else "pending" if item_status == "needs_review" else "not_applicable"
    )
    data["review_status"] = review_status
    data["requires_review"] = item_status == "needs_review" or review_status == "pending"
    data["ocr_raw_layers"] = analysis.get("ocr_raw_layers") or []
    data["recognition_metrics"] = analysis.get("recognition_metrics") or {}
    data["file_url"] = f"/api/content/covers/assets/{item.asset_id}/file"
    data["thumbnail_url"] = data["file_url"]
    return data


async def get_cover_bootstrap(db: AsyncSession, user: User) -> dict[str, Any]:
    tasks, _ = await ContentRepository(db).list_tasks(user=user, page=1, page_size=30)
    image2_state = await get_image2_config_state(
        db,
        owner_uid=_owner_uid(user),
    )
    return {
        "templates": list(COVER_TEMPLATES.values()),
        "themes": list(COVER_THEMES.values()),
        "sizes": [{"id": key, **value} for key, value in COVER_SIZES.items()],
        "image2": {
            **image2_state,
            "modes": ["text_to_image", "image_to_image", "multi_reference", "mask"],
        },
        "content_tasks": [
            {"id": item.id, "name": item.name, "status": item.status, "updated_at": item.to_dict()["updated_at"]}
            for item in tasks
        ],
    }


def _normalize_upload(data: bytes, role: str) -> tuple[bytes, int, int, str]:
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise _error(400, "COVER_IMAGE_FORMAT_UNSUPPORTED", "仅支持 JPG、PNG 或 WebP 图片")
            width, height = source.size
            if width < 2 or height < 2 or max(width, height) > MAX_COVER_DIMENSION or width * height > MAX_COVER_PIXELS:
                raise _error(400, "COVER_IMAGE_DIMENSION_INVALID", "图片尺寸必须在 2–8192 像素且不超过 4000 万像素")
            image = ImageOps.exif_transpose(source)
            image.load()
            width, height = image.size
            output = io.BytesIO()
            image.convert("RGBA").save(output, format="PNG", optimize=True)
            return output.getvalue(), width, height, "image/png"
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise _error(400, "COVER_IMAGE_INVALID", "上传文件不是有效图片") from exc


async def create_cover_asset(
    db: AsyncSession,
    user: User,
    file: UploadFile,
    *,
    role: str,
    content_task_id: str | None,
) -> dict[str, Any]:
    if role not in SUPPORTED_ROLES:
        raise _error(422, "COVER_ASSET_ROLE_INVALID", "素材角色必须是 source、template 或 mask")
    if not file.filename:
        raise _error(400, "COVER_FILE_NAME_REQUIRED", "无法识别上传文件名")
    if content_task_id:
        task = await ContentRepository(db).get_task_for_user(content_task_id, user)
        if task is None:
            raise _error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    try:
        raw = await read_upload_with_limit(
            file,
            max_size_bytes=MAX_COVER_IMAGE_BYTES,
            too_large_message="图片过大，当前仅支持 20 MB 以内的文件",
        )
    except ValueError as exc:
        raise _error(400, "COVER_IMAGE_TOO_LARGE", str(exc)) from exc
    if not raw:
        raise _error(400, "COVER_IMAGE_EMPTY", "上传图片不能为空")
    normalized, width, height, content_type = _normalize_upload(raw, role)
    if len(normalized) > MAX_COVER_IMAGE_BYTES:
        raise _error(400, "COVER_IMAGE_TOO_LARGE", "图片规范化后超过 20 MB，请降低分辨率后重试")
    owner_uid = _owner_uid(user)
    asset_id = f"cca_{uuid.uuid4().hex}"
    material_type = "image" if role == "source" else "cover_template"
    object_group = "images" if material_type == "image" else "cover-templates"
    object_name = f"material-library/{owner_uid}/{object_group}/{asset_id}/image.png"
    try:
        uploaded = await get_minio_client().aupload_file(
            bucket_name=MATERIAL_LIBRARY_BUCKET,
            object_name=object_name,
            data=normalized,
            content_type=content_type,
        )
    except StorageError as exc:
        raise _error(500, "COVER_STORAGE_FAILED", "封面素材保存失败", retryable=True) from exc
    try:
        item = await ContentCoverRepository(db).create_asset(
            id=asset_id,
            owner_uid=owner_uid,
            tenant_id=_tenant_id(user),
            content_task_id=content_task_id,
            role=role,
            original_file_name=Path(file.filename.replace("\\", "/")).name,
            content_type=content_type,
            file_size=len(normalized),
            image_width=width,
            image_height=height,
            sha256=hashlib.sha256(normalized).hexdigest(),
            bucket_name=uploaded.bucket_name,
            object_name=uploaded.object_name,
            metadata_json={"original_content_type": file.content_type or ""},
        )
        if role != "mask":
            await create_library_item_for_asset(
                db,
                asset=item,
                material_type=material_type,
                name=Path(file.filename).stem,
                category="uncategorized",
                metadata={"cover_role": role},
            )
        await db.commit()
    except Exception:
        await get_minio_client().adelete_file(uploaded.bucket_name, uploaded.object_name)
        raise
    return {"asset": serialize_asset(item)}


def _open_cover_image(data: bytes, *, error_message: str) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.load()
            return image
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise PosterBillboardError(error_message) from exc


async def _analyze_poster_image(image: Image.Image) -> dict[str, Any]:
    text_analysis = None
    try:
        text_analysis = await asyncio.to_thread(
            analyze_template,
            image,
            target_size=(1080, 1440),
        )
    except TemplateReplicationError as exc:
        if "未识别到" not in str(exc):
            raise
    return await asyncio.to_thread(analyze_poster_template, image, text_analysis=text_analysis)


async def import_poster_templates(
    db: AsyncSession,
    user: User,
    files: list[UploadFile],
    *,
    category: str,
) -> dict[str, Any]:
    if not files:
        raise _error(400, "POSTER_TEMPLATE_FILES_REQUIRED", "请至少上传一张大字报蒙版")
    if len(files) > 100:
        raise _error(400, "POSTER_TEMPLATE_BATCH_TOO_LARGE", "单次最多导入 100 张蒙版")
    owner_uid = _owner_uid(user)
    repo = ContentCoverRepository(db)
    resolved_category = await resolve_material_category(
        db,
        owner_uid=owner_uid,
        tenant_id=_tenant_id(user),
        material_type="cover_template",
        category_id=category,
    )
    category_names = {
        item.id: item.name for item in await MaterialLibraryRepository(db).list_categories(owner_uid, "cover_template")
    }
    results: list[dict[str, Any]] = []
    uploaded_objects: list[tuple[str, str]] = []
    created = 0
    duplicate = 0
    failed = 0
    normalized_category = resolved_category.id
    try:
        for index, file in enumerate(files):
            file_name = Path((file.filename or f"poster-{index + 1}.png").replace("\\", "/")).name
            try:
                raw = await read_upload_with_limit(
                    file,
                    max_size_bytes=MAX_COVER_IMAGE_BYTES,
                    too_large_message="图片过大，当前仅支持 20 MB 以内的文件",
                )
                if not raw:
                    raise _error(400, "COVER_IMAGE_EMPTY", "上传图片不能为空")
                normalized, width, height, content_type = _normalize_upload(raw, "poster_template")
                checksum = hashlib.sha256(normalized).hexdigest()
                existing = await repo.get_poster_template_by_checksum(owner_uid, checksum)
                if existing is not None:
                    existing_asset = await repo.get_asset_for_user(existing.asset_id, owner_uid)
                    if existing_asset is not None:
                        library_item = await create_library_item_for_asset(
                            db,
                            asset=existing_asset,
                            material_type="cover_template",
                            name=existing.name,
                            category=existing.category,
                            metadata={"poster_template_id": existing.id},
                        )
                        library_item.status = "enabled" if existing.status == "ready" else "disabled"
                    duplicate += 1
                    results.append(
                        {
                            "file_name": file_name,
                            "status": "duplicate",
                            "template": serialize_poster_template(existing, category_names.get(existing.category)),
                        }
                    )
                    continue
                image = _open_cover_image(normalized, error_message="大字报蒙版不是有效图片")
                analysis = await _analyze_poster_image(image)
                asset_id = f"cca_{uuid.uuid4().hex}"
                template_id = f"cpt_{uuid.uuid4().hex}"
                object_name = f"material-library/{owner_uid}/cover-templates/{asset_id}/image.png"
                uploaded = await get_minio_client().aupload_file(
                    bucket_name=MATERIAL_LIBRARY_BUCKET,
                    object_name=object_name,
                    data=normalized,
                    content_type=content_type,
                )
                uploaded_objects.append((uploaded.bucket_name, uploaded.object_name))
                async with db.begin_nested():
                    asset = await repo.create_asset(
                        id=asset_id,
                        owner_uid=owner_uid,
                        tenant_id=_tenant_id(user),
                        content_task_id=None,
                        role="poster_template",
                        original_file_name=file_name,
                        content_type=content_type,
                        file_size=len(normalized),
                        image_width=width,
                        image_height=height,
                        sha256=checksum,
                        bucket_name=uploaded.bucket_name,
                        object_name=uploaded.object_name,
                        metadata_json={
                            "original_content_type": file.content_type or "",
                            "poster_template_id": template_id,
                            "processing_version": POSTER_PROCESSING_VERSION,
                        },
                    )
                    item = await repo.create_poster_template(
                        id=template_id,
                        owner_uid=owner_uid,
                        tenant_id=_tenant_id(user),
                        asset_id=asset_id,
                        name=Path(file_name).stem[:255] or f"大字报画板 {index + 1}",
                        category=normalized_category,
                        tags_json=[],
                        template_type=analysis["template_type"],
                        canvas_width=1080,
                        canvas_height=1440,
                        product_box_json=analysis.get("product_box"),
                        safe_area_json=analysis["safe_area"],
                        text_slots_json=analysis["text_slots"],
                        fixed_regions_json=analysis["fixed_regions"],
                        editable_regions_json=analysis["editable_regions"],
                        analysis_json=analysis,
                        checksum=checksum,
                        analysis_version=POSTER_PROCESSING_VERSION,
                        status=analysis["status"],
                    )
                    library_item = await create_library_item_for_asset(
                        db,
                        asset=asset,
                        material_type="cover_template",
                        name=item.name,
                        category=item.category,
                        metadata={"poster_template_id": item.id},
                    )
                    library_item.status = "enabled" if item.status == "ready" else "disabled"
                created += 1
                results.append(
                    {
                        "file_name": file_name,
                        "status": "created",
                        "template": serialize_poster_template(item, resolved_category.name),
                    }
                )
            except HTTPException as exc:
                failed += 1
                detail = exc.detail.get("error", {}) if isinstance(exc.detail, dict) else {}
                results.append(
                    {
                        "file_name": file_name,
                        "status": "failed",
                        "error": {
                            "code": detail.get("code", "POSTER_TEMPLATE_IMPORT_FAILED"),
                            "message": detail.get("message", str(exc.detail)),
                        },
                    }
                )
            except IntegrityError as exc:
                uploaded_key = (uploaded.bucket_name, uploaded.object_name)
                try:
                    await get_minio_client().adelete_file(*uploaded_key)
                except StorageError:
                    logger.warning("Failed to clean duplicate poster object: %s/%s", *uploaded_key)
                if uploaded_key in uploaded_objects:
                    uploaded_objects.remove(uploaded_key)
                existing = await repo.get_poster_template_by_checksum(owner_uid, checksum)
                if existing is not None:
                    existing_asset = await repo.get_asset_for_user(existing.asset_id, owner_uid)
                    if existing_asset is not None:
                        library_item = await create_library_item_for_asset(
                            db,
                            asset=existing_asset,
                            material_type="cover_template",
                            name=existing.name,
                            category=existing.category,
                            metadata={"poster_template_id": existing.id},
                        )
                        library_item.status = "enabled" if existing.status == "ready" else "disabled"
                    duplicate += 1
                    results.append(
                        {
                            "file_name": file_name,
                            "status": "duplicate",
                            "template": serialize_poster_template(existing, category_names.get(existing.category)),
                        }
                    )
                else:
                    failed += 1
                    results.append(
                        {
                            "file_name": file_name,
                            "status": "failed",
                            "error": {"code": "POSTER_TEMPLATE_IMPORT_CONFLICT", "message": str(exc)},
                        }
                    )
            except (PosterBillboardError, TemplateReplicationError, StorageError) as exc:
                failed += 1
                results.append(
                    {
                        "file_name": file_name,
                        "status": "failed",
                        "error": {"code": "POSTER_TEMPLATE_IMPORT_FAILED", "message": str(exc)},
                    }
                )
        await db.commit()
    except Exception:
        await db.rollback()
        for bucket_name, object_name in uploaded_objects:
            try:
                await get_minio_client().adelete_file(bucket_name, object_name)
            except Exception:
                logger.warning("Failed to clean poster template object: %s/%s", bucket_name, object_name)
        raise
    return {
        "items": results,
        "summary": {"total": len(files), "created": created, "duplicate": duplicate, "failed": failed},
    }


async def list_poster_templates(
    db: AsyncSession,
    user: User,
    *,
    category: str | None,
    status: str | None,
    query: str | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    categories = await ensure_material_categories(
        db,
        owner_uid=_owner_uid(user),
        tenant_id=_tenant_id(user),
        material_type="cover_template",
    )
    category_names = {item.id: item.name for item in categories}
    resolved_category = (
        await resolve_material_category(
            db,
            owner_uid=_owner_uid(user),
            tenant_id=_tenant_id(user),
            material_type="cover_template",
            category_id=category,
        )
        if category
        else None
    )
    cover_repo = ContentCoverRepository(db)
    items, total = await cover_repo.list_poster_templates(
        _owner_uid(user),
        category=resolved_category.id if resolved_category else None,
        status=status,
        query_text=query,
        page=page,
        page_size=page_size,
    )
    library_items = await MaterialLibraryRepository(db).list_items_by_asset_ids(
        _owner_uid(user),
        [item.asset_id for item in items],
    )
    library_by_asset = {item.asset_id: item for item in library_items}
    for item in items:
        if item.asset_id in library_by_asset:
            continue
        asset = await cover_repo.get_asset_for_user(item.asset_id, _owner_uid(user))
        if asset is None:
            continue
        library_item = await create_library_item_for_asset(
            db,
            asset=asset,
            material_type="cover_template",
            name=item.name,
            category=item.category,
            metadata={"poster_template_id": item.id},
        )
        library_item.status = "enabled" if item.status == "ready" else "disabled"
        library_by_asset[item.asset_id] = library_item
    await db.commit()
    return {
        "items": [
            serialize_poster_template(
                item,
                category_names.get(library_by_asset.get(item.asset_id, item).category),
                library_by_asset.get(item.asset_id),
            )
            for item in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def get_poster_template(db: AsyncSession, user: User, template_id: str) -> dict[str, Any]:
    item = await ContentCoverRepository(db).get_poster_template_for_user(template_id, _owner_uid(user))
    if item is None:
        raise _error(404, "POSTER_TEMPLATE_NOT_FOUND", "大字报蒙版不存在")
    library_item = await MaterialLibraryRepository(db).get_item_by_asset(item.asset_id)
    category = await resolve_material_category(
        db,
        owner_uid=_owner_uid(user),
        tenant_id=_tenant_id(user),
        material_type="cover_template",
        category_id=library_item.category if library_item is not None else item.category,
    )
    await db.commit()
    return {"template": serialize_poster_template(item, category.name, library_item)}


async def update_poster_template(
    db: AsyncSession,
    user: User,
    template_id: str,
    payload: PosterTemplateUpdate,
) -> dict[str, Any]:
    item = await ContentCoverRepository(db).get_poster_template_for_user(template_id, _owner_uid(user), for_update=True)
    if item is None:
        raise _error(404, "POSTER_TEMPLATE_NOT_FOUND", "大字报蒙版不存在")
    if await ContentCoverRepository(db).poster_template_is_selected_by_task(
        template_id,
        _owner_uid(user),
        locked_only=True,
    ):
        raise _error(409, "POSTER_TEMPLATE_IN_USE", "模板已被内容任务锁定，不能修改")
    changes = payload.model_dump(exclude_unset=True, mode="json")
    if "name" in changes:
        item.name = changes["name"].strip()
    if "category" in changes:
        category = await resolve_material_category(
            db,
            owner_uid=_owner_uid(user),
            tenant_id=_tenant_id(user),
            material_type="cover_template",
            category_id=changes["category"],
        )
        item.category = category.id
    else:
        category = await resolve_material_category(
            db,
            owner_uid=_owner_uid(user),
            tenant_id=_tenant_id(user),
            material_type="cover_template",
            category_id=item.category,
        )
    field_map = {
        "product_box": "product_box_json",
        "safe_area": "safe_area_json",
        "text_slots": "text_slots_json",
        "fixed_regions": "fixed_regions_json",
        "editable_regions": "editable_regions_json",
    }
    for source, target in field_map.items():
        if source in changes:
            setattr(item, target, changes[source])
    if "status" in changes:
        if changes["status"] == "ready" and not item.product_box_json:
            raise _error(422, "POSTER_TEMPLATE_PRODUCT_BOX_REQUIRED", "标注产品替换区域后才能启用蒙版")
        if changes["status"] == "ready" and item.status == "needs_review":
            raise _error(409, "POSTER_TEMPLATE_REVIEW_REQUIRED", "请先校对并确认 OCR 文字图层")
        item.status = changes["status"]
    elif "product_box" in changes and item.status == "needs_annotation":
        item.status = "ready"
    item.version += 1
    item.updated_at = utc_now_naive()
    analysis = dict(item.analysis_json or {})
    analysis.update(
        {
            "product_box": item.product_box_json,
            "safe_area": item.safe_area_json or {},
            "text_slots": item.text_slots_json or [],
            "fixed_regions": item.fixed_regions_json or [],
            "editable_regions": item.editable_regions_json or [],
            "status": item.status,
        }
    )
    item.analysis_json = analysis
    library_item = await MaterialLibraryRepository(db).get_item_by_asset(item.asset_id)
    if library_item is not None:
        library_item.display_name = item.name
        library_item.category = item.category
        library_item.status = "enabled" if item.status == "ready" else "disabled"
        library_item.updated_at = utc_now_naive()
    await db.commit()
    return {"template": serialize_poster_template(item, category.name)}


async def reanalyze_poster_template(
    db: AsyncSession,
    user: User,
    template_id: str,
) -> dict[str, Any]:
    repo = ContentCoverRepository(db)
    owner_uid = _owner_uid(user)
    item = await repo.get_poster_template_for_user(template_id, owner_uid, for_update=True)
    if item is None:
        raise _error(404, "POSTER_TEMPLATE_NOT_FOUND", "大字报蒙版不存在")
    if await repo.poster_template_is_selected_by_task(template_id, owner_uid, locked_only=True):
        raise _error(409, "POSTER_TEMPLATE_IN_USE", "模板已被内容任务锁定，不能重新识别")
    asset = await repo.get_asset_for_user(item.asset_id, owner_uid)
    if asset is None:
        raise _error(409, "POSTER_TEMPLATE_ASSET_MISSING", "大字报蒙版原始文件不存在")
    try:
        data = await get_minio_client().adownload_file(asset.bucket_name, asset.object_name)
        analysis = await _analyze_poster_image(_open_cover_image(data, error_message="大字报蒙版不是有效图片"))
    except StorageError as exc:
        raise _error(500, "COVER_STORAGE_FAILED", "大字报蒙版读取失败", retryable=True) from exc
    item.template_type = analysis["template_type"]
    item.product_box_json = analysis.get("product_box")
    item.safe_area_json = analysis["safe_area"]
    item.text_slots_json = analysis["text_slots"]
    item.fixed_regions_json = analysis["fixed_regions"]
    item.editable_regions_json = analysis["editable_regions"]
    item.analysis_json = analysis
    item.analysis_version = POSTER_PROCESSING_VERSION
    item.status = analysis["status"]
    item.error_message = None
    item.version += 1
    item.updated_at = utc_now_naive()
    library_item = await MaterialLibraryRepository(db).get_item_by_asset(item.asset_id)
    if library_item is not None:
        library_item.status = "enabled" if item.status == "ready" else "disabled"
        library_item.updated_at = utc_now_naive()
    category = await resolve_material_category(
        db,
        owner_uid=_owner_uid(user),
        tenant_id=_tenant_id(user),
        material_type="cover_template",
        category_id=item.category,
    )
    await db.commit()
    return {"template": serialize_poster_template(item, category.name)}


async def review_poster_template(
    db: AsyncSession,
    user: User,
    template_id: str,
    payload: PosterTemplateReviewUpdate,
) -> dict[str, Any]:
    repo = ContentCoverRepository(db)
    owner_uid = _owner_uid(user)
    item = await repo.get_poster_template_for_user(template_id, owner_uid, for_update=True)
    if item is None:
        raise _error(404, "POSTER_TEMPLATE_NOT_FOUND", "大字报蒙版不存在")
    if item.version != payload.version:
        raise _error(409, "POSTER_TEMPLATE_VERSION_CONFLICT", "识别结果已更新，请刷新后重新校对")
    if await repo.poster_template_is_selected_by_task(template_id, owner_uid, locked_only=True):
        raise _error(409, "POSTER_TEMPLATE_IN_USE", "模板已被内容任务锁定，不能修改")

    previous_by_id = {slot.get("id"): slot for slot in item.text_slots_json or []}
    reviewed_slots = []
    for slot in payload.text_slots:
        data = slot.model_dump(mode="json")
        previous = previous_by_id.get(slot.id)
        if slot.review_state == "user_added" or previous is None:
            data["review_state"] = "user_added"
            data["confidence"] = None
            data["candidate_count"] = 0
            data["consensus_count"] = 0
            data["source_variant"] = "manual"
            data["alternatives"] = []
        elif data["source_text"] != previous.get("source_text") or data["box"] != previous.get("box"):
            data["review_state"] = "user_edited"
        reviewed_slots.append(data)

    analysis = dict(item.analysis_json or {})
    decoration_regions = list(analysis.get("decoration_regions") or [])
    item.product_box_json = payload.product_box.model_dump(mode="json")
    item.text_slots_json = reviewed_slots
    item.fixed_regions_json = [slot["box"] for slot in reviewed_slots if not slot["editable"]] + decoration_regions
    item.editable_regions_json = [slot["box"] for slot in reviewed_slots if slot["editable"]]
    item.status = "ready" if payload.confirm else "needs_review"
    item.error_message = None
    item.version += 1
    item.updated_at = utc_now_naive()
    metrics = dict(analysis.get("recognition_metrics") or {})
    metrics.update(
        {
            "final_layer_count": len(reviewed_slots),
            "user_added_count": sum(slot["review_state"] == "user_added" for slot in reviewed_slots),
            "user_edited_count": sum(slot["review_state"] == "user_edited" for slot in reviewed_slots),
            "low_confidence_count": sum(
                slot.get("review_state") == "recognized"
                and (float(slot.get("confidence") or 0) < 0.85 or int(slot.get("consensus_count") or 0) < 2)
                for slot in reviewed_slots
            ),
        }
    )
    analysis.update(
        {
            "product_box": item.product_box_json,
            "text_slots": reviewed_slots,
            "fixed_regions": item.fixed_regions_json,
            "editable_regions": item.editable_regions_json,
            "recognition_metrics": metrics,
            "review_status": "confirmed" if payload.confirm else "pending",
            "reviewed_at": utc_now_naive().isoformat() if payload.confirm else None,
            "confirmed_layers": reviewed_slots if payload.confirm else analysis.get("confirmed_layers") or [],
            "status": item.status,
        }
    )
    item.analysis_json = analysis
    library_item = await MaterialLibraryRepository(db).get_item_by_asset(item.asset_id)
    if library_item is not None:
        library_item.status = "enabled" if payload.confirm else "disabled"
        library_item.updated_at = utc_now_naive()
    category = await resolve_material_category(
        db,
        owner_uid=owner_uid,
        tenant_id=_tenant_id(user),
        material_type="cover_template",
        category_id=item.category,
    )
    await db.commit()
    return {"template": serialize_poster_template(item, category.name, library_item)}


async def delete_poster_template(db: AsyncSession, user: User, template_id: str) -> dict[str, bool]:
    repo = ContentCoverRepository(db)
    owner_uid = _owner_uid(user)
    item = await repo.get_poster_template_for_user(template_id, owner_uid, for_update=True)
    if item is None:
        raise _error(404, "POSTER_TEMPLATE_NOT_FOUND", "大字报蒙版不存在")
    if await repo.poster_template_is_in_active_job(
        template_id, owner_uid
    ) or await repo.poster_template_is_selected_by_task(template_id, owner_uid):
        raise _error(409, "POSTER_TEMPLATE_IN_USE", "模板正在被内容任务或封面任务使用，不能删除")
    asset = await repo.get_asset_for_user(item.asset_id, owner_uid, for_update=True)
    if asset is not None:
        try:
            await get_minio_client().adelete_file(asset.bucket_name, asset.object_name)
        except StorageError as exc:
            raise _error(500, "COVER_STORAGE_FAILED", "大字报蒙版删除失败", retryable=True) from exc
        asset.deleted_at = utc_now_naive()
        library_item = await MaterialLibraryRepository(db).get_item_by_asset(asset.id)
        if library_item is not None:
            library_item.deleted_at = utc_now_naive()
    item.deleted_at = utc_now_naive()
    await db.commit()
    return {"success": True}


async def get_cover_asset_file(db: AsyncSession, user: User, asset_id: str) -> tuple[bytes, str, str]:
    item = await ContentCoverRepository(db).get_asset_for_user(asset_id, _owner_uid(user))
    if item is None:
        raise _error(404, "COVER_ASSET_NOT_FOUND", "封面素材不存在")
    try:
        data = await get_minio_client().adownload_file(item.bucket_name, item.object_name)
    except StorageError as exc:
        raise _error(500, "COVER_STORAGE_FAILED", "封面素材读取失败", retryable=True) from exc
    return data, item.content_type, item.original_file_name


async def delete_cover_asset(db: AsyncSession, user: User, asset_id: str) -> dict[str, bool]:
    repo = ContentCoverRepository(db)
    owner_uid = _owner_uid(user)
    item = await repo.get_asset_for_user(asset_id, owner_uid, for_update=True)
    if item is None:
        raise _error(404, "COVER_ASSET_NOT_FOUND", "封面素材不存在")
    if item.role == "output":
        raise _error(409, "COVER_OUTPUT_DELETE_FORBIDDEN", "生成结果需通过任务历史保留，不能单独删除")
    if item.role == "poster_template":
        raise _error(409, "POSTER_TEMPLATE_DELETE_REQUIRED", "请从大字报素材库删除蒙版")
    if await repo.asset_is_in_active_job(item.id, owner_uid):
        raise _error(409, "COVER_ASSET_IN_USE", "素材正在被封面任务使用，任务结束后再删除")
    try:
        await get_minio_client().adelete_file(item.bucket_name, item.object_name)
    except StorageError as exc:
        raise _error(500, "COVER_STORAGE_FAILED", "封面素材删除失败", retryable=True) from exc
    item.deleted_at = utc_now_naive()
    library_item = await MaterialLibraryRepository(db).get_item_by_asset(item.id)
    if library_item is not None:
        library_item.deleted_at = utc_now_naive()
    await db.commit()
    return {"success": True}


async def _resolve_artifact(db: AsyncSession, user: User, task_id: str | None) -> ContentArtifact | None:
    if not task_id:
        return None
    content_repo = ContentRepository(db)
    task = await content_repo.get_task_for_user(task_id, user)
    if task is None:
        raise _error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    return await content_repo.get_artifact_for_task(task.id)


async def _enqueue(db: AsyncSession, job: ContentCoverJob) -> None:
    await db.commit()
    try:
        queue = await get_arq_pool()
        queued = await queue.enqueue_job(
            "process_content_cover_job",
            job.id,
            _job_id=f"content-cover:{job.id}",
        )
    except Exception as exc:
        job.status = "failed"
        job.error_code = "COVER_QUEUE_UNAVAILABLE"
        job.error_message = "封面生成队列暂不可用"
        job.completed_at = utc_now_naive()
        await db.commit()
        raise _error(503, job.error_code, job.error_message, retryable=True) from exc
    if queued is None:
        job.status = "failed"
        job.error_code = "COVER_QUEUE_REJECTED"
        job.error_message = "封面任务未能进入执行队列"
        job.completed_at = utc_now_naive()
        await db.commit()
        raise _error(503, job.error_code, job.error_message, retryable=True)


async def _create_job(
    db: AsyncSession,
    user: User,
    *,
    mode: str,
    content_task_id: str | None,
    artifact_id: str | None,
    idempotency_key: str,
    request: dict[str, Any],
    parent_job_id: str | None = None,
    provider_task_id: str | None = None,
    initial_result_json: dict[str, Any] | None = None,
    model: str | None = None,
) -> tuple[ContentCoverJob, bool]:
    repo = ContentCoverRepository(db)
    owner_uid = _owner_uid(user)
    existing = await repo.get_job_by_idempotency(owner_uid, idempotency_key)
    if existing:
        return existing, True
    try:
        job = await repo.create_job(
            id=f"ccj_{uuid.uuid4().hex}",
            owner_uid=owner_uid,
            tenant_id=_tenant_id(user),
            content_task_id=content_task_id,
            artifact_id=artifact_id,
            parent_job_id=parent_job_id,
            mode=mode,
            status="queued",
            model=model or (os.getenv("IMAGE2_MODEL") or "").strip() or None,
            provider_task_id=provider_task_id,
            idempotency_key=idempotency_key,
            request_json=request,
            result_json=initial_result_json or {},
            progress=0,
        )
        await _enqueue(db, job)
        return job, False
    except IntegrityError:
        await db.rollback()
        existing = await repo.get_job_by_idempotency(owner_uid, idempotency_key)
        if existing:
            return existing, True
        raise


async def create_cover_editor_project(
    db: AsyncSession,
    user: User,
    payload: CoverEditorProjectCreate,
) -> dict[str, Any]:
    repo = ContentCoverRepository(db)
    owner_uid = _owner_uid(user)
    source_asset = await repo.get_asset_for_user(payload.asset_id, owner_uid)
    if source_asset is None or source_asset.role != "output":
        raise _error(404, "COVER_EDITOR_SOURCE_NOT_FOUND", "当前封面不存在或不可编辑")

    artifact = None
    if payload.artifact_id:
        artifact = await ContentRepository(db).get_artifact_for_user(payload.artifact_id, user)
        if artifact is None:
            raise _error(404, "CONTENT_ARTIFACT_NOT_FOUND", "内容资产不存在")
        if artifact.cover_asset_id != source_asset.id:
            raise _error(409, "COVER_EDITOR_SOURCE_CHANGED", "当前内容封面已经发生变化，请刷新后重新编辑")

    source_job_id = str((source_asset.metadata_json or {}).get("job_id") or "") or (
        str(artifact.cover_job_id) if artifact and artifact.cover_job_id else ""
    )
    source_job = await repo.get_job_for_user(source_job_id, owner_uid) if source_job_id else None
    existing = await repo.get_edit_project_for_source(payload.asset_id, owner_uid)
    if existing is not None and artifact and existing.artifact_id not in {None, artifact.id}:
        raise _error(409, "COVER_EDITOR_PROJECT_CONFLICT", "该封面已有其他内容资产的编辑草稿")
    if existing is not None:
        CoverEditorScene.model_validate(existing.scene_json or {})
        if artifact:
            if existing.artifact_id is None:
                existing.artifact_id = artifact.id
                existing.content_task_id = artifact.task_id
                existing.updated_at = utc_now_naive()
                await db.commit()
        return {"project": serialize_editor_project(existing), "restored": True}

    base_asset = source_asset
    created_background = False
    editability = "flattened"
    scene = _flattened_editor_scene(source_asset)
    if source_job is not None and source_job.mode == "editor_render":
        request = source_job.request_json or {}
        inherited_base_id = str(request.get("base_asset_id") or "")
        inherited_base = await repo.get_asset_for_user(inherited_base_id, owner_uid) if inherited_base_id else None
        if inherited_base is None:
            raise _error(409, "COVER_EDITOR_BACKGROUND_MISSING", "封面编辑底图已不存在，无法恢复文字图层")
        base_asset = inherited_base
        scene = CoverEditorScene.model_validate(request.get("scene") or {}).model_dump(mode="json")
        scene["canvas"]["background_asset_id"] = base_asset.id
        parent_project_id = str(request.get("project_id") or "")
        parent_project = (
            await repo.get_edit_project_for_user(parent_project_id, owner_uid) if parent_project_id else None
        )
        editability = parent_project.editability if parent_project is not None else "structured"
    else:
        poster_job = await _find_poster_ancestor(repo, source_job, owner_uid)
        if poster_job is not None:
            base_asset, snapshot = await _create_poster_editor_background(db, user, source_asset, poster_job)
            created_background = True
            scene = _poster_editor_scene(poster_job, base_asset.id, snapshot_override=snapshot)
            editability = "structured"
    try:
        project = await repo.create_edit_project(
            id=f"ccep_{uuid.uuid4().hex}",
            owner_uid=owner_uid,
            tenant_id=_tenant_id(user),
            content_task_id=source_asset.content_task_id,
            artifact_id=artifact.id if artifact else None,
            source_asset_id=source_asset.id,
            source_job_id=source_job.id if source_job else None,
            base_asset_id=base_asset.id,
            scene_json=scene,
            revision=1,
            editability=editability,
            status="active",
        )
        await db.commit()
    except Exception:
        await db.rollback()
        if created_background:
            await get_minio_client().adelete_file(base_asset.bucket_name, base_asset.object_name)
        raise
    return {"project": serialize_editor_project(project), "restored": existing is not None}


async def get_cover_editor_project(db: AsyncSession, user: User, project_id: str) -> dict[str, Any]:
    project = await ContentCoverRepository(db).get_edit_project_for_user(project_id, _owner_uid(user))
    if project is None:
        raise _error(404, "COVER_EDITOR_PROJECT_NOT_FOUND", "封面编辑草稿不存在")
    return {"project": serialize_editor_project(project)}


async def update_cover_editor_project(
    db: AsyncSession,
    user: User,
    project_id: str,
    payload: CoverEditorSceneUpdate,
) -> dict[str, Any]:
    project = await ContentCoverRepository(db).get_edit_project_for_user(
        project_id,
        _owner_uid(user),
        for_update=True,
    )
    if project is None:
        raise _error(404, "COVER_EDITOR_PROJECT_NOT_FOUND", "封面编辑草稿不存在")
    if project.revision != payload.expected_revision:
        raise _error(409, "COVER_EDITOR_REVISION_CONFLICT", "草稿已在其他页面更新，请刷新后继续")
    if payload.scene.canvas.background_asset_id != project.base_asset_id:
        raise _error(422, "COVER_EDITOR_BACKGROUND_INVALID", "画板底图不能被替换")
    project.scene_json = payload.scene.model_dump(mode="json")
    project.revision += 1
    project.updated_at = utc_now_naive()
    await db.commit()
    return {"project": serialize_editor_project(project)}


async def render_cover_editor_project(
    db: AsyncSession,
    user: User,
    project_id: str,
    payload: CoverEditorRenderCreate,
) -> dict[str, Any]:
    repo = ContentCoverRepository(db)
    project = await repo.get_edit_project_for_user(project_id, _owner_uid(user), for_update=True)
    if project is None:
        raise _error(404, "COVER_EDITOR_PROJECT_NOT_FOUND", "封面编辑草稿不存在")
    if project.revision != payload.expected_revision:
        raise _error(409, "COVER_EDITOR_REVISION_CONFLICT", "最新编辑尚未保存，请等待自动保存后重试")
    if project.artifact_id:
        artifact = await ContentRepository(db).get_artifact_for_user(project.artifact_id, user, for_update=True)
        if artifact is None:
            raise _error(404, "CONTENT_ARTIFACT_NOT_FOUND", "内容资产不存在")
        if artifact.cover_asset_id != project.source_asset_id:
            raise _error(409, "COVER_EDITOR_SOURCE_CHANGED", "当前封面已经发生变化，请返回结果页后重新编辑")
    scene = CoverEditorScene.model_validate(project.scene_json).model_dump(mode="json")
    canvas = scene["canvas"]
    job, deduplicated = await _create_job(
        db,
        user,
        mode="editor_render",
        content_task_id=project.content_task_id,
        artifact_id=project.artifact_id,
        parent_job_id=project.source_job_id,
        idempotency_key=payload.idempotency_key,
        model="deterministic-canvas-v1",
        request={
            "project_id": project.id,
            "source_asset_id": project.source_asset_id,
            "base_asset_id": project.base_asset_id,
            "scene": scene,
            "size": f"{canvas['width']}x{canvas['height']}",
            "processing_version": "cover-editor-v1",
        },
    )
    return {"project": serialize_editor_project(project), "job": serialize_job(job), "deduplicated": deduplicated}


async def create_cover_compose_job(db: AsyncSession, user: User, payload: CoverComposeCreate) -> dict[str, Any]:
    template = COVER_TEMPLATES.get(payload.template_id)
    if template is None:
        raise _error(422, "COVER_TEMPLATE_INVALID", "封面版式不存在")
    if payload.theme_id not in COVER_THEMES:
        raise _error(422, "COVER_THEME_INVALID", "封面主题不存在")
    if not template["min_assets"] <= len(payload.asset_ids) <= template["max_assets"]:
        raise _error(
            422,
            "COVER_TEMPLATE_ASSET_COUNT_INVALID",
            f"{template['name']} 需要 {template['min_assets']}–{template['max_assets']} 张图片",
        )
    assets = await ContentCoverRepository(db).get_assets_for_user(
        payload.asset_ids,
        _owner_uid(user),
        for_update=True,
    )
    if len(assets) != len(payload.asset_ids) or any(item.role not in {"source", "library_image"} for item in assets):
        raise _error(422, "COVER_SOURCE_ASSET_INVALID", "拼图素材不存在或角色不正确")
    artifact = await _resolve_artifact(db, user, payload.content_task_id)
    request = payload.model_dump()
    request["processing_version"] = COVER_PROCESSING_VERSION
    job, deduplicated = await _create_job(
        db,
        user,
        mode="compose",
        content_task_id=payload.content_task_id,
        artifact_id=artifact.id if artifact else None,
        idempotency_key=payload.idempotency_key,
        request=request,
    )
    return {"job": serialize_job(job), "deduplicated": deduplicated}


async def create_hycanvas_cover_job(
    db: AsyncSession,
    user: User,
    *,
    content_task_id: str,
    source_asset_id: str,
    template_id: str,
    title: str,
    fields: dict[str, str],
    image_field_label: str | None,
    idempotency_key: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    source = await ContentCoverRepository(db).get_asset_for_user(source_asset_id, _owner_uid(user))
    if source is None or source.role not in {"source", "library_image"}:
        raise _error(422, "COVER_SOURCE_ASSET_INVALID", "HyCanvas 主图不存在或角色不正确")
    job, deduplicated = await _create_job(
        db,
        user,
        mode="hycanvas",
        content_task_id=content_task_id,
        artifact_id=None,
        idempotency_key=idempotency_key,
        model="hycanvas-deterministic",
        request={
            "source_asset_ids": [source_asset_id],
            "template_id": template_id,
            "title": title,
            "fields": fields,
            "image_field_label": image_field_label,
            "size": "1080x1440",
            "parameters": parameters,
            "processing_version": "hycanvas-v2-background-material",
        },
    )
    return {"job": serialize_job(job), "deduplicated": deduplicated}


async def _content_prompt(
    db: AsyncSession,
    user: User,
    task_id: str | None,
    prompt: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ContentArtifact | None, str]:
    artifact = await _resolve_artifact(db, user, task_id)
    sections = [prompt.strip()] if prompt.strip() else []
    linked_title = ""
    if task_id:
        task = await ContentRepository(db).get_task_for_user(task_id, user)
        linked_title = _linked_content_title(artifact, task)
        if task and artifact:
            sections.append(
                "根据以下内容资产生成小红书风格封面：\n"
                f"标题：{linked_title}\n"
                f"正文摘要：{artifact.body.strip()[:1500]}\n"
                f"话题：{'、'.join(artifact.topics or [])}"
            )
        elif task:
            sections.append(f"根据内容任务《{linked_title}》生成小红书风格封面。")
    if not sections:
        if not allow_empty:
            raise _error(422, "COVER_PROMPT_REQUIRED", "请填写封面生成提示词")
        sections.append("仅替换模板底图为原图，其余上层样式保持一致。")
    return "\n\n".join(sections), artifact, linked_title


async def _resolve_poster_context(
    db: AsyncSession,
    user: User,
    *,
    poster_template_id: str,
    product_asset_id: str,
    for_update: bool = False,
) -> tuple[ContentCoverPosterTemplate, ContentCoverAsset, ContentCoverAsset]:
    repo = ContentCoverRepository(db)
    owner_uid = _owner_uid(user)
    template_record = await repo.get_poster_template_for_user(poster_template_id, owner_uid, for_update=for_update)
    if template_record is None:
        raise _error(404, "POSTER_TEMPLATE_NOT_FOUND", "大字报蒙版不存在")
    if template_record.status == "needs_review":
        raise _error(409, "POSTER_TEMPLATE_REVIEW_REQUIRED", "请先校对并确认 OCR 文字图层")
    if template_record.status != "ready" or not template_record.product_box_json:
        raise _error(422, "POSTER_TEMPLATE_NOT_READY", "该蒙版尚未标注产品替换区域或已被停用")
    template_asset = await repo.get_asset_for_user(template_record.asset_id, owner_uid, for_update=for_update)
    if template_asset is None or template_asset.role != "poster_template":
        raise _error(409, "POSTER_TEMPLATE_ASSET_MISSING", "大字报蒙版原始文件不存在")
    product_asset = await repo.get_asset_for_user(product_asset_id, owner_uid, for_update=for_update)
    if product_asset is None or product_asset.role not in {"source", "library_image"}:
        raise _error(422, "POSTER_PRODUCT_ASSET_INVALID", "产品图片不存在或素材角色不正确")
    return template_record, template_asset, product_asset


def _poster_template_snapshot(item: ContentCoverPosterTemplate) -> dict[str, Any]:
    return {
        "id": item.id,
        "asset_id": item.asset_id,
        "name": item.name,
        "version": item.version,
        "template_type": item.template_type,
        "canvas_width": item.canvas_width,
        "canvas_height": item.canvas_height,
        "product_box": item.product_box_json,
        "background_mode": (item.analysis_json or {}).get("background_mode", "replace_region"),
        "safe_area": item.safe_area_json or {},
        "text_slots": normalize_poster_text_slots(item.text_slots_json or []),
        "fixed_regions": item.fixed_regions_json or [],
        "editable_regions": item.editable_regions_json or [],
        "analysis_version": item.analysis_version,
    }


async def _poster_copy_context(
    db: AsyncSession,
    user: User,
    payload: PosterPreviewCreate,
) -> tuple[ContentArtifact | None, dict[str, Any], dict[str, Any]]:
    artifact = await _resolve_artifact(db, user, payload.content_task_id)
    task = (
        await ContentRepository(db).get_task_for_user(payload.content_task_id, user)
        if payload.content_task_id
        else None
    )
    linked_title = _linked_content_title(artifact, task)
    title = payload.title.strip() or linked_title
    text_context = _template_texts(
        artifact,
        title,
        source="content_asset" if payload.content_task_id else ("manual" if payload.title.strip() else "template"),
    )
    if artifact is None and not title:
        text_context["source"] = "template"
    return (
        artifact,
        text_context,
        {
            "title": title,
            "linked_title": linked_title,
        },
    )


async def preview_poster_billboard(
    db: AsyncSession,
    user: User,
    payload: PosterPreviewCreate,
) -> dict[str, Any]:
    template_record, template_asset, product_asset = await _resolve_poster_context(
        db,
        user,
        poster_template_id=payload.poster_template_id,
        product_asset_id=payload.product_asset_id,
    )
    _, texts, _ = await _poster_copy_context(db, user, payload)
    copy_plan = build_poster_copy_plan(
        template_record.text_slots_json or [],
        title=str(texts.get("title") or ""),
        subtitle=str(texts.get("subtitle") or ""),
        tags=list(texts.get("tags") or []),
        source=str(texts.get("source") or "template"),
        overrides=payload.copy_overrides,
    )
    try:
        template_data, product_data = await asyncio.gather(
            get_minio_client().adownload_file(template_asset.bucket_name, template_asset.object_name),
            get_minio_client().adownload_file(product_asset.bucket_name, product_asset.object_name),
        )
        rendered, metadata = await asyncio.to_thread(
            render_poster_billboard,
            _open_cover_image(template_data, error_message="大字报蒙版不是有效图片"),
            _open_cover_image(product_data, error_message="产品图片不是有效图片"),
            _poster_template_snapshot(template_record),
            copy_plan,
            transform=payload.transform.model_dump(mode="json"),
        )
        output_image = _open_cover_image(rendered, error_message="大字报预览生成失败")
        quality_report = evaluate_poster_quality(output_image, _poster_template_snapshot(template_record), metadata)
    except StorageError as exc:
        raise _error(500, "COVER_STORAGE_FAILED", "大字报素材读取失败", retryable=True) from exc
    except PosterBillboardError as exc:
        raise _error(422, "POSTER_PREVIEW_FAILED", str(exc)) from exc
    category = await resolve_material_category(
        db,
        owner_uid=_owner_uid(user),
        tenant_id=_tenant_id(user),
        material_type="cover_template",
        category_id=template_record.category,
    )
    await db.commit()
    return {
        "preview_data_url": f"data:image/png;base64,{base64.b64encode(rendered).decode('ascii')}",
        "copy_plan": copy_plan,
        "transform": payload.transform.model_dump(mode="json"),
        "quality_report": quality_report,
        "template": serialize_poster_template(template_record, category.name),
    }


async def create_poster_billboard_job(
    db: AsyncSession,
    user: User,
    payload: PosterGenerateCreate,
) -> dict[str, Any]:
    template_record, _, _ = await _resolve_poster_context(
        db,
        user,
        poster_template_id=payload.poster_template_id,
        product_asset_id=payload.product_asset_id,
        for_update=True,
    )
    image2_config = None
    if payload.enhance_with_image2:
        if template_record.template_type != "alpha_overlay":
            raise _error(
                422,
                "POSTER_IMAGE2_REQUIRES_ALPHA",
                "不透明画板仅支持确定性合成，转换为透明蒙版后才能开启 image2 美化",
            )
        try:
            image2_config = await resolve_image2_config(db, owner_uid=_owner_uid(user))
        except Image2Error as exc:
            raise _error(503, "IMAGE2_NOT_CONFIGURED", "开启 image2 美化前请先配置可用的中转站") from exc
    artifact, texts, title_context = await _poster_copy_context(db, user, payload)
    copy_plan = build_poster_copy_plan(
        template_record.text_slots_json or [],
        title=str(texts.get("title") or ""),
        subtitle=str(texts.get("subtitle") or ""),
        tags=list(texts.get("tags") or []),
        source=str(texts.get("source") or "template"),
        overrides=payload.copy_overrides,
    )
    request = {
        **payload.model_dump(mode="json"),
        "size": "1080x1440",
        "processing_version": POSTER_PROCESSING_VERSION,
        "poster_template_snapshot": _poster_template_snapshot(template_record),
        "copy_plan": copy_plan,
        "title": title_context["title"],
    }
    job, deduplicated = await _create_job(
        db,
        user,
        mode="poster_billboard",
        content_task_id=payload.content_task_id,
        artifact_id=artifact.id if artifact else None,
        idempotency_key=payload.idempotency_key,
        request=request,
        model=image2_config.model if image2_config else None,
    )
    return {"job": serialize_job(job), "deduplicated": deduplicated}


async def create_cover_generate_job(db: AsyncSession, user: User, payload: CoverGenerateCreate) -> dict[str, Any]:
    try:
        image2_config = await resolve_image2_config(db, owner_uid=_owner_uid(user))
    except Image2Error as exc:
        raise _error(503, "IMAGE2_NOT_CONFIGURED", "image2 中转站尚未配置") from exc
    repo = ContentCoverRepository(db)
    owner_uid = _owner_uid(user)
    source_assets = await repo.get_assets_for_user(payload.source_asset_ids, owner_uid, for_update=True)
    if len(source_assets) != len(payload.source_asset_ids) or any(
        item.role not in {"source", "library_image"} for item in source_assets
    ):
        raise _error(422, "COVER_SOURCE_ASSET_INVALID", "原图不存在或角色不正确")
    template = None
    if payload.template_asset_id:
        template = await repo.get_asset_for_user(payload.template_asset_id, owner_uid, for_update=True)
        if template is None or template.role != "template":
            raise _error(422, "COVER_TEMPLATE_ASSET_INVALID", "模板图不存在或角色不正确")
    mask = None
    if payload.mask_asset_id:
        mask = await repo.get_asset_for_user(payload.mask_asset_id, owner_uid, for_update=True)
        if mask is None or mask.role != "mask":
            raise _error(422, "COVER_MASK_ASSET_INVALID", "蒙版图不存在或角色不正确")
        source = source_assets[0]
        if (mask.image_width, mask.image_height) != (source.image_width, source.image_height):
            raise _error(422, "COVER_MASK_SIZE_MISMATCH", "蒙版尺寸必须与原图一致")
    template_replicate = payload.mode == "multi_reference" and bool(payload.template_asset_id)
    if template_replicate and payload.size != "1080x1440":
        raise _error(422, "COVER_TEMPLATE_SIZE_INVALID", "模板复刻 V2 当前固定输出 1080×1440 PNG")
    prompt, artifact, linked_title = await _content_prompt(
        db,
        user,
        payload.content_task_id,
        payload.prompt,
        allow_empty=template_replicate,
    )
    title = payload.title.strip() or linked_title[:60]
    template_texts = (
        _template_texts(
            artifact,
            title,
            source="content_asset" if payload.content_task_id else ("manual" if payload.title.strip() else "template"),
        )
        if template_replicate
        else None
    )
    mode_guidance = {
        "text_to_image": "生成高点击率的小红书封面底图，构图简洁、主体突出、层次清晰。",
        "image_to_image": "保留原图主体身份与关键细节，优化构图、光影和小红书封面氛围，不要凭空替换主体。",
        "multi_reference": "综合所有参考图；保留原图主体，借鉴模板的布局与视觉语言，但不要照搬其中的文字或品牌元素。",
        "mask": "只优化蒙版指定区域，未指定区域保持原图结构与主体一致。",
    }
    if template_replicate:
        mode_guidance["multi_reference"] = (
            "严格模板复刻模式。参考图1是用户原图，参考图2是样式模板。最终封面必须以参考图1完整铺满画布，"
            "保留其人物、商品、空间、视角和关键细节；参考图2只提供上层视觉系统，不得保留其中的"
            "房间、人物、商品或任何底图内容。从参考图2迁移上层元素：标题和副标题的位置与层级、字体粗细和颜色关系、贴纸、图标、"
            "色块、线条、边框及角标；这些元素的坐标、尺寸比例、间距和整体风格应尽量与模板一致。"
            "严禁把参考图1缩小后塞入矩形、圆角卡片、相框或局部区域，"
            "严禁形成模板旧底图加新图卡片的套图。"
        )
        output_guidance = (
            "输出必须是单张完整、不透明的封面图。把参考图2视为透明上层，把其中所有可见文字、"
            "字体粗细、描边、阴影、贴纸、图标、色块和圆角文字条按原坐标、原比例迁移到参考图1。"
            "模板原文也是必须保留的上层样式，不得擦除、改写、翻译、模糊或变成伪文字；不得新增模板中"
            "不存在的卡片或装饰。参考图1的底图内容、手写字、表格、商品和人物必须保持清晰。"
            "系统会在生成后按内容资产需要精确替换主标题，因此不要自行扩写新口号或新段落。"
        )
    else:
        output_guidance = (
            "输出完整的封面视觉底图，左上区域预留干净、低细节的标题安全区。"
            "画面内不要生成任何文字、数字、字母、水印、平台 Logo 或伪造品牌标识；"
            "系统会在生成后叠加准确的中文标题。"
        )
    prompt = f"{mode_guidance[payload.mode]}\n{output_guidance}\n\n{prompt}"
    default_negative_prompt = (
        "乱码文字、错误汉字、随机字母、数字、水印、平台 Logo、伪造品牌标识、低清晰度、主体变形、过度锐化、杂乱背景"
    )
    if template_replicate:
        default_negative_prompt += (
            "、透明棋盘格、马赛克、模板旧底图残留、双重背景、画中画、原图缩小、"
            "原图置于卡片、圆角相框、白色大面板、新增边框、左右分栏、参考板、深色分隔线"
        )
    request = payload.model_dump()
    request["processing_version"] = COVER_PROCESSING_VERSION
    request["prompt"] = prompt
    request["title"] = title
    request["template_texts"] = template_texts
    request["template_replicate"] = template_replicate
    request["parameters"] = {
        **payload.parameters,
        **(
            {
                "template_replicate": True,
                "quality": "high",
                "output_format": "png",
            }
            if template_replicate
            else {}
        ),
    }
    request["negative_prompt"] = "，".join(
        item for item in ((payload.negative_prompt or "").strip(), default_negative_prompt) if item
    )
    job, deduplicated = await _create_job(
        db,
        user,
        mode=payload.mode,
        content_task_id=payload.content_task_id,
        artifact_id=artifact.id if artifact else None,
        idempotency_key=payload.idempotency_key,
        request=request,
        model=image2_config.model,
    )
    return {"job": serialize_job(job), "deduplicated": deduplicated}


async def preview_template_replication_plan(
    db: AsyncSession,
    user: User,
    payload: TemplateReplicatePlanCreate,
) -> dict[str, Any]:
    repo = ContentCoverRepository(db)
    owner_uid = _owner_uid(user)
    template = await repo.get_asset_for_user(payload.template_asset_id, owner_uid)
    source = await repo.get_asset_for_user(payload.source_asset_id, owner_uid)
    if template is None or template.role != "template":
        raise _error(422, "COVER_TEMPLATE_ASSET_INVALID", "模板图不存在或角色不正确")
    if source is None or source.role != "source":
        raise _error(422, "COVER_SOURCE_ASSET_INVALID", "原图不存在或角色不正确")
    size = COVER_SIZES[payload.size]
    try:
        template_data, source_data = await asyncio.gather(
            get_minio_client().adownload_file(template.bucket_name, template.object_name),
            get_minio_client().adownload_file(source.bucket_name, source.object_name),
        )
        with Image.open(io.BytesIO(template_data)) as opened_template:
            template_image = ImageOps.exif_transpose(opened_template).convert("RGB")
            template_image.load()
        with Image.open(io.BytesIO(source_data)) as opened_source:
            source_image = ImageOps.exif_transpose(opened_source).convert("RGB")
            source_image.load()
        base_analysis = await asyncio.to_thread(
            analyze_template,
            template_image,
            target_size=(size["width"], size["height"]),
        )
        analysis = apply_layout_overrides(base_analysis, payload.layout_overrides)
        await asyncio.to_thread(ensure_clean_source, source_image, analysis)
        metadata = dict(template.metadata_json or {})
        cache = dict(metadata.get("template_replication_analysis") or {})
        cache[payload.size] = base_analysis.model_dump(mode="json")
        metadata["template_replication_analysis"] = cache
        await repo.update_asset_metadata(template, metadata)
        await db.commit()
    except (StorageError, UnidentifiedImageError, OSError) as exc:
        raise _error(422, "COVER_TEMPLATE_ANALYSIS_FAILED", "模板或原图读取失败") from exc
    except TemplateReplicationError as exc:
        raise _error(422, "COVER_TEMPLATE_ANALYSIS_FAILED", str(exc)) from exc
    artifact = await _resolve_artifact(db, user, payload.content_task_id)
    task = (
        await ContentRepository(db).get_task_for_user(payload.content_task_id, user)
        if payload.content_task_id
        else None
    )
    linked_title = _linked_content_title(artifact, task)
    title = payload.title.strip() or linked_title
    texts = _template_texts(
        artifact,
        title,
        source="content_asset" if payload.content_task_id else ("manual" if payload.title.strip() else "template"),
    )
    if artifact is None and not title:
        texts["source"] = "template"
    copy_plan = build_copy_plan(
        analysis,
        title=str(texts.get("title") or ""),
        subtitle=str(texts.get("subtitle") or ""),
        tags=list(texts.get("tags") or []),
        source=str(texts.get("source") or "template"),
        overrides=payload.copy_overrides,
    )
    return {
        "analysis": analysis.model_dump(mode="json"),
        "copy_plan": copy_plan.model_dump(mode="json"),
        "render_plan": build_render_plan(analysis).model_dump(mode="json"),
        "source_preview": {
            "width": source_image.width,
            "height": source_image.height,
            "fit": "cover",
        },
    }


async def get_cover_job(db: AsyncSession, user: User, job_id: str) -> dict[str, Any]:
    job = await ContentCoverRepository(db).get_job_for_user(job_id, _owner_uid(user))
    if job is None:
        raise _error(404, "COVER_JOB_NOT_FOUND", "封面任务不存在")
    return {"job": serialize_job(job)}


async def list_cover_jobs(
    db: AsyncSession,
    user: User,
    *,
    content_task_id: str | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    items, total = await ContentCoverRepository(db).list_jobs(
        _owner_uid(user), content_task_id=content_task_id, page=page, page_size=page_size
    )
    return {"items": [serialize_job(item) for item in items], "total": total, "page": page, "page_size": page_size}


async def retry_cover_job(
    db: AsyncSession,
    user: User,
    job_id: str,
    payload: CoverRetryCreate,
    *,
    workflow_resume: dict[str, Any] | None = None,
) -> dict[str, Any]:
    old = await ContentCoverRepository(db).get_job_for_user(job_id, _owner_uid(user))
    if old is None:
        raise _error(404, "COVER_JOB_NOT_FOUND", "封面任务不存在")
    if old.status not in {"failed", "cancelled", "succeeded"}:
        raise _error(409, "COVER_JOB_NOT_RETRYABLE", "任务结束后才能重新生成")
    image2_config = None
    requires_image2 = old.mode not in {"compose", "hycanvas", "editor_render", "poster_billboard"} or (
        old.mode == "poster_billboard" and bool((old.request_json or {}).get("enhance_with_image2"))
    )
    if requires_image2:
        try:
            image2_config = await resolve_image2_config(db, owner_uid=_owner_uid(user))
        except Image2Error as exc:
            raise _error(503, "IMAGE2_NOT_CONFIGURED", "image2 中转站尚未配置") from exc
    recoverable_provider_task_id = None
    retry_result_json: dict[str, Any] = {}
    if old.provider_task_id and old.error_code in {
        "IMAGE2_POLL_TIMEOUT",
        "IMAGE2_NETWORK_ERROR",
        "IMAGE2_DOWNLOAD_FAILED",
        "IMAGE2_RESULT_EMPTY",
        "COVER_WORKER_FAILED",
    }:
        recoverable_provider_task_id = old.provider_task_id
        provider_task_ids = list((old.result_json or {}).get("provider_task_ids") or [])
        if provider_task_ids:
            retry_result_json["provider_task_ids"] = provider_task_ids
    request = deepcopy(old.request_json or {})
    if workflow_resume:
        resume_container = "layout" if old.mode == "compose" else "parameters"
        request[resume_container] = {
            **(request.get(resume_container) or {}),
            "workflow_resume": workflow_resume,
        }
    job, deduplicated = await _create_job(
        db,
        user,
        mode=old.mode,
        content_task_id=old.content_task_id,
        artifact_id=old.artifact_id,
        idempotency_key=payload.idempotency_key,
        request=request,
        parent_job_id=old.id,
        provider_task_id=recoverable_provider_task_id,
        initial_result_json=retry_result_json,
        model=image2_config.model if image2_config else old.model,
    )
    return {"job": serialize_job(job), "deduplicated": deduplicated}


async def update_image2_global_config(
    db: AsyncSession,
    user: User,
    payload: Image2GlobalConfigUpdate,
) -> dict[str, Any]:
    try:
        await save_image2_config(
            db,
            base_url=payload.base_url,
            api_key=payload.api_key,
            model=payload.model,
            owner_uid=_owner_uid(user),
        )
    except Image2Error as exc:
        raise _error(422, exc.code, str(exc)) from exc
    return await get_image2_config_state(db, owner_uid=_owner_uid(user))


async def test_image2_global_config(
    db: AsyncSession,
    user: User,
    payload: Image2ConfigTestRequest,
) -> dict[str, Any]:
    try:
        return await verify_image2_config(
            db,
            base_url=payload.base_url,
            api_key=payload.api_key,
            model=payload.model,
            owner_uid=_owner_uid(user),
        )
    except Image2Error as exc:
        raise _error(422, exc.code, str(exc), retryable=exc.retryable) from exc


async def cancel_cover_job(db: AsyncSession, user: User, job_id: str) -> dict[str, Any]:
    job = await ContentCoverRepository(db).get_job_for_user(job_id, _owner_uid(user), for_update=True)
    if job is None:
        raise _error(404, "COVER_JOB_NOT_FOUND", "封面任务不存在")
    if job.status == "saving":
        raise _error(409, "COVER_JOB_TOO_LATE_TO_CANCEL", "封面结果正在保存，当前阶段不能取消")
    if job.status not in TERMINAL_COVER_STATUSES:
        job.status = "cancel_requested"
        await db.commit()
        try:
            await publish_cancel_signal(job.id)
        except Exception:
            logger.warning("Failed to publish cover cancellation signal: %s", job.id, exc_info=True)
    return {"job": serialize_job(job)}


async def set_current_cover(
    db: AsyncSession,
    user: User,
    job_id: str,
    *,
    asset_id: str | None = None,
) -> dict[str, Any]:
    repo = ContentCoverRepository(db)
    job = await repo.get_job_for_user(job_id, _owner_uid(user), for_update=True)
    if job is None:
        raise _error(404, "COVER_JOB_NOT_FOUND", "封面任务不存在")
    if job.status != "succeeded" or not (job.result_json or {}).get("asset_ids"):
        raise _error(409, "COVER_JOB_NOT_READY", "封面生成完成后才能设为当前封面")
    content_repo = ContentRepository(db)
    artifact_id = job.artifact_id
    if not artifact_id and job.content_task_id:
        current_artifact = await content_repo.get_artifact_for_task(job.content_task_id)
        artifact_id = current_artifact.id if current_artifact else None
    if not artifact_id:
        raise _error(409, "COVER_ARTIFACT_REQUIRED", "关联内容任务生成产物后才能设置当前封面")
    artifact = await content_repo.get_artifact_for_user(artifact_id, user, for_update=True)
    if artifact is None:
        raise _error(404, "CONTENT_ARTIFACT_NOT_FOUND", "内容产物不存在")
    if job.artifact_id is None:
        job.artifact_id = artifact.id
    result_asset_ids = list(job.result_json["asset_ids"])
    selected_asset_id = asset_id or result_asset_ids[0]
    if selected_asset_id not in result_asset_ids:
        raise _error(422, "COVER_RESULT_ASSET_INVALID", "所选图片不属于该封面任务")
    asset = await repo.get_asset_for_user(selected_asset_id, _owner_uid(user))
    if asset is None or asset.role != "output":
        raise _error(404, "COVER_ASSET_NOT_FOUND", "封面结果不存在")
    version = await repo.set_current_cover(artifact=artifact, asset=asset, job=job, owner_uid=_owner_uid(user))
    await db.commit()
    return {
        "artifact": artifact.to_dict(),
        "version": {"id": version.id, "version": version.version, "cover_asset_id": asset.id},
        "cover": serialize_asset(asset),
    }


def _format_sse(data: dict, *, event: str, event_id: str | None = None) -> str:
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


async def stream_cover_job_events(job_id: str, owner_uid: str, after_seq: str) -> AsyncIterator[str]:
    last_seq = normalize_after_seq(after_seq)
    heartbeat = 0
    while True:
        from yuxi.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            job = await ContentCoverRepository(db).get_job_for_user(job_id, owner_uid)
        if job is None:
            yield _format_sse({"message": "封面任务不存在"}, event="error")
            return
        events = await list_run_stream_events(job_id, after_seq=last_seq, limit=100)
        terminal_event = False
        for item in events:
            last_seq = str(item.get("seq") or last_seq)
            event_type = item.get("event_type") or "message"
            yield _format_sse(item.get("payload") or {}, event=event_type, event_id=last_seq)
            terminal_event = terminal_event or event_type == "end"
        if terminal_event:
            return
        if job.status in TERMINAL_COVER_STATUSES and not events:
            terminal_seq = await get_last_run_stream_seq(job_id)
            yield _format_sse(
                {"run_id": job_id, "event": "end", "payload": {"status": job.status}},
                event="end",
                event_id=terminal_seq if terminal_seq != "0-0" else None,
            )
            return
        heartbeat += 1
        if heartbeat % 15 == 0:
            yield ": heartbeat\n\n"
        await asyncio.sleep(1)
