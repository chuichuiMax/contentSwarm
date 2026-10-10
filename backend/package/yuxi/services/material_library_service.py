from __future__ import annotations

import asyncio
import hashlib
import html
import io
import json
import logging
import os
import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.image_design.save_targets import (
    ENTERPRISE_ROOT_OWNER_UID,
    can_contribute_to_category,
    ensure_scope_root,
    is_storage_root,
    resolve_writable_save_target,
)
from yuxi.image_design.schemas import ImageDesignSaveTarget
from yuxi.services.employee_service import resolve_employee_for_user
from yuxi.services.material_library_categories import (
    DEFAULT_IMAGE_CATEGORY_IDS,
    RETIRED_PRIVATE_IMAGE_CATEGORY_IDS,
    PERSONAL_IMAGE_FOLDERS,
    personal_folder_key,
    list_material_categories,
    resolve_legacy_category,
)
from yuxi.services.material_upload_queue import (
    INGEST_PENDING,
    delete_material_display_cache,
    encode_material_thumbnail,
    enqueue_material_oss_upload,
    ingest_status_of,
    material_thumb_object_name,
    material_upload_redis_key,
    persist_material_thumbnail,
    read_material_bytes,
    stage_material_bytes,
    stage_material_thumb,
)
from yuxi.services.material_watermark import watermark_upload_bytes
from yuxi.services.material_share_images import (
    SHARE_CARD_COVER_SIZE,
    ensure_material_share_images,
    make_share_card_cover as _make_share_card_cover,
    make_share_display_webp as _make_share_display_webp,
    read_share_image_cache,
    write_share_image_cache,
)
from yuxi.storage.minio import StorageError, get_minio_client
from yuxi.storage.postgres.models_business import OperationLog, User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentCoverPosterTemplate,
    ContentEmployee,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
    ContentMaterialShare,
    ContentMaterialShareItem,
)
from yuxi.utils.datetime_utils import SHANGHAI_TZ, utc_now_naive
from yuxi.utils.upload_utils import read_upload_with_limit

logger = logging.getLogger(__name__)

INITIAL_ENTERPRISE_GALLERIES = (
    ("reference", "案例图库"),
    ("rough", "毛坯房图库"),
    ("generated", "生图图库"),
)

MATERIAL_LIBRARY_BUCKET = "image"
MAX_MATERIAL_BYTES = 100 * 1024 * 1024
MAX_MATERIAL_DIMENSION = 8192
MAX_MATERIAL_PIXELS = 40_000_000
MATERIAL_THUMBNAIL_SIZE = (480, 480)
DECORATION_GALLERY_INDUSTRY_SLUG = "decoration"
DECORATION_GALLERY_DESIGN_STYLES = frozenset(
    {
        "复合写意",
        "写意木构",
        "江南印象",
        "东方古雅",
        "轻欧简美",
        "欧美香颂",
        "欧式田园",
        "异域风情",
        "新装饰主义",
        "北欧之光",
        "意境东方",
        "雅致现代",
        "工业再造",
        "优雅缤纷",
        "极简侘寂",
        "仿生未来",
        "复古风潮",
        "艺术室界",
    }
)


def _normalize_area_value(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s*(?:㎡|m(?:²|2))\s*$", "", value.strip(), flags=re.IGNORECASE).strip()


def _display_area_value(value: str | None) -> str:
    normalized = _normalize_area_value(value)
    return f"{normalized}㎡" if normalized else ""


class MaterialItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    location: ImageDesignSaveTarget | None = None
    status: Literal["enabled", "disabled"] | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("名称不能为空")
        return value

    @model_validator(mode="after")
    def reject_ambiguous_location(self):
        if self.category is not None and self.location is not None:
            raise ValueError("category 与 location 不能同时提交")
        return self


class MaterialShareCreate(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=1000)

    @field_validator("item_ids")
    @classmethod
    def validate_item_ids(cls, value: list[str]) -> list[str]:
        normalized = [item_id.strip() for item_id in value]
        if any(not item_id for item_id in normalized):
            raise ValueError("图片标识不能为空")
        if len(set(normalized)) != len(normalized):
            raise ValueError("同一张图片只能选择一次")
        return normalized


class MaterialCategoryCreate(BaseModel):
    visibility: Literal["private", "enterprise"] = "private"
    material_type: Literal["image", "cover_template"]
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=255)
    parent_id: str | None = Field(default=None, max_length=64)
    industry_slug: str | None = Field(default=None, max_length=80)
    design_style: str | None = Field(default=None, max_length=32)
    building_name: str | None = Field(default=None, max_length=80)
    area: str | None = Field(default=None, max_length=32)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("名称不能为空")
        return value

    @field_validator("area")
    @classmethod
    def normalize_area(cls, value: str | None) -> str | None:
        return _normalize_area_value(value)


class MaterialCategoryUpdate(BaseModel):
    @field_validator("visibility")
    @classmethod
    def validate_visibility(cls, value):
        if value is None:
            raise ValueError("共享范围不能为空")
        return value

    visibility: Literal["private", "enterprise"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=255)
    sort_order: int | None = Field(default=None, ge=0, le=100000)
    industry_slug: str | None = Field(default=None, max_length=80)
    design_style: str | None = Field(default=None, max_length=32)
    building_name: str | None = Field(default=None, max_length=80)
    area: str | None = Field(default=None, max_length=32)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("名称不能为空")
        return value

    @field_validator("area")
    @classmethod
    def normalize_area(cls, value: str | None) -> str | None:
        return _normalize_area_value(value)


class MaterialCategoryDelete(BaseModel):
    target_category_id: str | None = Field(default=None, max_length=64)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


def _owner_uid(user: User) -> str:
    return str(user.uid)


def _tenant_id(user: User) -> str | None:
    return str(user.department_id) if user.department_id is not None else None


def _is_admin(user: User) -> bool:
    return user.role in {"admin", "superadmin"}


def is_enterprise_case_root(category: ContentMaterialCategory | None) -> bool:
    return bool(
        category
        and category.material_type == "image"
        and category.visibility == "enterprise"
        and not category.parent_id
        and (category.image_design_role == "reference" or category.name == "案例图库")
    )


def is_enterprise_rough_root(category: ContentMaterialCategory | None) -> bool:
    return bool(
        category
        and category.material_type == "image"
        and category.visibility == "enterprise"
        and not category.parent_id
        and (
            category.image_design_role == "rough"
            or category.name in {"毛坯房图库", "毛胚房图库"}
        )
    )


async def material_access_mode(db: AsyncSession, user: User) -> Literal["full", "rough_upload"]:
    from yuxi.services.role_service import effective_permission_grants

    grants = await effective_permission_grants(db, user)
    if not grants or "material_rough.upload" not in grants:
        return "full"
    return "rough_upload"


def _designer_may_see_category(category: ContentMaterialCategory | None) -> bool:
    if category is None or category.material_type != "image":
        return False
    if category.visibility == "private":
        return True
    return is_enterprise_rough_root(category)


def _reject_outside_rough_upload(category: ContentMaterialCategory | None) -> None:
    if not _designer_may_see_category(category):
        raise _error(403, "MATERIAL_PERMISSION_DENIED", "当前岗位只能上传企业共享毛坯房图库，或使用自己的素材")


def _designer_may_browse_case(category: ContentMaterialCategory | None, parent: ContentMaterialCategory | None) -> bool:
    return is_enterprise_case_root(category) or is_enterprise_case_root(parent)


async def _load_case_parent(repo, category: ContentMaterialCategory | None) -> ContentMaterialCategory | None:
    if category is None or not category.parent_id:
        return None
    return await repo.get_category(category.owner_uid, category.material_type, category.parent_id)


def _can_manage_category(user: User, category: ContentMaterialCategory) -> bool:
    if category.material_type == "image":
        if category.visibility == "enterprise":
            return _is_admin(user)
        if personal_folder_key(category):
            return False
        return category.owner_uid == str(user.uid) and _is_admin(user)
    if category.visibility == "enterprise":
        return _is_admin(user)
    return category.owner_uid == str(user.uid)


def _is_target_rough_category(category: ContentMaterialCategory, owner_uid: str) -> bool:
    return (
        category.material_type == "image"
        and category.visibility == "private"
        and category.owner_uid == owner_uid
        and (
            category.id == "mp-rough-private"
            or category.image_design_role == "rough"
            or category.name in {"毛坯房图库", "毛胚房图库"}
        )
    )


def _can_manage_item(user: User, item: ContentMaterialLibraryItem, category: ContentMaterialCategory) -> bool:
    return item.owner_uid == str(user.uid) or (category.visibility == "enterprise" and _is_admin(user))


def _audit(db: AsyncSession, user: User, operation: str, **details):
    db.add(OperationLog(user_id=user.id, operation=operation, details=json.dumps(details, ensure_ascii=False)))


def _audit_material(
    db: AsyncSession,
    owner_user: User,
    actor_user: User | None,
    operation: str,
    **details,
):
    actor = actor_user or owner_user
    if str(actor.uid) != str(owner_user.uid):
        details["target_owner_uid"] = str(owner_user.uid)
    _audit(db, actor, operation, **details)


def _share_page_path(token: str) -> str:
    return f"/api/material-library/shares/{token}/page"


def _share_image_path(token: str, display_order: int) -> str:
    return f"/api/material-library/shares/{token}/images/{display_order}"


def _share_webp_image_path(token: str, display_order: int) -> str:
    return f"{_share_image_path(token, display_order)}.webp"


def _share_card_cover_path(token: str) -> str:
    return f"/api/material-library/shares/{token}/cover.jpg"


def _share_case_path(token: str) -> str:
    return f"/share/case/{token}"


def _share_public_url(path: str, public_base_url: str | None = None) -> str:
    base_url = (public_base_url or os.getenv("MATERIAL_LIBRARY_SHARE_PUBLIC_BASE_URL", "")).strip().rstrip("/")
    return f"{base_url}{path}" if base_url else path


def _share_browser_path(request_base_url: str, path: str) -> str:
    """页面内图片只用路径，浏览器会沿用当前站点，不会带上代理改写后的内部域名。"""
    prefix = urlsplit(request_base_url).path.rstrip("/")
    return f"{prefix}{path}"


def _share_description(building_name: str | None, area: str | None, design_style: str | None) -> str:
    if not building_name or not area or not design_style:
        return ""
    area_text = _display_area_value(area)
    return f"{building_name}｜{area_text}｜{design_style}"


def _material_image_object_name(owner_uid: str, asset_id: str) -> str:
    return f"material-library/{owner_uid}/images/{asset_id}/image.webp"


def _normalize_design_style(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if text not in DECORATION_GALLERY_DESIGN_STYLES:
        raise _error(422, "MATERIAL_STYLE_INVALID", "请选择有效的设计风格")
    return text


def _is_enterprise_case_gallery(category: ContentMaterialCategory, parent: ContentMaterialCategory | None) -> bool:
    root = parent if category.parent_id else category
    return (
        category.visibility == "enterprise"
        and root is not None
        and root.visibility == "enterprise"
        and root.image_design_role == "reference"
    )


async def _resolve_upload_category(
    db: AsyncSession,
    user: User,
    category_id: str,
    design_style: str | None,
) -> tuple[ContentMaterialCategory, str | None]:
    owner_uid = _owner_uid(user)
    repo = MaterialLibraryRepository(db, include_shared=True)
    resolved = await resolve_material_category(
        db,
        owner_uid=owner_uid,
        tenant_id=_tenant_id(user),
        material_type="image",
        category_id=category_id,
    )
    if personal_folder_key(resolved):
        return resolved, None
    parent = await repo.get_category(owner_uid, "image", resolved.parent_id) if resolved.parent_id else None
    style = None
    if _is_enterprise_case_gallery(resolved, parent):
        style = _normalize_design_style(design_style) or _normalize_design_style(resolved.design_style)
        if not style:
            raise _error(422, "MATERIAL_STYLE_REQUIRED", "案例图库上传请选择设计风格")
    if not can_contribute_to_category(user, resolved):
        raise _error(403, "MATERIAL_UPLOAD_FORBIDDEN", "不能向该图库上传素材")
    return resolved, style


def _normalize_image(data: bytes) -> tuple[bytes, int, int, str]:
    try:
        try:
            from pillow_heif import register_heif_opener

            register_heif_opener()
        except Exception:
            pass
        with Image.open(io.BytesIO(data)) as source:
            # 不按扩展名/声明格式拦截：微信相册常把 HEIC/MPO/实况图标成 .jpg。
            # 只要 Pillow（含 HEIF 插件）能解码，就统一转成 WebP 入库。
            detected = (source.format or "").upper()
            image = ImageOps.exif_transpose(source)
            image.load()
            source_width, source_height = image.size
            if source_width < 2 or source_height < 2:
                raise _error(400, "MATERIAL_DIMENSION_INVALID", "图片尺寸必须在 2–8192 像素且不超过 4000 万像素")
            scale = 1.0
            longest = max(source_width, source_height)
            if longest > MAX_MATERIAL_DIMENSION:
                scale = min(scale, MAX_MATERIAL_DIMENSION / longest)
            pixels = source_width * source_height
            if pixels > MAX_MATERIAL_PIXELS:
                scale = min(scale, (MAX_MATERIAL_PIXELS / pixels) ** 0.5)
            if scale < 1:
                width = max(2, int(source_width * scale))
                height = max(2, int(source_height * scale))
                image = image.resize((width, height), Image.Resampling.LANCZOS)
            else:
                width, height = source_width, source_height
            if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
                image = image.convert("RGBA")
            else:
                image = image.convert("RGB")
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=80, method=4)
            logger.info(
                "material image normalized format=%s size=%sx%s stored=%sx%s bytes_in=%s bytes_out=%s",
                detected or "unknown",
                source_width,
                source_height,
                width,
                height,
                len(data),
                output.tell(),
            )
            return output.getvalue(), width, height, "image/webp"
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise _error(
            400,
            "MATERIAL_IMAGE_INVALID",
            "无法识别该图片（请用系统相册导出为 JPG/PNG 后再传，勿直接传实况图/未解码原片）",
        ) from exc


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
            "category_name": PERSONAL_IMAGE_FOLDERS[personal_folder_key(category)][1]
            if personal_folder_key(category)
            else category.name,
            "industry_slug": category.industry_slug,
            "visibility": category.visibility or "private",
            "file_name": asset.original_file_name,
            "content_type": asset.content_type,
            "file_size": asset.file_size,
            "width": asset.image_width,
            "height": asset.image_height,
            "sha256": asset.sha256,
            "file_url": f"/api/material-library/items/{item.id}/file",
            "design_style": (item.metadata_json or {}).get("design_style") or category.design_style,
            "storage_status": ingest_status_of(asset),
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
    own_categories = [category for category in categories if category.owner_uid == owner_uid]
    definitions = list_material_categories(material_type)
    if material_type == "image":
        definitions = [item for item in definitions if item["code"] == "uncategorized"]
    if not own_categories:
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
                for index, definition in enumerate(definitions)
            ]
        )
        categories = await repo.list_categories(owner_uid, material_type)
    elif not any(category.id == "uncategorized" for category in own_categories):
        definition = next(item for item in list_material_categories(material_type) if item["code"] == "uncategorized")
        await repo.ensure_default_categories(
            [
                {
                    "owner_uid": owner_uid,
                    "id": "uncategorized",
                    "tenant_id": tenant_id,
                    "material_type": material_type,
                    "name": definition["name"],
                    "description": definition["description"],
                    "sort_order": len(own_categories) * 10,
                    "is_system": True,
                }
            ]
        )
        categories = await repo.list_categories(owner_uid, material_type)
    fallback = next(
        category for category in categories if category.owner_uid == owner_uid and category.id == "uncategorized"
    )
    if material_type == "image":
        from yuxi.services.personal_materials import folder_categories

        await folder_categories(db, User(uid=owner_uid, department_id=tenant_id))
        categories = await repo.list_categories(owner_uid, material_type)
    await repo.normalize_orphan_categories(
        owner_uid,
        material_type,
        [category.id for category in categories],
        fallback.id,
    )
    return categories


async def ensure_initial_enterprise_galleries(db: AsyncSession) -> None:
    """Seed only an empty library; historical rows require a reviewed migration."""
    rows = list(
        (
            await db.execute(
                select(ContentMaterialCategory).where(
                    ContentMaterialCategory.material_type == "image",
                    ContentMaterialCategory.visibility == "enterprise",
                    ContentMaterialCategory.parent_id.is_(None),
                )
            )
        ).scalars()
    )
    legacy_rows = [category for category in rows if not is_storage_root(category)]
    for index, (role, name) in enumerate(INITIAL_ENTERPRISE_GALLERIES, 1):
        marked = [category for category in rows if category.image_design_role == role]
        if len(marked) > 1:
            raise _error(409, "MATERIAL_GALLERY_ROLE_AMBIGUOUS", f"{name}存在多个角色记录，请人工核对")
        if marked:
            continue
        if legacy_rows:
            raise _error(409, "MATERIAL_GALLERY_MIGRATION_REQUIRED", "企业初始图库需要先审阅迁移计划")
        category = ContentMaterialCategory(
            owner_uid=ENTERPRISE_ROOT_OWNER_UID,
            material_type="image",
            id=f"enterprise-initial-{role}",
            visibility="enterprise",
            parent_id=None,
            industry_slug="uncategorized",
            image_design_role=role,
            name=name,
            description="企业共享初始图库",
            sort_order=index * 10,
            is_system=False,
        )
        db.add(category)
        rows.append(category)
    await db.flush()


async def _industry_catalog(db: AsyncSession) -> dict[str, str]:
    from yuxi.repositories.content_repository import ContentRepository

    return {item["slug"]: item["name"] for item in await ContentRepository(db).list_templates()}


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
    categories = await MaterialLibraryRepository(db, include_shared=True).list_categories(owner_uid, material_type)
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
    category_owner_uid: str | None = None,
) -> ContentMaterialLibraryItem:
    repo = MaterialLibraryRepository(db, include_shared=True)
    if category_owner_uid is None:
        resolved_category = await resolve_material_category(
            db,
            owner_uid=asset.owner_uid,
            tenant_id=asset.tenant_id,
            material_type=material_type,
            category_id=category,
        )
    else:
        resolved_category = await repo.get_category_exact(
            requester_uid=asset.owner_uid,
            material_type=material_type,
            category_id=category,
            category_owner_uid=category_owner_uid,
        )
        if resolved_category is None:
            raise _error(422, "MATERIAL_CATEGORY_INVALID", "素材图库或分类不存在")
    existing = await repo.get_item_by_asset(asset.id)
    if existing is not None:
        if (
            await repo.get_category(existing.category_owner_uid or asset.owner_uid, material_type, existing.category)
            is None
        ):
            existing.category = resolved_category.id
            existing.category_owner_uid = resolved_category.owner_uid
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
        category_owner_uid=resolved_category.owner_uid,
        tags_json=[],
        status="enabled",
        metadata_json={**(metadata or {}), "ever_shared": resolved_category.visibility == "enterprise"},
    )


async def import_material_images(
    db: AsyncSession,
    user: User,
    files: list[UploadFile],
    *,
    category: str,
    design_style: str | None = None,
    source_channel: Literal["pc", "mp"] = "pc",
    source_folder: Literal["rough", "uploads"] | None = None,
    actor_user: User | None = None,
    manage_target_private: bool = False,
) -> dict[str, Any]:
    if not files or len(files) > 50:
        raise _error(422, "MATERIAL_FILE_COUNT_INVALID", "每次必须上传 1–50 张图片")
    rough_upload_only = await material_access_mode(db, actor_user or user) == "rough_upload"
    owner_uid = _owner_uid(user)
    if source_channel == "mp":
        from yuxi.services.personal_materials import upload_category

        source_folder = source_folder or "uploads"
        if category and category not in {"uncategorized", "rough", "uploads"}:
            resolved_category, style = await _resolve_upload_category(db, user, category, design_style)
            if resolved_category.visibility != "private":
                raise _error(403, "MATERIAL_UPLOAD_FORBIDDEN", "我的素材只能上传到个人图库")
        else:
            resolved_category = await upload_category(db, user, source_folder, "mp")
            style = None
    else:
        resolved_category, style = await _resolve_upload_category(db, user, category, design_style)
        if manage_target_private and not _is_target_rough_category(resolved_category, owner_uid):
            raise _error(403, "MATERIAL_TARGET_SCOPE_FORBIDDEN", "员工素材模式只能管理该员工的个人图库")
        source_folder = None
        if resolved_category.visibility == "private":
            if (
                resolved_category.id == "mp-rough-private"
                or resolved_category.image_design_role == "rough"
                or resolved_category.name in {"毛坯房图库", "毛胚房图库"}
            ):
                source_folder = "rough"
            elif resolved_category.id in {"private-root", "mp-uploads-private"} or resolved_category.name == "我的上传":
                source_folder = "uploads"
    category_id = resolved_category.id
    category_owner_uid = resolved_category.owner_uid
    category_visibility = resolved_category.visibility
    results: list[dict[str, Any]] = []
    staged_ids: list[str] = []
    for index, file in enumerate(files):
        # 与范围调整互斥；每张上传独立提交，逐次重新校验图库权限。
        resolved_category = await MaterialLibraryRepository(db, include_shared=True).get_category_exact(
            requester_uid=owner_uid,
            material_type="image",
            category_id=category_id,
            category_owner_uid=category_owner_uid,
            visibility=category_visibility,
            for_update=True,
        )
        if resolved_category is None:
            raise _error(422, "MATERIAL_CATEGORY_INVALID", "图库不存在或共享范围已变更")
        if manage_target_private and not _is_target_rough_category(resolved_category, owner_uid):
            raise _error(403, "MATERIAL_TARGET_SCOPE_FORBIDDEN", "员工素材模式只能管理该员工的个人图库")
        if rough_upload_only:
            _reject_outside_rough_upload(resolved_category)
        if not can_contribute_to_category(user, resolved_category):
            raise _error(403, "MATERIAL_UPLOAD_FORBIDDEN", "不能向该图库上传素材")
        if not file.filename:
            raise _error(400, "MATERIAL_FILE_NAME_REQUIRED", "无法识别上传文件名")
        try:
            raw = await read_upload_with_limit(
                file,
                max_size_bytes=MAX_MATERIAL_BYTES,
                too_large_message="图片过大，当前仅支持 100 MB 以内的文件",
            )
        except ValueError as exc:
            raise _error(400, "MATERIAL_IMAGE_TOO_LARGE", str(exc)) from exc
        normalized, width, height, content_type = _normalize_image(raw)
        normalized = watermark_upload_bytes(
            normalized,
            category_id=resolved_category.id,
            owner_uid=resolved_category.owner_uid,
            parent_id=resolved_category.parent_id,
        )
        if len(normalized) > MAX_MATERIAL_BYTES:
            raise _error(400, "MATERIAL_IMAGE_TOO_LARGE", "图片规范化后超过 100 MB")
        asset_id = f"cca_{uuid.uuid4().hex}"
        object_name = _material_image_object_name(owner_uid, asset_id)
        await stage_material_bytes(asset_id, normalized)
        await stage_material_thumb(asset_id, encode_material_thumbnail(normalized))
        staged_ids.append(asset_id)
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
                bucket_name=MATERIAL_LIBRARY_BUCKET,
                object_name=object_name,
                metadata_json={
                    "original_content_type": file.content_type or "",
                    "source_channel": source_channel,
                    "source_folder": source_folder,
                    "ingest_status": INGEST_PENDING,
                    "redis_key": material_upload_redis_key(asset_id),
                },
            )
            item = await create_library_item_for_asset(
                db,
                asset=asset,
                material_type="image",
                name=Path(file.filename).stem,
                category=resolved_category.id,
                category_owner_uid=resolved_category.owner_uid,
                metadata={
                    "source_channel": source_channel,
                    "source_folder": source_folder,
                    **({"design_style": style} if style else {}),
                },
            )
            _audit_material(
                db,
                user,
                actor_user,
                "material.upload",
                item_id=item.id,
                category_id=resolved_category.id,
            )
            await db.commit()
        except Exception:
            await db.rollback()
            await delete_material_display_cache(asset_id)
            raise
        try:
            await enqueue_material_oss_upload(asset_id)
        except Exception:
            logger.exception("material upload enqueue failed: asset=%s", asset_id)
            raise _error(500, "MATERIAL_UPLOAD_QUEUE_FAILED", "素材已暂存，入库队列提交失败") from None
        results.append(serialize_item(item, asset, resolved_category))
    return {"items": results, "summary": {"total": len(results), "created": len(results), "queued": len(staged_ids)}}


async def create_material_share(
    db: AsyncSession,
    user: User,
    payload: MaterialShareCreate,
    public_base_url: str | None = None,
    actor_user: User | None = None,
    target_private: bool = False,
    sharer_employee: ContentEmployee | None = None,
) -> dict[str, Any]:
    if await material_access_mode(db, actor_user or user) == "rough_upload":
        raise _error(403, "MATERIAL_PERMISSION_DENIED", "当前岗位只能上传企业共享毛坯房图库")
    owner_uid = _owner_uid(user)
    repo = MaterialLibraryRepository(db, include_shared=True)
    rows = await repo.list_image_items_with_assets_and_categories(owner_uid, payload.item_ids)
    if len(rows) != len(payload.item_ids):
        raise _error(404, "MATERIAL_NOT_FOUND", "所选图片不存在或无权访问")
    if target_private and any(
        item.owner_uid != owner_uid or not _is_target_rough_category(category, owner_uid) for item, _, category in rows
    ):
        raise _error(404, "MATERIAL_NOT_FOUND", "所选图片不存在或无权访问")

    selected = {item.id: (item, asset, category) for item, asset, category in rows}
    ordered_rows = [selected[item_id] for item_id in payload.item_ids]
    category = ordered_rows[0][2]
    if category.parent_id is None:
        raise _error(422, "MATERIAL_SHARE_CHILD_GALLERY_REQUIRED", "只能分享二级图库中的图片")
    if any(item_category.id != category.id for _, _, item_category in ordered_rows):
        raise _error(422, "MATERIAL_SHARE_GALLERY_MISMATCH", "请选择同一个二级图库中的图片")
    if category.industry_slug == DECORATION_GALLERY_INDUSTRY_SLUG:
        if not (category.design_style or "").strip():
            raise _error(422, "MATERIAL_DESIGN_STYLE_REQUIRED", "请选择设计风格")
        if not (category.building_name or "").strip():
            raise _error(422, "MATERIAL_BUILDING_NAME_REQUIRED", "请输入楼盘名称")
        if not (category.area or "").strip():
            raise _error(422, "MATERIAL_AREA_REQUIRED", "请输入面积")

    if sharer_employee is None:
        sharer_employee = await resolve_employee_for_user(db, actor_user or user)
    share = ContentMaterialShare(
        id=f"mls_{uuid.uuid4().hex}",
        token=uuid.uuid4().hex,
        owner_uid=owner_uid,
        category_id=category.id,
        title=category.name,
        building_name=category.building_name,
        area=category.area,
        design_style=category.design_style,
        sharer_name=sharer_employee.name if sharer_employee else None,
        sharer_phone=sharer_employee.login_account if sharer_employee else None,
    )
    snapshots: list[ContentMaterialShareItem] = []
    uploaded_objects: list[tuple[str, str]] = []
    storage = get_minio_client()
    semaphore = asyncio.Semaphore(4)

    async def copy_snapshot(display_order: int, asset: ContentCoverAsset):
        async with semaphore:
            pending_data = await read_material_bytes(asset) if ingest_status_of(asset) == INGEST_PENDING else None
            display_source, cover_source = await ensure_material_share_images(asset, pending_data)
            suffix = "webp" if (asset.content_type or "").endswith("webp") else "png"
            object_name = f"material-library-shares/{owner_uid}/{share.id}/{display_order}.{suffix}"
            if pending_data is not None:
                uploaded = await storage.aupload_file(
                    MATERIAL_LIBRARY_BUCKET, object_name, pending_data, content_type=asset.content_type
                )
            else:
                uploaded = await storage.acopy_file(
                    MATERIAL_LIBRARY_BUCKET, asset.object_name, object_name,
                    source_bucket_name=asset.bucket_name, content_type=asset.content_type,
                )
            uploaded_objects.append((uploaded.bucket_name, uploaded.object_name))
            display_uploaded = await storage.acopy_file(
                MATERIAL_LIBRARY_BUCKET, display_source, f"{object_name}.display.webp",
                source_bucket_name=asset.bucket_name, content_type="image/webp",
            )
            uploaded_objects.append((display_uploaded.bucket_name, display_uploaded.object_name))
            card_data = None
            if display_order == 1:
                cover_uploaded = await storage.acopy_file(
                    MATERIAL_LIBRARY_BUCKET, cover_source, f"{object_name}.card.jpg",
                    source_bucket_name=asset.bucket_name, content_type="image/jpeg",
                )
                uploaded_objects.append((cover_uploaded.bucket_name, cover_uploaded.object_name))
                card_data = await storage.adownload_file(cover_uploaded.bucket_name, cover_uploaded.object_name)
            return (
                ContentMaterialShareItem(
                    share_id=share.id,
                    display_order=display_order,
                    original_file_name=asset.original_file_name,
                    content_type=asset.content_type,
                    file_size=asset.file_size,
                    image_width=asset.image_width,
                    image_height=asset.image_height,
                    bucket_name=uploaded.bucket_name,
                    object_name=uploaded.object_name,
                ),
                card_data,
            )

    try:
        # Wait for every copy before rollback/cleanup, including siblings of a failed copy.
        results = await asyncio.gather(
            *(copy_snapshot(order, row[1]) for order, row in enumerate(ordered_rows, start=1)),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        snapshots = [result[0] for result in results]
        await repo.create_share(share, snapshots)
        _audit_material(db, user, actor_user, "material.share.create", share_id=share.id, category_id=category.id)
        await db.commit()
    except StorageError as exc:
        await db.rollback()
        for bucket_name, object_name in uploaded_objects:
            await storage.adelete_file(bucket_name, object_name)
        raise _error(500, "MATERIAL_SHARE_STORAGE_FAILED", "分享图片快照保存失败") from exc
    except Exception:
        await db.rollback()
        for bucket_name, object_name in uploaded_objects:
            await storage.adelete_file(bucket_name, object_name)
        raise

    await write_share_image_cache(share.token, "cover", results[0][1])
    return {
        "share": {
            "id": share.token,
            "url": _share_public_url(_share_case_path(share.token), public_base_url),
            "cover_url": _share_image_path(share.token, 1) if snapshots else None,
            "card_cover_url": (
                _share_public_url(_share_card_cover_path(share.token), public_base_url) if snapshots else None
            ),
            "token": share.token,
            "title": share.title,
            "description": _share_description(share.building_name, share.area, share.design_style),
            "image_count": len(snapshots),
            "page_path": _share_page_path(share.token),
            "image_path": _share_image_path(share.token, 1) if snapshots else None,
            "page_url": _share_public_url(_share_case_path(share.token), public_base_url),
            "image_url": _share_public_url(_share_image_path(share.token, 1), public_base_url) if snapshots else None,
        }
    }


async def get_public_material_share(
    db: AsyncSession, token: str
) -> tuple[ContentMaterialShare, list[ContentMaterialShareItem]]:
    share, items = await MaterialLibraryRepository(db).get_share_with_items(token)
    if share is None:
        raise _error(404, "MATERIAL_SHARE_NOT_FOUND", "分享不存在")
    return share, items


def serialize_public_material_share(
    share: ContentMaterialShare, items: list[ContentMaterialShareItem]
) -> dict[str, Any]:
    ordered_items = sorted(items, key=lambda item: item.display_order)
    return {
        "share": {
            "id": share.token,
            "gallery_name": share.title,
            "building_name": share.building_name,
            "area": _normalize_area_value(share.area),
            "design_style": share.design_style,
            "sharer_name": share.sharer_name,
            "sharer_phone": share.sharer_phone,
            "cover_url": _share_image_path(share.token, 1) if ordered_items else None,
            "cover_webp_url": _share_webp_image_path(share.token, 1) if ordered_items else None,
            "card_cover_url": _share_card_cover_path(share.token) if ordered_items else None,
            "images": [
                {
                    "order": item.display_order,
                    "file_name": item.original_file_name,
                    "url": _share_image_path(share.token, item.display_order),
                    "webp_url": _share_webp_image_path(share.token, item.display_order),
                }
                for item in ordered_items
            ],
        }
    }


def render_public_material_share_page(
    share: ContentMaterialShare,
    items: list[ContentMaterialShareItem],
    request_base_url: str,
) -> str:
    base_url = request_base_url.rstrip("/") or (
        os.getenv("MATERIAL_LIBRARY_SHARE_PUBLIC_BASE_URL", "").strip().rstrip("/")
    )
    ordered_items = sorted(items, key=lambda item: item.display_order)
    title = html.escape(share.title)
    first_item = ordered_items[0] if ordered_items else None
    first_image = (
        _share_browser_path(base_url, _share_webp_image_path(share.token, first_item.display_order))
        if first_item
        else ""
    )
    card_cover = f"{base_url}{_share_card_cover_path(share.token)}" if first_item else ""
    share_url = f"{base_url}{_share_case_path(share.token)}"
    first_image_type = "image/jpeg" if first_item else ""
    first_image_width = SHARE_CARD_COVER_SIZE[0] if first_item else ""
    first_image_height = SHARE_CARD_COVER_SIZE[1] if first_item else ""
    building_name = html.escape(share.building_name or "")
    area = html.escape(_display_area_value(share.area))
    design_style = html.escape(share.design_style or "")
    sharer_name = html.escape(share.sharer_name or "")
    sharer_phone = html.escape(share.sharer_phone or "", quote=True)
    contact = (
        f'<a class="share-phone" href="tel:{sharer_phone}">电话：{sharer_phone} {sharer_name}</a>'
        if sharer_phone
        else ""
    )
    style_class = "project-style" if contact else "project-style-wide"
    description = html.escape(_share_description(share.building_name, share.area, share.design_style), quote=True)
    project_fields = []
    if building_name and area and design_style:
        project_fields.extend(
            [
                f"<span>楼盘：{building_name}</span>",
                f"<span>面积：{area}</span>",
                f'<span class="{style_class}">风格：{design_style}</span>',
            ]
        )
    if contact:
        project_fields.append(contact)
    details = f'<section class="project-info-card">{"".join(project_fields)}</section>' if project_fields else ""
    hero = (
        f'<section class="share-hero"><img src="{html.escape(first_image, quote=True)}" alt="{title} 首图"></section>'
        if items
        else ""
    )
    image_tags = []
    for item in ordered_items:
        image_url = _share_browser_path(base_url, _share_webp_image_path(share.token, item.display_order))
        image_tags.append(
            f'<img src="{html.escape(image_url, quote=True)}" alt="{title} 第 {item.display_order} 张" loading="lazy">'
        )
    images = "".join(image_tags)
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{title}</title>",
            f'<meta name="description" content="{description}">',
            '<meta property="og:type" content="website">',
            f'<meta property="og:url" content="{html.escape(share_url, quote=True)}">',
            '<meta property="og:site_name" content="Yuxi">',
            f'<meta property="og:title" content="{title}">',
            f'<meta property="og:description" content="{description}">',
            f'<meta property="og:image" content="{html.escape(card_cover, quote=True)}">',
            f'<meta property="og:image:secure_url" content="{html.escape(card_cover, quote=True)}">',
            f'<meta property="og:image:type" content="{first_image_type}">',
            f'<meta property="og:image:width" content="{first_image_width}">',
            f'<meta property="og:image:height" content="{first_image_height}">',
            f'<meta name="twitter:title" content="{title}">',
            f'<meta name="twitter:description" content="{description}">',
            f'<meta name="twitter:image" content="{html.escape(card_cover, quote=True)}">',
            '<meta name="twitter:card" content="summary_large_image">',
            "<style>",
            "body{margin:0;background:#fff;color:#151616;font:16px/1.6 "
            '-apple-system,BlinkMacSystemFont,"Noto Sans SC","Segoe UI",sans-serif}',
            (
                "header{display:flex;align-items:center;justify-content:center;"
                "min-height:74px;padding:0 24px;background:#fff}"
            ),
            "h1{margin:0;font-size:21px;line-height:1.4;text-align:center}",
            "main{max-width:720px;margin:auto;padding:0 0 36px}",
            ".share-hero{height:min(53vw,382px);overflow:hidden;background:#eef0f0}",
            ".share-hero img{display:block;width:100%;height:100%;margin:0;object-fit:cover}",
            (
                ".project-info-card{position:relative;z-index:1;display:grid;"
                "grid-template-columns:repeat(2,minmax(0,1fr));gap:16px 24px;margin:-76px 28px 34px;"
                "padding:38px 30px 28px;border-radius:16px;background:#fff;"
                "box-shadow:0 5px 12px rgb(0 0 0 / 22%);font-size:18px;line-height:1.55}"
            ),
            ".project-style-wide{grid-column:1 / -1}",
            ".share-phone{color:#000;text-decoration:none;overflow-wrap:anywhere}",
            ".share-phone:only-child{grid-column:1 / -1}",
            ".case-section{padding:0 10px}",
            "h2{display:flex;align-items:center;gap:16px;margin:0 18px 20px;font-size:22px;line-height:1.4}",
            "h2::before{width:16px;height:35px;background:#ff1717;content:''}",
            ".case-images img{display:block;width:100%;margin:0 0 24px;background:#fff}",
            (
                "@media (max-width:480px){header{min-height:62px;padding:0 16px}"
                "h1{font-size:18px}.project-info-card{margin:-54px 18px 28px;"
                "padding:28px 24px 22px;font-size:17px}.case-section{padding:0 10px}"
                "h2{margin:0 18px 18px;font-size:20px}}"
            ),
            "</style></head><body>",
            (
                f"<header><h1>{title}</h1></header><main>{hero}{details}"
                f'<section class="case-section"><h2>实景案例</h2>'
                f'<div class="case-images">{images}</div></section></main></body></html>'
            ),
        ]
    )


async def get_public_material_share_image(db: AsyncSession, token: str, display_order: int) -> tuple[bytes, str, str]:
    _, items = await get_public_material_share(db, token)
    snapshot = next((item for item in items if item.display_order == display_order), None)
    if snapshot is None:
        raise _error(404, "MATERIAL_SHARE_IMAGE_NOT_FOUND", "分享图片不存在")
    try:
        data = await get_minio_client().adownload_file(snapshot.bucket_name, snapshot.object_name)
    except StorageError as exc:
        raise _error(500, "MATERIAL_SHARE_STORAGE_FAILED", "分享图片读取失败") from exc
    return data, snapshot.content_type, snapshot.original_file_name


async def get_public_material_share_display_webp(db: AsyncSession, token: str, display_order: int) -> bytes:
    _, items = await get_public_material_share(db, token)
    snapshot = next((item for item in items if item.display_order == display_order), None)
    if snapshot is None:
        raise _error(404, "MATERIAL_SHARE_IMAGE_NOT_FOUND", "分享图片不存在")

    variant = f"display:{display_order}"
    cached = await read_share_image_cache(token, variant)
    if cached is not None:
        return cached
    storage = get_minio_client()
    display_object_name = f"{snapshot.object_name}.display.webp"
    try:
        if await storage.astat_file(snapshot.bucket_name, display_object_name) is not None:
            display_data = await storage.adownload_file(snapshot.bucket_name, display_object_name)
        else:
            source_data = await storage.adownload_file(snapshot.bucket_name, snapshot.object_name)
            display_data = await asyncio.to_thread(_make_share_display_webp, source_data)
            await storage.aupload_file(
                bucket_name=snapshot.bucket_name,
                object_name=display_object_name,
                data=display_data,
                content_type="image/webp",
            )
        await write_share_image_cache(token, variant, display_data)
        return display_data
    except StorageError as exc:
        raise _error(500, "MATERIAL_SHARE_STORAGE_FAILED", "分享图片读取失败") from exc


async def get_public_material_share_card_cover(db: AsyncSession, token: str) -> bytes:
    _, items = await get_public_material_share(db, token)
    snapshot = next((item for item in items if item.display_order == 1), None)
    if snapshot is None:
        raise _error(404, "MATERIAL_SHARE_IMAGE_NOT_FOUND", "分享图片不存在")
    cached = await read_share_image_cache(token, "cover")
    if cached is not None:
        return cached
    storage = get_minio_client()
    object_name = f"{snapshot.object_name}.card.jpg"
    try:
        if await storage.astat_file(snapshot.bucket_name, object_name) is not None:
            data = await storage.adownload_file(snapshot.bucket_name, object_name)
        else:
            original = await storage.adownload_file(snapshot.bucket_name, snapshot.object_name)
            data = await asyncio.to_thread(_make_share_card_cover, original)
            await storage.aupload_file(snapshot.bucket_name, object_name, data, content_type="image/jpeg")
        await write_share_image_cache(token, "cover", data)
        return data
    except StorageError as exc:
        raise _error(500, "MATERIAL_SHARE_STORAGE_FAILED", "分享图片读取失败") from exc


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
    scope: Literal["private", "enterprise"] | None = None,
    include_descendants: bool = False,
    root_only: bool = False,
    exclude_task_id: str | None = None,
    include_case_for_designer: bool = False,
    personal_folder: Literal["rough"] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    rough_upload_only = await material_access_mode(db, user) == "rough_upload"
    if date_from and date_to and date_from > date_to:
        raise _error(422, "MATERIAL_DATE_RANGE_INVALID", "开始日期不能晚于结束日期")
    uploaded_from = (
        datetime.combine(date_from, time.min, SHANGHAI_TZ).astimezone(UTC).replace(tzinfo=None) if date_from else None
    )
    uploaded_before = (
        datetime.combine(date_to + timedelta(days=1), time.min, SHANGHAI_TZ).astimezone(UTC).replace(tzinfo=None)
        if date_to
        else None
    )
    if material_type not in {"image", "cover_template"}:
        raise _error(422, "MATERIAL_TYPE_INVALID", "素材类型不存在")
    if sort not in {"newest", "oldest", "name"}:
        raise _error(422, "MATERIAL_SORT_INVALID", "排序方式不存在")
    if root_only and (material_type != "image" or scope is None):
        raise _error(422, "MATERIAL_ROOT_SCOPE_INVALID", "根目录素材仅支持按图片共享范围查询")
    if personal_folder is not None and material_type != "image":
        raise _error(422, "MATERIAL_PERSONAL_FOLDER_INVALID", "个人固定图库仅支持图片素材")
    if personal_folder == "rough":
        from yuxi.services.personal_materials import folder_categories

        await folder_categories(db, user)
        category = None
        scope = "private"
        status = status or "enabled"
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
    if rough_upload_only:
        case_parent = None
        if include_case_for_designer:
            case_parent = await _load_case_parent(MaterialLibraryRepository(db, include_shared=True), resolved_category)
        if not _designer_may_browse_case(resolved_category, case_parent):
            _reject_outside_rough_upload(resolved_category)
    if (
        resolved_category is not None
        and personal_folder_key(resolved_category) in {"generated", "works"}
        and scope in {None, "private"}
        and not root_only
    ):
        from yuxi.services.personal_materials import list_folder

        result = await list_folder(
            db,
            user,
            personal_folder_key(resolved_category),
            page=page,
            page_size=page_size,
            date_from=date_from,
            date_to=date_to,
            query_text=query,
            sort=sort,
            status=status,
        )
        selected_ids = await MaterialLibraryRepository(db).list_selected_image_item_ids(
            _owner_uid(user), exclude_task_id=exclude_task_id
        )
        uploader_names = await MaterialLibraryRepository(db).uploader_names(
            [item["uploaded_by"] for item in result["items"] if item.get("uploaded_by")]
        )
        for item in result["items"]:
            item["in_use"] = item["id"] in selected_ids
            item["uploaded_by_name"] = uploader_names.get(item.get("uploaded_by")) or item.get("uploaded_by")
            if item.get("work_asset_id"):
                url = f"/api/content/covers/assets/{item['work_asset_id']}/file"
                item["file_url"] = item["thumbnail_file_url"] = url
            else:
                item["file_url"] = f"/api/material-library/items/{item['id']}/file"
                item["thumbnail_file_url"] = f"/api/material-library/items/{item['id']}/thumbnail"
        await db.commit()
        return result
    root = await ensure_scope_root(db, user, scope) if root_only else None
    repo = MaterialLibraryRepository(db, include_shared=True)
    category_ids = None
    if root is not None:
        category_ids = [root.id]
    if resolved_category is not None and include_descendants:
        children = await repo.list_child_categories(
            resolved_category.owner_uid,
            material_type,
            resolved_category.id,
        )
        category_ids = [resolved_category.id, *(child.id for child in children)]
    rows, total = await repo.list_items(
        _owner_uid(user),
        material_type=material_type,
        category=resolved_category.id if resolved_category and category_ids is None else None,
        category_ids=category_ids,
        category_owner_uid=root.owner_uid if root else None,
        status=status,
        query_text=query,
        page=page,
        page_size=page_size,
        sort=sort,
        scope=scope,
        private_rough_only=personal_folder == "rough",
        uploaded_from=uploaded_from,
        uploaded_before=uploaded_before,
    )
    uploader_names = await MaterialLibraryRepository(db).uploader_names([item.owner_uid for item, _, _ in rows])
    posters_by_asset: dict[str, ContentCoverPosterTemplate] = {}
    used_image_ids: set[str] = set()
    if material_type == "cover_template":
        posters = await repo.list_poster_templates_by_asset_ids(
            _owner_uid(user),
            [asset.id for _, asset, _ in rows],
        )
        posters_by_asset = {poster.asset_id: poster for poster in posters}
    elif material_type == "image":
        used_image_ids = await repo.list_selected_image_item_ids(_owner_uid(user), exclude_task_id=exclude_task_id)
    await db.commit()
    items = []
    for item, asset, item_category in rows:
        payload = serialize_item(item, asset, item_category, posters_by_asset.get(asset.id))
        payload["can_manage"] = _can_manage_item(user, item, item_category)
        payload["uploaded_by_name"] = uploader_names.get(item.owner_uid) or item.owner_uid
        if material_type == "image":
            payload["in_use"] = item.id in used_image_ids
        items.append(payload)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def update_material_item(
    db: AsyncSession,
    user: User,
    item_id: str,
    payload: MaterialItemUpdate,
    *,
    actor_user: User | None = None,
    target_private: bool = False,
) -> dict[str, Any]:
    repo = MaterialLibraryRepository(db, include_shared=True)
    item = (
        await repo.get_private_rough_item_for_owner(item_id, _owner_uid(user), for_update=True)
        if target_private
        else await repo.get_item_for_user(item_id, _owner_uid(user), for_update=True)
    )
    if item is None:
        raise _error(404, "MATERIAL_NOT_FOUND", "素材不存在")
    previous_category = await repo.get_category(
        item.category_owner_uid or item.owner_uid, item.material_type, item.category
    )
    if await material_access_mode(db, actor_user or user) == "rough_upload" and (
        previous_category is None or previous_category.visibility != "private"
    ):
        raise _error(403, "MATERIAL_PERMISSION_DENIED", "企业共享毛坯房图库仅可上传")
    if previous_category is None or not _can_manage_item(user, item, previous_category):
        raise _error(403, "MATERIAL_MANAGE_FORBIDDEN", "只能管理自己上传的素材，管理员可管理企业共享素材")
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        item.display_name = changes["name"].strip()
    if payload.location is not None:
        if item.material_type != "image":
            raise _error(422, "MATERIAL_LOCATION_INVALID", "仅图片素材支持保存位置")
        target = await resolve_writable_save_target(db, user, payload.location)
        item_category = await repo.get_category_exact(
            requester_uid=_owner_uid(user),
            material_type="image",
            category_id=target.category_id,
            category_owner_uid=target.category_owner_uid,
            visibility=target.scope,
        )
        if item_category is None:
            raise _error(422, "MATERIAL_CATEGORY_INVALID", "图库不存在或共享范围已变更")
        if target_private and not _is_target_rough_category(item_category, _owner_uid(user)):
            raise _error(403, "MATERIAL_TARGET_SCOPE_FORBIDDEN", "员工素材模式只能管理该员工的个人图库")
        if not can_contribute_to_category(user, item_category):
            raise _error(403, "MATERIAL_MOVE_FORBIDDEN", "不能向该图库移动素材")
        if item_category.visibility != "enterprise" and item_category.owner_uid != item.owner_uid:
            raise _error(403, "MATERIAL_MOVE_FORBIDDEN", "他人上传的素材只能移动到企业共享图库")
        item.category = item_category.id
        item.category_owner_uid = item_category.owner_uid
        if item_category.visibility == "enterprise":
            item.metadata_json = {**(item.metadata_json or {}), "ever_shared": True}
    elif "category" in changes:
        item_category = await resolve_material_category(
            db,
            owner_uid=_owner_uid(user),
            tenant_id=_tenant_id(user),
            material_type=item.material_type,
            category_id=changes["category"],
        )
        item_category = await repo.get_category(_owner_uid(user), item.material_type, item_category.id, for_update=True)
        if item_category is None:
            raise _error(422, "MATERIAL_CATEGORY_INVALID", "图库不存在或共享范围已变更")
        if target_private and not _is_target_rough_category(item_category, _owner_uid(user)):
            raise _error(403, "MATERIAL_TARGET_SCOPE_FORBIDDEN", "员工素材模式只能管理该员工的个人图库")
        if not can_contribute_to_category(user, item_category):
            raise _error(403, "MATERIAL_MOVE_FORBIDDEN", "不能向该图库移动素材")
        if item_category.visibility != "enterprise" and item_category.owner_uid != item.owner_uid:
            raise _error(403, "MATERIAL_MOVE_FORBIDDEN", "他人上传的素材只能移动到企业共享图库")
        item.category = item_category.id
        item.category_owner_uid = item_category.owner_uid
        if item_category.visibility == "enterprise":
            item.metadata_json = {**(item.metadata_json or {}), "ever_shared": True}
    else:
        item_category = await resolve_material_category(
            db,
            owner_uid=item.category_owner_uid or item.owner_uid,
            tenant_id=item.tenant_id,
            material_type=item.material_type,
            category_id=item.category,
        )
    if ("location" in changes or "category" in changes) and (item.metadata_json or {}).get("source") == "image_design":
        # Explicit moves must not be undone by the legacy private-root migration.
        item.metadata_json = {**item.metadata_json, "save_target_version": 2}
    if "status" in changes:
        item.status = changes["status"]
    item.updated_at = utc_now_naive()
    asset = await repo.get_asset(item.asset_id, item.owner_uid)
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
    _audit_material(db, user, actor_user, "material.update", item_id=item.id, changes=changes)
    await db.commit()
    return {"item": {**serialize_item(item, asset, item_category, poster), "can_manage": True}}


async def get_material_categories(
    db: AsyncSession,
    user: User,
    material_type: str,
    *,
    include_private_defaults: bool = True,
    personal_folder: Literal["rough"] | None = None,
) -> dict[str, Any]:
    if material_type not in {"image", "cover_template"}:
        raise _error(422, "MATERIAL_TYPE_INVALID", "素材类型不存在")
    if material_type == "image":
        await ensure_initial_enterprise_galleries(db)
    repo = MaterialLibraryRepository(db, include_shared=True)
    personal_rough_category_id = None
    if personal_folder == "rough":
        from yuxi.services.personal_materials import folder_categories

        rough_categories = (await folder_categories(db, user))["rough"]
        personal_rough_category_id = next(
            category.id
            for category in rough_categories
            if category.visibility == "private" and category.owner_uid == _owner_uid(user)
        )
    categories = await ensure_material_categories(
        db,
        owner_uid=_owner_uid(user),
        tenant_id=_tenant_id(user),
        material_type=material_type,
    )
    rough_upload_only = await material_access_mode(db, user) == "rough_upload"
    categories = [
        category
        for category in await repo.list_categories(_owner_uid(user), material_type)
        if not rough_upload_only or _designer_may_see_category(category)
        if personal_folder != "rough" or _is_target_rough_category(category, _owner_uid(user))
        if not is_storage_root(category)
        and (
            material_type != "image"
            or category.visibility != "private"
            or category.id not in RETIRED_PRIVATE_IMAGE_CATEGORY_IDS
            or personal_folder_key(category)
        )
        and (
            include_private_defaults
            or material_type != "image"
            or category.visibility != "private"
            or category.owner_uid != _owner_uid(user)
            or category.id not in DEFAULT_IMAGE_CATEGORY_IDS
        )
    ]
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
        key = personal_folder_key(category)
        if key:
            from yuxi.services.personal_materials import list_folder

            count = (await list_folder(db, user, key, page=1, page_size=1))["total"]
        else:
            count = await repo.category_item_count(_owner_uid(user), material_type, category.id)
        result.append(
            {
                **category.to_dict(),
                "personal_folder": personal_folder_key(category),
                "name": PERSONAL_IMAGE_FOLDERS[personal_folder_key(category)][1]
                if personal_folder_key(category)
                else category.name,
                "can_upload": can_contribute_to_category(user, category)
                and personal_folder_key(category) not in {"generated", "works"}
                and (not rough_upload_only or _designer_may_see_category(category)),
                "can_manage": _can_manage_category(user, category),
                "is_global_personal": category.is_global_personal,
                "industry_slug": effective_industry,
                "industry_name": industry_catalog.get(effective_industry, "未分类行业"),
                "count": count,
                "child_count": len(children),
            }
        )
    await db.commit()
    return {
        "material_type": material_type,
        "categories": result,
        "can_create_shared": _is_admin(user),
        "personal_rough_category_id": personal_rough_category_id,
    }


async def create_material_category(
    db: AsyncSession,
    user: User,
    payload: MaterialCategoryCreate,
    *,
    actor_user: User | None = None,
    target_private: bool = False,
) -> dict[str, Any]:
    if await material_access_mode(db, actor_user or user) == "rough_upload":
        raise _error(403, "MATERIAL_PERMISSION_DENIED", "当前岗位只能上传企业共享毛坯房图库")
    if target_private:
        raise _error(403, "MATERIAL_TARGET_SCOPE_FORBIDDEN", "员工素材模式只能管理该员工的毛坯房图库")
    if payload.material_type == "image" and not _is_admin(user):
        raise _error(403, "MATERIAL_CATEGORY_FORBIDDEN", "只有管理员可新建图片图库")
    if payload.material_type != "image" and payload.parent_id:
        raise _error(422, "MATERIAL_CATEGORY_DEPTH_INVALID", "封面模板分类不支持层级")
    repo = MaterialLibraryRepository(db, include_shared=True)
    categories = await ensure_material_categories(
        db,
        owner_uid=_owner_uid(user),
        tenant_id=_tenant_id(user),
        material_type=payload.material_type,
    )
    visibility = payload.visibility
    parent = None
    industry_slug = None
    design_style = None
    building_name = None
    area = None
    if payload.material_type == "image" and payload.parent_id:
        parent = await repo.get_category(_owner_uid(user), "image", payload.parent_id)
        if parent is None or parent.parent_id:
            raise _error(422, "MATERIAL_CATEGORY_PARENT_INVALID", "只能在一级图库下新建二级图库")
        if not _can_manage_category(user, parent):
            raise _error(403, "MATERIAL_CATEGORY_FORBIDDEN", "只有管理员可新建图片图库")
        visibility = parent.visibility
        industry_slug = parent.industry_slug
        if industry_slug == DECORATION_GALLERY_INDUSTRY_SLUG or is_enterprise_case_root(parent):
            design_style = (payload.design_style or "").strip()
            building_name = (payload.building_name or "").strip()
            area = (payload.area or "").strip()
            if design_style not in DECORATION_GALLERY_DESIGN_STYLES:
                raise _error(422, "MATERIAL_DESIGN_STYLE_REQUIRED", "请选择设计风格")
            # 案例图库文件夹按上传名称保存，楼盘和面积能识别时才写入。
            if not is_enterprise_case_root(parent):
                if not building_name:
                    raise _error(422, "MATERIAL_BUILDING_NAME_REQUIRED", "请输入楼盘名称")
                if not area:
                    raise _error(422, "MATERIAL_AREA_REQUIRED", "请输入面积")
        elif payload.design_style or payload.building_name or payload.area:
            raise _error(
                422,
                "MATERIAL_GALLERY_DETAILS_PARENT_INVALID",
                "设计风格、楼盘名称和面积仅适用于装修与家居二级图库",
            )
    elif payload.material_type == "image":
        if payload.design_style or payload.building_name or payload.area:
            raise _error(
                422,
                "MATERIAL_GALLERY_DETAILS_PARENT_INVALID",
                "设计风格、楼盘名称和面积仅适用于装修与家居二级图库",
            )
        industry_slug = await _validate_industry_slug(db, payload.industry_slug) or "uncategorized"
    if visibility == "enterprise" and (payload.material_type != "image" or not _is_admin(user)):
        raise _error(403, "MATERIAL_CATEGORY_FORBIDDEN", "只有管理员可创建企业共享图片图库")
    try:
        category = await repo.create_category(
            owner_uid=parent.owner_uid if parent else _owner_uid(user),
            visibility=visibility,
            is_global_personal=payload.material_type == "image"
            and visibility == "private"
            and user.role == "superadmin"
            and not target_private,
            id=f"mlc_{uuid.uuid4().hex}",
            tenant_id=_tenant_id(user),
            material_type=payload.material_type,
            parent_id=parent.id if parent else None,
            industry_slug=industry_slug,
            design_style=design_style or None,
            building_name=building_name or None,
            area=area or None,
            name=payload.name.strip(),
            description=payload.description.strip(),
            sort_order=(max(item.sort_order for item in categories) + 10),
            is_system=False,
        )
        _audit_material(
            db,
            user,
            actor_user,
            "material.gallery.create",
            category_id=category.id,
            visibility=visibility,
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _error(409, "MATERIAL_CATEGORY_NAME_DUPLICATE", "图库或分类名称已存在") from exc
    return {"category": {**category.to_dict(), "count": 0, "can_manage": True}}


async def update_material_category(
    db: AsyncSession,
    user: User,
    material_type: str,
    category_id: str,
    payload: MaterialCategoryUpdate,
    *,
    actor_user: User | None = None,
    target_private: bool = False,
) -> dict[str, Any]:
    if await material_access_mode(db, actor_user or user) == "rough_upload":
        raise _error(403, "MATERIAL_PERMISSION_DENIED", "当前岗位只能上传企业共享毛坯房图库")
    if material_type not in {"image", "cover_template"}:
        raise _error(422, "MATERIAL_TYPE_INVALID", "素材类型不存在")
    if material_type == "image":
        await ensure_initial_enterprise_galleries(db)
    await ensure_material_categories(
        db,
        owner_uid=_owner_uid(user),
        tenant_id=_tenant_id(user),
        material_type=material_type,
    )
    repo = MaterialLibraryRepository(db, include_shared=True)
    category = await repo.get_category(_owner_uid(user), material_type, category_id, for_update=True)
    if category is None:
        raise _error(404, "MATERIAL_CATEGORY_NOT_FOUND", "图库或分类不存在")
    if target_private and not _is_target_rough_category(category, _owner_uid(user)):
        raise _error(404, "MATERIAL_CATEGORY_NOT_FOUND", "图库或分类不存在")
    if not (target_private or _can_manage_category(user, category)):
        message = "只有管理员可管理图片图库" if material_type == "image" else "只能管理自己的分类"
        raise _error(403, "MATERIAL_CATEGORY_FORBIDDEN", message)
    if personal_folder_key(category) or (
        category.is_system and category.image_design_role not in {"reference", "rough", "generated"}
    ):
        raise _error(409, "MATERIAL_CATEGORY_SYSTEM_REQUIRED", "系统图库不能修改")
    changes = payload.model_dump(exclude_unset=True)
    if category.image_design_role in {"reference", "rough", "generated"} and (
        "visibility" in changes and changes["visibility"] != "enterprise"
    ):
        raise _error(422, "MATERIAL_GALLERY_ROLE_SCOPE", "初始企业图库不能改变共享范围")
    if "visibility" in changes and changes["visibility"] != category.visibility:
        if not _is_admin(user) or material_type != "image" or category.parent_id or category.is_system:
            raise _error(403, "MATERIAL_SHARING_FORBIDDEN", "仅管理员可设置普通一级图片图库的共享范围")
        children = await repo.list_child_categories(category.owner_uid, material_type, category.id)
        family = [category, *children]
        for gallery in family:
            await repo.get_category(category.owner_uid, material_type, gallery.id, for_update=True)
            gallery_items = await repo.category_items(gallery)
            if changes["visibility"] == "private" and any(
                item.owner_uid != gallery.owner_uid and item.deleted_at is None for item in gallery_items
            ):
                raise _error(
                    409, "MATERIAL_SHARED_CONTRIBUTIONS", "图库含其他成员上传的素材，请先将它们移到其他共享图库"
                )
            if changes["visibility"] == "enterprise":
                if gallery.visibility == "private" and any(
                    item.owner_uid != gallery.owner_uid and item.deleted_at is None for item in gallery_items
                ):
                    raise _error(409, "MATERIAL_PRIVATE_CONTRIBUTIONS", "图库含其他用户的个人图片，不能转为企业共享")
                # 默认图库 ID 在不同账号下重复；转共享时给图库分配独立 ID，素材 ID 和文件保持不变。
                old_id = gallery.id
                if not old_id.startswith("mlc_"):
                    gallery.id = f"mlc_{uuid.uuid4().hex}"
                    for child in children:
                        if child.parent_id == old_id:
                            child.parent_id = gallery.id
                for item in gallery_items:
                    item.category = gallery.id
                    item.category_owner_uid = gallery.owner_uid
                    item.metadata_json = {**(item.metadata_json or {}), "ever_shared": True}
            gallery.visibility = changes["visibility"]
            gallery.is_global_personal = changes["visibility"] == "private" and user.role == "superadmin"
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
        await repo.update_child_category_industry(_owner_uid(user), material_type, category.id, category.industry_slug)
    detail_fields = ("design_style", "building_name", "area")
    if any(field in changes for field in detail_fields):
        if material_type != "image" or not category.parent_id:
            if any(changes.get(field) for field in detail_fields):
                raise _error(
                    422,
                    "MATERIAL_GALLERY_DETAILS_PARENT_INVALID",
                    "设计风格、楼盘名称和面积仅适用于装修与家居二级图库",
                )
        else:
            parent = await repo.get_category(_owner_uid(user), material_type, category.parent_id)
            if parent is None or not (
                parent.industry_slug == DECORATION_GALLERY_INDUSTRY_SLUG or is_enterprise_case_root(parent)
            ):
                if any(changes.get(field) for field in detail_fields):
                    raise _error(
                        422,
                        "MATERIAL_GALLERY_DETAILS_PARENT_INVALID",
                        "设计风格、楼盘名称和面积仅适用于装修与家居二级图库",
                    )
            else:
                design_style = str(changes.get("design_style", category.design_style or "") or "").strip()
                building_name = str(changes.get("building_name", category.building_name or "") or "").strip()
                area = str(changes.get("area", category.area or "") or "").strip()
                if not design_style:
                    raise _error(422, "MATERIAL_DESIGN_STYLE_REQUIRED", "请选择设计风格")
                if design_style not in DECORATION_GALLERY_DESIGN_STYLES:
                    raise _error(422, "MATERIAL_DESIGN_STYLE_INVALID", "设计风格不在可选范围内")
                if not building_name:
                    raise _error(422, "MATERIAL_BUILDING_NAME_REQUIRED", "请输入楼盘名称")
                if not area:
                    raise _error(422, "MATERIAL_AREA_REQUIRED", "请输入面积")
                category.design_style = design_style
                category.building_name = building_name
                category.area = area
    category.updated_at = utc_now_naive()
    _audit_material(db, user, actor_user, "material.gallery.update", category_id=category.id, changes=changes)
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
            "can_manage": _can_manage_category(user, category),
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
    *,
    actor_user: User | None = None,
    target_private: bool = False,
) -> dict[str, Any]:
    if await material_access_mode(db, actor_user or user) == "rough_upload":
        raise _error(403, "MATERIAL_PERMISSION_DENIED", "当前岗位只能上传企业共享毛坯房图库")
    if material_type not in {"image", "cover_template"}:
        raise _error(422, "MATERIAL_TYPE_INVALID", "素材类型不存在")
    if material_type == "image":
        await ensure_initial_enterprise_galleries(db)
    categories = await ensure_material_categories(
        db,
        owner_uid=_owner_uid(user),
        tenant_id=_tenant_id(user),
        material_type=material_type,
    )
    repo = MaterialLibraryRepository(db, include_shared=True)
    category = await repo.get_category(_owner_uid(user), material_type, category_id, for_update=True)
    if category is None:
        raise _error(404, "MATERIAL_CATEGORY_NOT_FOUND", "图库或分类不存在")
    if target_private and not _is_target_rough_category(category, _owner_uid(user)):
        raise _error(404, "MATERIAL_CATEGORY_NOT_FOUND", "图库或分类不存在")
    if not (target_private or _can_manage_category(user, category)):
        message = "只有管理员可管理图片图库" if material_type == "image" else "只能管理自己的分类"
        raise _error(403, "MATERIAL_CATEGORY_FORBIDDEN", message)
    if personal_folder_key(category) or (
        category.is_system and category.image_design_role not in {"reference", "rough", "generated"}
    ):
        raise _error(409, "MATERIAL_CATEGORY_SYSTEM_REQUIRED", "系统图库不能删除")
    children = await repo.list_child_categories(_owner_uid(user), material_type, category.id)
    if children:
        raise _error(409, "MATERIAL_CATEGORY_HAS_CHILDREN", "一级图库仍有二级图库，请先移动或删除二级图库")
    fallback = (
        await ensure_scope_root(db, user, category.visibility)
        if material_type == "image"
        else next(item for item in categories if item.is_system)
    )
    target_id = payload.target_category_id or fallback.id
    if target_id == category.id:
        raise _error(422, "MATERIAL_CATEGORY_TARGET_INVALID", "迁移目标不能是当前图库或分类")
    target = await repo.get_category(_owner_uid(user), material_type, target_id, for_update=True)
    if target is None:
        raise _error(422, "MATERIAL_CATEGORY_TARGET_INVALID", "迁移目标图库或分类不存在")
    if target_private and (target.visibility != "private" or target.owner_uid != _owner_uid(user)):
        raise _error(422, "MATERIAL_CATEGORY_TARGET_INVALID", "迁移目标图库或分类不存在")
    moved = await repo.category_item_count(_owner_uid(user), material_type, category.id)
    if category.visibility == "private" and category.is_global_personal:
        from yuxi.services.personal_materials import folder_categories

        contributed = [
            item
            for item in await repo.category_items(category)
            if item.deleted_at is None and item.owner_uid != _owner_uid(user)
        ]
        for owner_uid in {item.owner_uid for item in contributed}:
            uploader = await db.scalar(select(User).where(User.uid == owner_uid))
            if uploader is None:
                raise _error(409, "MATERIAL_UPLOADER_NOT_FOUND", "图库内素材的上传账号不存在，请先核对归属")
            uploads = (await folder_categories(db, uploader))["uploads"][0]
            for item in contributed:
                if item.owner_uid == owner_uid:
                    item.category = uploads.id
                    item.category_owner_uid = uploads.owner_uid
        moved += len(contributed)
    if moved and category.visibility == "enterprise" and target.visibility != "enterprise":
        raise _error(422, "MATERIAL_CATEGORY_TARGET_INVALID", "共享图库内的素材必须迁移到其他共享图库")
    if moved:
        if target.visibility == "enterprise":
            for item in await repo.category_items(category):
                item.metadata_json = {**(item.metadata_json or {}), "ever_shared": True}
        await repo.reassign_category_items(_owner_uid(user), material_type, category.id, target.id, target.owner_uid)
    category.deleted_at = utc_now_naive()
    _audit_material(
        db,
        user,
        actor_user,
        "material.gallery.delete",
        category_id=category.id,
        target_category_id=target.id,
    )
    await db.commit()
    return {"success": True, "id": category.id, "moved": moved, "target_category_id": target.id}


async def list_image_galleries(
    db: AsyncSession,
    user: User,
    industry_slug: str | None = None,
    *,
    include_private_defaults: bool = True,
    personal_folder: Literal["rough"] | None = None,
    include_case_for_designer: bool = False,
) -> dict[str, Any]:
    await ensure_initial_enterprise_galleries(db)
    categories = await ensure_material_categories(
        db,
        owner_uid=_owner_uid(user),
        tenant_id=_tenant_id(user),
        material_type="image",
    )
    repo = MaterialLibraryRepository(db, include_shared=True)
    rough_upload_only = await material_access_mode(db, user) == "rough_upload"
    listed_categories = await repo.list_categories(_owner_uid(user), "image")
    case_root_ids = {category.id for category in listed_categories if is_enterprise_case_root(category)}
    categories = [
        category
        for category in listed_categories
        if not rough_upload_only
        or _designer_may_see_category(category)
        or (
            include_case_for_designer
            and (category.id in case_root_ids or category.parent_id in case_root_ids)
        )
        if personal_folder != "rough" or _is_target_rough_category(category, _owner_uid(user))
        if not is_storage_root(category)
        and (
            category.visibility != "private"
            or category.id not in RETIRED_PRIVATE_IMAGE_CATEGORY_IDS
            or personal_folder_key(category)
        )
        and (
            include_private_defaults
            or category.visibility != "private"
            or category.owner_uid != _owner_uid(user)
            or category.id not in DEFAULT_IMAGE_CATEGORY_IDS
        )
    ]
    raw = await repo.category_summaries(_owner_uid(user), material_type="image")
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
            or (industry_slug != "uncategorized" and effective_industry not in {industry_slug, "uncategorized"})
        ):
            continue
        direct_count, latest = raw.get((category.owner_uid, category.id), (0, None))
        children = [item for item in categories if item.parent_id == category.id]
        count = direct_count
        if category.parent_id is None:
            for child in children:
                child_count, child_latest = raw.get((child.owner_uid, child.id), (0, None))
                count += child_count
                child_updated_at = child_latest.updated_at or child_latest.created_at if child_latest else None
                latest_updated_at = latest.updated_at or latest.created_at if latest else None
                if child_latest is not None and (latest is None or child_updated_at > latest_updated_at):
                    latest = child_latest
        personal_key = personal_folder_key(category)
        personal_first = None
        if personal_key and category.owner_uid == _owner_uid(user):
            from yuxi.services.personal_materials import list_folder

            listing = await list_folder(db, user, personal_key, page=1, page_size=1)
            count = direct_count = listing["total"]
            personal_first = next(iter(listing["items"]), None)
        galleries.append(
            {
                **category.to_dict(),
                "personal_folder": personal_folder_key(category),
                "name": PERSONAL_IMAGE_FOLDERS[personal_folder_key(category)][1]
                if personal_folder_key(category)
                else category.name,
                "can_upload": can_contribute_to_category(user, category)
                and personal_folder_key(category) not in {"generated", "works"}
                and (not rough_upload_only or _designer_may_see_category(category)),
                "can_manage": _can_manage_category(user, category),
                "is_global_personal": category.is_global_personal,
                "industry_slug": effective_industry,
                "industry_name": industry_catalog.get(effective_industry, "未分类行业"),
                "count": count,
                "direct_count": direct_count,
                "child_count": len(children),
                "cover_item_id": personal_first["id"]
                if personal_first and not personal_first.get("work_asset_id")
                else (latest.id if latest is not None and not personal_key else None),
                "cover_work_asset_id": personal_first.get("work_asset_id") if personal_first else None,
                "updated_at": latest.to_dict()["updated_at"] if latest is not None else None,
            }
        )
    await db.commit()
    return {
        "galleries": galleries,
        "industries": [{"slug": slug, "name": name} for slug, name in industry_catalog.items()],
    }


async def get_material_file(
    db: AsyncSession,
    user: User,
    item_id: str,
    *,
    target_private: bool = False,
    allow_case_browse: bool = False,
) -> tuple[bytes, str, str]:
    repo = MaterialLibraryRepository(db, include_shared=True)
    item = (
        await repo.get_private_rough_item_for_owner(item_id, _owner_uid(user))
        if target_private
        else await repo.get_item_for_user(item_id, _owner_uid(user))
    )
    if item is None and not target_private:
        from yuxi.services.personal_materials import can_read_fixed_item

        if await can_read_fixed_item(db, user, item_id):
            item = await db.get(ContentMaterialLibraryItem, item_id)
    if item is None:
        raise _error(404, "MATERIAL_NOT_FOUND", "素材不存在")
    if await material_access_mode(db, user) == "rough_upload":
        category = await repo.get_category(item.category_owner_uid or item.owner_uid, item.material_type, item.category)
        parent = await _load_case_parent(repo, category) if allow_case_browse else None
        if not _designer_may_browse_case(category, parent):
            _reject_outside_rough_upload(category)
    asset = await repo.get_asset(item.asset_id, item.owner_uid)
    if asset is None:
        raise _error(404, "MATERIAL_ASSET_MISSING", "素材文件不存在")
    try:
        data = await read_material_bytes(asset)
    except StorageError as exc:
        raise _error(500, "MATERIAL_STORAGE_FAILED", "素材文件读取失败") from exc
    return data, asset.content_type, asset.original_file_name


async def get_material_thumbnail(
    db: AsyncSession,
    user: User,
    item_id: str,
    *,
    target_private: bool = False,
    allow_case_browse: bool = False,
) -> tuple[bytes, str]:
    repo = MaterialLibraryRepository(db, include_shared=True)
    item = (
        await repo.get_private_rough_item_for_owner(item_id, _owner_uid(user))
        if target_private
        else await repo.get_item_for_user(item_id, _owner_uid(user))
    )
    if item is None and not target_private:
        from yuxi.services.personal_materials import can_read_fixed_item

        if await can_read_fixed_item(db, user, item_id):
            item = await db.get(ContentMaterialLibraryItem, item_id)
    if item is None:
        raise _error(404, "MATERIAL_NOT_FOUND", "素材不存在")
    if await material_access_mode(db, user) == "rough_upload":
        category = await repo.get_category(item.category_owner_uid or item.owner_uid, item.material_type, item.category)
        parent = await _load_case_parent(repo, category) if allow_case_browse else None
        if not _designer_may_browse_case(category, parent):
            _reject_outside_rough_upload(category)
    asset = await repo.get_asset(item.asset_id, item.owner_uid)
    if asset is None:
        raise _error(404, "MATERIAL_ASSET_MISSING", "素材文件不存在")
    try:
        thumbnail = await persist_material_thumbnail(asset)
    except StorageError as exc:
        raise _error(500, "MATERIAL_STORAGE_FAILED", "素材缩略图读取失败") from exc
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise _error(400, "MATERIAL_IMAGE_INVALID", "素材文件不是有效图片") from exc
    return thumbnail, Path(asset.original_file_name).stem


async def delete_material_item(
    db: AsyncSession,
    user: User,
    item_id: str,
    *,
    actor_user: User | None = None,
    target_private: bool = False,
) -> dict[str, Any]:
    owner_uid = _owner_uid(user)
    repo = MaterialLibraryRepository(db, include_shared=True)
    item = (
        await repo.get_private_rough_item_for_owner(item_id, owner_uid, for_update=True)
        if target_private
        else await repo.get_item_for_user(item_id, owner_uid, for_update=True)
    )
    if item is None:
        raise _error(404, "MATERIAL_NOT_FOUND", "素材不存在")
    category = await repo.get_category(item.category_owner_uid or item.owner_uid, item.material_type, item.category)
    if await material_access_mode(db, actor_user or user) == "rough_upload" and (
        category is None or category.visibility != "private"
    ):
        raise _error(403, "MATERIAL_PERMISSION_DENIED", "企业共享毛坯房图库仅可上传")
    if category is None or not _can_manage_item(user, item, category):
        raise _error(403, "MATERIAL_MANAGE_FORBIDDEN", "只能删除自己上传的素材，管理员可删除企业共享素材")
    if (item.metadata_json or {}).get("ever_shared") or (item.metadata_json or {}).get("retain_asset_on_delete"):
        item.deleted_at = utc_now_naive()
        _audit_material(
            db,
            user,
            actor_user,
            "material.remove",
            item_id=item.id,
            retained_for_designs=True,
        )
        await db.commit()
        return {"success": True, "id": item_id, "object_deleted": False}
    asset = await repo.get_asset(item.asset_id, item.owner_uid, for_update=True)
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
    await delete_material_display_cache(asset.id)
    storage = get_minio_client()
    try:
        await storage.adelete_file(asset.bucket_name, asset.object_name)
        await storage.adelete_objects_by_prefix(asset.bucket_name, f"{asset.object_name}.share-v1.")
    except StorageError as exc:
        if ingest_status_of(asset) != INGEST_PENDING:
            raise _error(500, "MATERIAL_STORAGE_FAILED", "素材文件删除失败") from exc
    try:
        await storage.adelete_file(asset.bucket_name, material_thumb_object_name(asset.object_name))
    except StorageError:
        pass
    deleted_at = utc_now_naive()
    item.deleted_at = deleted_at
    asset.deleted_at = deleted_at
    if poster is not None:
        poster.deleted_at = deleted_at
    _audit_material(db, user, actor_user, "material.delete", item_id=item.id)
    await db.commit()
    return {"success": True, "id": item_id, "object_deleted": True}
