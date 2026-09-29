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

Folder = Literal["rough", "generated", "uploads"]
FOLDER_NAMES = {"rough": "毛坯房图库", "generated": "生图图库", "uploads": "我的上传"}
PRIVATE_IDS = {"rough": "mp-rough-private", "uploads": "mp-uploads-private"}
SHARED_UPLOAD_ID = "mp-uploads-shared"
SYSTEM_OWNER = "system:material-library"
ROUGH_NAMES = {"毛坯房图库", "毛胚房图库"}


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
    """Create only missing fixed categories; reuse the existing PC rough/result galleries."""
    owner = str(user.uid)
    repo = MaterialLibraryRepository(db, include_shared=True)
    categories = await repo.list_categories(owner, "image")
    rough = [
        c for c in categories
        if c.visibility == "enterprise" and c.parent_id is None
        and _is_rough_category(c)
    ]
    generated = [c for c in categories if c.visibility == "enterprise" and c.parent_id is None and c.name == "生图图库"]
    shared_uploads = [
        c for c in categories if c.visibility == "enterprise" and c.parent_id is None and c.name == "我的上传"
    ]
    if len(generated) > 1:
        raise HTTPException(409, "企业图库配置重复，请联系管理员")
    values = []
    for folder, category_id in PRIVATE_IDS.items():
        if not any(
            c.owner_uid == owner and c.visibility == "private"
            and (c.id == category_id or (folder == "rough" and _is_rough_category(c)) or c.name == FOLDER_NAMES[folder])
            for c in categories
        ):
            values.append(
                dict(
                    owner_uid=owner,
                    id=category_id,
                    tenant_id=None,
                    material_type="image",
                    visibility="private",
                    parent_id=None,
                    industry_slug="uncategorized",
                    name=_private_category_name(folder, categories, owner),
                    description="小程序个人上传",
                    sort_order=10,
                    is_system=True,
                )
            )
    for folder, category_id, present in (
        ("rough", "mp-rough-shared", rough),
        ("generated", "mp-generated-shared", generated),
        (
            "uploads",
            SHARED_UPLOAD_ID,
            shared_uploads,
        ),
    ):
        if not present:
            values.append(
                dict(
                    owner_uid=SYSTEM_OWNER,
                    id=category_id,
                    tenant_id=None,
                    material_type="image",
                    visibility="enterprise",
                    parent_id=None,
                    industry_slug="uncategorized",
                    image_design_role="rough" if folder == "rough" else None,
                    name=FOLDER_NAMES[folder],
                    description="企业共享素材",
                    sort_order=10,
                    is_system=True,
                )
            )
    if values:
        await repo.sync_system_categories(values)
        await db.flush()
        categories = await repo.list_categories(owner, "image")
        rough = [
            c
            for c in categories
            if c.visibility == "enterprise" and c.parent_id is None
            and _is_rough_category(c)
        ]
        generated = [
            c for c in categories if c.visibility == "enterprise" and c.parent_id is None and c.name == "生图图库"
        ]
        shared_uploads = [
            c for c in categories if c.visibility == "enterprise" and c.parent_id is None and c.name == "我的上传"
        ]
    private_rough = [
        c for c in categories
        if c.visibility == "private" and c.owner_uid == owner
        and _is_rough_category(c)
    ]
    private_fixed_rough = next(
        c for c in categories
        if c.owner_uid == owner and c.visibility == "private"
        and (c.id == PRIVATE_IDS["rough"] or _is_rough_category(c))
    )
    private_fixed_upload = next(
        c for c in categories
        if c.owner_uid == owner and c.visibility == "private"
        and (c.id == PRIVATE_IDS["uploads"] or c.name == "我的上传")
    )
    return {
        "rough": [*rough, *[c for c in private_rough if c.id != private_fixed_rough.id], private_fixed_rough],
        "generated": generated,
        "uploads": [
            *shared_uploads,
            private_fixed_upload,
        ],
    }


async def upload_category(
    db: AsyncSession, user: User, folder: Literal["rough", "uploads"], channel: Literal["pc", "mp"]
) -> ContentMaterialCategory:
    categories = (await folder_categories(db, user))[folder]
    return next(c for c in categories if c.visibility == ("enterprise" if channel == "pc" else "private"))


async def list_folder(
    db: AsyncSession, user: User, folder: Folder, *, page: int, page_size: int,
    date_from: date | None = None, date_to: date | None = None,
) -> dict:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    categories = (await folder_categories(db, user))[folder]
    item = ContentMaterialLibraryItem
    asset = ContentCoverAsset
    category = ContentMaterialCategory
    join = item.__table__.join(asset, asset.id == item.asset_id).join(
        category, MaterialLibraryRepository.category_join()
    )
    locations = or_(
        *(
            and_(item.category == c.id, func.coalesce(item.category_owner_uid, item.owner_uid) == c.owner_uid)
            for c in categories
        )
    )
    if folder == "uploads":
        # Legacy uploads had no channel marker. Shared ones remain shared; private
        # ones are included for their owner only, never promoted by inference.
        other_uploads = and_(
            asset.role == "library_image",
            or_(category.image_design_role.is_(None), category.image_design_role != "rough"),
            category.name.notin_({"生图图库", "AI生图图库", "毛坯房图库", "毛胚房图库"}),
            func.coalesce(item.metadata_json["source"].as_string(), "") != "image_design",
            or_(
                and_(category.visibility == "enterprise", category.name.in_({"我的上传", "我的图库", "未分类"})),
                and_(category.visibility == "private", item.owner_uid == str(user.uid)),
            ),
        )
        locations = or_(locations, other_uploads)
    filters = (
        locations,
        item.material_type == "image",
        item.status == "enabled",
        item.deleted_at.is_(None),
        asset.deleted_at.is_(None),
        category.deleted_at.is_(None),
        or_(category.visibility == "enterprise", item.owner_uid == str(user.uid)),
    )
    shanghai = timezone(timedelta(hours=8))
    if date_from:
        start_utc = datetime.combine(date_from, time.min, shanghai).astimezone(UTC).replace(tzinfo=None)
        filters += (asset.created_at >= start_utc,)
    if date_to:
        end_utc = (
            datetime.combine(date_to + timedelta(days=1), time.min, shanghai)
            .astimezone(UTC)
            .replace(tzinfo=None)
        )
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


async def folder_counts(db: AsyncSession, user: User) -> list[dict]:
    from yuxi.services.mp_service import visible_mp_works

    await folder_categories(db, user)
    result = []
    for folder, name in FOLDER_NAMES.items():
        listing = await list_folder(db, user, folder, page=1, page_size=1)
        result.append({"id": folder, "name": name, "count": listing["total"], "can_upload": folder in PRIVATE_IDS})
    works_count = len(await visible_mp_works(db, str(user.uid)))
    result.append({"id": "works", "name": "我的作品", "count": works_count, "can_upload": False})
    await db.commit()
    return result
