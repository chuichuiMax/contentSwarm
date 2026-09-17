"""发布行业组合与通用公式应用说明，保留装修原文和历史版本。

运行：docker compose exec api python scripts/import_industry_rule_library.py --uid <管理员UID>
"""

import argparse
import asyncio

from sqlalchemy import select

from yuxi.content.industry_matrix import import_industry_matrix
from yuxi.content.schemas import RuleBundleUpdate, RuleDraftCreate
from yuxi.repositories.content_repository import ContentRepository
from yuxi.services.content_service import (
    activate_content_rule_version,
    create_content_rule_draft,
    save_content_rule_draft,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User


async def main(uid: str) -> None:
    pg_manager.initialize()
    await pg_manager.ensure_content_schema()
    async with pg_manager.AsyncSession() as db:
        user = (await db.execute(select(User).where(User.uid == uid, User.is_deleted == 0))).scalar_one()
        if user.role not in {"admin", "superadmin"}:
            raise ValueError("需要管理员账号")
        repo = ContentRepository(db)
        published = await repo.get_published_rule_version(schema_version=3)
        bundle = await repo.get_rule_bundle(published.id, include_disabled=True)
        if any(
            item.get("source_metadata", {}).get("revision") == "2026-09-06-v2" for item in bundle["combination_rules"]
        ):
            print(f"行业矩阵已经导入：{published.id}")
            return
        imported = import_industry_matrix(bundle)
        note = "重编5个行业共70组创作组合，沿用7个标题与4个正文公式的通用逻辑；保留28组装修原文"
        draft = await create_content_rule_draft(
            db, user, RuleDraftCreate(source_version_id=published.id, changelog=note)
        )
        version_id = draft["bundle"]["version"]["id"]
        saved = await save_content_rule_draft(db, user, version_id, RuleBundleUpdate(**{**imported, "changelog": note}))
        if saved["validation"]["errors"]:
            raise ValueError(saved["validation"])
        await activate_content_rule_version(db, user, version_id, rollback=False, note=note)
        print(f"已发布：{version_id}；5个行业70组，装修28组；旧版保留")
    await pg_manager.async_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--uid", required=True)
    asyncio.run(main(parser.parse_args().uid))
