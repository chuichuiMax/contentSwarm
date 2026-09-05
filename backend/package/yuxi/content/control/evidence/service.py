from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content.infrastructure.postgres.evidence_repository import PostgresEvidenceRepository
from yuxi.content.model.evidence import EvidenceBundleV1
from yuxi.services.run_queue_service import append_run_stream_event


class EvidenceApplicationService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repository = PostgresEvidenceRepository(db)

    async def persist_frozen_bundle(
        self,
        bundle: EvidenceBundleV1,
        *,
        run_id: str,
        thread_id: str,
        added_evidence_ids: tuple[str, ...] = (),
        rejected_evidence_ids: tuple[str, ...] = (),
    ) -> None:
        await self.repository.save_frozen_bundle(bundle)
        await self.db.commit()
        if added_evidence_ids:
            await append_run_stream_event(
                run_id,
                "content.evidence.added",
                {"evidence_ids": list(added_evidence_ids), "count": len(added_evidence_ids)},
                thread_id=thread_id,
            )
        if rejected_evidence_ids:
            await append_run_stream_event(
                run_id,
                "content.evidence.rejected",
                {"evidence_ids": list(rejected_evidence_ids), "count": len(rejected_evidence_ids)},
                thread_id=thread_id,
            )
        await append_run_stream_event(
            run_id,
            "content.evidence.frozen",
            {
                "bundle_id": bundle.id,
                "bundle_version": bundle.version,
                "bundle_hash": bundle.bundle_hash,
                "source_counts": bundle.source_counts,
                "evidence_count": len(bundle.items),
            },
            thread_id=thread_id,
        )


__all__ = ["EvidenceApplicationService"]
