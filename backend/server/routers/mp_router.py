from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_mp_context
from yuxi.services.mp_service import (
    AuthCancelPayload,
    AuthConfirmPayload,
    MeUpdatePayload,
    MpCompileBriefPayload,
    MpContext,
    MpRunCreatePayload,
    MpRunResumePayload,
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
    duplicate_content,
    get_artifact,
    get_form_schema,
    get_me,
    get_pricing,
    get_run,
    get_task,
    list_contents,
    list_cover_templates,
    login_by_sms,
    login_by_wechat_code,
    logout,
    read_cover_file,
    read_cover_template_file,
    remove_favorite,
    resume_run,
    send_sms_code,
    start_run,
    stream_run_events,
    update_me,
    upload_cover,
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
    _ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await get_form_schema(db, service_entry)


@mp.get("/content/pricing")
async def mp_pricing(frame_area: str = Query(...), _ctx: MpContext = Depends(get_mp_context)):
    return await get_pricing(frame_area)


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


@mp.post("/content/uploads/cover")
async def mp_upload_cover(
    file: UploadFile = File(...),
    ctx: MpContext = Depends(get_mp_context),
    db: AsyncSession = Depends(get_db),
):
    return await upload_cover(db, ctx, file)


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
