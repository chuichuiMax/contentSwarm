"""同步当前发布矩阵的行业包引用版本；默认只校验，--apply 才提交。"""

import argparse
import asyncio

from sqlalchemy import select

from yuxi.repositories.content_repository import ContentRepository
from yuxi.services.content_industry_sync import sync_industry_pack_bindings
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User


async def main(uid: str, apply: bool) -> None:
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        user = (await db.execute(select(User).where(User.uid == uid, User.is_deleted == 0))).scalar_one()
        if user.role not in {"admin", "superadmin"}:
            raise ValueError("需要管理员账号")
        repo = ContentRepository(db)
        version = await repo.get_published_rule_version_for_update(schema_version=3)
        bundle = await repo.get_rule_bundle(version.id, include_disabled=True)
        ids = await sync_industry_pack_bindings(db, bundle=bundle, uid=uid)
        for pack_id in ids:
            pack = await repo.get_industry_pack(pack_id)
            print(
                f"{pack.id}: {len(pack.combination_overrides)}组 / {len(pack.golden_samples)}个结构样本 / {version.id}"
            )
        if apply:
            await db.commit()
            print("行业包引用版本已同步发布，旧任务版本保留")
        else:
            await db.rollback()
            print("校验通过；未提交变更")
    await pg_manager.async_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--uid", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.uid, args.apply))
