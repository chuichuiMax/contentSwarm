from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.image_design import mp_service
from yuxi.image_design.mp_schemas import MpDraftsUpdate, MpLibraryCreate, MpPolishCreate, MpTaskCreate
from yuxi.image_design.save_targets import list_mp_save_targets
from yuxi.services.mp_service import MpContext

from server.utils.auth_middleware import get_db, get_mp_context

mp_image_design = APIRouter(prefix="/mp/image-design", tags=["mp-image-design"])


@mp_image_design.get("/drafts")
async def get_drafts(ctx: MpContext = Depends(get_mp_context), db: AsyncSession = Depends(get_db)):
    return await mp_service.get_drafts(db, ctx.user)


@mp_image_design.put("/drafts")
async def save_drafts(
    payload: MpDraftsUpdate,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await mp_service.save_drafts(db, ctx.user, payload)


@mp_image_design.get("/save-targets")
async def save_targets(ctx: MpContext = Depends(get_mp_context), db: AsyncSession = Depends(get_db)):
    result = await list_mp_save_targets(db, ctx.user)
    await db.commit()
    return result


@mp_image_design.get("/library")
async def list_library(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await mp_service.list_library(db, ctx.user, page=page, page_size=page_size)


@mp_image_design.post("/library")
async def add_library_item(
    payload: MpLibraryCreate,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await mp_service.add_library_item(db, ctx.user, payload)


@mp_image_design.post("/uploads")
async def upload_image(
    file: UploadFile = File(...),
    role: Literal["source", "reference", "rough"] = Form(...),
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await mp_service.upload_image(db, ctx.user, file, role=role)


@mp_image_design.delete("/library/{item_id}")
async def remove_library_item(
    item_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await mp_service.remove_library_item(db, ctx.user, item_id)


@mp_image_design.get("/library/{item_id}/file")
async def library_file(
    item_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    data, content_type = await mp_service.read_library_file(db, ctx.user, item_id)
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "private, no-store"})


@mp_image_design.get("/library/{item_id}/thumbnail")
async def library_thumbnail(
    item_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    data, content_type = await mp_service.read_library_file(db, ctx.user, item_id, thumbnail=True)
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "private, no-store"})


@mp_image_design.post("/polish")
async def polish(
    payload: MpPolishCreate,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await mp_service.polish(db, ctx.user, payload)


@mp_image_design.post("/tasks")
async def create_task(
    payload: MpTaskCreate,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await mp_service.create_task(db, ctx.user, payload)


@mp_image_design.get("/tasks/{job_id}")
async def get_task(
    job_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await mp_service.get_task(db, ctx.user, job_id)


@mp_image_design.post("/tasks/{job_id}/retry")
async def retry_task(
    job_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await mp_service.retry_task(db, ctx.user, job_id)


@mp_image_design.get("/results")
async def list_results(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await mp_service.list_results(db, ctx.user, page=page, page_size=page_size)


@mp_image_design.get("/results/{asset_id}/file")
async def result_file(
    asset_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    from yuxi.image_design.service import get_result_file

    data, content_type, _ = await get_result_file(db, ctx.user, asset_id)
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "private, no-store"})


@mp_image_design.get("/tasks/{job_id}/inputs/{role}/file")
async def task_input_file(
    job_id: str,
    role: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    data, content_type = await mp_service.read_task_input(db, ctx.user, job_id, role)
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "private, no-store"})
