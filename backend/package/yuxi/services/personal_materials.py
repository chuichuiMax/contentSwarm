"""The four fixed mini-program material folders and their access boundaries."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.material_library_service import _can_manage_item, serialize_item
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentMaterialCategory,
    ContentMaterialFolderSetting,
    ContentMaterialLibraryItem,
)
from yuxi.utils.datetime_utils import format_utc_datetime

Folder = Literal["rough", "generated", "uploads"]
FOLDER_NAMES = {"rough": "毛坯房图库", "generated": "生图图库", "uploads": "我的上传"}
FIXED_FOLDER_NAMES = {**FOLDER_NAMES, "works": "我的作品"}
PRIVATE_IDS = {"rough": "mp-rough-private", "uploads": "mp-uploads-private"}
ROUGH_NAMES = {"毛坯房图库", "毛胚房图库"}


async def fixed_folder_settings(db: AsyncSession) -> dict[str, ContentMaterialFolderSetting]:
    rows = await db.execute(select(ContentMaterialFolderSetting))
    return {setting.folder_key: setting for setting in rows.scalars()}


async def rename_fixed_folder(db: AsyncSession, user: User, folder: str, name: str) -> dict:
    if user.role not in {"admin", "superadmin"}:
        raise HTTPException(403, "只有管理员可编辑固定图库")
    if folder not in FIXED_FOLDER_NAMES:
        raise HTTPException(404, "固定图库不存在")
    normalized = name.strip()
    if not normalized or len(normalized) > 80:
        raise HTTPException(422, "图库名称必须为 1 至 80 个字符")
    setting = (await fixed_folder_settings(db)).get(folder)
    if setting is not None and setting.deleted_at is not None:
        raise HTTPException(404, "固定图库不存在")
    if setting is None:
        setting = ContentMaterialFolderSetting(folder_key=folder, name=normalized)
        db.add(setting)
    else:
        setting.name = normalized
    setting.updated_by = str(user.uid)
    await db.commit()
    return {"id": folder, "name": normalized}


async def delete_fixed_folder(db: AsyncSession, user: User, folder: str) -> dict:
    if user.role not in {"admin", "superadmin"}:
        raise HTTPException(403, "只有管理员可删除固定图库")
    if folder not in FIXED_FOLDER_NAMES:
        raise HTTPException(404, "固定图库不存在")
    setting = (await fixed_folder_settings(db)).get(folder)
    if setting is not None and setting.deleted_at is not None:
        raise HTTPException(404, "固定图库不存在")
    if setting is None:
        setting = ContentMaterialFolderSetting(folder_key=folder, name=FIXED_FOLDER_NAMES[folder])
        db.add(setting)
    setting.deleted_at = datetime.now(UTC).replace(tzinfo=None)
    setting.updated_by = str(user.uid)
    await db.commit()
    return {"success": True, "id": folder}


def _is_rough_category(category: ContentMaterialCategory) -> bool:
    return category.image_design_role == "rough" or category.name in ROUGH_NAMES


def _private_category_name(folder: str, categories: list[ContentMaterialCategory], owner: str) -> str:
    used = {c.name for c in categories if c.owner_uid == owner}
    name = FOLDER_NAMES[folder]
    if name not in used:
        return name
    name = f"{name}（个人）"
    if name not in used:
        return name
    return f"{name}-{PRIVATE_IDS[folder]}"


async def folder_categories(db: AsyncSession, user: User) -> dict[str, list[ContentMaterialCategory]]:
    """Keep upload destinations under the owner's PC personal materials."""
    from yuxi.image_design.save_targets import ensure_scope_root

    settings = await fixed_folder_settings(db)
    owner = str(user.uid)
    repo = MaterialLibraryRepository(db)
    categories = await repo.list_categories(owner, "image")
    values = []
    for folder, category_id in PRIVATE_IDS.items():
        if settings.get(folder) is not None and settings[folder].deleted_at is not None:
            continue
        if not any(
            c.owner_uid == owner
            and c.visibility == "private"
            and (c.id == category_id or (folder == "rough" and _is_rough_category(c)) or c.name == FOLDER_NAMES[folder])
            for c in categories
        ):
            values.append(
                dict(
                    owner_uid=owner,
                    id=category_id,
                    tenant_id=str(user.department_id) if getattr(user, "department_id", None) is not None else None,
                    material_type="image",
                    visibility="private",
                    parent_id=None,
                    industry_slug="uncategorized",
                    name=_private_category_name(folder, categories, owner),
                    description="个人素材上传",
                    sort_order=10,
                    is_system=True,
                )
            )
    if values:
        await repo.sync_system_categories(values)
        await db.flush()
        categories = await repo.list_categories(owner, "image")
    private_fixed_rough = next(
        (
            c
            for c in categories
            if c.owner_uid == owner
            and c.visibility == "private"
            and (c.id == PRIVATE_IDS["rough"] or _is_rough_category(c))
        ),
        None,
    )
    private_fixed_upload = next(
        (
            c
            for c in categories
            if c.owner_uid == owner
            and c.visibility == "private"
            and (c.id == PRIVATE_IDS["uploads"] or c.name == "我的上传")
        ),
        None,
    )
    return {
        "rough": [private_fixed_rough] if private_fixed_rough is not None else [],
        "generated": [await ensure_scope_root(db, user, "private")],
        "uploads": [private_fixed_upload] if private_fixed_upload is not None else [],
    }


async def upload_category(
    db: AsyncSession, user: User, folder: Literal["rough", "uploads"], channel: Literal["pc", "mp"]
) -> ContentMaterialCategory:
    setting = (await fixed_folder_settings(db)).get(folder)
    if setting is not None and setting.deleted_at is not None:
        raise HTTPException(404, "固定图库不存在")
    categories = (await folder_categories(db, user))[folder]
    return next(c for c in categories if c.visibility == "private")


def folder_source_filter(user: User, folder: Folder):
    """Share designated PC sources while keeping mini-program originals personal."""
    item = ContentMaterialLibraryItem
    asset = ContentCoverAsset
    category = ContentMaterialCategory
    owner_uid = str(user.uid)
    requester_tenant_id = getattr(user, "department_id", None)
    shared_with_requester = item.owner_uid == owner_uid
    if requester_tenant_id is not None:
        item_tenant_id = func.coalesce(item.tenant_id, asset.tenant_id, category.tenant_id)
        shared_with_requester = or_(
            shared_with_requester,
            item_tenant_id == str(requester_tenant_id),
        )
    channel = func.coalesce(
        item.metadata_json["source_channel"].as_string(),
        asset.metadata_json["source_channel"].as_string(),
        "",
    )
    source = func.coalesce(
        item.metadata_json["source_folder"].as_string(),
        asset.metadata_json["source_folder"].as_string(),
        "",
    )
    if folder == "generated":
        origin = item.metadata_json["source"].as_string()
        allowed = and_(
            shared_with_requester,
            or_(
                and_(channel == "pc", origin == "content_production"),
                and_(channel == "mp", origin == "image_design"),
            ),
        )
    else:
        allowed = or_(
            and_(channel == "pc", shared_with_requester),
            and_(channel == "mp", item.owner_uid == owner_uid),
        )
    return and_(category.visibility == "private", source == folder, allowed)


async def can_read_fixed_item(db: AsyncSession, user: User, item_id: str) -> bool:
    item = ContentMaterialLibraryItem
    asset = ContentCoverAsset
    category = ContentMaterialCategory
    join = item.__table__.join(asset, asset.id == item.asset_id).join(
        category, MaterialLibraryRepository.category_join()
    )
    filters = (
        item.id == item_id,
        item.material_type == "image",
        item.status == "enabled",
        item.deleted_at.is_(None),
        asset.deleted_at.is_(None),
        or_(*(folder_source_filter(user, folder) for folder in FOLDER_NAMES)),
    )
    return (await db.scalar(select(item.id).select_from(join).where(*filters).limit(1))) is not None


async def list_folder(
    db: AsyncSession,
    user: User,
    folder: Folder,
    *,
    page: int,
    page_size: int,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    setting = (await fixed_folder_settings(db)).get(folder)
    if setting is not None and setting.deleted_at is not None:
        raise HTTPException(404, "固定图库不存在")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    await folder_categories(db, user)
    item = ContentMaterialLibraryItem
    asset = ContentCoverAsset
    category = ContentMaterialCategory
    join = item.__table__.join(asset, asset.id == item.asset_id).join(
        category, MaterialLibraryRepository.category_join()
    )
    filters = (
        folder_source_filter(user, folder),
        item.material_type == "image",
        item.status == "enabled",
        item.deleted_at.is_(None),
        asset.deleted_at.is_(None),
        category.deleted_at.is_(None),
    )
    shanghai = timezone(timedelta(hours=8))
    if date_from:
        start_utc = datetime.combine(date_from, time.min, shanghai).astimezone(UTC).replace(tzinfo=None)
        filters += (asset.created_at >= start_utc,)
    if date_to:
        end_utc = datetime.combine(date_to + timedelta(days=1), time.min, shanghai).astimezone(UTC).replace(tzinfo=None)
        filters += (asset.created_at < end_utc,)
    total = (await db.execute(select(func.count(item.id)).select_from(join).where(*filters))).scalar_one()
    rows = (
        await db.execute(
            select(item, asset, category)
            .select_from(join)
            .where(*filters)
            .order_by(asset.created_at.desc(), item.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [
            {
                **serialize_item(row, source, group),
                "uploaded_at": format_utc_datetime(source.created_at),
                "can_manage": _can_manage_item(user, row, group),
                "file_url": f"/api/mp/content/gallery-items/{row.id}/file",
                "thumbnail_file_url": f"/api/mp/content/gallery-items/{row.id}/thumbnail",
            }
            for row, source, group in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def folder_counts(
    db: AsyncSession,
    user: User,
    *,
    client: Literal["mp", "pc"] = "mp",
) -> list[dict]:
    from yuxi.services.mp_service import visible_mp_works

    settings = await fixed_folder_settings(db)
    await folder_categories(db, user)
    result = []
    for folder, name in FOLDER_NAMES.items():
        setting = settings.get(folder)
        if setting is not None and setting.deleted_at is not None:
            continue
        listing = await list_folder(db, user, folder, page=1, page_size=1)
        first_item = next(iter(listing["items"]), None)
        if first_item and client == "pc":
            cover_thumbnail_file_url = f"/api/material-library/items/{first_item['id']}/thumbnail"
            cover_file_url = f"/api/material-library/items/{first_item['id']}/file"
        else:
            cover_thumbnail_file_url = first_item["thumbnail_file_url"] if first_item else None
            cover_file_url = first_item["file_url"] if first_item else None
        result.append(
            {
                "id": folder,
                "name": setting.name if setting is not None else name,
                "count": listing["total"],
                "can_upload": folder in PRIVATE_IDS,
                "cover_thumbnail_file_url": cover_thumbnail_file_url,
                "cover_file_url": cover_file_url,
            }
        )
    works_setting = settings.get("works")
    if works_setting is not None and works_setting.deleted_at is not None:
        await db.commit()
        return result
    works = await visible_mp_works(db, str(user.uid))
    first_work_id = works[0]["id"] if works else None
    work_file_url = None
    if first_work_id:
        work_file_url = (
            f"/api/content/covers/assets/{first_work_id}/file"
            if client == "pc"
            else f"/api/mp/image/works/{first_work_id}/file"
        )
    result.append(
        {
            "id": "works",
            "name": works_setting.name if works_setting is not None else "我的作品",
            "count": len(works),
            "can_upload": False,
            "cover_thumbnail_file_url": work_file_url,
            "cover_file_url": work_file_url,
        }
    )
    await db.commit()
    return result
