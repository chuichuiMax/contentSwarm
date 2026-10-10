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
    ContentCoverJob,
    ContentTask,
    ImageDesignJob,
)
from yuxi.utils.datetime_utils import format_utc_datetime
from yuxi.services.material_library_categories import PERSONAL_IMAGE_FOLDERS, personal_folder_key
from yuxi.services.personal_gallery_settings import (
    load_personal_gallery_settings, lock_personal_gallery_settings,
    personal_gallery_fields, require_personal_gallery,
)
from yuxi.utils.datetime_utils import utc_now_naive


async def rename_fixed_folder(db: AsyncSession, user: User, folder: str, name: str) -> dict:
    from yuxi.services.material_library_service import MaterialCategoryUpdate
    return await update_personal_gallery(db, user, folder, MaterialCategoryUpdate(name=name))


async def delete_fixed_folder(db: AsyncSession, user: User, folder: str) -> dict:
    from yuxi.services.material_library_service import MaterialCategoryDelete
    return await delete_personal_gallery(db, user, user, folder, MaterialCategoryDelete())


async def update_personal_gallery(db, actor, folder, payload) -> dict:
    from yuxi.services.material_library_service import _audit, _validate_industry_slug

    if actor.role != "superadmin":
        raise HTTPException(403, "只有超管可修改全员个人图库")
    settings = await lock_personal_gallery_settings(db)
    setting = require_personal_gallery(settings, folder)
    changes = payload.model_dump(exclude_unset=True)
    if any(changes.get(field) is None for field in ("name", "description", "industry_slug") if field in changes):
        raise HTTPException(422, "图库名称、说明和行业不能为 null")
    if changes.get("visibility", "private") != "private":
        raise HTTPException(422, "预设个人图库不能改变图片可见范围")
    if any(changes.get(field) for field in ("design_style", "building_name", "area")):
        raise HTTPException(422, "预设个人图库不支持二级案例图库字段")
    if "name" in changes:
        name = changes["name"].strip()
        if any(row.folder_key != folder and row.deleted_at is None and row.name.casefold() == name.casefold()
               for row in settings.values()):
            raise HTTPException(409, "预设图库名称已存在")
        setting.name = name
    if "description" in changes:
        setting.description = changes["description"].strip()
    if "industry_slug" in changes:
        setting.industry_slug = await _validate_industry_slug(db, changes["industry_slug"]) or "uncategorized"
    setting.updated_by = str(actor.uid)
    setting.updated_at = utc_now_naive()
    _audit(db, actor, "material.personal_gallery.update", folder=folder, changes=changes)
    await db.flush()
    return {"folder": folder, "name": setting.name, "description": setting.description,
            "industry_slug": setting.industry_slug}


async def delete_personal_gallery(db, actor, owner_user, folder, payload) -> dict:
    from yuxi.services.material_library_service import _audit, create_library_item_for_asset
    from yuxi.image_design.save_targets import ensure_scope_root

    if actor.role != "superadmin":
        raise HTTPException(403, "只有超管可删除全员个人图库")
    settings = await lock_personal_gallery_settings(db)
    setting = require_personal_gallery(settings, folder)
    repo = MaterialLibraryRepository(db, include_shared=True)
    selected_target = await repo.get_category(str(owner_user.uid), "image", payload.target_category_id) \
        if payload.target_category_id else None
    target_key = personal_folder_key(selected_target) if selected_target else None
    if payload.target_category_id and (selected_target is None or selected_target.visibility != "private"):
        raise HTTPException(422, "全员个人图库的素材只能迁移到个人图库")
    if target_key == folder:
        raise HTTPException(422, "迁移目标不能是当前图库")
    if target_key:
        require_personal_gallery(settings, target_key)
    elif selected_target and not selected_target.is_global_personal:
        raise HTTPException(422, "全员操作请选择预设图库或全员可见的个人图库作为迁移目标")

    categories = list(await db.scalars(select(ContentMaterialCategory).where(
        ContentMaterialCategory.material_type == "image",
        ContentMaterialCategory.visibility == "private",
        ContentMaterialCategory.deleted_at.is_(None),
    ).order_by(ContentMaterialCategory.owner_uid, ContentMaterialCategory.id).with_for_update()))
    sources = [row for row in categories if personal_folder_key(row) == folder]
    # Older accounts may have task-only works/root saves without an entity folder yet.
    historical_owners = set()
    if folder == "works":
        jobs = await db.scalars(select(ContentCoverJob).where(
            ContentCoverJob.status == "succeeded", ContentCoverJob.content_task_id.is_not(None)))
        historical_owners = {job.owner_uid for job in jobs if (job.result_json or {}).get("asset_ids")}
    elif folder == "generated":
        rows = await db.scalars(select(ContentMaterialLibraryItem).join(
            ContentMaterialCategory, repo.category_join()
        ).where(ContentMaterialCategory.visibility == "private",
                ContentMaterialCategory.id.in_(["private-root", "uncategorized"]),
                ContentMaterialLibraryItem.deleted_at.is_(None)))
        historical_owners = {row.owner_uid for row in rows if
            (row.metadata_json or {}).get("source_folder") == "generated" or
            (row.metadata_json or {}).get("source") in {"image_design", "content_production"}}
    for owner_uid in sorted(historical_owners - {row.owner_uid for row in sources}):
        source = (await folder_categories(db, User(uid=owner_uid)))[folder][0]
        sources.append(source)
    by_owner = {}
    for source in sources:
        if source.owner_uid in by_owner:
            raise HTTPException(409, "个人图库存在多个历史候选，请先核对归属")
        if any(c.owner_uid == source.owner_uid and c.parent_id == source.id for c in categories):
            raise HTTPException(409, "一级图库仍有二级图库，请先移动或删除二级图库")
        by_owner[source.owner_uid] = source
    if folder == "generated":
        jobs = list(await db.scalars(select(ImageDesignJob).where(
            ImageDesignJob.status.in_(["queued", "running"]))))
        for job in jobs:
            target = (job.request_json or {}).get("requested_save_target") or {}
            source = by_owner.get(job.owner_uid)
            if target.get("scope") == "private" and target.get("gallery_id") in {
                None, "private-root", source.id if source else PERSONAL_IMAGE_FOLDERS["generated"][0]
            }:
                raise HTTPException(409, "图库有正在生成的图片，请任务完成后再删除")
        pending_tasks = await db.scalars(select(ContentTask).join(
            ContentCoverJob, ContentCoverJob.content_task_id == ContentTask.id
        ).where(ContentCoverJob.status.in_(["queued", "running"])))
        for task in pending_tasks:
            form_values = (task.brief_json or {}).get("form_values") or {}
            if not (form_values.get("mp_content_code") or form_values.get("mp_service_entry")):
                raise HTTPException(409, "图库有正在生成的封面，请任务完成后再删除")

    moved = 0
    for source in sources:
        owner = User(uid=source.owner_uid, department_id=source.tenant_id, role="user")
        if target_key:
            target = (await folder_categories(db, owner))[target_key][0]
        else:
            target = selected_target or await ensure_scope_root(db, owner, "private")
        # Include historical root saves and task-only works, just as the card does.
        listing = await list_folder(db, owner, folder, page=1, page_size=1, status=None)
        asset_ids = set()
        for page in range(1, (listing["total"] + 99) // 100 + 1):
            rows = await list_folder(db, owner, folder, page=page, page_size=100, status=None)
            asset_ids.update(row["asset_id"] for row in rows["items"])
        asset_ids.update(item.asset_id for item in await repo.category_items(source) if item.deleted_at is None)
        for asset_id in sorted(asset_ids):
            asset = await repo.get_asset(asset_id, source.owner_uid)
            if asset is None or asset.deleted_at is not None:
                raise HTTPException(409, "图库素材资产缺失，请核对后再删除")
            item = await repo.get_item_by_asset(asset_id)
            if item is not None and item.owner_uid != source.owner_uid:
                raise HTTPException(409, "图库素材归属不一致，请核对后再删除")
            if folder == "works" and item is not None and (
                item.category != source.id or (item.category_owner_uid or item.owner_uid) != source.owner_uid
            ):
                # A work already filed in another gallery remains there; do not move it twice.
                continue
            if item is None:
                item = await create_library_item_for_asset(db, asset=asset, material_type="image",
                    name=asset.original_file_name, category=target.id, category_owner_uid=target.owner_uid)
            else:
                item.category = target.id
                item.category_owner_uid = target.owner_uid
            # Uploader ownership and all original file/asset IDs stay unchanged.
            moved += 1
        source.deleted_at = utc_now_naive()
    setting.deleted_at = utc_now_naive()
    setting.updated_at = setting.deleted_at
    setting.updated_by = str(actor.uid)
    _audit(db, actor, "material.personal_gallery.delete", folder=folder,
           target_category_id=payload.target_category_id, moved=moved)
    await db.flush()
    return {"success": True, "id": PERSONAL_IMAGE_FOLDERS[folder][0], "moved": moved,
            "target_category_id": payload.target_category_id, "global": True}


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
    settings = await load_personal_gallery_settings(db)
    repo = MaterialLibraryRepository(db)
    categories = await repo.list_categories(owner, "image")
    result = {key: [] for key in PERSONAL_IMAGE_FOLDERS}
    for category in categories:
        key = personal_folder_key(category)
        if key and (key not in settings or settings[key].deleted_at is None):
            result[key].append(category)
    values = []
    for index, (key, (category_id, name)) in enumerate(PERSONAL_IMAGE_FOLDERS.items()):
        if key in settings and settings[key].deleted_at is not None:
            continue
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
        result = {key: [c for c in categories if personal_folder_key(c) == key]
                  if key not in settings or settings[key].deleted_at is None else [] for key in PERSONAL_IMAGE_FOLDERS}
    return result


async def upload_category(
    db: AsyncSession, user: User, folder: Literal["rough", "uploads"], channel: Literal["pc", "mp"]
) -> ContentMaterialCategory:
    require_personal_gallery(await load_personal_gallery_settings(db), folder)
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
    settings = await load_personal_gallery_settings(db)
    if folder in PERSONAL_IMAGE_FOLDERS:
        require_personal_gallery(settings, folder)
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
            **serialize_item(row, source, group, personal_settings=settings),
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
    settings = await load_personal_gallery_settings(db)
    categories = await MaterialLibraryRepository(db, include_shared=True).list_categories(str(user.uid), "image")
    galleries = [values[0] for values in mapping.values() if values]
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
                **personal_gallery_fields(gallery, settings),
                "count": listing["total"],
                "can_upload": key in {"rough", "uploads"} or key is None,
                "cover_thumbnail_file_url": first["thumbnail_file_url"] if first else None,
                "cover_file_url": first["file_url"] if first else None,
            }
        )
    await db.commit()
    return result
