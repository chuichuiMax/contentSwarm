from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_content import (
    ContentArtifactVersion,
    ContentDistributionJob,
    ContentDistributionResult,
    XiaohongshuAccount,
    XiaohongshuBrowserSession,
    XiaohongshuLoginSession,
)
from yuxi.utils.datetime_utils import utc_now_naive


class XiaohongshuRepository:
    """Persistence boundary for user-private Xiaohongshu data."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_accounts(self, owner_uid: str) -> list[XiaohongshuAccount]:
        rows = await self.db.execute(
            select(XiaohongshuAccount)
            .where(
                XiaohongshuAccount.owner_uid == owner_uid,
                XiaohongshuAccount.deleted_at.is_(None),
            )
            .order_by(XiaohongshuAccount.updated_at.desc())
        )
        return list(rows.scalars())

    async def create_account(self, *, owner_uid: str, display_name: str) -> XiaohongshuAccount:
        account = XiaohongshuAccount(
            id=f"xha_{uuid.uuid4().hex}",
            owner_uid=owner_uid,
            display_name=display_name,
        )
        self.db.add(account)
        await self.db.flush()
        return account

    async def get_account(
        self, account_id: str, owner_uid: str, *, for_update: bool = False
    ) -> XiaohongshuAccount | None:
        query = select(XiaohongshuAccount).where(
            XiaohongshuAccount.id == account_id,
            XiaohongshuAccount.owner_uid == owner_uid,
            XiaohongshuAccount.deleted_at.is_(None),
        )
        if for_update:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalar_one_or_none()

    async def get_account_for_worker(self, account_id: str, owner_uid: str) -> XiaohongshuAccount | None:
        row = await self.db.execute(
            select(XiaohongshuAccount).where(
                XiaohongshuAccount.id == account_id,
                XiaohongshuAccount.owner_uid == owner_uid,
            )
        )
        return row.scalar_one_or_none()

    async def get_accounts(self, account_ids: list[str], owner_uid: str) -> list[XiaohongshuAccount]:
        rows = await self.db.execute(
            select(XiaohongshuAccount).where(
                XiaohongshuAccount.id.in_(account_ids),
                XiaohongshuAccount.owner_uid == owner_uid,
                XiaohongshuAccount.deleted_at.is_(None),
            )
        )
        accounts_by_id = {item.id: item for item in rows.scalars()}
        return [accounts_by_id[item_id] for item_id in account_ids if item_id in accounts_by_id]

    async def delete_account(self, account: XiaohongshuAccount) -> None:
        account.enabled = False
        account.login_status = "unbound"
        account.deleted_at = utc_now_naive()
        account.display_name = f"{account.display_name[:100]} ({account.id[-8:]})"
        await self.db.flush()

    async def create_login_session(self, *, account: XiaohongshuAccount, expires_at) -> XiaohongshuLoginSession:
        session = XiaohongshuLoginSession(
            id=f"xhls_{uuid.uuid4().hex}",
            account_id=account.id,
            owner_uid=account.owner_uid,
            expires_at=expires_at,
        )
        self.db.add(session)
        await self.db.flush()
        return session

    async def get_pending_login_session(
        self, account_id: str, owner_uid: str, now: datetime
    ) -> XiaohongshuLoginSession | None:
        row = await self.db.execute(
            select(XiaohongshuLoginSession)
            .where(
                XiaohongshuLoginSession.account_id == account_id,
                XiaohongshuLoginSession.owner_uid == owner_uid,
                XiaohongshuLoginSession.status == "pending",
                XiaohongshuLoginSession.expires_at > now,
            )
            .order_by(XiaohongshuLoginSession.created_at.desc())
            .limit(1)
        )
        return row.scalar_one_or_none()

    async def get_login_session(
        self, session_id: str, owner_uid: str, *, for_update: bool = False
    ) -> XiaohongshuLoginSession | None:
        query = select(XiaohongshuLoginSession).where(
            XiaohongshuLoginSession.id == session_id,
            XiaohongshuLoginSession.owner_uid == owner_uid,
        )
        if for_update:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalar_one_or_none()

    async def get_login_session_for_worker(self, session_id: str) -> XiaohongshuLoginSession | None:
        return (
            await self.db.execute(select(XiaohongshuLoginSession).where(XiaohongshuLoginSession.id == session_id))
        ).scalar_one_or_none()

    async def get_browser_session(
        self, account_id: str, owner_uid: str, *, for_update: bool = False
    ) -> XiaohongshuBrowserSession | None:
        query = select(XiaohongshuBrowserSession).where(
            XiaohongshuBrowserSession.account_id == account_id,
            XiaohongshuBrowserSession.owner_uid == owner_uid,
        )
        if for_update:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalar_one_or_none()

    async def create_browser_session(
        self, *, owner_uid: str, account_id: str, session_id: str
    ) -> XiaohongshuBrowserSession:
        session = XiaohongshuBrowserSession(
            id=session_id,
            owner_uid=owner_uid,
            account_id=account_id,
            status="starting",
        )
        self.db.add(session)
        await self.db.flush()
        return session

    async def get_artifact_version(self, artifact_id: str, version: int) -> ContentArtifactVersion | None:
        row = await self.db.execute(
            select(ContentArtifactVersion).where(
                ContentArtifactVersion.artifact_id == artifact_id,
                ContentArtifactVersion.version == version,
            )
        )
        return row.scalar_one_or_none()

    async def create_distribution_job(
        self,
        *,
        owner_uid: str,
        artifact_id: str,
        artifact_version: int,
        mode: str,
        payload_snapshot: dict[str, Any],
        idempotency_key: str,
        dedupe_key: str | None,
        accounts: list[XiaohongshuAccount],
        confirmed_by: str | None = None,
        confirmed_at: datetime | None = None,
    ) -> ContentDistributionJob:
        job = ContentDistributionJob(
            id=f"cdj_{uuid.uuid4().hex}",
            owner_uid=owner_uid,
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            mode=mode,
            payload_snapshot=payload_snapshot,
            idempotency_key=idempotency_key,
            dedupe_key=dedupe_key,
            confirmed_by=confirmed_by,
            confirmed_at=confirmed_at,
        )
        self.db.add(job)
        await self.db.flush()
        for account in accounts:
            self.db.add(
                ContentDistributionResult(
                    id=f"cdr_{uuid.uuid4().hex}",
                    job_id=job.id,
                    account_id=account.id,
                )
            )
        await self.db.flush()
        return job

    async def get_distribution_job(
        self, job_id: str, owner_uid: str, *, for_update: bool = False
    ) -> ContentDistributionJob | None:
        query = select(ContentDistributionJob).where(
            ContentDistributionJob.id == job_id,
            ContentDistributionJob.owner_uid == owner_uid,
        )
        if for_update:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalar_one_or_none()

    async def get_distribution_job_for_worker(self, job_id: str) -> ContentDistributionJob | None:
        return (
            await self.db.execute(
                select(ContentDistributionJob).where(ContentDistributionJob.id == job_id).with_for_update()
            )
        ).scalar_one_or_none()

    async def get_job_by_idempotency_key(self, idempotency_key: str, owner_uid: str) -> ContentDistributionJob | None:
        row = await self.db.execute(
            select(ContentDistributionJob).where(
                ContentDistributionJob.idempotency_key == idempotency_key,
                ContentDistributionJob.owner_uid == owner_uid,
            )
        )
        return row.scalar_one_or_none()

    async def get_recent_publish_job(
        self, dedupe_key: str, owner_uid: str, created_after: datetime
    ) -> ContentDistributionJob | None:
        row = await self.db.execute(
            select(ContentDistributionJob)
            .where(
                ContentDistributionJob.dedupe_key == dedupe_key,
                ContentDistributionJob.owner_uid == owner_uid,
                ContentDistributionJob.mode == "publish",
                ContentDistributionJob.created_at >= created_after,
                ContentDistributionJob.status.in_(("queued", "running", "completed", "partial_failed", "uncertain")),
            )
            .order_by(ContentDistributionJob.created_at.desc())
            .limit(1)
        )
        return row.scalar_one_or_none()

    async def list_distribution_results(self, job_id: str) -> list[ContentDistributionResult]:
        rows = await self.db.execute(
            select(ContentDistributionResult)
            .where(ContentDistributionResult.job_id == job_id)
            .order_by(ContentDistributionResult.created_at.asc())
        )
        return list(rows.scalars())

    async def list_artifact_jobs(self, artifact_id: str, owner_uid: str) -> list[ContentDistributionJob]:
        rows = await self.db.execute(
            select(ContentDistributionJob)
            .where(
                ContentDistributionJob.artifact_id == artifact_id,
                ContentDistributionJob.owner_uid == owner_uid,
            )
            .order_by(ContentDistributionJob.created_at.desc())
            .limit(100)
        )
        return list(rows.scalars())

    async def get_result(self, result_id: str, owner_uid: str) -> ContentDistributionResult | None:
        row = await self.db.execute(
            select(ContentDistributionResult)
            .join(ContentDistributionJob, ContentDistributionJob.id == ContentDistributionResult.job_id)
            .where(
                ContentDistributionResult.id == result_id,
                ContentDistributionJob.owner_uid == owner_uid,
            )
        )
        return row.scalar_one_or_none()

    async def delete_completed_login_sessions(self, account_id: str) -> None:
        await self.db.execute(
            delete(XiaohongshuLoginSession).where(
                XiaohongshuLoginSession.account_id == account_id,
                XiaohongshuLoginSession.status.in_(("completed", "expired", "failed")),
            )
        )
