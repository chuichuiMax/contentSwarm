"""对外集成：素材库图片只读访问（API Key / JWT）。"""

from __future__ import annotations

import os
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.material_library_service import (
    _get_material_item_for_read,
    _owner_uid,
    get_material_thumbnail_presigned_url,
    list_material_items,
    serialize_item,
)
from yuxi.services.material_upload_queue import material_file_presigned_url, material_thumbnail_presigned_url
from yuxi.storage.postgres.models_business import User


def integration_presign_days() -> int:
    return max(1, min(7, int(os.getenv("MATERIAL_INTEGRATION_PRESIGN_DAYS", "1"))))


def _integration_api_urls(public_base_url: str, item_id: str) -> dict[str, str]:
    base = public_base_url.rstrip("/")
    prefix = f"{base}/api/integrations/material-library/items/{item_id}"
    return {
        "api_file_url": f"{prefix}/file",
        "api_thumbnail_url": f"{prefix}/thumbnail",
    }


async def _presign_work_asset(
    db: AsyncSession, user: User, asset_id: str, *, days: int
) -> tuple[str | None, str | None]:
    asset = await ContentCoverRepository(db).get_asset_for_user(
        asset_id, _owner_uid(user), allow_material_use=True
    )
    if asset is None:
        return None, None
    file_url = await material_file_presigned_url(asset, days=days)
    thumb_url = await material_thumbnail_presigned_url(asset, days=days) or file_url
    return file_url, thumb_url


async def attach_material_integration_urls(
    db: AsyncSession,
    user: User,
    item: dict[str, Any],
    *,
    public_base_url: str,
    presign_days: int | None = None,
) -> dict[str, Any]:
    days = presign_days if presign_days is not None else integration_presign_days()
    enriched = {**item, **_integration_api_urls(public_base_url, str(item["id"]))}
    work_asset_id = item.get("work_asset_id")
    if work_asset_id:
        file_url, thumb_url = await _presign_work_asset(db, user, str(work_asset_id), days=days)
        enriched["direct_file_url"] = file_url
        enriched["direct_thumbnail_url"] = thumb_url
        return enriched

    repo = MaterialLibraryRepository(db, include_shared=True)
    ml_item = await _get_material_item_for_read(db, repo, user, str(item["id"]))
    if ml_item is None:
        enriched["direct_file_url"] = None
        enriched["direct_thumbnail_url"] = None
        return enriched
    asset = await repo.get_asset(ml_item.asset_id, ml_item.owner_uid)
    if asset is None:
        enriched["direct_file_url"] = None
        enriched["direct_thumbnail_url"] = None
        return enriched
    enriched["direct_file_url"] = await material_file_presigned_url(asset, days=days)
    enriched["direct_thumbnail_url"] = await material_thumbnail_presigned_url(asset, days=days)
    return enriched


async def get_material_integration_item(
    db: AsyncSession,
    user: User,
    item_id: str,
    *,
    public_base_url: str,
    presign_days: int | None = None,
) -> dict[str, Any]:
    repo = MaterialLibraryRepository(db, include_shared=True)
    ml_item = await _get_material_item_for_read(db, repo, user, item_id)
    if ml_item is None:
        from yuxi.services.material_library_service import _error

        raise _error(404, "MATERIAL_NOT_FOUND", "素材不存在")
    asset = await repo.get_asset(ml_item.asset_id, ml_item.owner_uid)
    if asset is None:
        from yuxi.services.material_library_service import _error

        raise _error(404, "MATERIAL_ASSET_MISSING", "素材文件不存在")
    category = await repo.get_category(
        ml_item.category_owner_uid or ml_item.owner_uid,
        ml_item.material_type,
        ml_item.category,
    )
    if category is None:
        from yuxi.services.material_library_service import _error

        raise _error(404, "MATERIAL_NOT_FOUND", "素材不存在")
    payload = serialize_item(ml_item, asset, category)
    return await attach_material_integration_urls(
        db, user, payload, public_base_url=public_base_url, presign_days=presign_days
    )


async def resolve_material_integration_file_redirect(
    db: AsyncSession,
    user: User,
    item_id: str,
    *,
    presign_days: int | None = None,
) -> str | None:
    days = presign_days if presign_days is not None else integration_presign_days()
    repo = MaterialLibraryRepository(db, include_shared=True)
    ml_item = await _get_material_item_for_read(db, repo, user, item_id)
    if ml_item is None:
        return None
    asset = await repo.get_asset(ml_item.asset_id, ml_item.owner_uid)
    if asset is None:
        return None
    return await material_file_presigned_url(asset, days=days)


async def resolve_material_integration_thumbnail_redirect(
    db: AsyncSession,
    user: User,
    item_id: str,
    *,
    presign_days: int | None = None,
) -> str | None:
    days = presign_days if presign_days is not None else integration_presign_days()
    return await get_material_thumbnail_presigned_url(db, user, item_id, presign_days=days)


async def list_material_integration_items(
    db: AsyncSession,
    user: User,
    *,
    material_type: Literal["image", "cover_template"],
    category: str | None,
    item_status: str | None,
    query: str | None,
    page: int,
    page_size: int,
    sort: str,
    scope: Literal["private", "enterprise"] | None,
    public_base_url: str,
) -> dict[str, Any]:
    result = await list_material_items(
        db,
        user,
        material_type=material_type,
        category=category,
        status=item_status,
        query=query,
        page=page,
        page_size=page_size,
        sort=sort,
        scope=scope,
    )
    items = []
    for raw in result.get("items") or []:
        items.append(await attach_material_integration_urls(db, user, raw, public_base_url=public_base_url))
    result["items"] = items
    return result
