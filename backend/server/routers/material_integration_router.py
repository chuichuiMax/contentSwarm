from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from server.utils.public_url import request_public_base_url
from yuxi.services.material_integration_service import (
    get_material_integration_item,
    integration_presign_days,
    list_material_integration_items,
    resolve_material_integration_file_redirect,
    resolve_material_integration_thumbnail_redirect,
)
from yuxi.services.material_library_service import get_material_file, get_material_thumbnail
from yuxi.storage.postgres.models_business import User

material_integration = APIRouter(prefix="/integrations/material-library", tags=["material-integration"])


@material_integration.get("/items")
async def integration_material_items(
    request: Request,
    material_type: Literal["image", "cover_template"] = Query(...),
    category: str | None = Query(None),
    item_status: str | None = Query(None, alias="status"),
    query: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    sort: Literal["newest", "oldest", "name"] = Query("newest"),
    scope: Literal["private", "enterprise"] | None = Query(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_material_integration_items(
        db,
        current_user,
        material_type=material_type,
        category=category,
        item_status=item_status,
        query=query,
        page=page,
        page_size=page_size,
        sort=sort,
        scope=scope,
        public_base_url=request_public_base_url(request),
    )


@material_integration.get("/items/{item_id}")
async def integration_material_item(
    item_id: str,
    request: Request,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_material_integration_item(
        db,
        current_user,
        item_id,
        public_base_url=request_public_base_url(request),
    )


@material_integration.get("/items/{item_id}/file")
async def integration_material_item_file(
    item_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    redirect_url = await resolve_material_integration_file_redirect(db, current_user, item_id)
    if redirect_url:
        return RedirectResponse(
            redirect_url,
            status_code=302,
            headers={"Cache-Control": f"private, max-age={min(integration_presign_days(), 1) * 3600}"},
        )
    data, content_type, file_name = await get_material_file(db, current_user, item_id)
    encoded_name = quote(file_name, safe="")
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "private, no-cache",
            "Content-Disposition": f"inline; filename*=UTF-8''{encoded_name}",
        },
    )


@material_integration.get("/items/{item_id}/thumbnail")
async def integration_material_item_thumbnail(
    item_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    redirect_url = await resolve_material_integration_thumbnail_redirect(db, current_user, item_id)
    if redirect_url:
        return RedirectResponse(
            redirect_url,
            status_code=302,
            headers={"Cache-Control": f"private, max-age={min(integration_presign_days(), 1) * 3600}"},
        )
    data, file_name = await get_material_thumbnail(db, current_user, item_id)
    del file_name
    return Response(
        content=data,
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=86400"},
    )
