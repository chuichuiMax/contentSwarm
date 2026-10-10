from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from sqlalchemy.exc import MultipleResultsFound

from yuxi.image_design.schemas import ImageDesignSaveTarget
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.material_library_categories import personal_folder_key
from yuxi.services.personal_gallery_settings import (
    load_personal_gallery_settings, personal_gallery_fields, personal_gallery_is_active, require_personal_gallery,
)
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentMaterialCategory

PRIVATE_ROOT_CATEGORY_ID = "private-root"
ENTERPRISE_ROOT_CATEGORY_ID = "enterprise-root"
ENTERPRISE_ROOT_OWNER_UID = "system:material-library"


@dataclass(frozen=True)
class ResolvedSaveTarget:
    scope: Literal["private", "enterprise"]
    gallery_id: str | None
    category_id: str
    category_owner_uid: str
    warning: str | None = None

    @property
    def public_target(self) -> dict[str, str | None]:
        return {"scope": self.scope, "gallery_id": self.gallery_id}


def is_storage_root(category: ContentMaterialCategory) -> bool:
    if category.material_type != "image":
        return False
    return (category.visibility == "private" and category.id == PRIVATE_ROOT_CATEGORY_ID and category.is_system) or (
        category.owner_uid == ENTERPRISE_ROOT_OWNER_UID
        and category.visibility == "enterprise"
        and category.id == ENTERPRISE_ROOT_CATEGORY_ID
        and category.is_system
    )


def can_contribute_to_category(user: User, category: ContentMaterialCategory) -> bool:
    """Visible folders accept contributions; private images retain uploader ownership."""
    return category.deleted_at is None and (
        category.visibility == "enterprise"
        or (category.visibility == "private" and (category.owner_uid == str(user.uid) or category.is_global_personal))
    )


def _save_target_error(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"error": {"code": code, "message": message}})


async def ensure_private_system_galleries(db, user: User) -> None:
    from yuxi.services.personal_materials import folder_categories

    await folder_categories(db, user)


async def ensure_scope_root(
    db,
    user: User,
    scope: Literal["private", "enterprise"],
) -> ContentMaterialCategory:
    requester_uid = str(user.uid)
    if scope == "private":
        await ensure_private_system_galleries(db, user)
        owner_uid = requester_uid
        category_id = PRIVATE_ROOT_CATEGORY_ID
        visibility = "private"
        tenant_id = str(user.department_id) if getattr(user, "department_id", None) is not None else None
        name = "我的素材（根目录）"
        description = "图片设计生成结果的个人根目录"
    else:
        owner_uid = ENTERPRISE_ROOT_OWNER_UID
        category_id = ENTERPRISE_ROOT_CATEGORY_ID
        visibility = "enterprise"
        tenant_id = None
        name = "企业素材"
        description = "图片设计生成结果的企业根目录"

    repo = MaterialLibraryRepository(db, include_shared=True)
    await repo.ensure_default_categories(
        [
            {
                "owner_uid": owner_uid,
                "id": category_id,
                "tenant_id": tenant_id,
                "material_type": "image",
                "visibility": visibility,
                "parent_id": None,
                "industry_slug": "uncategorized",
                "name": name,
                "description": description,
                "sort_order": 0,
                "is_system": True,
            }
        ]
    )
    category = await repo.get_category_exact(
        requester_uid=requester_uid,
        material_type="image",
        category_id=category_id,
        category_owner_uid=owner_uid,
        visibility=visibility,
    )
    if category is None or not is_storage_root(category) or not can_contribute_to_category(user, category):
        raise _save_target_error("IMAGE_DESIGN_SAVE_TARGET_INVALID", "保存根目录不可用")
    if scope == "private":
        await repo.migrate_generated_private_root(requester_uid, category.id)
    return category


async def resolve_writable_save_target(
    db,
    user: User,
    target: ImageDesignSaveTarget,
    *,
    fallback_invalid_folder: bool = False,
) -> ResolvedSaveTarget:
    if target.gallery_id is None:
        category = await ensure_scope_root(db, user, target.scope)
        return ResolvedSaveTarget(target.scope, None, category.id, category.owner_uid)

    repo = MaterialLibraryRepository(db, include_shared=True)
    try:
        category = await repo.get_category_exact(
            requester_uid=str(user.uid),
            material_type="image",
            category_id=target.gallery_id,
            category_owner_uid=str(user.uid) if target.scope == "private" else None,
            visibility=target.scope,
        )
    except MultipleResultsFound as exc:
        raise _save_target_error(
            "IMAGE_DESIGN_SAVE_TARGET_AMBIGUOUS", "保存范围内有重复图库标识，请联系管理员"
        ) from exc
    if category is None and target.scope == "private":
        category = await repo.get_category_exact(
            requester_uid=str(user.uid),
            material_type="image",
            category_id=target.gallery_id,
            visibility="private",
        )
    if category is None:
        try:
            other_scope = await repo.get_category_exact(
                requester_uid=str(user.uid),
                material_type="image",
                category_id=target.gallery_id,
                visibility="enterprise" if target.scope == "private" else "private",
            )
        except MultipleResultsFound:
            raise _save_target_error("IMAGE_DESIGN_SAVE_TARGET_SCOPE_MISMATCH", "保存范围与文件夹不一致") from None
        if other_scope is not None:
            raise _save_target_error("IMAGE_DESIGN_SAVE_TARGET_SCOPE_MISMATCH", "保存范围与文件夹不一致")
    if not category or is_storage_root(category):
        if fallback_invalid_folder:
            root = await ensure_scope_root(db, user, target.scope)
            return ResolvedSaveTarget(target.scope, None, root.id, root.owner_uid, "SAVE_TARGET_FALLBACK_TO_ROOT")
        raise _save_target_error("IMAGE_DESIGN_SAVE_TARGET_INVALID", "保存位置不存在或不可写")
    if not can_contribute_to_category(user, category):
        raise _save_target_error("IMAGE_DESIGN_SAVE_TARGET_INVALID", "不能写入其他人的个人素材")
    if key := personal_folder_key(category):
        require_personal_gallery(await load_personal_gallery_settings(db), key)
    return ResolvedSaveTarget(target.scope, category.id, category.id, category.owner_uid)


async def resolve_mp_save_target(db, user: User, target: ImageDesignSaveTarget) -> ResolvedSaveTarget:
    """Resolve the two fixed destinations without redirecting between scopes."""
    from yuxi.services.personal_materials import folder_categories

    if target.scope == "private":
        require_personal_gallery(await load_personal_gallery_settings(db), "generated")
        gallery = (await folder_categories(db, user))["generated"][0]
        if target.gallery_id not in {None, PRIVATE_ROOT_CATEGORY_ID, gallery.id}:
            raise _save_target_error("IMAGE_DESIGN_SAVE_TARGET_INVALID", "请选择自己的个人生图图库")
    else:
        galleries = await _enterprise_generated_galleries(db, user)
        if len(galleries) != 1:
            raise _save_target_error("IMAGE_DESIGN_SAVE_TARGET_INVALID", _enterprise_target_error(galleries))
        gallery = galleries[0]
        if target.gallery_id != gallery.id:
            raise _save_target_error("IMAGE_DESIGN_SAVE_TARGET_INVALID", "请选择企业共享的生图图库")
    locked = await MaterialLibraryRepository(db, include_shared=True).get_category_exact(
        requester_uid=str(user.uid),
        material_type="image",
        category_id=gallery.id,
        category_owner_uid=gallery.owner_uid,
        visibility=target.scope,
        for_update=True,
    )
    if locked is None or not can_contribute_to_category(user, locked):
        raise _save_target_error("IMAGE_DESIGN_SAVE_TARGET_INVALID", "保存图库已不可用，请重新选择")
    return ResolvedSaveTarget(target.scope, locked.id, locked.id, locked.owner_uid)


async def _enterprise_generated_galleries(db, user: User) -> list[ContentMaterialCategory]:
    categories = await MaterialLibraryRepository(db, include_shared=True).list_categories(str(user.uid), "image")
    return [
        category
        for category in categories
        if category.visibility == "enterprise"
        and category.parent_id is None
        and can_contribute_to_category(user, category)
        and (
            category.image_design_role == "generated"
            or (category.image_design_role is None and category.name == "生图图库")
        )
    ]


def _enterprise_target_error(galleries: list[ContentMaterialCategory]) -> str:
    return "企业生图图库存在多个候选，请联系管理员核对" if galleries else "企业生图图库尚未配置或不可写"


def _folder_path(category: ContentMaterialCategory, by_id: dict[str, ContentMaterialCategory], settings: dict) -> str:
    names = [personal_gallery_fields(category, settings)["name"]]
    parent_id = category.parent_id
    visited = {category.id}
    while parent_id and parent_id not in visited:
        parent = by_id.get(parent_id)
        if parent is None:
            break
        names.append(personal_gallery_fields(parent, settings)["name"])
        visited.add(parent.id)
        parent_id = parent.parent_id
    return " / ".join(reversed(names))


async def list_writable_save_targets(db, user: User) -> dict[str, list[dict[str, object]]]:
    requester_uid = str(user.uid)
    await ensure_scope_root(db, user, "private")
    await ensure_scope_root(db, user, "enterprise")
    categories = await MaterialLibraryRepository(db, include_shared=True).list_categories(requester_uid, "image")

    settings = await load_personal_gallery_settings(db)
    scopes: list[dict[str, object]] = []
    for scope, label in (("private", "我的素材"), ("enterprise", "企业图库")):
        folders = [
            category
            for category in categories
            if category.visibility == scope
            and personal_gallery_is_active(category, settings)
            and not is_storage_root(category)
            and can_contribute_to_category(user, category)
        ]
        by_id = {category.id: category for category in folders}
        scopes.append(
            {
                "scope": scope,
                "label": label,
                "can_write_root": True,
                "folders": [
                    {
                        "id": category.id,
                        "name": personal_gallery_fields(category, settings)["name"],
                        "parent_id": category.parent_id,
                        "path": _folder_path(category, by_id, settings),
                    }
                    for category in folders
                ],
            }
        )
    return {"scopes": scopes}


async def list_mp_save_targets(db, user: User) -> dict:
    """Expose actual galleries; unavailable enterprise targets keep their reason."""
    from yuxi.services.personal_materials import folder_categories

    galleries = (await folder_categories(db, user))["generated"]
    gallery = galleries[0] if galleries else None
    settings = await load_personal_gallery_settings(db)
    enterprise = await _enterprise_generated_galleries(db, user)
    return {
        "scopes": [
            {
                "scope": "private",
                "label": "我的素材",
                "can_write_root": gallery is not None,
                "error": None if gallery else "个人生图图库已删除或不可用",
                "folders": [{"id": gallery.id, "name": personal_gallery_fields(gallery, settings)["name"],
                             "personal_folder": "generated"}] if gallery else [],
            },
            {
                "scope": "enterprise",
                "label": "企业共享",
                "can_write_root": False,
                "error": None if len(enterprise) == 1 else _enterprise_target_error(enterprise),
                "folders": [
                    {"id": enterprise[0].id, "name": "生图图库", "can_write": True, "image_design_role": "generated"}
                ]
                if len(enterprise) == 1
                else [],
            },
        ]
    }


async def validate_mp_save_target(db, user: User, target: ImageDesignSaveTarget) -> None:
    await resolve_mp_save_target(db, user, target)
