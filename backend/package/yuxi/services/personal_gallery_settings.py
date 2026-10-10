"""Shared display configuration for user-owned personal galleries."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from yuxi.services.material_library_categories import PERSONAL_IMAGE_FOLDERS, personal_folder_key
from yuxi.storage.postgres.models_content import ContentMaterialFolderSetting


async def load_personal_gallery_settings(db) -> dict:
    rows = await db.scalars(
        select(ContentMaterialFolderSetting).where(ContentMaterialFolderSetting.config_version == 1)
    )
    return {row.folder_key: row for row in rows}


def personal_gallery_fields(category, settings: dict) -> dict:
    key = personal_folder_key(category)
    setting = settings.get(key)
    return {
        "name": setting.name if setting else PERSONAL_IMAGE_FOLDERS[key][1] if key else category.name,
        "description": setting.description if setting else category.description or "",
        "industry_slug": setting.industry_slug if setting else category.industry_slug,
    }


def personal_gallery_is_active(category, settings: dict) -> bool:
    setting = settings.get(personal_folder_key(category))
    return setting is None or setting.deleted_at is None


async def lock_personal_gallery_settings(db) -> dict:
    # Lock all four definitions in a stable order, including migration targets.
    for key in sorted(PERSONAL_IMAGE_FOLDERS):
        await db.execute(
            insert(ContentMaterialFolderSetting)
            .values(folder_key=key, name=PERSONAL_IMAGE_FOLDERS[key][1], config_version=1)
            .on_conflict_do_nothing()
        )
    rows = list(await db.scalars(select(ContentMaterialFolderSetting).order_by(
        ContentMaterialFolderSetting.folder_key
    ).with_for_update().execution_options(populate_existing=True)))
    for row in rows:
        if row.folder_key in PERSONAL_IMAGE_FOLDERS and row.config_version != 1:
            row.name = PERSONAL_IMAGE_FOLDERS[row.folder_key][1]
            row.description = ""
            row.industry_slug = "decoration"
            row.deleted_at = None
            row.config_version = 1
    await db.flush()
    return {row.folder_key: row for row in rows if row.config_version == 1}


def require_personal_gallery(settings: dict, key: str):
    if key not in PERSONAL_IMAGE_FOLDERS:
        raise HTTPException(404, "个人图库不存在")
    setting = settings.get(key)
    if setting is not None and setting.deleted_at is not None:
        raise HTTPException(409, detail={"error": {
            "code": "MATERIAL_PERSONAL_GALLERY_DELETED", "message": "该个人图库已删除，不可继续使用"
        }})
    return setting
