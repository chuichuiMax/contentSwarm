from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.services.material_library_service import (
    MaterialCategoryCreate,
    MaterialCategoryDelete,
    MaterialCategoryUpdate,
    MaterialItemUpdate,
    MaterialShareCreate,
    create_material_category,
    create_material_share,
    delete_material_category,
    delete_material_item,
    get_material_categories,
    get_material_file,
    get_material_thumbnail,
    get_public_material_share,
    get_public_material_share_card_cover,
    get_public_material_share_display_webp,
    get_public_material_share_image,
    import_material_images,
    list_image_galleries,
    list_material_items,
    render_public_material_share_page,
    serialize_public_material_share,
    update_material_category,
    update_material_item,
)
from yuxi.services.remote_material_library_service import (
    RemoteMaterialConfigUpdate,
    create_remote_material_sync_job,
    get_remote_material_config_state,
    get_remote_material_sync_job,
    verify_and_save_remote_material_config,
)
from yuxi.storage.postgres.models_business import User

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user, get_superadmin_user
from server.utils.public_url import request_public_base_url

material_library = APIRouter(prefix="/material-library", tags=["material-library"])
public_share_router = APIRouter(tags=["public-share"])


@material_library.get("/remote-config")
async def remote_material_config(
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_remote_material_config_state(db, current_user)


@material_library.put("/remote-config")
async def update_remote_material_config(
    payload: RemoteMaterialConfigUpdate,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    return await verify_and_save_remote_material_config(db, current_user, payload)


@material_library.get("/remote-sync/status")
async def remote_material_sync_status(
    job_id: str | None = Query(None, min_length=8, max_length=64),
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    del current_user
    return await get_remote_material_sync_job(db, job_id=job_id)


@material_library.post("/remote-sync", status_code=status.HTTP_202_ACCEPTED)
async def sync_remote_materials(
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_remote_material_sync_job(db, current_user)


@material_library.post("/images/import", status_code=status.HTTP_201_CREATED)
async def import_images(
    files: list[UploadFile] = File(...),
    category: str = Form(...),
    design_style: str | None = Form(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await import_material_images(
        db,
        current_user,
        files,
        category=category,
        design_style=design_style,
    )


@material_library.get("/categories")
async def material_categories(
    material_type: str = Query(...),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_material_categories(db, current_user, material_type)


@material_library.post("/categories", status_code=status.HTTP_201_CREATED)
async def add_material_category(
    payload: MaterialCategoryCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_material_category(db, current_user, payload)


@material_library.patch("/categories/{category_id}")
async def edit_material_category(
    category_id: str,
    payload: MaterialCategoryUpdate,
    material_type: str = Query(...),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_material_category(db, current_user, material_type, category_id, payload)


@material_library.delete("/categories/{category_id}")
async def remove_material_category(
    category_id: str,
    payload: MaterialCategoryDelete,
    material_type: str = Query(...),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_material_category(db, current_user, material_type, category_id, payload)


@material_library.get("/galleries")
async def image_galleries(
    industry_slug: str | None = Query(None, max_length=80),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_image_galleries(db, current_user, industry_slug=industry_slug)


@material_library.post("/shares", status_code=status.HTTP_201_CREATED)
async def create_share(
    payload: MaterialShareCreate,
    request: Request,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_material_share(
        db,
        current_user,
        payload,
        public_base_url=request_public_base_url(request),
    )


@material_library.get("/shares/{token}/page", response_class=HTMLResponse)
async def public_share_page(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    share, items = await get_public_material_share(db, token)
    return HTMLResponse(render_public_material_share_page(share, items, request_public_base_url(request)))


@public_share_router.get("/share/case/{token}", response_class=HTMLResponse)
async def canonical_public_share_page(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    share, items = await get_public_material_share(db, token)
    return HTMLResponse(render_public_material_share_page(share, items, request_public_base_url(request)))


@material_library.get("/shares/{token}")
async def public_share_data(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    share, items = await get_public_material_share(db, token)
    return serialize_public_material_share(share, items)


@material_library.get("/shares/{token}/images/{display_order}.webp")
async def public_share_display_image(
    token: str,
    display_order: int,
    db: AsyncSession = Depends(get_db),
):
    data = await get_public_material_share_display_webp(db, token, display_order)
    return Response(
        content=data,
        media_type="image/webp",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Disposition": f'inline; filename="share-{display_order}.webp"',
        },
    )


@material_library.get("/shares/{token}/images/{display_order}")
async def public_share_image(
    token: str,
    display_order: int,
    db: AsyncSession = Depends(get_db),
):
    data, content_type, file_name = await get_public_material_share_image(db, token, display_order)
    encoded_name = quote(file_name, safe="")
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Disposition": f"inline; filename*=UTF-8''{encoded_name}",
        },
    )


@material_library.get("/shares/{token}/cover.jpg")
async def public_share_card_cover(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    data = await get_public_material_share_card_cover(db, token)
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Disposition": 'inline; filename="share-cover.jpg"',
        },
    )


@material_library.get("/items")
async def material_items(
    material_type: str = Query(...),
    category: str | None = Query(None),
    item_status: str | None = Query(None, alias="status"),
    query: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    sort: str = Query("newest"),
    scope: Literal["private", "enterprise"] | None = Query(None),
    exclude_task_id: str | None = Query(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_material_items(
        db,
        current_user,
        material_type=material_type,
        category=category,
        status=item_status,
        query=query,
        page=page,
        page_size=page_size,
        sort=sort,
        scope=scope,
        exclude_task_id=exclude_task_id,
    )


@material_library.patch("/items/{item_id}")
async def edit_material_item(
    item_id: str,
    payload: MaterialItemUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_material_item(db, current_user, item_id, payload)


@material_library.get("/items/{item_id}/file")
async def material_item_file(
    item_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
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


@material_library.get("/items/{item_id}/thumbnail")
async def material_item_thumbnail(
    item_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    data, file_name = await get_material_thumbnail(db, current_user, item_id)
    encoded_name = quote(file_name, safe="")
    return Response(
        content=data,
        media_type="image/webp",
        headers={
            "Cache-Control": "private, max-age=86400",
            "Content-Disposition": f"inline; filename*=UTF-8''{encoded_name}.thumb.webp",
        },
    )


@material_library.delete("/items/{item_id}")
async def remove_material_item(
    item_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_material_item(db, current_user, item_id)
