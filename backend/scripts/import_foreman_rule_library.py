"""发布装修工长新版创作逻辑，保留历史规则和其他行业规则。

运行：docker compose exec api python scripts/import_foreman_rule_library.py --uid <管理员UID>
"""

import argparse
import asyncio

from sqlalchemy import select

from yuxi.content.schemas import RuleBundleUpdate, RuleDraftCreate
from yuxi.content.v3.foreman_rules import DIRECTION_BINDINGS, import_foreman_rules, load_foreman_rule_catalog
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
        catalog = load_foreman_rule_catalog()
        revision = catalog["source"]["catalog_revision"]
        foreman_groups = [item for item in bundle["combination_rules"] if item.get("industry_scope") == ["decoration"]]
        directions = [code for item in foreman_groups for code in item.get("content_type_codes", [])]
        if (
            len(directions) == len(DIRECTION_BINDINGS)
            and set(directions) == set(DIRECTION_BINDINGS)
            and all(item.get("source_metadata", {}).get("catalog_revision") == revision for item in foreman_groups)
        ):
            print(f"装修工长新版规则已经发布：{published.id}")
            return
        imported = import_foreman_rules(bundle)
        note = "装修工长一级内容方向按表格一一绑定：7组层级组合和词组组合；其他行业规则保持不变"
        draft = await create_content_rule_draft(
            db, user, RuleDraftCreate(source_version_id=published.id, changelog=note)
        )
        version_id = draft["bundle"]["version"]["id"]
        saved = await save_content_rule_draft(db, user, version_id, RuleBundleUpdate(**{**imported, "changelog": note}))
        if saved["validation"]["errors"]:
            raise ValueError(saved["validation"])
        await activate_content_rule_version(db, user, version_id, rollback=False, note=note)
        print(f"已发布：{version_id}；装修7个一级方向已逐行绑定层级与词组组合；旧版保留")
    await pg_manager.async_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--uid", required=True)
    asyncio.run(main(parser.parse_args().uid))
