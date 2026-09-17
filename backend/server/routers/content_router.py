from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.content.schemas import (
    ContentArtifactAIEdit,
    ContentArtifactRegenerate,
    ContentArtifactReview,
    ContentArtifactUpdate,
    ContentBriefSave,
    ContentFinalizeRequest,
    ContentNodeRetry,
    ContentOCRCorrection,
    ContentRunCreate,
    ContentRunResume,
    ContentTaskCreate,
    ContentTaskBatchDelete,
    ContentTaskUpdate,
    ChannelPreviewRequest,
    MaterialConfirmation,
    MaterialCreate,
    IndustryPackRegressionSubmission,
    IndustryPackTransitionRequest,
    RuleBundleUpdate,
    RuleDraftCreate,
    RuleVersionAction,
    StrategyRecommendV3Request,
    XiaohongshuAccountCreate,
    XiaohongshuAccountUpdate,
    XiaohongshuBrowserAction,
    XiaohongshuBrowserOpen,
    XiaohongshuDistributionCreate,
)
from yuxi.repositories.content_repository import ContentRepository
from yuxi.content.control.errors import ContentApplicationError
from yuxi.content.control.strategy.recommend_v3 import (
    PreviewV3StrategyCommand,
    PreviewV3StrategyHandler,
    StrategyPreviewActor,
)
from yuxi.content.infrastructure.postgres.strategy_preview_repository import PostgresStrategyPreviewRepository
from yuxi.content.model.viral_assets import ViralAssetImport
from yuxi.repositories.viral_asset_repository import asset_dict
from yuxi.services.content_viral_assets import (
    check_asset_source, import_viral_assets, list_viral_assets,
    preparation_skill_hash, require_asset, retry_viral_asset,
)
from yuxi.services.agent_run_service import cancel_agent_run_view, stream_agent_run_events
from yuxi.services.content_ocr_service import (
    create_content_ocr_result,
    get_content_ocr_image,
    get_content_ocr_result,
    list_content_ocr_results,
    retry_content_ocr_result,
    update_content_ocr_result,
)
from yuxi.services.content_service import (
    activate_content_rule_version,
    activate_content_workflow_version,
    ai_edit_content_artifact,
    create_content_rule_draft,
    create_content_run,
    create_content_task,
    delete_content_task,
    delete_content_tasks,
    discard_content_rule_draft,
    duplicate_content_task,
    finalize_content_artifact,
    get_content_bootstrap,
    get_content_run,
    get_content_task,
    get_artifact_viral_reference,
    get_task_artifact,
    list_content_artifact_versions,
    list_content_tasks,
    list_task_materials,
    regenerate_content_artifact,
    resume_content_run,
    retry_content_node,
    review_content_artifact,
    save_content_brief,
    save_content_rule_draft,
    add_task_material,
    confirm_task_material,
    preview_task_channel,
    update_content_artifact,
    update_content_task,
    validate_rule_bundle_for_publish,
    validate_content_industry_pack,
    submit_content_industry_pack_regression,
    transition_content_industry_pack,
)
from yuxi.services.xiaohongshu_service import (
    check_account_login,
    claim_browser_session,
    create_account,
    create_distribution,
    browser_session_action,
    close_browser_session,
    delete_account,
    get_browser_screenshot,
    get_browser_session,
    get_distribution_job,
    get_login_session,
    get_result_screenshot,
    heartbeat_browser_session,
    list_accounts,
    list_artifact_distributions,
    start_account_login,
    open_browser_session,
    update_account,
)
from yuxi.storage.postgres.models_business import User

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user, get_superadmin_user
from server.utils.content_presenter import present_content_error

content = APIRouter(prefix="/content", tags=["content"])


@content.get("/xiaohongshu/accounts")
async def list_xiaohongshu_accounts(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_accounts(db, current_user)


@content.post("/xiaohongshu/accounts")
async def create_xiaohongshu_account(
    payload: XiaohongshuAccountCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_account(db, current_user, payload)


@content.patch("/xiaohongshu/accounts/{account_id}")
async def update_xiaohongshu_account(
    account_id: str,
    payload: XiaohongshuAccountUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_account(db, current_user, account_id, payload)


@content.delete("/xiaohongshu/accounts/{account_id}")
async def delete_xiaohongshu_account(
    account_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_account(db, current_user, account_id)


@content.post("/xiaohongshu/accounts/{account_id}/login")
async def login_xiaohongshu_account(
    account_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await start_account_login(db, current_user, account_id)


@content.post("/xiaohongshu/accounts/{account_id}/check")
async def check_xiaohongshu_account(
    account_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await check_account_login(db, current_user, account_id)


@content.post("/xiaohongshu/accounts/{account_id}/browser-session")
async def open_xiaohongshu_browser_session(
    account_id: str,
    payload: XiaohongshuBrowserOpen | None = None,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await open_browser_session(
        db,
        current_user,
        account_id,
        target=payload.target if payload is not None else "home",
    )


@content.get("/xiaohongshu/accounts/{account_id}/browser-session")
async def get_xiaohongshu_browser_session(
    account_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_browser_session(db, current_user, account_id)


@content.post("/xiaohongshu/accounts/{account_id}/browser-session/heartbeat")
async def heartbeat_xiaohongshu_browser_session(
    account_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await heartbeat_browser_session(db, current_user, account_id)


@content.post("/xiaohongshu/accounts/{account_id}/browser-session/claim")
async def claim_xiaohongshu_browser_session(
    account_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await claim_browser_session(db, current_user, account_id)


@content.post("/xiaohongshu/accounts/{account_id}/browser-session/action")
async def act_xiaohongshu_browser_session(
    account_id: str,
    payload: XiaohongshuBrowserAction,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await browser_session_action(db, current_user, account_id, payload.model_dump(exclude_none=True))


@content.get("/xiaohongshu/accounts/{account_id}/browser-session/screenshot")
async def screenshot_xiaohongshu_browser_session(
    account_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    data = await get_browser_screenshot(db, current_user, account_id)
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store"})


@content.delete("/xiaohongshu/accounts/{account_id}/browser-session")
async def close_xiaohongshu_browser_session(
    account_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await close_browser_session(db, current_user, account_id)


@content.get("/xiaohongshu/login-sessions/{session_id}")
async def get_xiaohongshu_login_session(
    session_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_login_session(db, current_user, session_id)


@content.post("/artifacts/{artifact_id}/distributions")
async def distribute_content_artifact(
    artifact_id: str,
    payload: XiaohongshuDistributionCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_distribution(db, current_user, artifact_id, payload)


@content.get("/artifacts/{artifact_id}/distributions")
async def list_content_artifact_distributions(
    artifact_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_artifact_distributions(db, current_user, artifact_id)


@content.get("/distributions/{job_id}")
async def get_content_distribution(
    job_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_distribution_job(db, current_user, job_id)


@content.get("/distribution-results/{result_id}/screenshot")
async def get_content_distribution_screenshot(
    result_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    path = await get_result_screenshot(db, current_user, result_id)
    return FileResponse(path, media_type="image/png", filename=f"{result_id}.png")


@content.get("/bootstrap")
async def content_bootstrap(current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    return await get_content_bootstrap(db, current_user)


@content.post("/tasks")
async def create_task(
    payload: ContentTaskCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_content_task(db, current_user, payload)


@content.get("/tasks")
async def list_tasks(
    generated_only: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = None,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_content_tasks(
        db, current_user, page=page, page_size=page_size, status=status, generated_only=generated_only
    )


@content.post("/tasks/batch-delete")
async def batch_delete_tasks(
    payload: ContentTaskBatchDelete,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_content_tasks(db, current_user, payload.task_ids)


@content.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_content_task(db, current_user, task_id)


@content.patch("/tasks/{task_id}")
async def update_task(
    task_id: str,
    payload: ContentTaskUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_content_task(db, current_user, task_id, payload)


@content.delete("/tasks/{task_id}")
async def delete_task(
    task_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_content_task(db, current_user, task_id)


@content.post("/tasks/{task_id}/duplicate")
async def duplicate_task(
    task_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await duplicate_content_task(db, current_user, task_id)


@content.post("/tasks/{task_id}/ocr-results")
async def create_ocr_result(
    task_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_content_ocr_result(db, current_user, task_id, file)


@content.get("/tasks/{task_id}/ocr-results")
async def list_ocr_results(
    task_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_content_ocr_results(db, current_user, task_id)


@content.post("/tasks/{task_id}/materials")
async def add_material(
    task_id: str,
    payload: MaterialCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await add_task_material(db, current_user, task_id, payload)


@content.get("/ocr-results/{result_id}")
async def get_ocr_result(
    result_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_content_ocr_result(db, current_user, result_id)


@content.patch("/ocr-results/{result_id}")
async def update_ocr_result(
    result_id: str,
    payload: ContentOCRCorrection,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_content_ocr_result(db, current_user, result_id, payload)


@content.post("/ocr-results/{result_id}/retry")
async def retry_ocr_result(
    result_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await retry_content_ocr_result(db, current_user, result_id)


@content.get("/ocr-results/{result_id}/image")
async def get_ocr_image(
    result_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    data, content_type, _ = await get_content_ocr_image(db, current_user, result_id)
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "private, max-age=300"})


@content.get("/tasks/{task_id}/materials")
async def list_materials(
    task_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_task_materials(db, current_user, task_id)


@content.put("/tasks/{task_id}/materials/{evidence_id}/confirmation")
async def confirm_material(
    task_id: str,
    evidence_id: str,
    payload: MaterialConfirmation,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await confirm_task_material(db, current_user, task_id, evidence_id, payload)


@content.put("/tasks/{task_id}/brief")
async def save_brief(
    task_id: str,
    payload: ContentBriefSave,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await save_content_brief(db, current_user, task_id, payload.brief, compile_now=False)


@content.post("/tasks/{task_id}/compile-brief")
async def compile_brief(
    task_id: str,
    payload: ContentBriefSave,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await save_content_brief(db, current_user, task_id, payload.brief, compile_now=True)


@content.get("/viral-assets")
async def get_viral_assets(
    industry_slug: str | None = None,
    ready_only: bool = False,
    limit: int = Query(default=100, ge=1, le=100),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_viral_assets(db, current_user, industry_slug=industry_slug, ready_only=ready_only, limit=limit)


@content.post("/viral-assets/import")
async def import_viral_asset_file(
    payload: ViralAssetImport,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await import_viral_assets(db, current_user, payload)


@content.get("/viral-file-jobs")
async def get_viral_file_jobs(
    current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db),
):
    from yuxi.services.viral_document_service import list_reference_file_jobs
    return await list_reference_file_jobs(db, current_user)


@content.post("/viral-file-jobs")
async def prepare_viral_files(
    payload: dict = Body(...), current_user: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db),
):
    from yuxi.services.viral_document_service import schedule_reference_file
    kb_id, file_ids = payload.get("kb_id"), payload.get("file_ids")
    if not isinstance(kb_id, str) or not isinstance(file_ids, list) or not 1 <= len(file_ids) <= 100:
        raise HTTPException(422, "请选择知识库和 1—100 个原文文件")
    if any(not isinstance(item, str) or not item for item in file_ids):
        raise HTTPException(422, "原文文件标识无效")
    retry = payload.get("retry", False)
    if not isinstance(retry, bool):
        raise HTTPException(422, "重试标记必须为布尔值")
    items, errors = [], []
    for file_id in dict.fromkeys(file_ids):
        try:
            items.append(await schedule_reference_file(db, current_user, kb_id, file_id, retry=retry))
        except HTTPException as exc:
            errors.append({"file_id": file_id, "message": str(exc.detail)})
    return {"items": items, "errors": errors}


@content.get("/viral-assets/{asset_id}")
async def get_viral_asset(
    asset_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    asset = await require_asset(db, current_user, asset_id)
    if asset.status == "ready" and not await check_asset_source(db, asset):
        asset.status, asset.error_message = "invalidated", "原文已更新，请重新准备"
        await db.commit()
    elif asset.status == "ready" and asset.preparation_skill_hash != preparation_skill_hash():
        asset.status, asset.error_message = "invalidated", "准备标准已更新，请重新导入"
        await db.commit()
    return {"asset": asset_dict(asset, include_source=True)}


@content.post("/viral-assets/{asset_id}/retry")
async def retry_viral_asset_preparation(
    asset_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await retry_viral_asset(db, current_user, asset_id)


@content.get("/tasks/{task_id}/strategy/candidates")
async def get_strategy_candidates(
    task_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await PostgresStrategyPreviewRepository(db).load_candidates(
            task_id=task_id,
            actor=StrategyPreviewActor(
                uid=str(current_user.uid),
                role=current_user.role,
                tenant_id=str(current_user.department_id) if current_user.department_id is not None else None,
            ),
        )
    except ContentApplicationError as exc:
        raise present_content_error(exc) from exc


@content.get("/tasks/{task_id}/strategy/decision")
async def get_strategy_decision(
    task_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    from yuxi.storage.postgres.models_business import AgentRun
    from yuxi.storage.postgres.models_content import ContentNodeRun

    task = await ContentRepository(db).get_task_for_user(task_id, current_user)
    if task is None:
        raise HTTPException(404, "内容任务不存在")
    run_ids = []
    run = await db.get(AgentRun, task.latest_run_id) if task.latest_run_id else None
    while run and run.thread_id == task_id and run.id not in run_ids:
        run_ids.append(run.id)
        run = await db.get(AgentRun, run.parent_run_id) if run.parent_run_id else None
    rows = list((await db.execute(
        select(ContentNodeRun).where(
            ContentNodeRun.task_id == task_id,
            ContentNodeRun.agent_run_id.in_(run_ids),
            ContentNodeRun.node_id.in_(["select_creation_strategy", "lock_creation_strategy"]),
            ContentNodeRun.status == "completed",
        ).order_by(ContentNodeRun.finished_at.desc(), ContentNodeRun.id.desc()).limit(6)
    )).scalars())
    selection_row = next((row for row in rows if row.node_id == "select_creation_strategy"), None)
    selection_input = ((selection_row.input_snapshot or {}).get("visible_payload") or {}) if selection_row else {}
    candidates = (
        ((selection_row.input_snapshot or {}).get("visible_payload") or {}).get("strategy_candidates", {})
        if selection_row else {}
    )
    automatic_view = {
        "automatic_direction": candidates.get("auto_direction", False),
        "direction_names": {item["code"]: item["name"] for item in candidates.get("direction_options", [])},
    } if candidates.get("auto_direction") else {}
    for row in rows:
        output = (row.output_snapshot or {}).get("result") or {}
        snapshot = output.get("strategy_snapshot")
        decision = (snapshot or {}).get("decision") or output.get("joint_strategy_decision")
        if decision:
            from yuxi.services.content_strategy_presentation import build_decision_presentation

            return {
                **build_decision_presentation(decision, selection_input),
                "decision": decision, "snapshot": snapshot, "node_run_id": row.id,
                "run_id": row.agent_run_id, **automatic_view,
            }
    return {"decision": None, "snapshot": None, **automatic_view}


@content.post("/tasks/{task_id}/strategy/recommend-v3")
async def recommend_strategy_v3(
    task_id: str,
    payload: StrategyRecommendV3Request,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    handler = PreviewV3StrategyHandler(PostgresStrategyPreviewRepository(db))
    try:
        return await handler.execute(
            PreviewV3StrategyCommand(
                task_id=task_id,
                actor=StrategyPreviewActor(
                    uid=str(current_user.uid),
                    role=current_user.role,
                    tenant_id=str(current_user.department_id) if current_user.department_id is not None else None,
                ),
                content_direction_code=payload.content_direction_code,
                limit=payload.limit,
            )
        )
    except ContentApplicationError as exc:
        raise present_content_error(exc) from exc


@content.post("/tasks/{task_id}/channel-preview")
async def preview_channel(
    task_id: str,
    payload: ChannelPreviewRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await preview_task_channel(db, current_user, task_id, payload)


@content.get("/rule-versions/{version_id}/bundle")
async def get_rule_bundle(
    version_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    del current_user
    bundle = await ContentRepository(db).get_rule_bundle(version_id)
    return {"bundle": bundle}


@content.post("/tasks/{task_id}/runs")
async def create_run(
    task_id: str,
    payload: ContentRunCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_content_run(db, current_user, task_id, payload)


@content.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_content_run(db, current_user, run_id)


@content.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    after_seq: str = "0-0",
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    current_user: User = Depends(get_required_user),
):
    cursor = last_event_id or after_seq
    return StreamingResponse(
        stream_agent_run_events(run_id=run_id, after_seq=cursor, current_uid=str(current_user.uid), verbose=True),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@content.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    payload: ContentRunResume,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await resume_content_run(db, current_user, run_id, payload)


@content.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    await get_content_run(db, current_user, run_id)
    return await cancel_agent_run_view(run_id=run_id, current_uid=str(current_user.uid), db=db)


@content.post("/runs/{run_id}/retry-node")
async def retry_node(
    run_id: str,
    payload: ContentNodeRetry,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await retry_content_node(
        db,
        current_user,
        run_id,
        request_id=payload.request_id,
        node_id=payload.node_id,
        model_spec=payload.model_spec,
    )


@content.get("/tasks/{task_id}/artifact")
async def get_artifact_for_task(
    task_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_task_artifact(db, current_user, task_id)


@content.patch("/artifacts/{artifact_id}")
async def update_artifact(
    artifact_id: str,
    payload: ContentArtifactUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_content_artifact(db, current_user, artifact_id, payload)


@content.get("/artifacts/{artifact_id}/viral-reference")
async def get_viral_reference(
    artifact_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_artifact_viral_reference(db, current_user, artifact_id)


@content.post("/artifacts/{artifact_id}/ai-edit")
async def ai_edit_artifact(
    artifact_id: str,
    payload: ContentArtifactAIEdit,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await ai_edit_content_artifact(db, current_user, artifact_id, payload)


@content.post("/artifacts/{artifact_id}/review")
async def review_artifact(
    artifact_id: str,
    payload: ContentArtifactReview,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await review_content_artifact(db, current_user, artifact_id, model_spec=payload.model_spec)


@content.post("/artifacts/{artifact_id}/finalize")
async def finalize_artifact(
    artifact_id: str,
    payload: ContentFinalizeRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    del payload
    return await finalize_content_artifact(db, current_user, artifact_id)


@content.get("/artifacts/{artifact_id}/versions")
async def list_artifact_versions(
    artifact_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_content_artifact_versions(db, current_user, artifact_id)


@content.post("/artifacts/{artifact_id}/regenerate")
async def regenerate_artifact(
    artifact_id: str,
    payload: ContentArtifactRegenerate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await regenerate_content_artifact(db, current_user, artifact_id, payload)


@content.get("/admin/rules")
async def list_rule_versions(current_user: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    del current_user
    return {"items": await ContentRepository(db).list_rule_versions()}


@content.get("/admin/rules/{version_id}/bundle")
async def get_admin_rule_bundle(
    version_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    del current_user
    bundle = await ContentRepository(db).get_rule_bundle(version_id, include_disabled=True)
    if bundle is None:
        raise HTTPException(status_code=404, detail="规则版本不存在")
    return {"bundle": bundle, "validation": validate_rule_bundle_for_publish(bundle)}


@content.post("/admin/rules/drafts")
async def create_rule_draft(
    payload: RuleDraftCreate,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_content_rule_draft(db, current_user, payload)


@content.put("/admin/rules/{version_id}/bundle")
async def save_rule_draft(
    version_id: str,
    payload: RuleBundleUpdate,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await save_content_rule_draft(db, current_user, version_id, payload)


@content.delete("/admin/rules/{version_id}")
async def discard_rule_draft(
    version_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await discard_content_rule_draft(db, current_user, version_id)


@content.post("/admin/rules/{version_id}/publish")
async def publish_rule_version(
    version_id: str,
    payload: RuleVersionAction,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await activate_content_rule_version(
        db,
        current_user,
        version_id,
        rollback=False,
        note=payload.note,
    )


@content.post("/admin/rules/{version_id}/rollback")
async def rollback_rule_version(
    version_id: str,
    payload: RuleVersionAction,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    return await activate_content_rule_version(
        db,
        current_user,
        version_id,
        rollback=True,
        note=payload.note,
    )


@content.get("/admin/industry-templates")
async def list_industry_templates(current_user: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    del current_user
    return {"items": await ContentRepository(db).list_templates(published_only=False)}


@content.get("/admin/industry-packs")
async def list_industry_packs(current_user: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    del current_user
    return {"items": await ContentRepository(db).list_industry_packs(published_only=False)}


@content.post("/admin/industry-packs/{version_id}/validate")
async def validate_industry_pack(
    version_id: str,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    return await validate_content_industry_pack(db, current_user, version_id)


@content.post("/admin/industry-packs/{version_id}/transition")
async def transition_industry_pack(
    version_id: str,
    payload: IndustryPackTransitionRequest,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    return await transition_content_industry_pack(db, current_user, version_id, payload)


@content.post("/admin/industry-packs/{version_id}/regression")
async def submit_industry_pack_regression(
    version_id: str,
    payload: IndustryPackRegressionSubmission,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    return await submit_content_industry_pack_regression(db, current_user, version_id, payload)


@content.get("/admin/workflow-templates")
async def list_workflow_templates(current_user: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db)):
    del current_user
    return {"items": await ContentRepository(db).list_workflows(published_only=False)}


@content.post("/admin/workflows/{version_id}/publish")
async def publish_workflow_version(
    version_id: str,
    payload: RuleVersionAction,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    return await activate_content_workflow_version(
        db,
        current_user,
        version_id,
        rollback=False,
        note=payload.note,
    )


@content.post("/admin/workflows/{version_id}/rollback")
async def rollback_workflow_version(
    version_id: str,
    payload: RuleVersionAction,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    return await activate_content_workflow_version(
        db,
        current_user,
        version_id,
        rollback=True,
        note=payload.note,
    )
