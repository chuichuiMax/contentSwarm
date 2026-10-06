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
    ContentMaterialLibraryItem,
)
from yuxi.utils.datetime_utils import format_utc_datetime
from yuxi.services.material_library_categories import PERSONAL_IMAGE_FOLDERS, personal_folder_key


async def rename_fixed_folder(db: AsyncSession, user: User, folder: str, name: str) -> dict:
    if user.role not in {"admin", "superadmin"}:
        raise HTTPException(403, "只有管理员可编辑固定图库")
    if folder not in PERSONAL_IMAGE_FOLDERS:
        raise HTTPException(404, "固定图库不存在")
    raise HTTPException(409, "预设个人图库名称不能修改")


async def delete_fixed_folder(db: AsyncSession, user: User, folder: str) -> dict:
    if user.role not in {"admin", "superadmin"}:
        raise HTTPException(403, "只有管理员可删除固定图库")
    if folder not in PERSONAL_IMAGE_FOLDERS:
        raise HTTPException(404, "固定图库不存在")
    raise HTTPException(409, "预设个人图库不能删除")


def _private_category_name(folder: str, categories: list[ContentMaterialCategory], owner: str) -> str:
    used = {c.name for c in categories if c.owner_uid == owner}
    name = PERSONAL_IMAGE_FOLDERS[folder][1]
    if name not in used:
        return name
    name = f"{name}（个人）"
    if name not in used:
        return name
    return f"{name}-{PERSONAL_IMAGE_FOLDERS[folder][0]}"


async def folder_categories(db: AsyncSession, user: User) -> dict[str, list[ContentMaterialCategory]]:
    """Reuse the user's existing real galleries, provisioning only missing folders."""
    owner = str(user.uid)
    repo = MaterialLibraryRepository(db)
    categories = await repo.list_categories(owner, "image")
    result = {key: [] for key in PERSONAL_IMAGE_FOLDERS}
    for category in categories:
        key = personal_folder_key(category)
        if key:
            result[key].append(category)
    values = []
    for index, (key, (category_id, name)) in enumerate(PERSONAL_IMAGE_FOLDERS.items()):
        if len(result[key]) > 1:
            raise HTTPException(409, f"个人图库 {name} 存在多个历史候选，请先核对归属")
        if result[key]:
            continue
        values.append(
            dict(
                owner_uid=owner,
                id=category_id,
                material_type="image",
                visibility="private",
                tenant_id=str(user.department_id) if getattr(user, "department_id", None) is not None else None,
                parent_id=None,
                industry_slug="decoration",
                name=_private_category_name(key, categories, owner),
                description="",
                sort_order=index * 10,
                is_system=True,
            )
        )
    if values:
        await repo.sync_system_categories(values)
        categories = await repo.list_categories(owner, "image")
        result = {key: [c for c in categories if personal_folder_key(c) == key] for key in PERSONAL_IMAGE_FOLDERS}
    return result


async def upload_category(
    db: AsyncSession, user: User, folder: Literal["rough", "uploads"], channel: Literal["pc", "mp"]
) -> ContentMaterialCategory:
    return (await folder_categories(db, user))[folder][0]


def folder_source_filter(user: User, folder: str):
    """Private galleries and historical root saves belong to their uploader only."""
    item = ContentMaterialLibraryItem
    category = ContentMaterialCategory
    category_id, name = PERSONAL_IMAGE_FOLDERS[folder]
    names = {name, f"{name}（个人）"}
    if folder == "generated":
        names.add("生图图库")
    if folder == "rough":
        names.add("毛胚房图库")
    destination = or_(category.id == category_id, category.name.in_(names))
    if folder == "generated":
        destination = or_(
            destination,
            and_(
                category.id.in_(["private-root", "uncategorized"]),
                or_(
                    item.metadata_json["source_folder"].as_string() == "generated",
                    item.metadata_json["source"].as_string().in_(["image_design", "content_production"]),
                ),
            ),
        )
    return and_(category.visibility == "private", item.owner_uid == str(user.uid), destination)


async def can_read_fixed_item(db: AsyncSession, user: User, item_id: str) -> bool:
    return (
        await MaterialLibraryRepository(db, include_shared=True).get_item_for_user(item_id, str(user.uid)) is not None
    )


async def list_folder(
    db: AsyncSession,
    user: User,
    folder: str,
    *,
    page: int,
    page_size: int,
    date_from: date | None = None,
    date_to: date | None = None,
    query_text: str | None = None,
    sort: str = "newest",
    status: str | None = "enabled",
) -> dict:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    mapping = await folder_categories(db, user)
    repo = MaterialLibraryRepository(db, include_shared=True)
    categories = await repo.list_categories(str(user.uid), "image")
    own = next(
        (c for c in categories if c.id == folder and c.owner_uid == str(user.uid) and c.visibility == "private"), None
    )
    gallery = (
        mapping[folder][0]
        if folder in mapping
        else own
        or next((c for c in categories if c.id == folder and c.visibility == "private" and c.is_global_personal), None)
    )
    if gallery is None:
        raise HTTPException(404, "个人图库不存在")
    key = personal_folder_key(gallery)
    item, asset, category = ContentMaterialLibraryItem, ContentCoverAsset, ContentMaterialCategory
    destination = and_(item.category == gallery.id, category.owner_uid == gallery.owner_uid)
    if key == "generated":
        destination = or_(
            destination,
            and_(
                category.owner_uid == str(user.uid),
                category.id.in_(["private-root", "uncategorized"]),
                folder_source_filter(user, "generated"),
            ),
        )
    filters = [
        repo.item_access(str(user.uid)),
        destination,
        category.visibility == "private",
        item.material_type == "image",
        item.deleted_at.is_(None),
        asset.deleted_at.is_(None),
    ]
    if status:
        filters.append(item.status == status)
    shanghai = timezone(timedelta(hours=8))
    if date_from:
        filters.append(
            asset.created_at >= datetime.combine(date_from, time.min, shanghai).astimezone(UTC).replace(tzinfo=None)
        )
    if date_to:
        filters.append(
            asset.created_at
            < datetime.combine(date_to + timedelta(days=1), time.min, shanghai).astimezone(UTC).replace(tzinfo=None)
        )
    if key == "works":
        filters.append(asset.hidden_from_works_at.is_(None))
    elif query_text:
        filters.append(item.display_name.ilike(f"%{query_text.strip()}%"))
    join = item.__table__.join(asset, asset.id == item.asset_id).join(category, repo.category_join())
    order = {"newest": asset.created_at.desc(), "oldest": asset.created_at.asc(), "name": item.display_name.asc()}[sort]
    query = select(item, asset, category).select_from(join).where(*filters).order_by(order, item.id.desc())
    total = int(await db.scalar(select(func.count(item.id)).select_from(join).where(*filters)))
    if key != "works":
        query = query.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(query)).all()
    items = [
        {
            **serialize_item(row, source, group),
            "uploaded_at": format_utc_datetime(source.created_at),
            "can_manage": _can_manage_item(user, row, group),
            "file_url": f"/api/mp/content/gallery-items/{row.id}/file",
            "thumbnail_file_url": f"/api/mp/content/gallery-items/{row.id}/thumbnail",
        }
        for row, source, group in rows
    ]
    if key == "works":
        from yuxi.services.mp_service import visible_mp_works

        seen = {row["asset_id"] for row in items}
        for work in await visible_mp_works(db, str(user.uid)) if status in {None, "enabled"} else []:
            uploaded = work["uploaded_at"].replace(tzinfo=UTC).astimezone(shanghai).date()
            if work["id"] in seen:
                existing = next(row for row in items if row["asset_id"] == work["id"])
                existing.update(
                    work_asset_id=work["id"],
                    can_manage=False,
                    file_url=f"/api/mp/image/works/{work['id']}/file",
                    thumbnail_file_url=f"/api/mp/image/works/{work['id']}/file",
                )
                continue
            if (date_from and uploaded < date_from) or (date_to and uploaded > date_to):
                continue
            items.append(
                {
                    **work,
                    "asset_id": work["id"],
                    "work_asset_id": work["id"],
                    "name": f"作品 {work['id'][:8]}",
                    "uploaded_at": format_utc_datetime(work["uploaded_at"]),
                    "created_at": format_utc_datetime(work["created_at"]),
                    "can_manage": False,
                    "file_url": f"/api/mp/image/works/{work['id']}/file",
                    "thumbnail_file_url": f"/api/mp/image/works/{work['id']}/file",
                }
            )
        if query_text:
            items = [row for row in items if query_text.strip().casefold() in row["name"].casefold()]
        items.sort(key=lambda row: row["name"] if sort == "name" else row["uploaded_at"], reverse=sort == "newest")
        total = len(items)
        items = items[(page - 1) * page_size : page * page_size]
    return {"items": items, "total": total, "page": page, "page_size": page_size, "gallery_id": gallery.id}


async def folder_counts(db: AsyncSession, user: User, *, client: Literal["pc", "mp"] = "mp") -> list[dict]:
    mapping = await folder_categories(db, user)
    categories = await MaterialLibraryRepository(db, include_shared=True).list_categories(str(user.uid), "image")
    galleries = [values[0] for values in mapping.values()]
    galleries += [
        c
        for c in categories
        if c.visibility == "private" and not c.parent_id and c.is_global_personal and not personal_folder_key(c)
    ]
    result = []
    for gallery in galleries:
        key = personal_folder_key(gallery)
        listing = await list_folder(db, user, key or gallery.id, page=1, page_size=1)
        first = next(iter(listing["items"]), None)
        if first and client == "pc":
            if first.get("work_asset_id"):
                first["file_url"] = first["thumbnail_file_url"] = (
                    f"/api/content/covers/assets/{first['work_asset_id']}/file"
                )
            else:
                first["file_url"] = f"/api/material-library/items/{first['id']}/file"
                first["thumbnail_file_url"] = f"/api/material-library/items/{first['id']}/thumbnail"
        result.append(
            {
                "id": key or gallery.id,
                "gallery_id": gallery.id,
                "owner_uid": gallery.owner_uid,
                "name": PERSONAL_IMAGE_FOLDERS[key][1] if key else gallery.name,
                "count": listing["total"],
                "can_upload": key in {"rough", "uploads"} or key is None,
                "cover_thumbnail_file_url": first["thumbnail_file_url"] if first else None,
                "cover_file_url": first["file_url"] if first else None,
            }
        )
    await db.commit()
    return result
