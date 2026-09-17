from __future__ import annotations

import hashlib
import asyncio
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.run_queue_service import get_arq_pool
from yuxi.services.material_library_service import (
    MATERIAL_LIBRARY_BUCKET,
    MAX_MATERIAL_BYTES,
    _normalize_image,
    _owner_uid,
    _tenant_id,
)
from yuxi.storage.minio import StorageError, get_minio_client
from yuxi.storage.postgres.models_business import OperationLog, User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
    RemoteMaterialSyncJob,
)
from yuxi.utils.datetime_utils import utc_now_naive


REMOTE_SOURCE = "visioflow"
REMOTE_ROOT_NAME = "10.10.10.50:8088"
DEFAULT_REMOTE_BASE_URL = "http://10.10.10.50:8088"
REMOTE_PAGE_SIZE = 100
REMOTE_TIMEOUT_SECONDS = max(10.0, float(os.getenv("REMOTE_MATERIAL_TIMEOUT_SECONDS", "60")))
REMOTE_DOWNLOAD_CONCURRENCY = max(1, int(os.getenv("REMOTE_MATERIAL_DOWNLOAD_CONCURRENCY", "4")))
REMOTE_SYNC_BATCH_SIZE = max(1, int(os.getenv("REMOTE_MATERIAL_SYNC_BATCH_SIZE", "12")))
REMOTE_SYNC_ACTIVE_STATUSES = {"queued", "running"}
RemoteProgressCallback = Callable[..., Awaitable[None]]


class RemoteMaterialConfigUpdate(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=500)

    @field_validator("username", "password")
    @classmethod
    def strip_required_value(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value


def _remote_error(
    message: str,
    *,
    code: str = "REMOTE_MATERIAL_SYNC_FAILED",
    status_code: int = 502,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message}},
    )


def _sync_error(message: str, status_code: int = 502) -> HTTPException:
    return _remote_error(message, status_code=status_code)


class VisioFlowMaterialClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ):
        self.base_url = (base_url or os.getenv("REMOTE_MATERIAL_BASE_URL") or DEFAULT_REMOTE_BASE_URL).rstrip("/")
        self.token = token if token is not None else os.getenv("REMOTE_MATERIAL_TOKEN", "")
        self.username = username if username is not None else os.getenv("REMOTE_MATERIAL_USERNAME", "")
        self.password = password if password is not None else os.getenv("REMOTE_MATERIAL_PASSWORD", "")
        self.csrf_token = ""

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        if self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
        return headers

    async def authenticate(self, client: httpx.AsyncClient) -> None:
        if self.token or self.csrf_token:
            return
        if not self.username or not self.password:
            raise _remote_error(
                "远程素材库尚未配置登录账号和密码",
                code="REMOTE_MATERIAL_CONFIG_REQUIRED",
                status_code=409,
            )
        try:
            response = await client.post(
                f"{self.base_url}/api/v1/auth/login",
                json={"username": self.username, "password": self.password},
            )
            response.raise_for_status()
            payload = response.json()
            self.csrf_token = str(payload.get("csrf_token") or "")
            if not self.csrf_token:
                raise ValueError("csrf_token missing")
        except (httpx.HTTPError, ValueError) as exc:
            raise _remote_error(
                "远程素材库登录失败，请检查账号密码",
                code="REMOTE_MATERIAL_AUTH_FAILED",
            ) from exc

    async def _get(self, client: httpx.AsyncClient, path: str, **params: Any) -> Any:
        try:
            response = await client.get(path, params=params, headers=self._headers())
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise _sync_error(f"远程素材库请求失败: {path}") from exc

    async def list_groups(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        await self.authenticate(client)
        payload = await self._get(client, f"{self.base_url}/api/v1/library/groups")
        if isinstance(payload, dict):
            payload = payload.get("items") or payload.get("groups") or []
        return [item for item in payload if isinstance(item, dict) and item.get("id")]

    async def list_assets(
        self,
        client: httpx.AsyncClient,
        *,
        group_id: str,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        payload = await self._get(
            client,
            f"{self.base_url}/api/v1/library/assets",
            limit=REMOTE_PAGE_SIZE,
            offset=offset,
            group_id=group_id,
        )
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict) and item.get("id")], len(payload)
        items = payload.get("items") or []
        return [item for item in items if isinstance(item, dict) and item.get("id")], int(payload.get("total") or 0)

    async def presign_download(self, client: httpx.AsyncClient, object_key: str) -> str:
        try:
            response = await client.post(
                f"{self.base_url}/api/v1/uploads/presign-download",
                json={"object_key": object_key},
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()
            url = payload.get("download_url") if isinstance(payload, dict) else None
            if not url:
                raise ValueError("download_url missing")
            return str(url)
        except (httpx.HTTPError, ValueError) as exc:
            raise _sync_error("远程素材下载地址获取失败") from exc

    async def download(self, client: httpx.AsyncClient, object_key: str) -> bytes:
        url = await self.presign_download(client, object_key)
        try:
            response = await client.get(url)
            response.raise_for_status()
            if len(response.content) > MAX_MATERIAL_BYTES:
                raise _sync_error("远程图片超过 20 MB 限制", 422)
            return response.content
        except HTTPException:
            raise
        except httpx.HTTPError as exc:
            raise _sync_error("远程图片下载失败") from exc


async def resolve_remote_material_client(db: AsyncSession) -> VisioFlowMaterialClient:
    setting = await MaterialLibraryRepository(db).get_remote_setting()
    if setting is not None:
        return VisioFlowMaterialClient(
            base_url=setting.base_url,
            token="",
            username=setting.username,
            password=setting.password,
        )
    return VisioFlowMaterialClient()


async def get_remote_material_config_state(db: AsyncSession, user: User) -> dict[str, Any]:
    setting = await MaterialLibraryRepository(db).get_remote_setting()
    if setting is not None:
        return {
            "base_url": setting.base_url,
            "configured": bool(setting.username and setting.password),
            "source": "database",
            "can_manage": user.role == "superadmin",
            "verification_status": setting.verification_status,
            "verified_at": setting.verified_at,
        }
    client = VisioFlowMaterialClient()
    return {
        "base_url": client.base_url,
        "configured": bool(client.token or (client.username and client.password)),
        "source": "environment",
        "can_manage": user.role == "superadmin",
        "verification_status": "environment"
        if client.token or (client.username and client.password)
        else "unconfigured",
        "verified_at": None,
    }


async def verify_and_save_remote_material_config(
    db: AsyncSession,
    user: User,
    payload: RemoteMaterialConfigUpdate,
) -> dict[str, Any]:
    client_api = VisioFlowMaterialClient(token="", username=payload.username, password=payload.password)
    timeout = httpx.Timeout(REMOTE_TIMEOUT_SECONDS, connect=min(15.0, REMOTE_TIMEOUT_SECONDS))
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        await client_api.authenticate(client)
    await MaterialLibraryRepository(db).upsert_remote_setting(
        base_url=client_api.base_url,
        username=payload.username,
        password=payload.password,
        verification_status="verified",
        verified_at=utc_now_naive(),
        updated_by=user.id,
    )
    db.add(OperationLog(user_id=user.id, operation="material.remote_config.update", details=client_api.base_url))
    await db.commit()
    return await get_remote_material_config_state(db, user)


async def create_remote_material_sync_job(db: AsyncSession, user: User) -> dict[str, Any]:
    setting = await MaterialLibraryRepository(db).get_remote_setting(for_update=True)
    if setting is None:
        client = VisioFlowMaterialClient()
        if not client.token and not (client.username and client.password):
            raise _remote_error(
                "远程素材库尚未配置登录账号和密码",
                code="REMOTE_MATERIAL_CONFIG_REQUIRED",
                status_code=409,
            )
    active_job = await db.scalar(
        select(RemoteMaterialSyncJob)
        .where(RemoteMaterialSyncJob.status.in_(REMOTE_SYNC_ACTIVE_STATUSES))
        .order_by(RemoteMaterialSyncJob.created_at.desc())
        .limit(1)
    )
    if active_job is not None:
        await db.commit()
        return {"job": active_job.to_dict(), "reused": True}

    job = RemoteMaterialSyncJob(
        id=f"rmsj_{uuid.uuid4().hex}",
        owner_uid=_owner_uid(user),
        tenant_id=_tenant_id(user),
        requested_by=user.id,
        status="queued",
        phase="queued",
        progress=0,
    )
    db.add(job)
    await db.commit()
    try:
        queue = await get_arq_pool()
        queued = await queue.enqueue_job("process_remote_material_sync_job", job.id, _job_id=f"remote-sync:{job.id}")
    except Exception as exc:
        job.status = "failed"
        job.phase = "failed"
        job.error_code = "REMOTE_MATERIAL_QUEUE_UNAVAILABLE"
        job.error_message = "远程素材同步队列暂不可用"
        job.completed_at = utc_now_naive()
        await db.commit()
        raise _remote_error(job.error_message, code=job.error_code, status_code=503) from exc
    if queued is None:
        job.status = "failed"
        job.phase = "failed"
        job.error_code = "REMOTE_MATERIAL_QUEUE_REJECTED"
        job.error_message = "远程素材同步任务未能进入执行队列"
        job.completed_at = utc_now_naive()
        await db.commit()
        raise _remote_error(job.error_message, code=job.error_code, status_code=503)
    return {"job": job.to_dict(), "reused": False}


async def get_remote_material_sync_job(
    db: AsyncSession,
    *,
    job_id: str | None = None,
) -> dict[str, Any]:
    query = select(RemoteMaterialSyncJob)
    if job_id:
        query = query.where(RemoteMaterialSyncJob.id == job_id)
    job = await db.scalar(query.order_by(RemoteMaterialSyncJob.created_at.desc()).limit(1))
    if job_id and job is None:
        raise _remote_error("远程素材同步任务不存在", code="REMOTE_MATERIAL_JOB_NOT_FOUND", status_code=404)
    return {"job": job.to_dict() if job is not None else None}


def _safe_filename(name: str, fallback: str) -> str:
    name = Path(str(name or "")).name.strip() or fallback
    return re.sub(r"[^\w.\-一-龥 ]", "_", name)[:255] or fallback


def _category_id(first_tag: str) -> str:
    """Use the first tag as the gallery identity, while keeping the DB id compact and stable."""
    tag_key = hashlib.sha1(first_tag.encode("utf-8")).hexdigest()[:20]
    return f"rmlc_tag_{tag_key}"


def _asset_id(remote_id: str) -> str:
    return f"rca_{remote_id}"[:64]


def _item_id(remote_id: str) -> str:
    return f"rmi_{remote_id}"[:64]


def _metadata(asset: dict[str, Any], group: dict[str, Any], base_url: str) -> dict[str, Any]:
    return {
        "source": REMOTE_SOURCE,
        "remote_base_url": base_url,
        "remote_asset_id": str(asset["id"]),
        "remote_group_id": str(group["id"]),
        "remote_object_key": asset.get("original_object_key") or "",
    }


async def _download_remote_asset(
    semaphore: asyncio.Semaphore,
    client_api: VisioFlowMaterialClient,
    client: httpx.AsyncClient,
    owner_uid: str,
    remote_asset: dict[str, Any],
) -> dict[str, Any]:
    async with semaphore:
        remote_id = str(remote_asset["id"])
        raw = await client_api.download(client, str(remote_asset.get("original_object_key") or ""))
        normalized, width, height, content_type = await asyncio.to_thread(_normalize_image, raw)
        try:
            uploaded = await get_minio_client().aupload_file(
                bucket_name=MATERIAL_LIBRARY_BUCKET,
                object_name=f"material-library/{owner_uid}/remote/{remote_id}/image.png",
                data=normalized,
                content_type=content_type,
            )
        except StorageError as exc:
            raise _sync_error("远程图片保存到素材库失败", 500) from exc
        return {
            "remote_id": remote_id,
            "uploaded": uploaded,
            "content_type": content_type,
            "file_size": len(normalized),
            "width": width,
            "height": height,
            "sha256": hashlib.sha256(normalized).hexdigest(),
        }


async def sync_remote_material_library(
    db: AsyncSession,
    user: User,
    *,
    progress_callback: RemoteProgressCallback | None = None,
) -> dict[str, Any]:
    """Mirror active VisioFlow images with concurrent transfers and batched commits."""

    async def report(**values: Any) -> None:
        if progress_callback is not None:
            await progress_callback(**values)

    owner_uid = _owner_uid(user)
    tenant_id = _tenant_id(user)
    client_api = await resolve_remote_material_client(db)
    timeout = httpx.Timeout(REMOTE_TIMEOUT_SECONDS, connect=min(15.0, REMOTE_TIMEOUT_SECONDS))
    repo = MaterialLibraryRepository(db, include_shared=True)
    groups_seen: set[str] = set()
    assets_seen: set[str] = set()
    category_ids_seen: set[str] = set()
    counters = {"groups": 0, "assets": 0, "created": 0, "updated": 0, "disabled": 0, "failed": 0}

    await report(phase="authenticating", progress=2)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        groups = [group for group in await client_api.list_groups(client) if group.get("status") in {None, "active"}]
        total_groups = len(groups)
        discovered: list[dict[str, Any]] = []
        await report(phase="discovering", progress=5, total_groups=total_groups, processed_groups=0)
        for group_index, group in enumerate(groups, start=1):
            group_id = str(group["id"])
            tags = [str(tag).strip() for tag in (group.get("tags") or []) if str(tag).strip()]
            assets_for_group: list[dict[str, Any]] = []
            offset = 0
            while True:
                assets, total = await client_api.list_assets(client, group_id=group_id, offset=offset)
                if not assets:
                    break
                assets_for_group.extend(asset for asset in assets if asset.get("status") in {None, "active"})
                offset += len(assets)
                if len(assets) < REMOTE_PAGE_SIZE or (total and offset >= total):
                    break
            discovered.append(
                {
                    "group": group,
                    "group_id": group_id,
                    "tags": tags,
                    "first_tag": tags[0] if tags else "未命名素材组",
                    "assets": assets_for_group,
                }
            )
            await report(
                phase="discovering",
                progress=5 + int(group_index * 5 / max(total_groups, 1)),
                total_groups=total_groups,
                processed_groups=group_index,
                total_assets=sum(len(item["assets"]) for item in discovered),
            )

        total_assets = sum(len(item["assets"]) for item in discovered)
        counters["groups"] = total_groups
        categories = await repo.list_categories(owner_uid, "image")
        root = next((item for item in categories if item.parent_id is None and item.name == REMOTE_ROOT_NAME), None)
        if root is None:
            root = (
                await db.execute(
                    select(ContentMaterialCategory)
                    .where(
                        ContentMaterialCategory.owner_uid == owner_uid,
                        ContentMaterialCategory.material_type == "image",
                        ContentMaterialCategory.parent_id.is_(None),
                        ContentMaterialCategory.name == REMOTE_ROOT_NAME,
                    )
                    .order_by(ContentMaterialCategory.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if root is None:
            root = await repo.create_category(
                owner_uid=owner_uid,
                visibility="enterprise",
                id=f"rmlroot_{uuid.uuid4().hex}",
                tenant_id=tenant_id,
                material_type="image",
                parent_id=None,
                industry_slug="uncategorized",
                name=REMOTE_ROOT_NAME,
                description="VisioFlow 远程素材库同步",
                sort_order=(max((item.sort_order for item in categories), default=0) + 10),
                is_system=False,
            )
        else:
            root.visibility = "enterprise"
            root.deleted_at = None
        await db.commit()

        categories_by_first_tag: dict[str, ContentMaterialCategory] = {}
        semaphore = asyncio.Semaphore(REMOTE_DOWNLOAD_CONCURRENCY)
        processed_groups = 0
        for group_entry in discovered:
            group = group_entry["group"]
            group_id = group_entry["group_id"]
            tags = group_entry["tags"]
            first_tag = group_entry["first_tag"]
            group_assets = group_entry["assets"]
            groups_seen.add(group_id)
            category = categories_by_first_tag.get(first_tag)
            category_id = _category_id(first_tag)
            if category is None:
                category = await repo.get_category(owner_uid, "image", category_id, for_update=True)
            if category is None:
                category = (
                    await db.execute(
                        select(ContentMaterialCategory).where(
                            ContentMaterialCategory.owner_uid == owner_uid,
                            ContentMaterialCategory.material_type == "image",
                            ContentMaterialCategory.id == category_id,
                        )
                    )
                ).scalar_one_or_none()
            if category is None:
                category = await repo.create_category(
                    owner_uid=owner_uid,
                    visibility="enterprise",
                    id=category_id,
                    tenant_id=tenant_id,
                    material_type="image",
                    parent_id=root.id,
                    industry_slug="uncategorized",
                    name=first_tag,
                    description="VisioFlow 素材组首标签图库",
                    sort_order=(group.get("sort_order") or 0),
                    is_system=False,
                )
            else:
                category.name = first_tag
                category.parent_id = root.id
                category.visibility = "enterprise"
                category.deleted_at = None
            categories_by_first_tag[first_tag] = category
            category_ids_seen.add(category.id)
            await db.commit()

            for batch_start in range(0, len(group_assets), REMOTE_SYNC_BATCH_SIZE):
                batch = group_assets[batch_start : batch_start + REMOTE_SYNC_BATCH_SIZE]
                prepared: list[dict[str, Any]] = []
                for remote_asset in batch:
                    remote_id = str(remote_asset["id"])
                    assets_seen.add(remote_id)
                    local_asset_id = _asset_id(remote_id)
                    asset = await db.scalar(
                        select(ContentCoverAsset).where(
                            ContentCoverAsset.id == local_asset_id,
                            ContentCoverAsset.owner_uid == owner_uid,
                        )
                    )
                    item = await db.scalar(
                        select(ContentMaterialLibraryItem).where(
                            ContentMaterialLibraryItem.id == _item_id(remote_id),
                            ContentMaterialLibraryItem.owner_uid == owner_uid,
                        )
                    )
                    if item is None:
                        item = await repo.get_item_by_asset(local_asset_id)
                    object_key = str(remote_asset.get("original_object_key") or "")
                    old_metadata = (asset.metadata_json or {}) if asset else {}
                    prepared.append(
                        {
                            "remote_asset": remote_asset,
                            "remote_id": remote_id,
                            "local_asset_id": local_asset_id,
                            "asset": asset,
                            "item": item,
                            "metadata": _metadata(remote_asset, group, client_api.base_url),
                            "needs_download": asset is None or old_metadata.get("remote_object_key") != object_key,
                        }
                    )

                downloads = await asyncio.gather(
                    *[
                        _download_remote_asset(semaphore, client_api, client, owner_uid, entry["remote_asset"])
                        for entry in prepared
                        if entry["needs_download"]
                    ]
                )
                downloaded_by_id = {result["remote_id"]: result for result in downloads}
                for entry in prepared:
                    remote_asset = entry["remote_asset"]
                    remote_id = entry["remote_id"]
                    asset = entry["asset"]
                    item = entry["item"]
                    metadata = entry["metadata"]
                    downloaded = downloaded_by_id.get(remote_id)
                    if downloaded is not None:
                        if asset is None:
                            asset = ContentCoverAsset(
                                id=entry["local_asset_id"],
                                owner_uid=owner_uid,
                                tenant_id=tenant_id,
                                content_task_id=None,
                                role="library_image",
                                original_file_name=_safe_filename(
                                    remote_asset.get("original_filename"), f"{remote_id}.png"
                                ),
                                content_type=downloaded["content_type"],
                                file_size=downloaded["file_size"],
                                image_width=downloaded["width"],
                                image_height=downloaded["height"],
                                sha256=downloaded["sha256"],
                                bucket_name=downloaded["uploaded"].bucket_name,
                                object_name=downloaded["uploaded"].object_name,
                                metadata_json=metadata,
                            )
                            db.add(asset)
                            await db.flush()
                        else:
                            asset.original_file_name = _safe_filename(
                                remote_asset.get("original_filename"), f"{remote_id}.png"
                            )
                            asset.content_type = downloaded["content_type"]
                            asset.file_size = downloaded["file_size"]
                            asset.image_width = downloaded["width"]
                            asset.image_height = downloaded["height"]
                            asset.sha256 = downloaded["sha256"]
                            asset.bucket_name = downloaded["uploaded"].bucket_name
                            asset.object_name = downloaded["uploaded"].object_name
                            asset.metadata_json = metadata
                            asset.deleted_at = None
                    elif asset is not None:
                        asset.deleted_at = None
                    if item is None:
                        item = ContentMaterialLibraryItem(
                            id=_item_id(remote_id),
                            owner_uid=owner_uid,
                            tenant_id=tenant_id,
                            asset_id=entry["local_asset_id"],
                            material_type="image",
                            display_name=Path(
                                _safe_filename(remote_asset.get("original_filename"), f"{remote_id}.png")
                            ).stem,
                            category_owner_uid=owner_uid,
                            category=category.id,
                            tags_json=tags,
                            status="enabled",
                            metadata_json={**metadata, "ever_shared": True},
                        )
                        db.add(item)
                        counters["created"] += 1
                    else:
                        item.category_owner_uid = owner_uid
                        item.category = category.id
                        item.tags_json = tags
                        item.status = "enabled"
                        item.metadata_json = {**(item.metadata_json or {}), **metadata, "ever_shared": True}
                        item.deleted_at = None
                        counters["updated"] += 1
                    counters["assets"] += 1
                await db.commit()
                await report(
                    phase="syncing",
                    progress=10 + int(counters["assets"] * 80 / max(total_assets, 1)),
                    total_groups=total_groups,
                    processed_groups=processed_groups,
                    total_assets=total_assets,
                    processed_assets=counters["assets"],
                    summary=counters,
                )
            processed_groups += 1

        await report(
            phase="finalizing",
            progress=95,
            total_groups=total_groups,
            processed_groups=processed_groups,
            total_assets=total_assets,
            processed_assets=counters["assets"],
            summary=counters,
        )
        local_remote_items = list(
            (
                await db.execute(
                    select(ContentMaterialLibraryItem).where(
                        ContentMaterialLibraryItem.owner_uid == owner_uid,
                        ContentMaterialLibraryItem.material_type == "image",
                        ContentMaterialLibraryItem.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )
        for item in local_remote_items:
            metadata = item.metadata_json or {}
            if metadata.get("source") != REMOTE_SOURCE:
                continue
            remote_group_id = str(metadata.get("remote_group_id") or "")
            remote_asset_id = str(metadata.get("remote_asset_id") or "")
            if remote_group_id not in groups_seen or remote_asset_id not in assets_seen:
                item.status = "disabled"
                counters["disabled"] += 1
        for category in await repo.list_child_categories(owner_uid, "image", root.id):
            if category.id.startswith("rmlc_") and category.id not in category_ids_seen:
                category.deleted_at = utc_now_naive()

    db.add(OperationLog(user_id=user.id, operation="material.remote_sync", details=str(counters)))
    await db.commit()
    return {"source": client_api.base_url, "root_gallery": REMOTE_ROOT_NAME, "summary": counters}
