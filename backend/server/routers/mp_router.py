from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_mp_context
from server.utils.public_url import request_public_base_url
from yuxi.services.material_library_service import MaterialShareCreate, create_material_share
from yuxi.services.personal_materials import folder_counts, list_folder
from yuxi.services.mp_service import (
    AuthCancelPayload,
    AuthConfirmPayload,
    MeUpdatePayload,
    MpCompileBriefPayload,
    MpContext,
    MpRunCreatePayload,
    MpRunResumePayload,
    MpRunRetryPayload,
    SmsLoginPayload,
    SmsSendPayload,
    WechatCodePayload,
    WechatPhonePayload,
    add_favorite,
    bind_wechat_phone,
    cancel_login,
    compile_brief,
    confirm_login,
    delete_content,
    delete_mp_gallery_item,
    duplicate_content,
    get_artifact,
    get_form_schema,
    get_me,
    get_pricing,
    get_run,
    get_task,
    list_contents,
    list_cover_templates,
    list_hycanvas_templates,
    list_mp_galleries,
    list_mp_gallery_items,
    list_mp_viral_assets,
    list_mp_works,
    login_by_sms,
    login_by_wechat_code,
    logout,
    read_cover_file,
    read_cover_template_file,
    read_hycanvas_template_overlay,
    read_hycanvas_template_preview,
    read_mp_gallery_item_file,
    read_mp_gallery_item_thumbnail,
    read_mp_work_file,
    remove_favorite,
    resume_run,
    retry_run,
    send_sms_code,
    start_run,
    stream_run_events,
    update_me,
    upload_cover,
    hide_mp_work,
)

mp = APIRouter(prefix="/mp", tags=["mp"])


@mp.post("/auth/sms/send")
async def mp_send_sms(payload: SmsSendPayload, db: AsyncSession = Depends(get_db)):
    return await send_sms_code(db, payload.phone)


@mp.post("/auth/sms/login")
async def mp_sms_login(payload: SmsLoginPayload, db: AsyncSession = Depends(get_db)):
    return await login_by_sms(db, payload)


@mp.post("/auth/wechat/code")
async def mp_wechat_code(payload: WechatCodePayload):
    return await login_by_wechat_code(payload)


@mp.post("/auth/wechat/phone")
async def mp_wechat_phone(payload: WechatPhonePayload, db: AsyncSession = Depends(get_db)):
    return await bind_wechat_phone(db, payload)


@mp.post("/auth/confirm")
async def mp_confirm(payload: AuthConfirmPayload, db: AsyncSession = Depends(get_db)):
    return await confirm_login(db, payload)


@mp.post("/auth/cancel")
async def mp_cancel(payload: AuthCancelPayload):
    return await cancel_login(payload)


@mp.post("/auth/logout")
async def mp_logout(_ctx: MpContext = Depends(get_mp_context)):
    return await logout()


@mp.get("/me")
async def mp_get_me(ctx: MpContext = Depends(get_mp_context)):
    return await get_me(ctx)


@mp.get("/auth/profile")
async def mp_auth_profile(ctx: MpContext = Depends(get_mp_context)):
    return await get_me(ctx)


@mp.patch("/me")
async def mp_update_me(
    payload: MeUpdatePayload,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await update_me(db, ctx, payload)


@mp.get("/content/form-schema")
async def mp_form_schema(
    service_entry: str = Query(...),
    include_hycanvas_templates: bool = Query(True),
    _ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await get_form_schema(db, service_entry, include_hycanvas_templates=include_hycanvas_templates)


@mp.get("/content/pricing")
async def mp_pricing(frame_area: str = Query(...), _ctx: MpContext = Depends(get_mp_context)):
    return await get_pricing(frame_area)


@mp.get("/content/viral-assets")
async def mp_viral_assets(
    content_type_code: str | None = Query(default=None, pattern=r"^CT0[1-7]$"),
    industry_slug: str | None = None,
    ready_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=100),
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await list_mp_viral_assets(
        db,
        ctx,
        industry_slug=industry_slug,
        ready_only=ready_only,
        content_type_code=content_type_code,
        limit=limit,
    )


@mp.get("/content/cover-templates")
async def mp_cover_templates(_ctx: MpContext = Depends(get_mp_context), db: AsyncSession = Depends(get_db)):
    return await list_cover_templates(db)


@mp.get("/content/cover-templates/{cover_pk}/file")
async def mp_cover_template_file(
    cover_pk: str,
    _ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    data, content_type, file_name = await read_cover_template_file(db, cover_pk)
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'inline; filename="{file_name}"'},
    )


@mp.get("/content/hycanvas-templates")
async def mp_hycanvas_templates(_ctx: MpContext = Depends(get_mp_context)):
    return await list_hycanvas_templates()


@mp.get("/content/hycanvas-templates/{template_id}/preview")
async def mp_hycanvas_template_preview(
    template_id: str,
    _ctx: MpContext = Depends(get_mp_context),
):
    data, content_type = await read_hycanvas_template_preview(template_id)
    return Response(content=data, media_type=content_type)


@mp.get("/content/hycanvas-templates/{template_id}/overlay")
async def mp_hycanvas_template_overlay(
    template_id: str,
    _ctx: MpContext = Depends(get_mp_context),
):
    data, content_type = await read_hycanvas_template_overlay(template_id)
    return Response(content=data, media_type=content_type)


@mp.get("/content/galleries")
async def mp_galleries(
    scope: Literal["private", "enterprise"] | None = Query(None),
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await list_mp_galleries(db, ctx, scope=scope)


@mp.get("/content/gallery-items")
async def mp_gallery_items(
    category: str = Query(...),
    scope: Literal["private", "enterprise"] | None = Query(None),
    include_descendants: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await list_mp_gallery_items(
        db,
        ctx,
        category,
        scope=scope,
        include_descendants=include_descendants,
        page=page,
        page_size=page_size,
    )


@mp.get("/content/my-materials/folders")
async def mp_my_material_folders(
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return {"folders": await folder_counts(db, ctx.user)}


@mp.get("/content/my-materials/{folder}")
async def mp_my_material_items(
    folder: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await list_folder(db, ctx.user, folder, page=page, page_size=page_size, date_from=date_from, date_to=date_to)


@mp.get("/content/gallery-items/{item_id}/file")
async def mp_gallery_item_file(
    item_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    data, content_type, file_name = await read_mp_gallery_item_file(db, ctx, item_id)
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'inline; filename="{file_name}"'},
    )


@mp.get("/content/gallery-items/{item_id}/thumbnail")
async def mp_gallery_item_thumbnail(
    item_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    data = await read_mp_gallery_item_thumbnail(db, ctx, item_id)
    return Response(
        content=data,
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@mp.delete("/content/gallery-items/{item_id}")
async def mp_delete_gallery_item(
    item_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await delete_mp_gallery_item(db, ctx, item_id)


@mp.get("/image/works")
async def mp_works(
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await list_mp_works(db, ctx, page=page, page_size=page_size)


@mp.get("/image/works/{asset_id}/file")
async def mp_work_file(
    asset_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    data, content_type, file_name = await read_mp_work_file(db, ctx, asset_id)
    return Response(
        content=data, media_type=content_type, headers={"Content-Disposition": f'inline; filename="{file_name}"'}
    )


@mp.delete("/image/works/{asset_id}")
async def mp_hide_work(
    asset_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await hide_mp_work(db, ctx, asset_id)


@mp.post("/share/cases", status_code=status.HTTP_201_CREATED)
async def mp_create_material_share(
    payload: MaterialShareCreate,
    request: Request,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await create_material_share(
        db,
        ctx.user,
        payload,
        public_base_url=request_public_base_url(request),
    )


@mp.post("/content/uploads/cover")
async def mp_upload_cover(
    file: UploadFile = File(...),
    category: str = Form("uncategorized"),
    design_style: str | None = Form(None),
    folder: Literal["rough", "uploads"] = Form("uploads"),
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await upload_cover(db, ctx, file, category=category, design_style=design_style, folder=folder)


@mp.get("/content/covers/{asset_id}/file")
async def mp_cover_file(
    asset_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    data, content_type, file_name = await read_cover_file(db, ctx, asset_id)
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'inline; filename="{file_name}"'},
    )


@mp.post("/content/compile-brief")
async def mp_compile_brief(
    payload: MpCompileBriefPayload,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await compile_brief(db, ctx, payload)


@mp.get("/content/tasks/{task_id}")
async def mp_get_task(
    task_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await get_task(db, ctx, task_id)


@mp.post("/content/tasks/{task_id}/runs")
async def mp_start_run(
    task_id: str,
    payload: MpRunCreatePayload,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await start_run(db, ctx, task_id, payload)


@mp.get("/content/runs/{run_id}")
async def mp_get_run(
    run_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await get_run(db, ctx, run_id)


@mp.get("/content/runs/{run_id}/events")
async def mp_run_events(
    run_id: str,
    after_seq: str = Query(default="0-0"),
    ctx: MpContext = Depends(get_mp_context),
):
    return StreamingResponse(
        stream_run_events(run_id, after_seq, ctx),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@mp.post("/content/runs/{run_id}/resume")
async def mp_resume_run(
    run_id: str,
    payload: MpRunResumePayload,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await resume_run(db, ctx, run_id, payload)


@mp.post("/content/runs/{run_id}/retry")
async def mp_retry_run(
    run_id: str,
    payload: MpRunRetryPayload,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await retry_run(db, ctx, run_id, payload)


@mp.get("/content/tasks/{task_id}/artifact")
async def mp_get_artifact(
    task_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await get_artifact(db, ctx, task_id)


@mp.get("/contents")
async def mp_list_contents(
    service_entry: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await list_contents(db, ctx, service_entry=service_entry, page=page, page_size=page_size)


@mp.post("/contents/{task_id}/favorite")
async def mp_add_favorite(
    task_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await add_favorite(db, ctx, task_id)


@mp.delete("/contents/{task_id}/favorite")
async def mp_remove_favorite(
    task_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await remove_favorite(db, ctx, task_id)


@mp.post("/contents/{task_id}/duplicate")
async def mp_duplicate_content(
    task_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await duplicate_content(db, ctx, task_id)


@mp.delete("/contents/{task_id}")
async def mp_delete_content(
    task_id: str,
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await delete_content(db, ctx, task_id)
