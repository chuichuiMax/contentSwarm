"""将当前运营规则收敛为装修行业，保留历史任务绑定的不可变版本。"""

from copy import deepcopy

from sqlalchemy import select, text

from yuxi.repositories.content_repository import ContentRepository
from yuxi.storage.postgres.models_content import IndustryContentPackVersion, IndustryTemplateVersion
from yuxi.utils.datetime_utils import utc_now_naive


def decoration_rule_bundle(bundle: dict) -> dict:
    result = deepcopy(bundle)
    for section in ("methods", "title_formulas", "content_formulas", "combination_rules"):
        result[section] = [
            item
            for item in result.get(section, [])
            if not item.get("industry_scope") or "decoration" in item["industry_scope"]
        ]
        for item in result[section]:
            item["industry_scope"] = ["decoration"]
            if isinstance(item.get("source_content"), dict):
                item["source_content"].pop("cross_industry", None)
            if "industry_aliases" in item:
                item["industry_aliases"] = {
                    key: value for key, value in item["industry_aliases"].items() if key == "decoration"
                }
    methods = {item["code"] for item in result["methods"]}
    for section in ("title_formulas", "content_formulas"):
        for item in result[section]:
            item["compatible_methods"] = [code for code in item.get("compatible_methods", []) if code in methods]
    return result


async def ensure_decoration_only_rules(db) -> None:
    from yuxi.services.content_industry_sync import sync_industry_pack_bindings
    from yuxi.services.content_service import validate_rule_bundle_for_publish

    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('content_decoration_only_rules'))"))
    repo = ContentRepository(db)
    current = await repo.get_published_rule_version_for_update(schema_version=3)
    if current is None:
        raise RuntimeError("装修规则迁移缺少已发布规则版本")
    source = await repo.get_rule_bundle(current.id, include_disabled=True)
    cleaned = decoration_rule_bundle(source)
    # 停用其他行业入口；旧任务仍通过精确版本 ID 读取其原有配置。
    for model, inactive in ((IndustryTemplateVersion, "archived"), (IndustryContentPackVersion, "deprecated")):
        rows = (
            await db.execute(select(model).where(model.slug != "decoration", model.status == "published"))
        ).scalars()
        for row in rows:
            row.status = inactive
    if cleaned == source:
        await db.commit()
        return
    validation = validate_rule_bundle_for_publish(cleaned)
    if validation["errors"]:
        raise RuntimeError(f"装修规则迁移校验失败: {validation['errors']}")
    version = await repo.next_platform_rule_version()
    version_id = f"content-rules-platform-v{version}"
    await repo.create_rule_version(
        version_id=version_id,
        version=version,
        changelog="仅保留装修与家居规则，移除其他行业及跨行业规则配置",
        created_by="system",
    )
    await repo.replace_rule_bundle(version_id, cleaned)
    await db.flush()
    target_bundle = await repo.get_rule_bundle(version_id, include_disabled=True)
    await sync_industry_pack_bindings(db, bundle=target_bundle, uid="system")
    target = await repo.get_rule_version_for_update(version_id)
    current.status = "archived"
    target.status = "published"
    target.published_at = utc_now_naive()
    await repo.track(
        "content_decoration_only_rules_published",
        uid="system",
        properties={
            "source_version_id": current.id,
            "version_id": version_id,
            "removed_combinations": len(source["combination_rules"]) - len(cleaned["combination_rules"]),
        },
    )
    await db.commit()
