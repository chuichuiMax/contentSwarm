from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_content import (
    ContentArtifact,
    ContentArtifactVersion,
    ContentCoverAsset,
    ContentCoverEditProject,
    ContentCoverImage2Setting,
    ContentCoverJob,
    ContentCoverPosterTemplate,
    ContentTask,
)
from yuxi.utils.datetime_utils import utc_now_naive


class ContentCoverRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_image2_setting(
        self,
        owner_uid: str,
        *,
        for_update: bool = False,
    ) -> ContentCoverImage2Setting | None:
        query = select(ContentCoverImage2Setting).where(ContentCoverImage2Setting.owner_uid == owner_uid)
        if for_update:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalar_one_or_none()

    async def upsert_image2_setting(
        self,
        *,
        owner_uid: str,
        base_url: str,
        api_key: str,
        model: str,
        capabilities_json: dict[str, Any] | None = None,
        verification_status: str = "unverified",
        verified_at=None,
    ) -> ContentCoverImage2Setting:
        setting = await self.get_image2_setting(owner_uid, for_update=True)
        if setting is None:
            setting = ContentCoverImage2Setting(
                owner_uid=owner_uid,
                base_url=base_url,
                api_key=api_key,
                model=model,
                capabilities_json=capabilities_json or {},
                verification_status=verification_status,
                verified_at=verified_at,
            )
            self.db.add(setting)
        else:
            setting.base_url = base_url
            setting.api_key = api_key
            setting.model = model
            setting.capabilities_json = capabilities_json or {}
            setting.verification_status = verification_status
            setting.verified_at = verified_at
            setting.updated_at = utc_now_naive()
        await self.db.flush()
        return setting

    async def create_asset(self, **values: Any) -> ContentCoverAsset:
        item = ContentCoverAsset(**values)
        self.db.add(item)
        await self.db.flush()
        return item

    async def update_asset_metadata(
        self,
        asset: ContentCoverAsset,
        metadata: dict[str, Any],
    ) -> None:
        asset.metadata_json = metadata
        await self.db.flush()

    async def create_poster_template(self, **values: Any) -> ContentCoverPosterTemplate:
        item = ContentCoverPosterTemplate(**values)
        self.db.add(item)
        await self.db.flush()
        return item

    async def get_poster_template_by_checksum(
        self,
        owner_uid: str,
        checksum: str,
    ) -> ContentCoverPosterTemplate | None:
        return (
            await self.db.execute(
                select(ContentCoverPosterTemplate).where(
                    ContentCoverPosterTemplate.owner_uid == owner_uid,
                    ContentCoverPosterTemplate.checksum == checksum,
                    ContentCoverPosterTemplate.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def get_poster_template_for_user(
        self,
        template_id: str,
        owner_uid: str,
        *,
        for_update: bool = False,
    ) -> ContentCoverPosterTemplate | None:
        query = select(ContentCoverPosterTemplate).where(
            ContentCoverPosterTemplate.id == template_id,
            ContentCoverPosterTemplate.owner_uid == owner_uid,
            ContentCoverPosterTemplate.deleted_at.is_(None),
        )
        if for_update:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalar_one_or_none()

    async def list_poster_templates(
        self,
        owner_uid: str,
        *,
        category: str | None,
        status: str | None,
        query_text: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[ContentCoverPosterTemplate], int]:
        filters = [
            ContentCoverPosterTemplate.owner_uid == owner_uid,
            ContentCoverPosterTemplate.deleted_at.is_(None),
        ]
        if category:
            filters.append(ContentCoverPosterTemplate.category == category)
        if status:
            filters.append(ContentCoverPosterTemplate.status == status)
        if query_text:
            pattern = f"%{query_text.strip()}%"
            filters.append(
                or_(
                    ContentCoverPosterTemplate.name.ilike(pattern),
                    ContentCoverPosterTemplate.category.ilike(pattern),
                )
            )
        total = (await self.db.execute(select(func.count(ContentCoverPosterTemplate.id)).where(*filters))).scalar_one()
        items = (
            await self.db.execute(
                select(ContentCoverPosterTemplate)
                .where(*filters)
                .order_by(ContentCoverPosterTemplate.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
        return list(items), int(total)

    async def poster_template_is_in_active_job(self, template_id: str, owner_uid: str) -> bool:
        requests = (
            await self.db.execute(
                select(ContentCoverJob.request_json).where(
                    ContentCoverJob.owner_uid == owner_uid,
                    ContentCoverJob.status.not_in(("succeeded", "failed", "cancelled")),
                    ContentCoverJob.mode == "poster_billboard",
                )
            )
        ).scalars()
        return any((request or {}).get("poster_template_id") == template_id for request in requests)

    async def poster_template_is_selected_by_task(
        self,
        template_id: str,
        owner_uid: str,
        *,
        locked_only: bool = False,
    ) -> bool:
        filters = [
            ContentTask.created_by == owner_uid,
            ContentTask.selected_poster_template_id == template_id,
            ContentTask.deleted_at.is_(None),
        ]
        if locked_only:
            filters.append(ContentTask.current_stage != "brief")
        return bool((await self.db.execute(select(func.count(ContentTask.id)).where(*filters))).scalar_one())

    async def get_asset(self, asset_id: str, *, for_update: bool = False) -> ContentCoverAsset | None:
        query = select(ContentCoverAsset).where(
            ContentCoverAsset.id == asset_id,
            ContentCoverAsset.deleted_at.is_(None),
        )
        if for_update:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalar_one_or_none()

    async def get_asset_for_user(
        self, asset_id: str, owner_uid: str, *, for_update: bool = False
    ) -> ContentCoverAsset | None:
        query = select(ContentCoverAsset).where(
            ContentCoverAsset.id == asset_id,
            ContentCoverAsset.owner_uid == owner_uid,
            ContentCoverAsset.deleted_at.is_(None),
        )
        if for_update:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalar_one_or_none()

    async def get_assets_for_user(
        self,
        asset_ids: list[str],
        owner_uid: str,
        *,
        for_update: bool = False,
    ) -> list[ContentCoverAsset]:
        if not asset_ids:
            return []
        query = select(ContentCoverAsset).where(
            ContentCoverAsset.id.in_(asset_ids),
            ContentCoverAsset.owner_uid == owner_uid,
            ContentCoverAsset.deleted_at.is_(None),
        )
        if for_update:
            query = query.with_for_update()
        items = (await self.db.execute(query)).scalars()
        by_id = {item.id: item for item in items}
        return [by_id[item_id] for item_id in asset_ids if item_id in by_id]

    async def asset_is_in_active_job(self, asset_id: str, owner_uid: str) -> bool:
        jobs = (
            await self.db.execute(
                select(ContentCoverJob.request_json).where(
                    ContentCoverJob.owner_uid == owner_uid,
                    ContentCoverJob.status.not_in(("succeeded", "failed", "cancelled")),
                )
            )
        ).scalars()
        for request in jobs:
            payload = request or {}
            referenced = list(payload.get("asset_ids") or []) + list(payload.get("source_asset_ids") or [])
            referenced.extend(
                [payload.get("template_asset_id"), payload.get("mask_asset_id"), payload.get("product_asset_id")]
            )
            if asset_id in referenced:
                return True
        return False

    async def create_job(self, **values: Any) -> ContentCoverJob:
        item = ContentCoverJob(**values)
        self.db.add(item)
        await self.db.flush()
        return item

    async def create_edit_project(self, **values: Any) -> ContentCoverEditProject:
        item = ContentCoverEditProject(**values)
        self.db.add(item)
        await self.db.flush()
        return item

    async def get_edit_project_for_source(
        self,
        source_asset_id: str,
        owner_uid: str,
        *,
        for_update: bool = False,
    ) -> ContentCoverEditProject | None:
        query = select(ContentCoverEditProject).where(
            ContentCoverEditProject.source_asset_id == source_asset_id,
            ContentCoverEditProject.owner_uid == owner_uid,
            ContentCoverEditProject.status == "active",
        )
        if for_update:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalar_one_or_none()

    async def get_edit_project_for_user(
        self,
        project_id: str,
        owner_uid: str,
        *,
        for_update: bool = False,
    ) -> ContentCoverEditProject | None:
        query = select(ContentCoverEditProject).where(
            ContentCoverEditProject.id == project_id,
            ContentCoverEditProject.owner_uid == owner_uid,
            ContentCoverEditProject.status == "active",
        )
        if for_update:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalar_one_or_none()

    async def get_job(self, job_id: str, *, for_update: bool = False) -> ContentCoverJob | None:
        query = select(ContentCoverJob).where(ContentCoverJob.id == job_id)
        if for_update:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalar_one_or_none()

    async def get_job_for_user(
        self, job_id: str, owner_uid: str, *, for_update: bool = False
    ) -> ContentCoverJob | None:
        query = select(ContentCoverJob).where(
            ContentCoverJob.id == job_id,
            ContentCoverJob.owner_uid == owner_uid,
        )
        if for_update:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalar_one_or_none()

    async def get_job_by_idempotency(self, owner_uid: str, idempotency_key: str) -> ContentCoverJob | None:
        return (
            await self.db.execute(
                select(ContentCoverJob).where(
                    ContentCoverJob.owner_uid == owner_uid,
                    ContentCoverJob.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()

    async def list_jobs(
        self,
        owner_uid: str,
        *,
        content_task_id: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[ContentCoverJob], int]:
        filters = [ContentCoverJob.owner_uid == owner_uid]
        if content_task_id:
            filters.append(ContentCoverJob.content_task_id == content_task_id)
        total = (await self.db.execute(select(func.count(ContentCoverJob.id)).where(*filters))).scalar_one()
        items = (
            await self.db.execute(
                select(ContentCoverJob)
                .where(*filters)
                .order_by(ContentCoverJob.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
        return list(items), int(total)

    async def set_current_cover(
        self,
        *,
        artifact: ContentArtifact,
        asset: ContentCoverAsset,
        job: ContentCoverJob,
        owner_uid: str,
    ) -> ContentArtifactVersion:
        previous = (
            await self.db.execute(
                select(ContentArtifactVersion).where(
                    ContentArtifactVersion.artifact_id == artifact.id,
                    ContentArtifactVersion.version == artifact.current_version,
                )
            )
        ).scalar_one_or_none()
        if previous is None:
            raise ValueError("内容产物当前版本不存在")
        if artifact.cover_asset_id == asset.id and artifact.cover_job_id == job.id:
            return previous
        artifact.current_version += 1
        artifact.cover_asset_id = asset.id
        artifact.cover_job_id = job.id
        artifact.updated_at = utc_now_naive()
        version = ContentArtifactVersion(
            id=f"cav_{uuid.uuid4().hex}",
            artifact_id=artifact.id,
            version=artifact.current_version,
            title=artifact.title,
            body=artifact.body,
            topics=artifact.topics or [],
            source_type="cover_update",
            model_spec=previous.model_spec,
            skill_versions=previous.skill_versions or {},
            rule_version_id=previous.rule_version_id,
            knowledge_snapshot=previous.knowledge_snapshot or {},
            evidence_usage_snapshot=previous.evidence_usage_snapshot or {},
            review_snapshot=previous.review_snapshot or {},
            cover_asset_id=asset.id,
            cover_job_id=job.id,
            created_by=owner_uid,
        )
        self.db.add(version)
        await self.db.flush()
        return version

    async def bind_hycanvas_design(
        self,
        *,
        artifact: ContentArtifact,
        asset: ContentCoverAsset,
        snapshot: dict[str, Any],
        owner_uid: str,
    ) -> ContentArtifactVersion:
        previous = (
            await self.db.execute(
                select(ContentArtifactVersion).where(
                    ContentArtifactVersion.artifact_id == artifact.id,
                    ContentArtifactVersion.version == artifact.current_version,
                )
            )
        ).scalar_one_or_none()
        if previous is None:
            raise ValueError("内容产物当前版本不存在")
        artifact.current_version += 1
        artifact.cover_asset_id = asset.id
        artifact.cover_job_id = None
        artifact.hycanvas_design_snapshot = snapshot
        artifact.updated_at = utc_now_naive()
        version = ContentArtifactVersion(
            id=f"cav_{uuid.uuid4().hex}",
            artifact_id=artifact.id,
            version=artifact.current_version,
            title=artifact.title,
            body=artifact.body,
            topics=artifact.topics or [],
            source_type="hycanvas_design",
            model_spec=previous.model_spec,
            skill_versions=previous.skill_versions or {},
            rule_version_id=previous.rule_version_id,
            knowledge_snapshot=previous.knowledge_snapshot or {},
            review_snapshot=previous.review_snapshot or {},
            cover_asset_id=asset.id,
            cover_job_id=None,
            hycanvas_design_snapshot=snapshot,
            content_type_snapshot=artifact.content_type_snapshot or {},
            angle_snapshot=artifact.angle_snapshot or {},
            pattern_slot_snapshot=artifact.pattern_slot_snapshot or {},
            persona_snapshot=artifact.persona_snapshot or {},
            channel_snapshot=artifact.channel_snapshot or {},
            compliance_snapshot=artifact.compliance_snapshot or {},
            runtime_config_snapshot=artifact.runtime_config_snapshot or {},
            edit_diff_snapshot=artifact.edit_diff_snapshot or [],
            created_by=owner_uid,
        )
        self.db.add(version)
        await self.db.flush()
        return version
