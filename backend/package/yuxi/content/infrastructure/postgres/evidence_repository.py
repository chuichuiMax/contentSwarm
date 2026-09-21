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

    async def canonicalize_existing_items(self, items: list[EvidenceItemV1]) -> list[EvidenceItemV1]:
        if not items:
            return []
        records = list(
            (
                await self.db.execute(
                    select(ContentEvidenceItem).where(ContentEvidenceItem.id.in_([item.id for item in items]))
                )
            ).scalars()
        )
        existing_by_id = {record.id: self._from_record(record) for record in records}
        canonical: list[EvidenceItemV1] = []
        for item in items:
            existing = existing_by_id.get(item.id)
            if existing is None:
                canonical.append(item)
                continue
            if not self._same_fact_identity(existing, item):
                raise ValueError(f"Evidence ID 对应事实不一致：{item.id}")
            canonical.append(existing)
        return canonical

    async def get_latest_frozen_bundle(self, task_id: str) -> EvidenceBundleV1 | None:
        record = (
            await self.db.execute(
                select(ContentEvidenceBundleVersion)
                .where(ContentEvidenceBundleVersion.task_id == task_id)
                .order_by(ContentEvidenceBundleVersion.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if record is None:
            return None
        evidence_records = []
        if record.evidence_ids:
            evidence_records = list(
                (
                    await self.db.execute(
                        select(ContentEvidenceItem).where(ContentEvidenceItem.id.in_(record.evidence_ids))
                    )
                ).scalars()
            )
        by_id = {item.id: item for item in evidence_records}
        if missing := [evidence_id for evidence_id in record.evidence_ids if evidence_id not in by_id]:
            raise ValueError(f"EvidenceBundle 缺少 EvidenceItem：{', '.join(missing)}")
        items = tuple(self._from_record(by_id[evidence_id]) for evidence_id in record.evidence_ids)
        return EvidenceBundleV1(
            id=record.id,
            task_id=record.task_id,
            version=record.version,
            status="frozen",
            items=items,
            source_counts=record.source_counts or {},
            citations=tuple(record.citations or []),
            bundle_hash=record.bundle_hash,
            supersedes_id=record.supersedes_id,
            frozen_at=record.frozen_at,
        )

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

        existing_items = list(
            (
                await self.db.execute(
                    select(ContentEvidenceItem).where(ContentEvidenceItem.id.in_([item.id for item in bundle.items]))
                )
            ).scalars()
        )
        existing_by_id = {item.id: item for item in existing_items}
        for item in bundle.items:
            existing = existing_by_id.get(item.id)
            if existing is not None:
                persisted = self._from_record(existing)
                if not self._same_fact_identity(persisted, item):
                    raise ValueError(f"Evidence ID 对应内容不一致：{item.id}")
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

    @staticmethod
    def _from_record(item: ContentEvidenceItem) -> EvidenceItemV1:
        return EvidenceItemV1(
            id=item.id,
            variable_codes=tuple(item.variable_codes or []),
            value=item.value_json,
            source_type=item.source_type,
            source_id=item.source_id,
            source_version=item.source_version,
            verified_status=item.verified_status,
            allowed_usage=tuple(item.allowed_usage or []),
            risk_level=item.risk_level,
            metadata=item.metadata_json or {},
            source_hash=item.source_hash,
            created_at=item.created_at,
        )

    @staticmethod
    def _same_fact_identity(left: EvidenceItemV1, right: EvidenceItemV1) -> bool:
        return (
            left.id == right.id
            and left.variable_codes == right.variable_codes
            and left.value == right.value
            and left.source_type == right.source_type
            and left.verified_status == right.verified_status
            and left.allowed_usage == right.allowed_usage
            and left.risk_level == right.risk_level
            and left.source_hash == right.source_hash
        )


__all__ = ["PostgresEvidenceRepository"]
