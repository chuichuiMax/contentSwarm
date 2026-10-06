from __future__ import annotations

from datetime import date
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
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
from yuxi.services.employee_service import EmployeeMaterialOwner, resolve_employee_material_owner
from yuxi.services.personal_materials import (
    delete_fixed_folder,
    folder_counts,
    list_folder,
    rename_fixed_folder,
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


class FixedFolderRename(BaseModel):
    name: str = Field(min_length=1, max_length=80)


async def _material_owner(
    db: AsyncSession,
    current_user: User,
    employee_id: str | None,
) -> tuple[User, EmployeeMaterialOwner | None]:
    if not employee_id:
        return current_user, None
    await get_admin_user(current_user)
    target = await resolve_employee_material_owner(db, employee_id)
    return target.user, target


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
    employee_id: str | None = Query(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    owner_user, target = await _material_owner(db, current_user, employee_id)
    return await import_material_images(
        db,
        owner_user,
        files,
        category=category,
        design_style=design_style,
        actor_user=current_user,
        manage_target_private=target is not None,
    )


@material_library.get("/categories")
async def material_categories(
    material_type: str = Query(...),
    employee_id: str | None = Query(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    owner_user, target = await _material_owner(db, current_user, employee_id)
    response = await get_material_categories(
        db,
        owner_user,
        material_type,
        personal_folder="rough" if target is not None else None,
    )
    if target is not None:
        response["target_employee"] = target.to_dict()
    return response


@material_library.post("/categories", status_code=status.HTTP_201_CREATED)
async def add_material_category(
    payload: MaterialCategoryCreate,
    employee_id: str | None = Query(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    owner_user, target = await _material_owner(db, current_user, employee_id)
    return await create_material_category(
        db, owner_user, payload, actor_user=current_user, target_private=target is not None
    )


@material_library.patch("/categories/{category_id}")
async def edit_material_category(
    category_id: str,
    payload: MaterialCategoryUpdate,
    material_type: str = Query(...),
    employee_id: str | None = Query(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    owner_user, target = await _material_owner(db, current_user, employee_id)
    return await update_material_category(
        db,
        owner_user,
        material_type,
        category_id,
        payload,
        actor_user=current_user,
        target_private=target is not None,
    )


@material_library.delete("/categories/{category_id}")
async def remove_material_category(
    category_id: str,
    payload: MaterialCategoryDelete,
    material_type: str = Query(...),
    employee_id: str | None = Query(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    owner_user, target = await _material_owner(db, current_user, employee_id)
    return await delete_material_category(
        db,
        owner_user,
        material_type,
        category_id,
        payload,
        actor_user=current_user,
        target_private=target is not None,
    )


@material_library.get("/galleries")
async def image_galleries(
    industry_slug: str | None = Query(None, max_length=80),
    employee_id: str | None = Query(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    owner_user, target = await _material_owner(db, current_user, employee_id)
    return await list_image_galleries(
        db,
        owner_user,
        industry_slug=industry_slug,
        personal_folder="rough" if target is not None else None,
    )


@material_library.get("/my-materials/folders")
async def personal_material_folders(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return {"folders": await folder_counts(db, current_user, client="pc")}


@material_library.patch("/my-materials/folders/{folder}")
async def edit_personal_material_folder(
    folder: str,
    payload: FixedFolderRename,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await rename_fixed_folder(db, current_user, folder, payload.name)


@material_library.delete("/my-materials/folders/{folder}")
async def remove_personal_material_folder(
    folder: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_fixed_folder(db, current_user, folder)


@material_library.get("/my-materials/{folder}")
async def personal_material_items(
    folder: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    result = await list_folder(
        db, current_user, folder, page=page, page_size=page_size, date_from=date_from, date_to=date_to
    )
    for item in result["items"]:
        if item.get("work_asset_id"):
            item["file_url"] = item["thumbnail_file_url"] = f"/api/content/covers/assets/{item['work_asset_id']}/file"
            continue
        item["file_url"] = f"/api/material-library/items/{item['id']}/file"
        item["thumbnail_file_url"] = f"/api/material-library/items/{item['id']}/thumbnail"
    return result


@material_library.post("/shares", status_code=status.HTTP_201_CREATED)
async def create_share(
    payload: MaterialShareCreate,
    request: Request,
    employee_id: str | None = Query(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    owner_user, target = await _material_owner(db, current_user, employee_id)
    return await create_material_share(
        db,
        owner_user,
        payload,
        public_base_url=request_public_base_url(request),
        actor_user=current_user,
        target_private=target is not None,
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
    employee_id: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    owner_user, target = await _material_owner(db, current_user, employee_id)
    return await list_material_items(
        db,
        owner_user,
        material_type=material_type,
        category=category,
        status=item_status,
        query=query,
        page=page,
        page_size=page_size,
        sort=sort,
        scope=scope,
        exclude_task_id=exclude_task_id,
        personal_folder="rough" if target is not None else None,
        date_from=date_from,
        date_to=date_to,
    )


@material_library.patch("/items/{item_id}")
async def edit_material_item(
    item_id: str,
    payload: MaterialItemUpdate,
    employee_id: str | None = Query(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    owner_user, target = await _material_owner(db, current_user, employee_id)
    return await update_material_item(
        db,
        owner_user,
        item_id,
        payload,
        actor_user=current_user,
        target_private=target is not None,
    )


@material_library.get("/items/{item_id}/file")
async def material_item_file(
    item_id: str,
    employee_id: str | None = Query(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    owner_user, target = await _material_owner(db, current_user, employee_id)
    data, content_type, file_name = await get_material_file(
        db,
        owner_user,
        item_id,
        target_private=target is not None,
    )
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
    employee_id: str | None = Query(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    owner_user, target = await _material_owner(db, current_user, employee_id)
    data, file_name = await get_material_thumbnail(
        db,
        owner_user,
        item_id,
        target_private=target is not None,
    )
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
    employee_id: str | None = Query(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    owner_user, target = await _material_owner(db, current_user, employee_id)
    return await delete_material_item(
        db,
        owner_user,
        item_id,
        actor_user=current_user,
        target_private=target is not None,
    )
