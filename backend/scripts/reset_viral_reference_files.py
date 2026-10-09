"""Remove viral file jobs and article versions so reference files can be re-prepared."""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import delete, func, select, update

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import ContentViralArticleVersion, ContentViralFileJob
from yuxi.storage.postgres.models_knowledge import KnowledgeFile


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("filenames", nargs="*", help="Exact knowledge file names; omit when using --all")
    parser.add_argument("--all", action="store_true", help="Clear every viral article version and file job")
    parser.add_argument("--apply", action="store_true", help="Persist changes (default is dry-run)")
    args = parser.parse_args()
    if args.all and args.filenames:
        raise SystemExit("use either --all or explicit filenames, not both")
    if not args.all and not args.filenames:
        raise SystemExit("pass --all or at least one filename")

    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        if args.all:
            asset_count = await db.scalar(select(func.count()).select_from(ContentViralArticleVersion)) or 0
            job_count = await db.scalar(select(func.count()).select_from(ContentViralFileJob)) or 0
            print(f"assets={asset_count} jobs={job_count}")
            job_rows = list(
                (
                    await db.execute(
                        select(ContentViralFileJob.filename, ContentViralFileJob.status, func.count())
                        .group_by(ContentViralFileJob.filename, ContentViralFileJob.status)
                        .order_by(ContentViralFileJob.filename, ContentViralFileJob.status)
                    )
                ).all()
            )
            for filename, status, count in job_rows:
                print(f"  job {filename!r} status={status} count={count}")
            asset_rows = list(
                (
                    await db.execute(
                        select(ContentViralArticleVersion.status, func.count())
                        .group_by(ContentViralArticleVersion.status)
                        .order_by(ContentViralArticleVersion.status)
                    )
                ).all()
            )
            for status, count in asset_rows:
                print(f"  asset status={status} count={count}")
            if not args.apply:
                print("dry-run only; pass --apply to delete all assets and file jobs")
                return
            await db.execute(delete(ContentViralArticleVersion))
            await db.execute(delete(ContentViralFileJob))
            await db.commit()
            print("done: all viral assets and file jobs removed (knowledge files unchanged)")
            await pg_manager.async_engine.dispose()
            return

        files = list(
            (
                await db.execute(select(KnowledgeFile).where(KnowledgeFile.filename.in_(args.filenames)))
            ).scalars()
        )
        if not files:
            print("no matching knowledge_files")
            return
        file_ids = [f.file_id for f in files]
        for f in files:
            print(f"file {f.filename!r} kb_id={f.kb_id} file_id={f.file_id}")

        assets = list(
            (
                await db.execute(
                    select(ContentViralArticleVersion).where(ContentViralArticleVersion.file_id.in_(file_ids))
                )
            ).scalars()
        )
        jobs = list(
            (await db.execute(select(ContentViralFileJob).where(ContentViralFileJob.file_id.in_(file_ids)))).scalars()
        )
        print(f"assets={len(assets)} jobs={len(jobs)}")
        for asset in assets:
            print(f"  asset {asset.id} status={asset.status} file_id={asset.file_id}")
        for job in jobs:
            print(f"  job {job.id} status={job.status} filename={job.filename!r}")

        if not args.apply:
            print("dry-run only; pass --apply to delete assets and invalidate jobs")
            return

        if assets:
            await db.execute(delete(ContentViralArticleVersion).where(ContentViralArticleVersion.file_id.in_(file_ids)))
        if jobs:
            await db.execute(delete(ContentViralFileJob).where(ContentViralFileJob.file_id.in_(file_ids)))
        await db.commit()
        print("done: assets deleted, file jobs removed (knowledge files unchanged)")

    await pg_manager.async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
