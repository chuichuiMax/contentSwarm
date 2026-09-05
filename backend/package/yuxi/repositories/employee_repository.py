from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_content import ContentEmployee
from yuxi.utils.datetime_utils import utc_now_naive


class EmployeeRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, employee_pk: str) -> ContentEmployee | None:
        result = await self.db.execute(select(ContentEmployee).where(ContentEmployee.id == employee_pk))
        return result.scalar_one_or_none()

    async def get_by_code(self, employee_code: str, *, exclude_id: str | None = None) -> ContentEmployee | None:
        query = select(ContentEmployee).where(ContentEmployee.employee_code == employee_code)
        if exclude_id:
            query = query.where(ContentEmployee.id != exclude_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_by_login_account(
        self, login_account: str, *, exclude_id: str | None = None
    ) -> ContentEmployee | None:
        query = select(ContentEmployee).where(ContentEmployee.login_account == login_account)
        if exclude_id:
            query = query.where(ContentEmployee.id != exclude_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def list_employees(self, *, keyword: str | None = None) -> list[ContentEmployee]:
        query = select(ContentEmployee)
        if keyword:
            escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            query = query.where(
                or_(
                    ContentEmployee.employee_code.ilike(pattern, escape="\\"),
                    ContentEmployee.name.ilike(pattern, escape="\\"),
                    ContentEmployee.login_account.ilike(pattern, escape="\\"),
                )
            )
        result = await self.db.execute(query.order_by(ContentEmployee.created_at.desc()))
        return list(result.scalars().all())

    async def list_by_role(self, role: str, *, keyword: str | None = None) -> list[ContentEmployee]:
        query = select(ContentEmployee).where(ContentEmployee.role == role)
        if keyword:
            escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            query = query.where(
                or_(
                    ContentEmployee.name.ilike(pattern, escape="\\"),
                    ContentEmployee.employee_code.ilike(pattern, escape="\\"),
                )
            )
        result = await self.db.execute(query.order_by(ContentEmployee.created_at.desc()))
        return list(result.scalars().all())

    async def count_by_role(self) -> dict[str, int]:
        result = await self.db.execute(select(ContentEmployee.role, func.count()).group_by(ContentEmployee.role))
        return {name: count for name, count in result.all()}

    async def rename_role(self, old_name: str, new_name: str) -> None:
        await self.db.execute(
            update(ContentEmployee)
            .where(ContentEmployee.role == old_name)
            .values(role=new_name, updated_at=utc_now_naive())
        )

    async def create(self, data: dict[str, Any]) -> ContentEmployee:
        employee = ContentEmployee(**data)
        self.db.add(employee)
        await self.db.flush()
        return employee

    async def update(self, employee: ContentEmployee, data: dict[str, Any]) -> ContentEmployee:
        for key, value in data.items():
            setattr(employee, key, value)
        employee.updated_at = utc_now_naive()
        await self.db.flush()
        return employee

    async def delete(self, employee: ContentEmployee) -> None:
        await self.db.delete(employee)
        await self.db.flush()
