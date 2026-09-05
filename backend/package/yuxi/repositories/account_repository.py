from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_content import ContentAccount
from yuxi.utils.datetime_utils import utc_now_naive


class AccountRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, account_pk: str) -> ContentAccount | None:
        result = await self.db.execute(select(ContentAccount).where(ContentAccount.id == account_pk))
        return result.scalar_one_or_none()

    async def get_by_account_id(self, account_id: str, *, exclude_id: str | None = None) -> ContentAccount | None:
        query = select(ContentAccount).where(ContentAccount.account_id == account_id)
        if exclude_id:
            query = query.where(ContentAccount.id != exclude_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def list_accounts(self, *, keyword: str | None = None) -> list[ContentAccount]:
        query = select(ContentAccount)
        if keyword:
            pattern = f"%{keyword}%"
            query = query.where(or_(ContentAccount.name.ilike(pattern), ContentAccount.account_id.ilike(pattern)))
        result = await self.db.execute(query.order_by(ContentAccount.created_at.desc()))
        return list(result.scalars().all())

    async def create(self, data: dict[str, Any]) -> ContentAccount:
        account = ContentAccount(**data)
        self.db.add(account)
        await self.db.flush()
        return account

    async def update(self, account: ContentAccount, data: dict[str, Any]) -> ContentAccount:
        for key, value in data.items():
            setattr(account, key, value)
        account.updated_at = utc_now_naive()
        await self.db.flush()
        return account

    async def delete(self, account: ContentAccount) -> None:
        await self.db.delete(account)
        await self.db.flush()
