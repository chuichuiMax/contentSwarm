from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.account_repository import AccountRepository
from yuxi.storage.postgres.models_business import User

AccountType = Literal["enterprise", "personal"]


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    account_id: str = Field(min_length=1, max_length=64)
    account_type: AccountType
    following_count: int = Field(default=0, ge=0)
    follower_count: int = Field(default=0, ge=0)
    likes_count: int = Field(default=0, ge=0)
    works_count: int = Field(default=0, ge=0)
    enabled: bool = True


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    account_id: str | None = Field(default=None, min_length=1, max_length=64)
    account_type: AccountType | None = None
    following_count: int | None = Field(default=None, ge=0)
    follower_count: int | None = Field(default=None, ge=0)
    likes_count: int | None = Field(default=None, ge=0)
    works_count: int | None = Field(default=None, ge=0)
    enabled: bool | None = None


def _account_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


def _normalize_text(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise _account_error(422, "ACCOUNT_INVALID_FIELD", f"{field} 不能为空")
    return normalized


async def list_accounts(db: AsyncSession, keyword: str | None = None) -> dict[str, Any]:
    items = await AccountRepository(db).list_accounts(keyword=keyword.strip() if keyword else None)
    return {"accounts": [item.to_dict() for item in items], "total": len(items)}


async def create_account(db: AsyncSession, user: User, payload: AccountCreate) -> dict[str, Any]:
    repo = AccountRepository(db)
    account_id = _normalize_text(payload.account_id, field="ID")
    if await repo.get_by_account_id(account_id):
        raise _account_error(409, "ACCOUNT_ID_EXISTS", "账号 ID 已存在")
    try:
        account = await repo.create(
            {
                "id": str(uuid.uuid4()),
                "account_id": account_id,
                "name": _normalize_text(payload.name, field="账号名称"),
                "account_type": payload.account_type,
                "following_count": payload.following_count,
                "follower_count": payload.follower_count,
                "likes_count": payload.likes_count,
                "works_count": payload.works_count,
                "enabled": payload.enabled,
                "created_by": str(user.uid),
            }
        )
    except IntegrityError as exc:
        raise _account_error(409, "ACCOUNT_ID_EXISTS", "账号 ID 已存在") from exc
    return {"account": account.to_dict()}


async def update_account(db: AsyncSession, account_pk: str, payload: AccountUpdate) -> dict[str, Any]:
    repo = AccountRepository(db)
    account = await repo.get(account_pk)
    if account is None:
        raise _account_error(404, "ACCOUNT_NOT_FOUND", "账号不存在")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        data["name"] = _normalize_text(data["name"], field="账号名称")
    if "account_id" in data:
        data["account_id"] = _normalize_text(data["account_id"], field="ID")
        existing = await repo.get_by_account_id(data["account_id"], exclude_id=account.id)
        if existing is not None:
            raise _account_error(409, "ACCOUNT_ID_EXISTS", "账号 ID 已存在")
    try:
        account = await repo.update(account, data)
    except IntegrityError as exc:
        raise _account_error(409, "ACCOUNT_ID_EXISTS", "账号 ID 已存在") from exc
    return {"account": account.to_dict()}


async def delete_account(db: AsyncSession, account_pk: str) -> dict[str, Any]:
    repo = AccountRepository(db)
    account = await repo.get(account_pk)
    if account is None:
        raise _account_error(404, "ACCOUNT_NOT_FOUND", "账号不存在")
    await repo.delete(account)
    return {"success": True, "id": account_pk}
