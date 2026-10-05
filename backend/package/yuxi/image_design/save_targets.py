from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from sqlalchemy.exc import MultipleResultsFound

from yuxi.image_design.schemas import ImageDesignSaveTarget
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.material_library_categories import list_material_categories
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
    """Visible enterprise galleries accept contributions; private galleries stay owner-only."""
    return category.deleted_at is None and (
        category.visibility == "enterprise"
        or (
            category.visibility == "private"
            and category.owner_uid == str(user.uid)
            and (not category.is_global_personal or user.role in {"admin", "superadmin"})
        )
    )


def _save_target_error(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"error": {"code": code, "message": message}})


async def ensure_private_system_galleries(db, user: User) -> None:
    """Follow the PC rule: seed defaults only when the user has no image categories."""
    owner_uid = str(user.uid)
    tenant_id = str(user.department_id) if getattr(user, "department_id", None) is not None else None
    repo = MaterialLibraryRepository(db, include_shared=True)
    if any(category.owner_uid == owner_uid for category in await repo.list_categories(owner_uid, "image")):
        return
    await repo.ensure_default_categories(
        [
            {
                "owner_uid": owner_uid,
                "id": definition["code"],
                "tenant_id": tenant_id,
                "material_type": "image",
                "visibility": "private",
                "parent_id": None,
                "industry_slug": "uncategorized",
                "name": definition["name"],
                "description": definition["description"],
                "sort_order": index * 10,
                "is_system": definition["code"] == "uncategorized",
            }
            for index, definition in enumerate(list_material_categories("image"))
        ]
    )


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
    return ResolvedSaveTarget(target.scope, category.id, category.id, category.owner_uid)


async def resolve_mp_save_target(db, user: User, target: ImageDesignSaveTarget) -> ResolvedSaveTarget:
    """Save mini-program effects in the creator's PC personal materials."""
    await validate_mp_save_target(db, user, target)
    root = await ensure_scope_root(db, user, "private")
    return ResolvedSaveTarget("private", None, root.id, root.owner_uid)


def _folder_path(category: ContentMaterialCategory, by_id: dict[str, ContentMaterialCategory]) -> str:
    names = [category.name]
    parent_id = category.parent_id
    visited = {category.id}
    while parent_id and parent_id not in visited:
        parent = by_id.get(parent_id)
        if parent is None:
            break
        names.append(parent.name)
        visited.add(parent.id)
        parent_id = parent.parent_id
    return " / ".join(reversed(names))


async def list_writable_save_targets(db, user: User) -> dict[str, list[dict[str, object]]]:
    requester_uid = str(user.uid)
    await ensure_scope_root(db, user, "private")
    await ensure_scope_root(db, user, "enterprise")
    categories = await MaterialLibraryRepository(db, include_shared=True).list_categories(requester_uid, "image")

    scopes: list[dict[str, object]] = []
    for scope, label in (("private", "我的素材"), ("enterprise", "企业图库")):
        folders = [
            category
            for category in categories
            if category.visibility == scope
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
                        "name": category.name,
                        "parent_id": category.parent_id,
                        "path": _folder_path(category, by_id),
                    }
                    for category in folders
                ],
            }
        )
    return {"scopes": scopes}


async def list_mp_save_targets(db, user: User) -> dict:
    """New mini-program effects use the owner's PC personal materials root."""
    await ensure_scope_root(db, user, "private")
    return {
        "scopes": [
            {"scope": "private", "label": "我的素材", "can_write_root": True, "folders": []},
        ]
    }


async def validate_mp_save_target(db, user: User, target: ImageDesignSaveTarget) -> None:
    if target.scope == "private" and target.gallery_id is None:
        return
    if target.scope == "enterprise" and target.gallery_id:
        # Already queued drafts may still name the former enterprise result gallery.
        categories = await MaterialLibraryRepository(db, include_shared=True).list_categories(str(user.uid), "image")
        if any(
            category.id == target.gallery_id
            and category.visibility == "enterprise"
            and category.parent_id is None
            and (
                category.image_design_role == "generated"
                or (category.image_design_role is None and category.name == "生图图库")
            )
            for category in categories
        ):
            return
    raise _save_target_error(
        "IMAGE_DESIGN_SAVE_TARGET_INVALID",
        "请选择我的素材作为保存路径",
    )
