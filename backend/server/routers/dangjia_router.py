from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services.dangjia_service import (
    DangjiaContentCreate,
    create_dangjia_content,
    get_dangjia_run,
    get_dangjia_task,
)
from yuxi.services.dangjia_callback_service import read_dangjia_cover_media
from yuxi.storage.postgres.models_business import User

dangjia = APIRouter(prefix="/dangjia", tags=["dangjia"])


@dangjia.get("/content/media/{asset_id}/{file_name}")
async def get_content_media_endpoint(asset_id: str, file_name: str):
    result = await read_dangjia_cover_media(asset_id)
    if result is None or file_name != result[2]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dangjia media not found")
    data, content_type, _ = result
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@dangjia.post("/content/tasks", status_code=status.HTTP_201_CREATED)
async def create_content_task_endpoint(
    payload: DangjiaContentCreate,
    response: Response,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    result = await create_dangjia_content(db, current_user, payload)
    if result.get("idempotent"):
        response.status_code = status.HTTP_200_OK
    return result


@dangjia.get("/content/tasks/{task_id}")
async def get_content_task_endpoint(
    task_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_dangjia_task(db, current_user, task_id)


@dangjia.get("/content/runs/{run_id}")
async def get_content_run_endpoint(
    run_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_dangjia_run(db, current_user, run_id)
