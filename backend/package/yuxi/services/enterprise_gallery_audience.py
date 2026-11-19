"""企业共享图库受众：按员工当前分部包含匹配，互斥可见。"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.services.employee_service import resolve_employee_for_user
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentMaterialAudience, ContentMaterialCategory
from yuxi.utils.datetime_utils import utc_now_naive

DEFAULT_AUDIENCE_ID = "mga_default"
DEFAULT_AUDIENCE_CODE = "default"
WOODWORK_AUDIENCE_ID = "mga_woodwork"
WOODWORK_AUDIENCE_CODE = "woodwork"
WOODWORK_GALLERY_NAME = "邻奢木作"
WOODWORK_MATCH_KEYWORD = "木作事业部"

INITIAL_AUDIENCES: tuple[dict[str, Any], ...] = (
    {
        "id": DEFAULT_AUDIENCE_ID,
        "code": DEFAULT_AUDIENCE_CODE,
        "name": "默认员工",
        "match_keywords": [],
        "is_default": True,
        "sort_order": 1000,
        "enabled": True,
    },
    {
        "id": WOODWORK_AUDIENCE_ID,
        "code": WOODWORK_AUDIENCE_CODE,
        "name": "木作事业部",
        "match_keywords": [WOODWORK_MATCH_KEYWORD],
        "is_default": False,
        "sort_order": 10,
        "enabled": True,
    },
)


class MaterialAudienceCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    match_keywords: list[str] = Field(default_factory=list)
    sort_order: int = Field(default=100, ge=0, le=100000)
    enabled: bool = True

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized or not all(ch.isalnum() or ch in {"_", "-"} for ch in normalized):
            raise ValueError("受众编码仅支持字母、数字、下划线和短横线")
        return normalized

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("受众名称不能为空")
        return normalized

    @field_validator("match_keywords")
    @classmethod
    def validate_keywords(cls, value: list[str]) -> list[str]:
        keywords: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in keywords:
                keywords.append(text)
        return keywords


class MaterialAudienceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    match_keywords: list[str] | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100000)
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("受众名称不能为空")
        return normalized

    @field_validator("match_keywords")
    @classmethod
    def validate_keywords(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        keywords: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in keywords:
                keywords.append(text)
        return keywords


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


def match_audience_for_branch(
    current_branch: str | None,
    audiences: list[ContentMaterialAudience],
) -> ContentMaterialAudience:
    default = next((item for item in audiences if item.is_default), None)
    if default is None:
        raise _error(500, "MATERIAL_AUDIENCE_DEFAULT_MISSING", "缺少默认受众配置")
    branch = (current_branch or "").strip()
    ranked = sorted(
        (item for item in audiences if item.enabled and not item.is_default),
        key=lambda item: (item.sort_order, item.code),
    )
    for audience in ranked:
        keywords = [str(item).strip() for item in (audience.match_keywords or []) if str(item).strip()]
        if any(keyword in branch for keyword in keywords):
            return audience
    return default


async def ensure_material_audiences(db: AsyncSession) -> list[ContentMaterialAudience]:
    rows = list((await db.execute(select(ContentMaterialAudience))).scalars())
    by_id = {row.id: row for row in rows}
    by_code = {row.code: row for row in rows}
    changed = False
    for seed in INITIAL_AUDIENCES:
        existing = by_id.get(seed["id"]) or by_code.get(seed["code"])
        if existing is None:
            row = ContentMaterialAudience(**seed)
            db.add(row)
            rows.append(row)
            by_id[row.id] = row
            by_code[row.code] = row
            changed = True
            continue
        if seed["is_default"] and not existing.is_default:
            existing.is_default = True
            changed = True
        if existing.code == WOODWORK_AUDIENCE_CODE and not (existing.match_keywords or []):
            existing.match_keywords = list(seed["match_keywords"])
            changed = True
    if not any(row.is_default for row in rows):
        fallback = by_id.get(DEFAULT_AUDIENCE_ID) or by_code.get(DEFAULT_AUDIENCE_CODE)
        if fallback is None:
            raise _error(500, "MATERIAL_AUDIENCE_DEFAULT_MISSING", "缺少默认受众配置")
        fallback.is_default = True
        changed = True
    if changed:
        await db.flush()
    return list(
        (
            await db.execute(
                select(ContentMaterialAudience).order_by(
                    ContentMaterialAudience.sort_order,
                    ContentMaterialAudience.code,
                )
            )
        ).scalars()
    )


async def ensure_enterprise_gallery_audiences(db: AsyncSession) -> list[ContentMaterialAudience]:
    audiences = await ensure_material_audiences(db)
    default_id = next(item.id for item in audiences if item.is_default)
    woodwork = next((item for item in audiences if item.code == WOODWORK_AUDIENCE_CODE), None)
    woodwork_id = woodwork.id if woodwork is not None else WOODWORK_AUDIENCE_ID
    roots = list(
        (
            await db.execute(
                select(ContentMaterialCategory).where(
                    ContentMaterialCategory.material_type == "image",
                    ContentMaterialCategory.visibility == "enterprise",
                    ContentMaterialCategory.parent_id.is_(None),
                    ContentMaterialCategory.deleted_at.is_(None),
                )
            )
        ).scalars()
    )
    changed = False
    for category in roots:
        if category.audience_id:
            continue
        if category.name == WOODWORK_GALLERY_NAME:
            category.audience_id = woodwork_id
            changed = True
            continue
        category.audience_id = default_id
        changed = True
    if changed:
        await db.flush()
    return audiences


def resolve_root_audience_id(
    category: ContentMaterialCategory | None,
    *,
    roots_by_id: dict[str, ContentMaterialCategory],
    default_audience_id: str,
) -> str | None:
    if category is None or category.visibility != "enterprise":
        return None
    root = category if not category.parent_id else roots_by_id.get(category.parent_id)
    if root is None:
        return default_audience_id
    return root.audience_id or default_audience_id


def enterprise_category_visible_for_audience(
    category: ContentMaterialCategory,
    *,
    user_audience_id: str,
    is_admin: bool,
    roots_by_id: dict[str, ContentMaterialCategory],
    default_audience_id: str,
) -> bool:
    if is_admin or category.visibility != "enterprise":
        return True
    assigned = resolve_root_audience_id(
        category,
        roots_by_id=roots_by_id,
        default_audience_id=default_audience_id,
    )
    return assigned == user_audience_id


async def resolve_user_material_audience(
    db: AsyncSession,
    user: User,
) -> ContentMaterialAudience:
    audiences = await ensure_enterprise_gallery_audiences(db)
    if user.role in {"admin", "superadmin"}:
        return next(item for item in audiences if item.is_default)
    employee = await resolve_employee_for_user(db, user)
    branch = employee.current_branch if employee is not None else ""
    return match_audience_for_branch(branch, audiences)


async def filter_categories_by_audience(
    db: AsyncSession,
    user: User,
    categories: list[ContentMaterialCategory],
) -> list[ContentMaterialCategory]:
    if user.role in {"admin", "superadmin"}:
        await ensure_enterprise_gallery_audiences(db)
        return categories
    audience = await resolve_user_material_audience(db, user)
    audiences = await ensure_material_audiences(db)
    default_id = next(item.id for item in audiences if item.is_default)
    roots_by_id = {
        category.id: category
        for category in categories
        if category.parent_id is None and category.visibility == "enterprise"
    }
    # 子图库父级可能因其他过滤条件尚未进入列表；补齐企业根以便继承受众。
    missing_parent_ids = {
        category.parent_id
        for category in categories
        if category.visibility == "enterprise" and category.parent_id and category.parent_id not in roots_by_id
    }
    if missing_parent_ids:
        parents = list(
            (
                await db.execute(
                    select(ContentMaterialCategory).where(
                        ContentMaterialCategory.id.in_(missing_parent_ids),
                        ContentMaterialCategory.material_type == "image",
                        ContentMaterialCategory.visibility == "enterprise",
                        ContentMaterialCategory.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )
        for parent in parents:
            roots_by_id[parent.id] = parent
    return [
        category
        for category in categories
        if enterprise_category_visible_for_audience(
            category,
            user_audience_id=audience.id,
            is_admin=False,
            roots_by_id=roots_by_id,
            default_audience_id=default_id,
        )
    ]


async def reject_enterprise_audience_denied(
    db: AsyncSession,
    user: User,
    category: ContentMaterialCategory | None,
) -> None:
    if category is None or category.visibility != "enterprise" or user.role in {"admin", "superadmin"}:
        return
    audience = await resolve_user_material_audience(db, user)
    audiences = await ensure_material_audiences(db)
    default_id = next(item.id for item in audiences if item.is_default)
    root = category
    if category.parent_id:
        root = await db.scalar(
            select(ContentMaterialCategory).where(
                ContentMaterialCategory.owner_uid == category.owner_uid,
                ContentMaterialCategory.material_type == category.material_type,
                ContentMaterialCategory.id == category.parent_id,
                ContentMaterialCategory.deleted_at.is_(None),
            )
        )
        if root is None:
            raise _error(403, "MATERIAL_PERMISSION_DENIED", "当前岗位无权访问该图库")
    assigned = root.audience_id or default_id
    if assigned != audience.id:
        raise _error(403, "MATERIAL_PERMISSION_DENIED", "当前岗位无权访问该图库")


async def audience_catalog(db: AsyncSession) -> dict[str, ContentMaterialAudience]:
    audiences = await ensure_material_audiences(db)
    return {item.id: item for item in audiences}


async def list_material_audiences(db: AsyncSession) -> dict[str, Any]:
    audiences = await ensure_enterprise_gallery_audiences(db)
    return {"audiences": [item.to_dict() for item in audiences], "total": len(audiences)}


async def create_material_audience(
    db: AsyncSession,
    user: User,
    payload: MaterialAudienceCreate,
) -> dict[str, Any]:
    if user.role not in {"admin", "superadmin"}:
        raise _error(403, "MATERIAL_AUDIENCE_FORBIDDEN", "只有管理员可管理图库受众")
    await ensure_material_audiences(db)
    if not payload.match_keywords:
        raise _error(422, "MATERIAL_AUDIENCE_KEYWORDS_REQUIRED", "非默认受众至少配置一个分部匹配关键词")
    item = ContentMaterialAudience(
        id=f"mga_{payload.code}",
        code=payload.code,
        name=payload.name,
        match_keywords=payload.match_keywords,
        is_default=False,
        sort_order=payload.sort_order,
        enabled=payload.enabled,
    )
    try:
        db.add(item)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _error(409, "MATERIAL_AUDIENCE_EXISTS", "受众编码已存在") from exc
    return {"audience": item.to_dict()}


async def update_material_audience(
    db: AsyncSession,
    user: User,
    audience_id: str,
    payload: MaterialAudienceUpdate,
) -> dict[str, Any]:
    if user.role not in {"admin", "superadmin"}:
        raise _error(403, "MATERIAL_AUDIENCE_FORBIDDEN", "只有管理员可管理图库受众")
    await ensure_material_audiences(db)
    item = await db.get(ContentMaterialAudience, audience_id)
    if item is None:
        raise _error(404, "MATERIAL_AUDIENCE_NOT_FOUND", "受众不存在")
    data = payload.model_dump(exclude_unset=True)
    if item.is_default and "match_keywords" in data and data["match_keywords"]:
        raise _error(422, "MATERIAL_AUDIENCE_DEFAULT_KEYWORDS", "默认受众不使用分部匹配关键词")
    if not item.is_default and "match_keywords" in data and not data["match_keywords"]:
        raise _error(422, "MATERIAL_AUDIENCE_KEYWORDS_REQUIRED", "非默认受众至少配置一个分部匹配关键词")
    if "name" in data:
        item.name = data["name"]
    if "match_keywords" in data:
        item.match_keywords = data["match_keywords"]
    if "sort_order" in data:
        item.sort_order = data["sort_order"]
    if "enabled" in data:
        if item.is_default and not data["enabled"]:
            raise _error(422, "MATERIAL_AUDIENCE_DEFAULT_REQUIRED", "默认受众不能禁用")
        item.enabled = data["enabled"]
    item.updated_at = utc_now_naive()
    await db.commit()
    return {"audience": item.to_dict()}


async def delete_material_audience(db: AsyncSession, user: User, audience_id: str) -> dict[str, Any]:
    if user.role not in {"admin", "superadmin"}:
        raise _error(403, "MATERIAL_AUDIENCE_FORBIDDEN", "只有管理员可管理图库受众")
    item = await db.get(ContentMaterialAudience, audience_id)
    if item is None:
        raise _error(404, "MATERIAL_AUDIENCE_NOT_FOUND", "受众不存在")
    if item.is_default:
        raise _error(409, "MATERIAL_AUDIENCE_DEFAULT_REQUIRED", "默认受众不能删除")
    in_use = await db.scalar(
        select(ContentMaterialCategory.id).where(
            ContentMaterialCategory.audience_id == audience_id,
            ContentMaterialCategory.deleted_at.is_(None),
        ).limit(1)
    )
    if in_use:
        raise _error(409, "MATERIAL_AUDIENCE_IN_USE", "仍有企业图库归属该受众，请先调整图库归属")
    await db.delete(item)
    await db.commit()
    return {"success": True, "id": audience_id}


async def validate_audience_id(db: AsyncSession, audience_id: str | None) -> str | None:
    if audience_id is None:
        return None
    normalized = audience_id.strip()
    if not normalized:
        return None
    await ensure_material_audiences(db)
    item = await db.get(ContentMaterialAudience, normalized)
    if item is None or not item.enabled:
        raise _error(422, "MATERIAL_AUDIENCE_INVALID", "所选受众不存在或已禁用")
    return item.id
