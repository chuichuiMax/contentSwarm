from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Header, Query, UploadFile, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.content_cover.schemas import (
    CoverComposeCreate,
    CoverEditorProjectCreate,
    CoverEditorRenderCreate,
    CoverEditorSceneUpdate,
    CoverGenerateCreate,
    CoverRetryCreate,
    CoverSetCurrentCreate,
    HyCanvasDesignCreate,
    HyCanvasDesignSync,
    HyCanvasEditorSessionCreate,
    HyCanvasTemplatePreviewCreate,
    Image2ConfigTestRequest,
    Image2GlobalConfigUpdate,
    PosterGenerateCreate,
    PosterPreviewCreate,
    PosterTemplateReviewUpdate,
    PosterTemplateUpdate,
    TemplateReplicatePlanCreate,
)
from yuxi.services.content_cover_service import (
    cancel_cover_job,
    create_cover_asset,
    create_cover_compose_job,
    create_cover_editor_project,
    create_cover_generate_job,
    create_poster_billboard_job,
    delete_cover_asset,
    delete_poster_template,
    get_cover_asset_file,
    get_cover_bootstrap,
    get_cover_editor_project,
    get_cover_job,
    get_poster_template,
    import_poster_templates,
    list_cover_jobs,
    list_poster_templates,
    preview_poster_billboard,
    preview_template_replication_plan,
    reanalyze_poster_template,
    resolve_cover_editor_font,
    review_poster_template,
    retry_cover_job,
    render_cover_editor_project,
    set_current_cover,
    stream_cover_job_events,
    test_image2_global_config,
    update_image2_global_config,
    update_cover_editor_project,
    update_poster_template,
)
from yuxi.services.hycanvas_service import HyCanvasClient
from yuxi.services.material_library_service import get_material_file
from yuxi.storage.postgres.models_business import User

content_covers = APIRouter(prefix="/content/covers", tags=["content-covers"])


@content_covers.get("/hycanvas/templates")
async def hycanvas_templates(current_user: User = Depends(get_required_user)):
    del current_user
    return await HyCanvasClient.from_env().list_xiaohongshu_templates()


@content_covers.get("/hycanvas/templates/{template_id}/render.png")
async def render_hycanvas_template(
    template_id: str,
    current_user: User = Depends(get_required_user),
):
    del current_user
    content, content_type = await HyCanvasClient.from_env().render_template_png(template_id)
    return Response(content=content, media_type=content_type)


@content_covers.post("/hycanvas/templates/{template_id}/preview.png")
async def preview_hycanvas_template(
    template_id: str,
    payload: HyCanvasTemplatePreviewCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    image = await get_material_file(db, current_user, payload.image_item_id)
    content, content_type = await HyCanvasClient.from_env().render_template_with_background_png(
        template_id,
        image,
    )
    return Response(content=content, media_type=content_type, headers={"Cache-Control": "no-store"})


@content_covers.post("/hycanvas/designs", status_code=status.HTTP_201_CREATED)
async def create_hycanvas_design(
    payload: HyCanvasDesignCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await HyCanvasClient.from_env().create_and_bind(db, current_user, payload)


@content_covers.post("/hycanvas/designs/{design_id}/sync")
async def sync_hycanvas_design(
    design_id: str,
    payload: HyCanvasDesignSync,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await HyCanvasClient.from_env().sync_and_bind(db, current_user, payload.artifact_id, design_id)


@content_covers.post("/hycanvas/designs/{design_id}/editor-session")
async def create_hycanvas_editor_session(
    design_id: str,
    payload: HyCanvasEditorSessionCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await HyCanvasClient.from_env().create_editor_session(
        db,
        current_user,
        payload.artifact_id,
        design_id,
        payload.return_url,
        payload.return_label,
    )


@content_covers.post("/hycanvas/workspace-session")
async def create_hycanvas_workspace_session(
    current_user: User = Depends(get_required_user),
):
    del current_user
    return await HyCanvasClient.from_env().create_workspace_session()


@content_covers.get("/hycanvas/designs/{design_id}/render.png")
async def render_hycanvas_design(
    design_id: str,
    current_user: User = Depends(get_required_user),
):
    del current_user
    content, content_type = await HyCanvasClient.from_env().render_png(design_id)
    return Response(content=content, media_type=content_type)


@content_covers.get("/bootstrap")
async def cover_bootstrap(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_cover_bootstrap(db, current_user)


@content_covers.put("/image2-config")
async def update_cover_image2_config(
    payload: Image2GlobalConfigUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return {"image2": await update_image2_global_config(db, current_user, payload)}


@content_covers.post("/image2-config/test")
async def test_cover_image2_config(
    payload: Image2ConfigTestRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await test_image2_global_config(db, current_user, payload)


@content_covers.post("/assets", status_code=status.HTTP_201_CREATED)
async def upload_cover_asset(
    file: UploadFile = File(...),
    role: str = Form("source"),
    content_task_id: str | None = Form(None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_cover_asset(db, current_user, file, role=role, content_task_id=content_task_id)


@content_covers.post("/poster-templates/import", status_code=status.HTTP_201_CREATED)
async def import_cover_poster_templates(
    files: list[UploadFile] = File(...),
    category: str = Form(...),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await import_poster_templates(
        db,
        current_user,
        files,
        category=category,
    )


@content_covers.get("/poster-templates")
async def cover_poster_templates(
    category: str | None = Query(None),
    template_status: str | None = Query(None, alias="status"),
    query: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_poster_templates(
        db,
        current_user,
        category=category,
        status=template_status,
        query=query,
        page=page,
        page_size=page_size,
    )


@content_covers.get("/poster-templates/{template_id}")
async def cover_poster_template(
    template_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_poster_template(db, current_user, template_id)


@content_covers.patch("/poster-templates/{template_id}")
async def edit_cover_poster_template(
    template_id: str,
    payload: PosterTemplateUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_poster_template(db, current_user, template_id, payload)


@content_covers.delete("/poster-templates/{template_id}")
async def remove_cover_poster_template(
    template_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_poster_template(db, current_user, template_id)


@content_covers.post("/poster-templates/{template_id}/analyze")
async def analyze_cover_poster_template(
    template_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await reanalyze_poster_template(db, current_user, template_id)


@content_covers.put("/poster-templates/{template_id}/review")
async def review_cover_poster_template(
    template_id: str,
    payload: PosterTemplateReviewUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await review_poster_template(db, current_user, template_id, payload)


@content_covers.post("/poster-billboard/preview")
async def preview_cover_poster_billboard(
    payload: PosterPreviewCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await preview_poster_billboard(db, current_user, payload)


@content_covers.post("/poster-billboard/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_cover_poster_billboard(
    payload: PosterGenerateCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_poster_billboard_job(db, current_user, payload)


@content_covers.post("/template-replication/preview")
async def preview_cover_template_replication(
    payload: TemplateReplicatePlanCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """Return OCR slots and editable copy before a paid image2 request."""
    return await preview_template_replication_plan(db, current_user, payload)


@content_covers.post("/editor-projects", status_code=status.HTTP_201_CREATED)
async def create_editor_project(
    payload: CoverEditorProjectCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_cover_editor_project(db, current_user, payload)


@content_covers.get("/editor-fonts/{font_key}")
async def editor_font(font_key: str):
    path = resolve_cover_editor_font(font_key)
    return FileResponse(
        path,
        media_type="font/collection",
        filename=path.name,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@content_covers.get("/editor-projects/{project_id}")
async def editor_project(
    project_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_cover_editor_project(db, current_user, project_id)


@content_covers.patch("/editor-projects/{project_id}")
async def save_editor_project(
    project_id: str,
    payload: CoverEditorSceneUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_cover_editor_project(db, current_user, project_id, payload)


@content_covers.post("/editor-projects/{project_id}/render", status_code=status.HTTP_202_ACCEPTED)
async def render_editor_project(
    project_id: str,
    payload: CoverEditorRenderCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await render_cover_editor_project(db, current_user, project_id, payload)


@content_covers.get("/assets/{asset_id}/file")
async def cover_asset_file(
    asset_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    data, content_type, file_name = await get_cover_asset_file(db, current_user, asset_id)
    encoded_name = quote(file_name, safe="")
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": f"inline; filename*=UTF-8''{encoded_name}",
        },
    )


@content_covers.delete("/assets/{asset_id}")
async def remove_cover_asset(
    asset_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_cover_asset(db, current_user, asset_id)


@content_covers.post("/compose", status_code=status.HTTP_202_ACCEPTED)
async def compose_cover(
    payload: CoverComposeCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_cover_compose_job(db, current_user, payload)


@content_covers.post("/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_cover(
    payload: CoverGenerateCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_cover_generate_job(db, current_user, payload)


@content_covers.get("/jobs")
async def cover_jobs(
    content_task_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_cover_jobs(
        db,
        current_user,
        content_task_id=content_task_id,
        page=page,
        page_size=page_size,
    )


@content_covers.get("/jobs/{job_id}")
async def cover_job(
    job_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_cover_job(db, current_user, job_id)


@content_covers.get("/jobs/{job_id}/events")
async def cover_job_events(
    job_id: str,
    after_seq: str = Query("0-0"),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    await get_cover_job(db, current_user, job_id)
    return StreamingResponse(
        stream_cover_job_events(job_id, str(current_user.uid), last_event_id or after_seq),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@content_covers.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_cover(
    job_id: str,
    payload: CoverRetryCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await retry_cover_job(db, current_user, job_id, payload)


@content_covers.post("/jobs/{job_id}/cancel")
async def cancel_cover(
    job_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await cancel_cover_job(db, current_user, job_id)


@content_covers.post("/jobs/{job_id}/set-current")
async def select_current_cover(
    job_id: str,
    payload: CoverSetCurrentCreate | None = None,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await set_current_cover(
        db,
        current_user,
        job_id,
        asset_id=payload.asset_id if payload else None,
    )
