"""为已有装修爆款建立带精确创作类型的新准备版本；默认只输出计划。"""

import argparse
import asyncio

from sqlalchemy import select

from yuxi.content.model.viral_assets import ViralArticleSource
from yuxi.repositories.viral_asset_repository import ViralAssetRepository
from yuxi.services.content_viral_assets import check_asset_source, enqueue_asset, preparation_skill_hash
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import ContentViralArticleVersion


async def main(apply: bool):
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        rows = list(
            (
                await db.execute(
                    select(ContentViralArticleVersion)
                    .where(ContentViralArticleVersion.industry_slug == "decoration")
                    .order_by(ContentViralArticleVersion.created_at.desc())
                )
            ).scalars()
        )
        latest = {}
        for row in rows:
            latest.setdefault(row.article_id, row)
        candidates = [row for row in latest.values() if row.preparation_skill_hash != preparation_skill_hash()]
        print(f"需要重新准备 {len(candidates)} 篇装修文章", flush=True)
        if apply:
            queued = 0
            for row in candidates:
                if not await check_asset_source(db, row):
                    print(f"跳过已变更原文 {row.id}，需重新导入", flush=True)
                    continue
                asset = await ViralAssetRepository(db).register(
                    ViralArticleSource.model_validate(row.source_json),
                    skill_hash=preparation_skill_hash(),
                    uid=row.created_by,
                )
                await enqueue_asset(db, asset)
                queued += 1
                while asset.status in {"pending", "running"}:
                    await asyncio.sleep(3)
                    await db.refresh(asset)
                print(f"{asset.id}: {asset.status}", flush=True)
            print(f"已处理 {queued} 篇，新版本保留原文及来源", flush=True)
    await pg_manager.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args().apply))
