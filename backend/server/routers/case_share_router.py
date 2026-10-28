from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from server.utils.public_url import request_public_base_url
from yuxi.services.case_share_service import ShareFilters, delete_share, export_shares, list_shares, share_summary
from yuxi.storage.postgres.models_business import User

case_shares = APIRouter(prefix="/case-shares", tags=["case-shares"])


@case_shares.get("")
async def listing(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    filters: ShareFilters = Depends(),
    user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return JSONResponse(
        await list_shares(db, user, page, page_size, request_public_base_url(request), filters),
        headers={"Cache-Control": "private, no-store"},
    )


@case_shares.get("/summary")
async def summary(user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    return JSONResponse(await share_summary(db, user), headers={"Cache-Control": "private, no-store"})


@case_shares.get("/export")
async def export(
    filters: ShareFilters = Depends(), user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)
):
    return Response(
        await export_shares(db, user, filters),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="case-shares.xlsx"',
            "Cache-Control": "private, no-store",
        },
    )


@case_shares.delete("/{share_id}")
async def remove(share_id: str, user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    return await delete_share(db, user, share_id)
