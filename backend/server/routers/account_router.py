from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services.account_service import (
    AccountCreate,
    AccountUpdate,
    create_account,
    delete_account,
    list_accounts,
    update_account,
)
from yuxi.storage.postgres.models_business import User

accounts = APIRouter(prefix="/accounts", tags=["accounts"])


@accounts.get("")
async def list_content_accounts(
    keyword: str | None = Query(default=None),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_accounts(db, keyword)


@accounts.post("")
async def create_content_account(
    payload: AccountCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_account(db, current_user, payload)


@accounts.patch("/{account_pk}")
async def update_content_account(
    account_pk: str,
    payload: AccountUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await update_account(db, account_pk, payload)


@accounts.delete("/{account_pk}")
async def delete_content_account(
    account_pk: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    return await delete_account(db, account_pk)
