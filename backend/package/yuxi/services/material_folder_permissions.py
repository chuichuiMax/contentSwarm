from __future__ import annotations

import re
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.image_design.save_targets import ENTERPRISE_ROOT_OWNER_UID, is_storage_root
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.material_library_categories import is_personal_folder_visible, personal_folder_key
from yuxi.storage.postgres.models_content import ContentMaterialCategory

LEGACY_ROUGH_UPLOAD_PERMISSION = "material_rough.upload"
MATERIAL_FOLDER_KEY_PREFIX = "material_folder."
_MATERIAL_FOLDER_GRANT = re.compile(r"^material_folder\.[^.]+\.(view|upload)$")


def material_folder_view_key(folder_id: str) -> str:
    return f"{MATERIAL_FOLDER_KEY_PREFIX}{folder_id}.view"


def material_folder_upload_key(folder_id: str) -> str:
    return f"{MATERIAL_FOLDER_KEY_PREFIX}{folder_id}.upload"


def folder_id_for_category(category: ContentMaterialCategory | None) -> str | None:
    if category is None:
        return None
    personal_key = personal_folder_key(category)
    if personal_key:
        return f"personal.{personal_key}"
    return category.id


def uses_explicit_folder_grants(grants: list[str]) -> bool:
    return any(item.startswith(MATERIAL_FOLDER_KEY_PREFIX) for item in grants)


def material_permissions_active(grants: list[str]) -> bool:
    return LEGACY_ROUGH_UPLOAD_PERMISSION in grants or uses_explicit_folder_grants(grants)


def is_valid_material_grant_key(key: str) -> bool:
    if key == LEGACY_ROUGH_UPLOAD_PERMISSION:
        return True
    return bool(_MATERIAL_FOLDER_GRANT.fullmatch(key))


def _folder_label(category: ContentMaterialCategory, by_id: dict[str, ContentMaterialCategory]) -> str:
    if category.visibility == "enterprise":
        prefix = "素材库-企业共享"
        if category.parent_id and category.parent_id in by_id:
            return f"{prefix}-{by_id[category.parent_id].name}-{category.name}"
        return f"{prefix}-{category.name}"
    name = category.name
    return f"素材库-{name}"


def _folder_actions(folder_id: str) -> list[dict[str, str]]:
    return [
        {"key": material_folder_view_key(folder_id), "label": "查看"},
        {"key": material_folder_upload_key(folder_id), "label": "上传"},
    ]


def _folder_entry(folder_id: str, label: str) -> dict[str, Any]:
    return {"list_key": folder_id, "list": label, "actions": _folder_actions(folder_id)}


async def list_material_folder_permission_entries(db: AsyncSession) -> list[dict[str, Any]]:
    from yuxi.services.material_library_service import ensure_initial_enterprise_galleries

    await ensure_initial_enterprise_galleries(db)
    repo = MaterialLibraryRepository(db, include_shared=True)
    categories = [
        category
        for category in await repo.list_categories(ENTERPRISE_ROOT_OWNER_UID, "image")
        if not is_storage_root(category)
    ]
    by_id = {category.id: category for category in categories}
    entries: list[dict[str, Any]] = []
    for category in sorted(
        categories,
        key=lambda row: (
            row.parent_id or "",
            row.sort_order,
            row.name,
        ),
    ):
        if category.parent_id or category.visibility != "enterprise":
            continue
        entries.append(_folder_entry(category.id, _folder_label(category, by_id)))
    return entries


def personal_material_always_allowed(category: ContentMaterialCategory | None) -> bool:
    key = personal_folder_key(category) if category is not None else None
    return bool(key and is_personal_folder_visible(key))


def grant_allows_folder_view(
    grants: list[str],
    category: ContentMaterialCategory | None,
    *,
    legacy_may_view,
    legacy_may_browse_case,
    case_parent: ContentMaterialCategory | None = None,
) -> bool:
    if category is None:
        return True
    if personal_material_always_allowed(category):
        return True
    if not material_permissions_active(grants):
        return True
    if not uses_explicit_folder_grants(grants):
        return legacy_may_view(category) or legacy_may_browse_case(category, case_parent)
    folder_id = folder_id_for_category(category)
    if folder_id is None:
        return False
    if material_folder_view_key(folder_id) in grants:
        return True
    if category.parent_id and material_folder_view_key(category.parent_id) in grants:
        return True
    if case_parent is not None and material_folder_view_key(case_parent.id) in grants:
        return True
    return False


def grant_allows_folder_upload(
    grants: list[str],
    category: ContentMaterialCategory | None,
    *,
    legacy_may_upload,
) -> bool:
    if category is None:
        return False
    if personal_material_always_allowed(category):
        return True
    if not material_permissions_active(grants):
        return True
    if not uses_explicit_folder_grants(grants):
        return legacy_may_upload(category)
    folder_id = folder_id_for_category(category)
    if folder_id is None:
        return False
    if material_folder_upload_key(folder_id) in grants:
        return True
    if category.parent_id and material_folder_upload_key(category.parent_id) in grants:
        return True
    return False


async def material_permission_state(
    db: AsyncSession,
    user,
) -> tuple[Literal["full", "restricted"], list[str] | None]:
    from yuxi.services.role_service import effective_permission_grants

    grants = await effective_permission_grants(db, user)
    if grants is None:
        return "full", None
    if material_permissions_active(grants):
        return "restricted", grants
    return "full", grants
