from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services.requirement_list_service import (
    ShareMembersPayload,
    SubmissionCreate,
    SubmissionFilters,
    create_shared_submission,
    create_submission,
    delete_submission,
    export_submissions,
    get_submission,
    list_share_employees,
    list_submissions,
    share_submission,
)
from yuxi.storage.postgres.models_business import User

requirement_lists = APIRouter(prefix="/requirement-lists", tags=["requirement-lists"])


@requirement_lists.get("")
async def listing(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    filters: SubmissionFilters = Depends(),
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return JSONResponse(
        await list_submissions(db, user, page, page_size, filters),
        headers={"Cache-Control": "private, no-store"},
    )


@requirement_lists.get("/export")
async def export(
    filters: SubmissionFilters = Depends(),
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return Response(
        await export_submissions(db, user, filters),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="requirement-lists.csv"',
            "Cache-Control": "private, no-store",
        },
    )


@requirement_lists.post("/public")
async def public_create(payload: SubmissionCreate, db: AsyncSession = Depends(get_db)):
    return JSONResponse(await create_shared_submission(db, payload), headers={"Cache-Control": "private, no-store"})


@requirement_lists.get("/employees")
async def share_employees(
    keyword: str = Query("", max_length=64),
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    del user
    return JSONResponse(await list_share_employees(db, keyword=keyword), headers={"Cache-Control": "private, no-store"})


@requirement_lists.post("/{submission_id}/share")
async def share(
    submission_id: str,
    payload: ShareMembersPayload,
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return JSONResponse(
        await share_submission(db, user, submission_id, payload),
        headers={"Cache-Control": "private, no-store"},
    )


@requirement_lists.get("/{submission_id}")
async def detail(
    submission_id: str,
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return JSONResponse(await get_submission(db, submission_id, user), headers={"Cache-Control": "private, no-store"})


@requirement_lists.delete("/{submission_id}")
async def remove(
    submission_id: str,
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return JSONResponse(
        await delete_submission(db, submission_id, user), headers={"Cache-Control": "private, no-store"}
    )


@requirement_lists.post("")
async def create(
    payload: SubmissionCreate,
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return JSONResponse(await create_submission(db, user, payload), headers={"Cache-Control": "private, no-store"})
