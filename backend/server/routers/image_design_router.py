from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.image_design.schemas import (
    ImageDesignAnalysisCreate,
    ImageDesignClientCreate,
    ImageDesignGenerateCreate,
    ImageDesignRefinementCreate,
    ImageDesignRecognizeCreate,
    ImageDesignShowcaseCreate,
)
from yuxi.image_design.service import (
    _error,
    create_analysis,
    create_client,
    create_generate_job,
    create_refinement,
    delete_result,
    get_analysis,
    get_bootstrap,
    get_job,
    get_refinement,
    get_result_file,
    list_clients,
    list_jobs,
    list_results,
    list_showcase,
    create_showcase,
    delete_showcase,
)
from yuxi.storage.postgres.models_business import User

image_design = APIRouter(prefix="/image-design", tags=["image-design"])


@image_design.get("/bootstrap")
async def image_design_bootstrap(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_bootstrap(db, current_user)


@image_design.get("/clients")
async def image_design_clients(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_clients(db, current_user)


@image_design.post("/clients", status_code=status.HTTP_201_CREATED)
async def image_design_create_client(
    payload: ImageDesignClientCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_client(db, current_user, payload)


@image_design.get("/showcase")
async def image_design_showcase(
    category: str | None = Query(None, max_length=80),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_showcase(db, current_user, category=category)


@image_design.post("/showcase", status_code=status.HTTP_201_CREATED)
async def image_design_create_showcase(
    payload: ImageDesignShowcaseCreate,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_showcase(db, current_user, payload)


@image_design.delete("/showcase/{showcase_id}")
async def image_design_delete_showcase(
    showcase_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    del current_user
    return await delete_showcase(db, showcase_id)


@image_design.post("/refinements", status_code=status.HTTP_201_CREATED)
async def image_design_create_refinement(
    payload: ImageDesignRefinementCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_refinement(db, current_user, payload)


@image_design.get("/refinements/{refinement_id}")
async def image_design_get_refinement(
    refinement_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_refinement(db, current_user, refinement_id)


@image_design.post("/refine-prompt", status_code=status.HTTP_201_CREATED)
async def image_design_refine_prompt_compatibility(
    payload: ImageDesignRefinementCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_refinement(db, current_user, payload)


@image_design.post("/analyses", status_code=status.HTTP_201_CREATED)
async def image_design_create_analysis(
    payload: ImageDesignAnalysisCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_analysis(db, current_user, payload)


@image_design.get("/analyses/{analysis_id}")
async def image_design_get_analysis(
    analysis_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_analysis(db, current_user, analysis_id)


@image_design.post("/recognize")
async def image_design_recognize(
    payload: ImageDesignRecognizeCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    del payload, current_user, db
    raise _error("IMAGE_DESIGN_ANALYSIS_ROLE_REQUIRED", "图片识别接口已升级，请明确指定图片分析角色", 410)


@image_design.get("/recognitions")
async def image_design_recognitions(
    material_item_id: str | None = Query(None, max_length=80),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    del material_item_id, current_user, db
    raise _error("IMAGE_DESIGN_ANALYSIS_ROLE_REQUIRED", "请使用新的角色化图片分析接口", 410)


@image_design.post("/generate", status_code=status.HTTP_202_ACCEPTED)
async def image_design_generate(
    payload: ImageDesignGenerateCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_generate_job(db, current_user, payload)


@image_design.get("/generate/status")
async def image_design_generate_status(
    job_id: str = Query(..., min_length=8, max_length=80),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_job(db, current_user, job_id)


@image_design.get("/jobs")
async def image_design_jobs(
    client_id: str | None = Query(None, max_length=80),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_jobs(db, current_user, client_id=client_id)


@image_design.get("/results")
async def image_design_results(
    client_id: str | None = Query(None, max_length=80),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_results(db, current_user, client_id=client_id)


@image_design.get("/results/{asset_id}/file")
async def image_design_result_file(
    asset_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    data, content_type, file_name = await get_result_file(db, current_user, asset_id)
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(file_name, safe='')}"},
    )


@image_design.delete("/results/{asset_id}")
async def image_design_delete_result(
    asset_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_result(db, current_user, asset_id)
