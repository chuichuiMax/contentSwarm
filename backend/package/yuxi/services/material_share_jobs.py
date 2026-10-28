"""Durable, idempotent case-share requests, accepted only after a channel click."""

from __future__ import annotations

import asyncio
import html
import json
import logging
from datetime import timedelta
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB, insert

from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.material_library_service import (
    MaterialShareCreate,
    prepare_material_share_snapshot,
    serialize_public_material_share,
    store_material_share_snapshot,
)
from yuxi.services.run_queue_service import get_arq_pool
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentMaterialLibraryItem,
    ContentMaterialShare,
    ContentMaterialShareJob,
)
from yuxi.utils.datetime_utils import utc_now_naive

logger = logging.getLogger(__name__)
SHARE_FIELDS = (
    "id",
    "token",
    "owner_uid",
    "category_id",
    "title",
    "building_name",
    "area",
    "design_style",
    "sharer_name",
    "sharer_phone",
    "sharer_uid",
    "sharer_employee_id",
    "sharer_employee_name",
    "sharer_employee_code",
    "sharer_branch",
    "sharer_department_id",
    "source_platform",
    "mp_entry_code",
)
SOURCE_FIELDS = (
    "id",
    "sha256",
    "bucket_name",
    "object_name",
    "content_type",
    "original_file_name",
    "file_size",
    "image_width",
    "image_height",
    "metadata_json",
)


class MaterialShareRequest(MaterialShareCreate):
    # Optional for older mini-programs, which still use the synchronous endpoint.
    request_token: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def check_same_request(job, user, payload):
    contact_changed = payload.contact_name is not None and (
        job.share_json.get("sharer_name") != payload.contact_name
        or job.share_json.get("sharer_phone") != payload.contact_phone
    )
    if job.requester_uid != str(user.uid) or job.item_ids != payload.item_ids or contact_changed:
        raise HTTPException(
            409,
            detail={
                "error": {"code": "MATERIAL_SHARE_REQUEST_CONFLICT", "message": "分享标识已绑定其他请求，请重新分享"}
            },
        )


async def enqueue_share(token: str) -> None:
    pool = await get_arq_pool()
    await pool.enqueue_job("process_material_share", token, _job_id=f"material-share:{token}")


async def accept_material_share(db, user, payload: MaterialShareRequest, employee):
    existing = await db.get(ContentMaterialShareJob, payload.request_token)
    if existing is not None:
        check_same_request(existing, user, payload)
        return await read_material_share_state(db, existing.token)

    failure = None
    share_data = {"sharer_name": payload.contact_name, "sharer_phone": payload.contact_phone}
    sources = []
    try:
        # The delete endpoint takes these same locks before deleting storage objects.
        await db.execute(
            select(ContentMaterialLibraryItem.id)
            .where(ContentMaterialLibraryItem.id.in_(payload.item_ids))
            .order_by(ContentMaterialLibraryItem.id)
            .with_for_update()
        )
        share, assets = await prepare_material_share_snapshot(
            db, user, payload, sharer_employee=employee, source_platform="mp"
        )
        locked_assets = (
            await db.scalars(
                select(ContentCoverAsset)
                .where(ContentCoverAsset.id.in_([asset.id for asset in assets]))
                .order_by(ContentCoverAsset.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
        if len(locked_assets) != len(assets) or any(asset.deleted_at is not None for asset in locked_assets):
            raise HTTPException(404, detail={"error": {"code": "MATERIAL_NOT_FOUND", "message": "所选图片已被删除"}})
        share.token = payload.request_token
        share_data = {key: getattr(share, key) for key in SHARE_FIELDS}
        sources = [{key: getattr(asset, key) for key in SOURCE_FIELDS} for asset in assets]
    except HTTPException as exc:
        failure = exc

    error = failure.detail.get("error", {}) if failure and isinstance(failure.detail, dict) else {}
    inserted = await db.scalar(
        insert(ContentMaterialShareJob)
        .values(
            token=payload.request_token,
            requester_uid=str(user.uid),
            item_ids=payload.item_ids,
            share_json=share_data,
            sources_json=sources,
            status="failed" if failure else "pending",
            error_code=error.get("code"),
            completed_at=utc_now_naive() if failure else None,
        )
        .on_conflict_do_nothing(index_elements=["token"])
        .returning(ContentMaterialShareJob.token)
    )
    await db.commit()
    job = await db.get(ContentMaterialShareJob, payload.request_token, populate_existing=True)
    check_same_request(job, user, payload)
    if failure and inserted:
        raise failure
    if job.status == "pending":
        try:
            await asyncio.wait_for(enqueue_share(job.token), timeout=2)
        except Exception:
            # The committed row is the outbox; the periodic dispatcher retries enqueueing.
            logger.exception("Case-share enqueue delayed; durable request retained")
    return await read_material_share_state(db, job.token)


async def read_material_share_state(db, token: str):
    # Read the job first. A worker may commit between these queries; never return
    # completed from the job alone after an earlier query missed the snapshot.
    job = await db.get(ContentMaterialShareJob, token, populate_existing=True)
    share, items = await MaterialLibraryRepository(db).get_share_with_items(token)
    if share is not None:
        result = serialize_public_material_share(share, items)
        result["share"]["status"] = "completed"
        return result
    if job is None:
        return {"share": {"id": token, "status": "unknown"}}
    if job.status == "completed":
        # Deletion can commit after the first job read and before the snapshot read.
        job = await db.get(ContentMaterialShareJob, token, populate_existing=True)
        if job.status != "deleted":
            raise RuntimeError("Completed case-share job has no committed snapshot")
    if job.status == "deleted":
        raise HTTPException(410, detail={"error": {"code": "MATERIAL_SHARE_DELETED", "message": "案例分享已失效"}})
    # Failure responses never expose selected private IDs, storage paths or internal errors.
    return {"share": {"id": token, "status": job.status, "title": job.share_json.get("title", "案例分享")}}


async def asset_has_pending_share(db, asset_id: str) -> bool:
    return (
        await db.scalar(
            select(ContentMaterialShareJob.token)
            .where(
                ContentMaterialShareJob.status == "pending",
                cast(ContentMaterialShareJob.sources_json, JSONB).contains([{"id": asset_id}]),
            )
            .limit(1)
        )
        is not None
    )


def render_share_status_page(token: str, state: dict, public_base_url: str) -> str:
    messages = {
        "pending": "案例正在准备中",
        "failed": "案例生成失败，请联系分享人重新分享",
        "unknown": "案例暂未就绪，请稍后重试",
    }
    status = state["share"]["status"]
    title = html.escape(state["share"].get("title") or "案例分享")
    endpoint = json.dumps(f"{public_base_url.rstrip('/')}/api/material-library/shares/{token}").replace("<", "\\u003c")
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{font-family:system-ui;margin:0;background:#f5f5f5;color:#333}}main{{padding:30vh 24px;text-align:center}}
button{{padding:10px 24px;border:0;border-radius:6px;background:#fff;font-size:16px}}</style>
<main><h2>{title}</h2><p id="state">{messages[status]}</p><button onclick="location.reload()">重新加载</button></main>
<script>
const messages={json.dumps(messages, ensure_ascii=False)};let attempts=0;
async function poll(){{
  if(++attempts>15)return;
  try{{const response=await fetch({endpoint},{{cache:'no-store'}});
    if(!response.ok)throw new Error('request failed');
    const data=await response.json();const state=data.share.status||'completed';
    if(state==='completed'){{location.reload();return;}}
    document.getElementById('state').textContent=messages[state]||messages.unknown;
    if(state==='failed')return;
  }}catch(error){{document.getElementById('state').textContent='案例暂时无法查看，请稍后重试';}}
  setTimeout(poll,2000);
}}
if({json.dumps(status)}!=='failed')setTimeout(poll,1000);
</script></html>"""


async def process_material_share(ctx, token: str) -> None:
    del ctx
    async with pg_manager.get_async_session_context() as db:
        job = await db.scalar(
            select(ContentMaterialShareJob)
            .where(
                ContentMaterialShareJob.token == token,
                ContentMaterialShareJob.status == "pending",
            )
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return
        try:
            user = await db.scalar(select(User).where(User.uid == job.requester_uid, User.is_deleted == 0))
            if user is None:
                raise ValueError("share requester no longer exists")
            share = ContentMaterialShare(**job.share_json)
            share.created_at = job.created_at
            assets = [SimpleNamespace(**source) for source in job.sources_json]
            # Snapshot insertion and completed state commit atomically in the storage service.
            job.status = "completed"
            job.completed_at = utc_now_naive()
            await store_material_share_snapshot(db, user, share, assets)
        except asyncio.CancelledError:
            # Keep the durable pending row; another worker/dispatcher can resume it.
            await db.rollback()
            raise
        except Exception:
            await db.rollback()
            logger.exception("Case-share snapshot generation failed")
            job = await db.scalar(
                select(ContentMaterialShareJob)
                .where(
                    ContentMaterialShareJob.token == token,
                )
                .with_for_update()
            )
            # A post-commit cache failure must not turn a completed share into a failure.
            if job.status == "pending":
                job.status = "failed"
                job.error_code = "MATERIAL_SHARE_GENERATION_FAILED"
                job.completed_at = utc_now_naive()
                await db.commit()


async def dispatch_material_shares(ctx) -> None:
    del ctx
    async with pg_manager.get_async_session_context() as db:
        jobs = (
            await db.scalars(
                select(ContentMaterialShareJob)
                .where(
                    ContentMaterialShareJob.status == "pending",
                )
                .order_by(ContentMaterialShareJob.created_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        ).all()
        tokens = []
        for job in jobs:
            if job.created_at < utc_now_naive() - timedelta(minutes=30):
                job.status = "failed"
                job.error_code = "MATERIAL_SHARE_QUEUE_TIMEOUT"
                job.completed_at = utc_now_naive()
                continue
            tokens.append(job.token)
        # Release dispatch locks before enqueueing, so a fast worker can take the row.
        await db.commit()
        for token in tokens:
            try:
                await asyncio.wait_for(enqueue_share(token), timeout=2)
            except Exception:
                logger.exception("Case-share outbox dispatch failed")
                break
