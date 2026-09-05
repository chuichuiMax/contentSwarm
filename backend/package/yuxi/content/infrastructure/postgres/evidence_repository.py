from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content.model.evidence import EvidenceBundleV1, EvidenceItemV1
from yuxi.storage.postgres.models_content import (
    ContentEvidenceBundleVersion,
    ContentEvidenceItem,
    ContentTask,
)


class PostgresEvidenceRepository:
    """以追加方式保存 EvidenceItem 和不可变冻结包。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def save_frozen_bundle(self, bundle: EvidenceBundleV1) -> ContentEvidenceBundleVersion:
        task = (
            await self.db.execute(select(ContentTask).where(ContentTask.id == bundle.task_id).with_for_update())
        ).scalar_one_or_none()
        if task is None:
            raise ValueError("内容任务不存在")
        existing_version = (
            await self.db.execute(
                select(ContentEvidenceBundleVersion.id).where(
                    ContentEvidenceBundleVersion.task_id == bundle.task_id,
                    ContentEvidenceBundleVersion.version == bundle.version,
                )
            )
        ).scalar_one_or_none()
        if existing_version is not None:
            raise ValueError(f"EvidenceBundle v{bundle.version} 已存在")

        existing_ids = set(
            (
                await self.db.execute(
                    select(ContentEvidenceItem.id).where(ContentEvidenceItem.id.in_([item.id for item in bundle.items]))
                )
            ).scalars()
        )
        for item in bundle.items:
            if item.id in existing_ids:
                continue
            self.db.add(self._to_record(bundle.task_id, item))

        record = ContentEvidenceBundleVersion(
            id=bundle.id,
            task_id=bundle.task_id,
            version=bundle.version,
            status="frozen",
            evidence_ids=[item.id for item in bundle.items],
            source_counts=bundle.source_counts,
            citations=list(bundle.citations),
            bundle_hash=bundle.bundle_hash,
            supersedes_id=bundle.supersedes_id,
            frozen_at=bundle.frozen_at.replace(tzinfo=None),
        )
        self.db.add(record)
        task.active_evidence_bundle_id = bundle.id
        task.evidence_json = bundle.model_dump(mode="json")
        await self.db.flush()
        return record

    @staticmethod
    def _to_record(task_id: str, item: EvidenceItemV1) -> ContentEvidenceItem:
        return ContentEvidenceItem(
            id=item.id,
            task_id=task_id,
            variable_codes=list(item.variable_codes),
            value_json=item.value,
            source_type=item.source_type,
            source_id=item.source_id,
            source_version=item.source_version,
            verified_status=item.verified_status,
            allowed_usage=list(item.allowed_usage),
            risk_level=item.risk_level,
            metadata_json=item.metadata,
            source_hash=item.source_hash,
            created_at=item.created_at.replace(tzinfo=None),
        )


__all__ = ["PostgresEvidenceRepository"]
