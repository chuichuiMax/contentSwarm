from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.services.employee_service import (
    EmployeeCreate,
    EmployeeUpdate,
    create_employee,
    delete_employee,
    export_employees,
    export_traffic_stats,
    get_current_employee,
    list_employees,
    list_traffic_stats,
    update_employee,
)
from yuxi.storage.postgres.models_business import User

employees = APIRouter(prefix="/employees", tags=["employees"])


@employees.get("/me")
async def get_my_content_employee(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await get_current_employee(db, current_user)


@employees.get("")
async def list_content_employees(
    keyword: str | None = Query(default=None),
    branch: str | None = Query(default=None),
    department: str | None = Query(default=None),
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_employees(db, keyword, branch, department)


@employees.post("")
async def create_content_employee(
    payload: EmployeeCreate,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_employee(db, current_user, payload)


@employees.get("/traffic")
async def content_traffic_stats(
    keyword: str | None = Query(default=None),
    branch: str | None = Query(default=None),
    department: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    del current_user
    return await list_traffic_stats(
        db, keyword=keyword, branch=branch, department=department, date_from=date_from, date_to=date_to
    )


@employees.get("/traffic/export")
async def export_content_traffic_stats(
    keyword: str | None = Query(default=None),
    branch: str | None = Query(default=None),
    department: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    del current_user
    return Response(
        content=await export_traffic_stats(
            db, keyword=keyword, branch=branch, department=department, date_from=date_from, date_to=date_to
        ),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="traffic-stats.xlsx"'},
    )


@employees.get("/export")
async def export_content_employees(
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return Response(
        content=await export_employees(db),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="employees.xlsx"'},
    )


@employees.patch("/{employee_pk}")
async def update_content_employee(
    employee_pk: str,
    payload: EmployeeUpdate,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_employee(db, employee_pk, payload)


@employees.delete("/{employee_pk}")
async def delete_content_employee(
    employee_pk: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_employee(db, employee_pk)
