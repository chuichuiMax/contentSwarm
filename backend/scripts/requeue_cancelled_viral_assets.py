"""Reset cancelled viral asset prep failures for persona/case KBs and requeue."""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from yuxi.services.content_viral_assets import enqueue_asset, preparation_skill_hash
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import ContentViralArticleVersion
from yuxi.storage.postgres.models_knowledge import KnowledgeBase


async def main() -> None:
    pg_manager.initialize()
    skill = preparation_skill_hash()
    names = ("人设自荐爆款库", "装修案例分享爆款库")
    async with pg_manager.AsyncSession() as db:
        kb_ids = list(
            (
                await db.execute(select(KnowledgeBase.kb_id).where(KnowledgeBase.name.in_(names)))
            ).scalars()
        )
        assets = list(
            (
                await db.execute(
                    select(ContentViralArticleVersion)
                    .where(
                        ContentViralArticleVersion.kb_id.in_(kb_ids),
                        ContentViralArticleVersion.status == "failed",
                        ContentViralArticleVersion.error_message.ilike("%取消%"),
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        print(f"skill={skill}")
        print(f"failed_cancel={len(assets)}")
        for asset in assets:
            title = (asset.source_json or {}).get("title", "")
            asset.status = "pending"
            asset.error_message = None
            asset.attempt = int(asset.attempt or 0) + 1
            asset.preparation_skill_hash = skill
            prepared = dict(asset.prepared_json or {})
            prepared["interrupt_count"] = 0
            asset.prepared_json = prepared
            print(f"requeue {asset.id} attempt={asset.attempt} title={title!r}")
        await db.commit()
        for asset in assets:
            await enqueue_asset(db, asset)
    await pg_manager.async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
