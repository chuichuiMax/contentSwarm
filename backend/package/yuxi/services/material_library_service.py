from __future__ import annotations

import asyncio
import hashlib
import io
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.repositories.content_repository import ContentRepository
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.material_library_categories import (
    list_material_categories,
    resolve_legacy_category,
)
from yuxi.storage.minio import StorageError, get_minio_client
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentCoverPosterTemplate,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
)
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.upload_utils import read_upload_with_limit

MATERIAL_LIBRARY_BUCKET = "image"
MAX_MATERIAL_BYTES = 20 * 1024 * 1024
MAX_MATERIAL_DIMENSION = 8192
MAX_MATERIAL_PIXELS = 40_000_000
MATERIAL_THUMBNAIL_SIZE = (480, 480)


class MaterialItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    status: Literal["enabled", "disabled"] | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("名称不能为空")
        return value


class MaterialCategoryCreate(BaseModel):
    material_type: Literal["image", "cover_template"]
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=255)
    parent_id: str | None = Field(default=None, max_length=64)
    industry_slug: str | None = Field(default=None, max_length=80)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("名称不能为空")
        return value


class MaterialCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=255)
    sort_order: int | None = Field(default=None, ge=0, le=100000)
    industry_slug: str | None = Field(default=None, max_length=80)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("名称不能为空")
        return value


class MaterialCategoryDelete(BaseModel):
    target_category_id: str | None = Field(default=None, max_length=64)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


def _owner_uid(user: User) -> str:
    return str(user.uid)


def _tenant_id(user: User) -> str | None:
    return str(user.department_id) if user.department_id is not None else None


def _normalize_image(data: bytes) -> tuple[bytes, int, int, str]:
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise _error(400, "MATERIAL_FORMAT_UNSUPPORTED", "仅支持 JPG、PNG 或 WebP 图片")
            image = ImageOps.exif_transpose(source)
            image.load()
            width, height = image.size
            if (
                width < 2
                or height < 2
                or max(width, height) > MAX_MATERIAL_DIMENSION
                or width * height > MAX_MATERIAL_PIXELS
            ):
                raise _error(400, "MATERIAL_DIMENSION_INVALID", "图片尺寸必须在 2–8192 像素且不超过 4000 万像素")
            output = io.BytesIO()
            image.convert("RGBA").save(output, format="PNG", optimize=True)
            return output.getvalue(), width, height, "image/png"
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise _error(400, "MATERIAL_IMAGE_INVALID", "上传文件不是有效图片") from exc


def _make_image_thumbnail(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail(MATERIAL_THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=78, optimize=True)
        return output.getvalue()


def serialize_item(
    item: ContentMaterialLibraryItem,
    asset: ContentCoverAsset,
    category: ContentMaterialCategory,
    poster_template: ContentCoverPosterTemplate | None = None,
) -> dict[str, Any]:
    result = item.to_dict()
    result.update(
        {
            "category": category.id,
            "category_name": category.name,
            "industry_slug": category.industry_slug,
            "file_name": asset.original_file_name,
            "content_type": asset.content_type,
            "file_size": asset.file_size,
            "width": asset.image_width,
            "height": asset.image_height,
            "sha256": asset.sha256,
            "file_url": f"/api/material-library/items/{item.id}/file",
        }
    )
    if item.material_type == "cover_template":
        analysis = (poster_template.analysis_json or {}) if poster_template else {}
        review_status = analysis.get("review_status") or (
            "confirmed"
            if poster_template and poster_template.status == "ready"
            else "pending"
            if poster_template and poster_template.status == "needs_review"
            else "not_applicable"
        )
        result.update(
            {
                "poster_template_id": poster_template.id if poster_template else None,
                "template_status": poster_template.status if poster_template else "unavailable",
                "template_version": poster_template.version if poster_template else None,
                "review_status": review_status,
                "requires_review": review_status == "pending",
                "recognition_metrics": analysis.get("recognition_metrics") or {},
                "selectable": bool(
                    item.status == "enabled"
                    and poster_template
                    and poster_template.status == "ready"
                    and poster_template.product_box_json
                ),
            }
        )
    return result


async def ensure_material_categories(
    db: AsyncSession,
    *,
    owner_uid: str,
    tenant_id: str | None,
    material_type: Literal["image", "cover_template"],
) -> list[ContentMaterialCategory]:
    repo = MaterialLibraryRepository(db)
    categories = await repo.list_categories(owner_uid, material_type)
    if not categories:
        await repo.ensure_default_categories(
            [
                {
                    "owner_uid": owner_uid,
                    "id": definition["code"],
                    "tenant_id": tenant_id,
                    "material_type": material_type,
                    "name": definition["name"],
                    "description": definition["description"],
                    "sort_order": index * 10,
                    "is_system": definition["code"] == "uncategorized",
                }
                for index, definition in enumerate(list_material_categories(material_type))
            ]
        )
        categories = await repo.list_categories(owner_uid, material_type)
    fallback = next(category for category in categories if category.is_system)
    await repo.normalize_orphan_categories(
        owner_uid,
        material_type,
        [category.id for category in categories],
        fallback.id,
    )
    return categories


async def _industry_catalog(db: AsyncSession) -> dict[str, str]:
    return {
        item["slug"]: item["name"]
        for item in await ContentRepository(db).list_templates()
    }


async def _validate_industry_slug(db: AsyncSession, value: str | None) -> str | None:
    slug = (value or "").strip() or None
    if slug is not None and slug != "uncategorized" and slug not in await _industry_catalog(db):
        raise _error(422, "MATERIAL_INDUSTRY_INVALID", "所选行业不存在或尚未发布")
    return slug


async def resolve_material_category(
    db: AsyncSession,
    *,
    owner_uid: str,
    tenant_id: str | None,
    material_type: Literal["image", "cover_template"],
    category_id: str | None,
) -> ContentMaterialCategory:
    categories = await ensure_material_categories(
        db,
        owner_uid=owner_uid,
        tenant_id=tenant_id,
        material_type=material_type,
    )
    requested = (category_id or "uncategorized").strip()
    by_id = {category.id: category for category in categories}
    legacy_id = resolve_legacy_category(material_type, requested)
    category = by_id.get(requested) or (by_id.get(legacy_id) if legacy_id else None)
    if category is None:
        raise _error(422, "MATERIAL_CATEGORY_INVALID", "素材图库或分类不存在")
    return category


async def create_library_item_for_asset(
    db: AsyncSession,
    *,
    asset: ContentCoverAsset,
    material_type: Literal["image", "cover_template"],
    name: str,
    category: str = "uncategorized",
    metadata: dict[str, Any] | None = None,
) -> ContentMaterialLibraryItem:
    repo = MaterialLibraryRepository(db)
    resolved_category = await resolve_material_category(
        db,
        owner_uid=asset.owner_uid,
        tenant_id=asset.tenant_id,
        material_type=material_type,
        category_id=category,
    )
    existing = await repo.get_item_by_asset(asset.id)
    if existing is not None:
        if await repo.get_category(asset.owner_uid, material_type, existing.category) is None:
            existing.category = resolved_category.id
        existing.tags_json = []
        return existing
    return await repo.create_item(
        id=f"mli_{uuid.uuid4().hex}",
        owner_uid=asset.owner_uid,
        tenant_id=asset.tenant_id,
        asset_id=asset.id,
        material_type=material_type,
        display_name=name.strip()[:255] or Path(asset.original_file_name).stem[:255],
        category=resolved_category.id,
        tags_json=[],
        status="enabled",
        metadata_json=metadata or {},
    )


async def import_material_images(
    db: AsyncSession,
    user: User,
    files: list[UploadFile],
    *,
    category: str,
) -> dict[str, Any]:
    if not files or len(files) > 50:
        raise _error(422, "MATERIAL_FILE_COUNT_INVALID", "每次必须上传 1–50 张图片")
    owner_uid = _owner_uid(user)
    resolved_category = await resolve_material_category(
        db,
        owner_uid=owner_uid,
        tenant_id=_tenant_id(user),
        material_type="image",
        category_id=category,
    )
    results: list[dict[str, Any]] = []
    for index, file in enumerate(files):
        if not file.filename:
            raise _error(400, "MATERIAL_FILE_NAME_REQUIRED", "无法识别上传文件名")
        try:
            raw = await read_upload_with_limit(
                file,
                max_size_bytes=MAX_MATERIAL_BYTES,
                too_large_message="图片过大，当前仅支持 20 MB 以内的文件",
            )
        except ValueError as exc:
            raise _error(400, "MATERIAL_IMAGE_TOO_LARGE", str(exc)) from exc
        normalized, width, height, content_type = _normalize_image(raw)
        if len(normalized) > MAX_MATERIAL_BYTES:
            raise _error(400, "MATERIAL_IMAGE_TOO_LARGE", "图片规范化后超过 20 MB")
        asset_id = f"cca_{uuid.uuid4().hex}"
        object_name = f"material-library/{owner_uid}/images/{asset_id}/image.png"
        try:
            uploaded = await get_minio_client().aupload_file(
                bucket_name=MATERIAL_LIBRARY_BUCKET,
                object_name=object_name,
                data=normalized,
                content_type=content_type,
            )
        except StorageError as exc:
            raise _error(500, "MATERIAL_STORAGE_FAILED", "素材保存失败") from exc
        try:
            asset = await ContentCoverRepository(db).create_asset(
                id=asset_id,
                owner_uid=owner_uid,
                tenant_id=_tenant_id(user),
                content_task_id=None,
                role="library_image",
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
            item = await create_library_item_for_asset(
                db,
                asset=asset,
                material_type="image",
                name=Path(file.filename).stem,
                category=resolved_category.id,
            )
            await db.commit()
        except Exception:
            await db.rollback()
            await get_minio_client().adelete_file(uploaded.bucket_name, uploaded.object_name)
            raise
        results.append(serialize_item(item, asset, resolved_category))
    return {"items": results, "summary": {"total": len(results), "created": len(results)}}


async def list_material_items(
    db: AsyncSession,
    user: User,
    *,
    material_type: str,
    category: str | None,
    status: str | None,
    query: str | None,
    page: int,
    page_size: int,
    sort: str,
) -> dict[str, Any]:
    if material_type not in {"image", "cover_template"}:
        raise _error(422, "MATERIAL_TYPE_INVALID", "素材类型不存在")
    if sort not in {"newest", "oldest", "name"}:
        raise _error(422, "MATERIAL_SORT_INVALID", "排序方式不存在")
    await ensure_material_categories(
        db,
        owner_uid=_owner_uid(user),
        tenant_id=_tenant_id(user),
        material_type=material_type,
    )
    resolved_category = (
        await resolve_material_category(
            db,
            owner_uid=_owner_uid(user),
            tenant_id=_tenant_id(user),
            material_type=material_type,
            category_id=category,
        )
        if category
        else None
    )
    rows, total = await MaterialLibraryRepository(db).list_items(
        _owner_uid(user),
        material_type=material_type,
        category=resolved_category.id if resolved_category else None,
        status=status,
        query_text=query,
        page=page,
        page_size=page_size,
        sort=sort,
    )
    posters_by_asset: dict[str, ContentCoverPosterTemplate] = {}
    if material_type == "cover_template":
        posters = await MaterialLibraryRepository(db).list_poster_templates_by_asset_ids(
            _owner_uid(user),
            [asset.id for _, asset, _ in rows],
        )
        posters_by_asset = {poster.asset_id: poster for poster in posters}
    await db.commit()
    return {
        "items": [
            serialize_item(item, asset, item_category, posters_by_asset.get(asset.id))
            for item, asset, item_category in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def update_material_item(
    db: AsyncSession, user: User, item_id: str, payload: MaterialItemUpdate
) -> dict[str, Any]:
    repo = MaterialLibraryRepository(db)
    item = await repo.get_item_for_user(item_id, _owner_uid(user), for_update=True)
    if item is None:
        raise _error(404, "MATERIAL_NOT_FOUND", "素材不存在")
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        item.display_name = changes["name"].strip()
    if "category" in changes:
        item_category = await resolve_material_category(
            db,
            owner_uid=_owner_uid(user),
            tenant_id=_tenant_id(user),
            material_type=item.material_type,
            category_id=changes["category"],
        )
        item.category = item_category.id
    else:
        item_category = await resolve_material_category(
            db,
            owner_uid=_owner_uid(user),
            tenant_id=_tenant_id(user),
            material_type=item.material_type,
            category_id=item.category,
        )
    if "status" in changes:
        item.status = changes["status"]
    item.updated_at = utc_now_naive()
    asset = await repo.get_asset(item.asset_id, _owner_uid(user))
    if asset is None:
        raise _error(409, "MATERIAL_ASSET_MISSING", "素材文件记录不存在")
    poster = await repo.get_poster_template_by_asset(asset.id)
    if poster is not None:
        poster.name = item.display_name
        poster.category = item.category
        if "status" in changes:
            if item.status == "enabled" and (poster.analysis_json or {}).get("review_status") == "pending":
                raise _error(409, "POSTER_TEMPLATE_REVIEW_REQUIRED", "请先校对并确认 OCR 文字图层")
            poster.status = "ready" if item.status == "enabled" and poster.product_box_json else "disabled"
    await db.commit()
    return {"item": serialize_item(item, asset, item_category, poster)}


async def get_material_categories(db: AsyncSession, user: User, material_type: str) -> dict[str, Any]:
    if material_type not in {"image", "cover_template"}:
        raise _error(422, "MATERIAL_TYPE_INVALID", "素材类型不存在")
    repo = MaterialLibraryRepository(db)
    categories = await ensure_material_categories(
        db,
        owner_uid=_owner_uid(user),
        tenant_id=_tenant_id(user),
        material_type=material_type,
    )
    industry_catalog = await _industry_catalog(db)
    parents = {category.id: category for category in categories if category.parent_id is None}
    result = []
    for category in categories:
        effective_industry = (
            parents[category.parent_id].industry_slug
            if category.parent_id and category.parent_id in parents
            else category.industry_slug
        )
        children = await repo.list_child_categories(_owner_uid(user), material_type, category.id)
        result.append(
            {
                **category.to_dict(),
                "industry_slug": effective_industry,
                "industry_name": industry_catalog.get(effective_industry, "未分类行业"),
                "count": await repo.category_item_count(_owner_uid(user), material_type, category.id),
                "child_count": len(children),
            }
        )
    await db.commit()
    return {"material_type": material_type, "categories": result}


async def create_material_category(
    db: AsyncSession,
    user: User,
    payload: MaterialCategoryCreate,
) -> dict[str, Any]:
    repo = MaterialLibraryRepository(db)
    categories = await ensure_material_categories(
        db,
        owner_uid=_owner_uid(user),
        tenant_id=_tenant_id(user),
        material_type=payload.material_type,
    )
    parent = None
    industry_slug = None
    if payload.parent_id:
        if payload.material_type != "image":
            raise _error(422, "MATERIAL_CATEGORY_DEPTH_INVALID", "只有素材图片图库支持二级图库")
        parent = await repo.get_category(_owner_uid(user), payload.material_type, payload.parent_id)
        if parent is None:
            raise _error(422, "MATERIAL_CATEGORY_PARENT_INVALID", "所属一级图库不存在")
        if parent.parent_id or parent.is_system:
            raise _error(422, "MATERIAL_CATEGORY_DEPTH_INVALID", "二级图库只能创建在普通一级图库下")
        if payload.industry_slug and payload.industry_slug != parent.industry_slug:
            raise _error(422, "MATERIAL_INDUSTRY_INHERITED", "二级图库必须继承一级图库行业")
        industry_slug = parent.industry_slug
    elif payload.material_type == "image":
        industry_slug = await _validate_industry_slug(db, payload.industry_slug) or "uncategorized"
    try:
        category = await repo.create_category(
            owner_uid=_owner_uid(user),
            id=f"mlc_{uuid.uuid4().hex}",
            tenant_id=_tenant_id(user),
            material_type=payload.material_type,
            parent_id=parent.id if parent else None,
            industry_slug=industry_slug,
            name=payload.name.strip(),
            description=payload.description.strip(),
            sort_order=(max(item.sort_order for item in categories) + 10),
            is_system=False,
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _error(409, "MATERIAL_CATEGORY_NAME_DUPLICATE", "图库或分类名称已存在") from exc
    return {"category": {**category.to_dict(), "count": 0}}


async def update_material_category(
    db: AsyncSession,
    user: User,
    material_type: str,
    category_id: str,
    payload: MaterialCategoryUpdate,
) -> dict[str, Any]:
    if material_type not in {"image", "cover_template"}:
        raise _error(422, "MATERIAL_TYPE_INVALID", "素材类型不存在")
    await ensure_material_categories(
        db,
        owner_uid=_owner_uid(user),
        tenant_id=_tenant_id(user),
        material_type=material_type,
    )
    repo = MaterialLibraryRepository(db)
    category = await repo.get_category(_owner_uid(user), material_type, category_id, for_update=True)
    if category is None:
        raise _error(404, "MATERIAL_CATEGORY_NOT_FOUND", "图库或分类不存在")
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        category.name = changes["name"].strip()
    if "description" in changes:
        category.description = changes["description"].strip()
    if "sort_order" in changes:
        category.sort_order = changes["sort_order"]
    if "industry_slug" in changes:
        if material_type != "image" or category.parent_id:
            raise _error(422, "MATERIAL_INDUSTRY_INHERITED", "只有一级图片图库可以设置行业")
        category.industry_slug = await _validate_industry_slug(db, changes["industry_slug"]) or "uncategorized"
        await repo.update_child_category_industry(
            _owner_uid(user), material_type, category.id, category.industry_slug
        )
    category.updated_at = utc_now_naive()
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _error(409, "MATERIAL_CATEGORY_NAME_DUPLICATE", "图库或分类名称已存在") from exc
    count = await repo.category_item_count(_owner_uid(user), material_type, category.id)
    industry_catalog = await _industry_catalog(db)
    return {
        "category": {
            **category.to_dict(),
            "industry_name": industry_catalog.get(category.industry_slug, "未分类行业"),
            "count": count,
        }
    }


async def delete_material_category(
    db: AsyncSession,
    user: User,
    material_type: str,
    category_id: str,
    payload: MaterialCategoryDelete,
) -> dict[str, Any]:
    if material_type not in {"image", "cover_template"}:
        raise _error(422, "MATERIAL_TYPE_INVALID", "素材类型不存在")
    categories = await ensure_material_categories(
        db,
        owner_uid=_owner_uid(user),
        tenant_id=_tenant_id(user),
        material_type=material_type,
    )
    repo = MaterialLibraryRepository(db)
    category = await repo.get_category(_owner_uid(user), material_type, category_id, for_update=True)
    if category is None:
        raise _error(404, "MATERIAL_CATEGORY_NOT_FOUND", "图库或分类不存在")
    if category.is_system:
        raise _error(409, "MATERIAL_CATEGORY_SYSTEM_REQUIRED", "未分类是系统兜底项，不能删除")
    children = await repo.list_child_categories(_owner_uid(user), material_type, category.id)
    if children:
        raise _error(409, "MATERIAL_CATEGORY_HAS_CHILDREN", "一级图库仍有二级图库，请先移动或删除二级图库")
    fallback = next(item for item in categories if item.is_system)
    target_id = payload.target_category_id or fallback.id
    if target_id == category.id:
        raise _error(422, "MATERIAL_CATEGORY_TARGET_INVALID", "迁移目标不能是当前图库或分类")
    target = await repo.get_category(_owner_uid(user), material_type, target_id)
    if target is None:
        raise _error(422, "MATERIAL_CATEGORY_TARGET_INVALID", "迁移目标图库或分类不存在")
    moved = await repo.category_item_count(_owner_uid(user), material_type, category.id)
    if moved:
        await repo.reassign_category_items(_owner_uid(user), material_type, category.id, target.id)
    category.deleted_at = utc_now_naive()
    await db.commit()
    return {"success": True, "id": category.id, "moved": moved, "target_category_id": target.id}


async def list_image_galleries(
    db: AsyncSession, user: User, industry_slug: str | None = None
) -> dict[str, Any]:
    categories = await ensure_material_categories(
        db,
        owner_uid=_owner_uid(user),
        tenant_id=_tenant_id(user),
        material_type="image",
    )
    raw = await MaterialLibraryRepository(db).category_summaries(_owner_uid(user), material_type="image")
    industry_catalog = await _industry_catalog(db)
    parents = {category.id: category for category in categories if category.parent_id is None}
    galleries = []
    for category in categories:
        effective_industry = (
            parents[category.parent_id].industry_slug
            if category.parent_id and category.parent_id in parents
            else category.industry_slug
        )
        if industry_slug and (
            (industry_slug == "uncategorized" and effective_industry != "uncategorized")
            # Unclassified galleries are shared legacy/general-purpose assets;
            # keep them available when content is scoped to a specific industry.
            or (
                industry_slug != "uncategorized"
                and effective_industry not in {industry_slug, "uncategorized"}
            )
        ):
            continue
        direct_count, latest = raw.get(category.id, (0, None))
        children = [item for item in categories if item.parent_id == category.id]
        count = direct_count
        if category.parent_id is None:
            for child in children:
                child_count, child_latest = raw.get(child.id, (0, None))
                count += child_count
                child_updated_at = child_latest.updated_at or child_latest.created_at if child_latest else None
                latest_updated_at = latest.updated_at or latest.created_at if latest else None
                if child_latest is not None and (latest is None or child_updated_at > latest_updated_at):
                    latest = child_latest
        galleries.append(
            {
                **category.to_dict(),
                "industry_slug": effective_industry,
                "industry_name": industry_catalog.get(effective_industry, "未分类行业"),
                "count": count,
                "direct_count": direct_count,
                "child_count": len(children),
                "cover_item_id": latest.id if latest is not None else None,
                "updated_at": latest.to_dict()["updated_at"] if latest is not None else None,
            }
        )
    await db.commit()
    return {
        "galleries": galleries,
        "industries": [
            {"slug": slug, "name": name} for slug, name in industry_catalog.items()
        ],
    }


async def get_material_file(db: AsyncSession, user: User, item_id: str) -> tuple[bytes, str, str]:
    repo = MaterialLibraryRepository(db)
    item = await repo.get_item_for_user(item_id, _owner_uid(user))
    if item is None:
        raise _error(404, "MATERIAL_NOT_FOUND", "素材不存在")
    asset = await repo.get_asset(item.asset_id, _owner_uid(user))
    if asset is None:
        raise _error(404, "MATERIAL_ASSET_MISSING", "素材文件不存在")
    try:
        data = await get_minio_client().adownload_file(asset.bucket_name, asset.object_name)
    except StorageError as exc:
        raise _error(500, "MATERIAL_STORAGE_FAILED", "素材文件读取失败") from exc
    return data, asset.content_type, asset.original_file_name


async def get_material_thumbnail(db: AsyncSession, user: User, item_id: str) -> tuple[bytes, str]:
    data, _, file_name = await get_material_file(db, user, item_id)
    try:
        thumbnail = await asyncio.to_thread(_make_image_thumbnail, data)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise _error(400, "MATERIAL_IMAGE_INVALID", "素材文件不是有效图片") from exc
    return thumbnail, file_name


async def delete_material_item(db: AsyncSession, user: User, item_id: str) -> dict[str, Any]:
    owner_uid = _owner_uid(user)
    repo = MaterialLibraryRepository(db)
    item = await repo.get_item_for_user(item_id, owner_uid, for_update=True)
    if item is None:
        raise _error(404, "MATERIAL_NOT_FOUND", "素材不存在")
    asset = await repo.get_asset(item.asset_id, owner_uid, for_update=True)
    if asset is None:
        raise _error(404, "MATERIAL_ASSET_MISSING", "素材文件不存在")
    cover_repo = ContentCoverRepository(db)
    referenced = await cover_repo.asset_is_in_active_job(asset.id, owner_uid)
    poster = await repo.get_poster_template_by_asset(asset.id)
    poster_referenced = poster is not None and await cover_repo.poster_template_is_in_active_job(poster.id, owner_uid)
    task_referenced = await repo.item_is_selected_by_task(item.id, owner_uid)
    poster_task_referenced = poster is not None and await cover_repo.poster_template_is_selected_by_task(
        poster.id, owner_uid
    )
    if referenced or poster_referenced or task_referenced or poster_task_referenced:
        raise _error(409, "MATERIAL_IN_USE", "素材正在被内容任务或封面任务使用，不能删除")
    try:
        await get_minio_client().adelete_file(asset.bucket_name, asset.object_name)
    except StorageError as exc:
        raise _error(500, "MATERIAL_STORAGE_FAILED", "素材文件删除失败") from exc
    deleted_at = utc_now_naive()
    item.deleted_at = deleted_at
    asset.deleted_at = deleted_at
    if poster is not None:
        poster.deleted_at = deleted_at
    await db.commit()
    return {"success": True, "id": item_id, "object_deleted": True}
