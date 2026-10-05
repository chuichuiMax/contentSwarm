from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.services.employee_service import (
    EmployeeCreate,
    EmployeeUpdate,
    create_employee,
    delete_employee,
    get_current_employee,
    list_employees,
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
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_employees(db, keyword)


@employees.post("")
async def create_content_employee(
    payload: EmployeeCreate,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_employee(db, current_user, payload)


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
