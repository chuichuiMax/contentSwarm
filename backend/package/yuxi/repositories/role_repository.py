from __future__ import annotations

from typing import Any

from sqlalchemy import case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_content import ContentRole
from yuxi.utils.datetime_utils import utc_now_naive


class RoleRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, role_pk: str) -> ContentRole | None:
        result = await self.db.execute(select(ContentRole).where(ContentRole.id == role_pk))
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> ContentRole | None:
        result = await self.db.execute(select(ContentRole).where(ContentRole.name == name))
        return result.scalar_one_or_none()

    async def get_by_code(self, role_code: str) -> ContentRole | None:
        result = await self.db.execute(select(ContentRole).where(ContentRole.role_code == role_code))
        return result.scalar_one_or_none()

    async def get_by_code_or_name(self, value: str) -> ContentRole | None:
        return await self.get_by_code(value) or await self.get_by_name(value)

    async def has_any(self) -> bool:
        result = await self.db.execute(select(ContentRole.id).limit(1))
        return result.scalar_one_or_none() is not None

    async def list_codes(self) -> list[str]:
        result = await self.db.execute(select(ContentRole.role_code))
        return [code for (code,) in result.all()]

    async def list_roles(self, *, keyword: str | None = None, enabled: bool | None = None) -> list[ContentRole]:
        query = select(ContentRole)
        if keyword:
            escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            query = query.where(
                or_(
                    ContentRole.role_code.ilike(pattern, escape="\\"),
                    ContentRole.name.ilike(pattern, escape="\\"),
                )
            )
        if enabled is not None:
            query = query.where(ContentRole.enabled.is_(enabled))
        result = await self.db.execute(
            query.order_by(
                case((ContentRole.role_type == "系统", 0), else_=1),
                ContentRole.role_code.asc(),
                ContentRole.created_at.asc(),
            )
        )
        return list(result.scalars().all())

    async def create(self, data: dict[str, Any]) -> ContentRole:
        role = ContentRole(**data)
        self.db.add(role)
        await self.db.flush()
        return role

    async def update(self, role: ContentRole, data: dict[str, Any]) -> ContentRole:
        for key, value in data.items():
            setattr(role, key, value)
        role.updated_at = utc_now_naive()
        await self.db.flush()
        return role

    async def delete(self, role: ContentRole) -> None:
        await self.db.delete(role)
        await self.db.flush()
