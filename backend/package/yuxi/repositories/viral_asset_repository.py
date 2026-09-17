from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content.model.viral_assets import ViralArticleSource
from yuxi.storage.postgres.models_content import ContentViralArticleVersion


def asset_dict(asset: ContentViralArticleVersion, *, include_source: bool = False) -> dict[str, Any]:
    result = {
        "id": asset.id,
        "article_id": asset.article_id,
        "kb_id": asset.kb_id,
        "file_id": asset.file_id,
        "industry_slug": asset.industry_slug,
        "source_hash": asset.source_hash,
        "preparation_skill_hash": asset.preparation_skill_hash,
        "status": asset.status,
        "title": asset.source_json["title"],
        "locator": asset.source_json["locator"],
        "reference_card": (asset.prepared_json or {}).get("reference_card"),
        "attempt": asset.attempt,
        "error_message": asset.error_message,
        "agent_run_id": asset.agent_run_id,
    }
    if include_source:
        result.update(source=asset.source_json, preparation=asset.prepared_json)
    return result


class ViralAssetRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def register(self, source: ViralArticleSource, *, skill_hash: str, uid: str) -> ContentViralArticleVersion:
        identity = f"{source.article_id}:{source.source_hash}:{skill_hash}"
        asset_id = "vav_" + hashlib.sha256(identity.encode()).hexdigest()[:56]
        await self.db.execute(
            insert(ContentViralArticleVersion)
            .values(
                id=asset_id,
                article_id=source.article_id,
                kb_id=source.kb_id,
                file_id=source.file_id,
                industry_slug=source.industry_slug,
                source_hash=source.source_hash,
                preparation_skill_hash=skill_hash,
                source_json=source.model_dump(mode="json"),
                prepared_json={},
                status="pending",
                created_by=uid,
                attempt=1,
            )
            .on_conflict_do_nothing(index_elements=["article_id", "source_hash", "preparation_skill_hash"])
        )
        await self.db.execute(
            update(ContentViralArticleVersion)
            .where(
                ContentViralArticleVersion.article_id == source.article_id,
                ContentViralArticleVersion.id != asset_id,
                ContentViralArticleVersion.status != "invalidated",
            )
            .values(status="invalidated")
        )
        return (
            await self.db.execute(select(ContentViralArticleVersion).where(ContentViralArticleVersion.id == asset_id))
        ).scalar_one()

    async def get(self, asset_id: str, *, kb_ids: list[str], for_update: bool = False):
        query = select(ContentViralArticleVersion).where(
            ContentViralArticleVersion.id == asset_id,
            ContentViralArticleVersion.kb_id.in_(kb_ids),
        )
        if for_update:
            query = query.with_for_update()
        return (await self.db.execute(query)).scalar_one_or_none()

    async def list(
        self, *, kb_ids: list[str], industry_slug: str | None = None, ready_only: bool = False, limit: int = 100
    ):
        query = select(ContentViralArticleVersion).where(ContentViralArticleVersion.kb_id.in_(kb_ids))
        if industry_slug:
            query = query.where(ContentViralArticleVersion.industry_slug == industry_slug)
        if ready_only:
            query = query.where(ContentViralArticleVersion.status == "ready")
        query = query.order_by(ContentViralArticleVersion.created_at.desc(), ContentViralArticleVersion.id).limit(limit)
        return list((await self.db.execute(query)).scalars())
