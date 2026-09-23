"""发布 CT01 身份 / CT07 日常记录规则修复；保留历史版本及其他运营配置。

容器内运行：python scripts/publish_intro_daily_rules.py --uid <管理员UID> [--publish]
默认只验证并显示变更，--publish 才创建并发布新规则版本。
"""

import argparse
import asyncio
import json

from sqlalchemy import select

from yuxi.content.schemas import RuleBundleUpdate, RuleDraftCreate
from yuxi.content.v3.foreman_rules import upgrade_intro_daily_rules
from yuxi.repositories.content_repository import ContentRepository
from yuxi.services.content_service import (
    activate_content_rule_version,
    create_content_rule_draft,
    normalize_rule_bundle,
    save_content_rule_draft,
    validate_rule_bundle_for_publish,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User


async def main(uid: str, publish: bool) -> None:
    pg_manager.initialize()
    try:
        async with pg_manager.AsyncSession() as db:
            user = (await db.execute(select(User).where(User.uid == uid, User.is_deleted == 0))).scalar_one()
            if user.role not in {"admin", "superadmin"}:
                raise ValueError("需要管理员账号")
            repo = ContentRepository(db)
            current = await repo.get_published_rule_version(schema_version=3)
            bundle = await repo.get_rule_bundle(current.id, include_disabled=True)
            updated = upgrade_intro_daily_rules(bundle)
            if updated == bundle:
                print(f"当前版本已包含修复：{current.id}")
                return
            note = "修正 FRT12 作者身份槽位；CT07 独立绑定日常工作 FRM10/FRB10，不强制用户痛点和完工结果"
            payload = RuleBundleUpdate(**{**updated, "changelog": note})
            validation = validate_rule_bundle_for_publish(normalize_rule_bundle(payload))
            if validation["errors"]:
                raise ValueError(validation)
            changes = {
                section: [
                    item.get("code") or item.get("id") for item in updated[section] if item not in bundle[section]
                ]
                for section in ("methods", "title_formulas", "content_formulas", "combination_rules")
            }
            print(json.dumps({"source": current.id, "changes": changes, "validation": validation}, ensure_ascii=False))
            if not publish:
                return
            draft = await create_content_rule_draft(
                db, user, RuleDraftCreate(source_version_id=current.id, changelog=note)
            )
            version_id = draft["bundle"]["version"]["id"]
            saved = await save_content_rule_draft(db, user, version_id, payload)
            if saved["validation"]["errors"]:
                raise ValueError(saved["validation"])
            result = await activate_content_rule_version(db, user, version_id, rollback=False, note=note)
            print(json.dumps(result, ensure_ascii=False))
    finally:
        await pg_manager.async_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--uid", required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.uid, args.publish))
