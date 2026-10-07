"""补齐旧系统装修 V3 配置缺失的变量；默认只输出计划，--apply 才提交。

运行前备份规则版本、行业包、变量定义和字段映射表。
此迁移只插入缺失记录，不修改已发布配置、已有映射或历史任务。
"""

import argparse
import asyncio
import json

from sqlalchemy import select, text

from yuxi.content.catalog import INDUSTRY_CONFIG, VARIABLES
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import (
    ContentRuleVersion,
    IndustryContentPackVersion,
    IndustryVariableMapping,
    VariableDefinition,
)

RULE_ID = "content-rules-platform-v3"
PACK_ID = "industry-pack-decoration-v3"


async def repair(db, *, apply=False):
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": "yuxi_content_seed_v3"},
    )
    rules = await db.get(ContentRuleVersion, RULE_ID)
    pack = await db.get(IndustryContentPackVersion, PACK_ID)
    if (
        rules is None
        or pack is None
        or rules.tenant_id is not None
        or pack.tenant_id is not None
        or rules.status != "published"
        or pack.status != "published"
        or (pack.source_metadata or {}).get("source") != "platform-v3-seed"
    ):
        raise ValueError("仅允许修复已发布的系统装修 V3 配置，请核对规则与行业包来源")

    definitions = (
        (await db.execute(select(VariableDefinition).where(VariableDefinition.rule_version_id == RULE_ID)))
        .scalars()
        .all()
    )
    mappings = (
        (
            await db.execute(
                select(IndustryVariableMapping).where(IndustryVariableMapping.industry_pack_version_id == PACK_ID)
            )
        )
        .scalars()
        .all()
    )
    existing_codes = {item.code for item in definitions}
    existing_fields = {item.field_key for item in mappings}
    plan = {"variable_definitions": [], "variable_mappings": []}
    for order, (code, name, value_type, sensitivity, evidence_required) in enumerate(VARIABLES, 1):
        if code in existing_codes:
            continue
        plan["variable_definitions"].append(
            {
                "id": f"variable-{code}-v3",
                "rule_version_id": RULE_ID,
                "code": code,
                "name": name,
                "value_type": value_type,
                "unit_schema": {},
                "evidence_policy": {"required": evidence_required},
                "sensitivity": sensitivity,
                "allowed_usages": ["title", "body", "topic", "media"],
                "validation_schema": {},
                "enabled": True,
                "sort_order": order,
            }
        )
    for field_key, _label, variable_code in INDUSTRY_CONFIG["decoration"]["fields"]:
        if field_key in existing_fields:
            continue
        plan["variable_mappings"].append(
            {
                "id": f"mapping-v3-decoration-{field_key}",
                "industry_pack_version_id": PACK_ID,
                "field_key": field_key,
                "variable_code": variable_code,
                "transform_type": "identity",
                "transform_config": {},
                "required_by_content_types": (
                    ["CT01", "CT02", "CT03", "CT05"] if variable_code in {"product", "process"} else []
                ),
            }
        )
    if apply:
        db.add_all(VariableDefinition(**values) for values in plan["variable_definitions"])
        db.add_all(IndustryVariableMapping(**values) for values in plan["variable_mappings"])
        await db.flush()
    return plan


async def main(apply):
    pg_manager.initialize()
    try:
        async with pg_manager.AsyncSession() as db:
            plan = await repair(db, apply=apply)
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            if apply:
                await db.commit()
                print("缺失变量已补齐；已有配置保持不变")
            else:
                await db.rollback()
                print("仅检查，未提交变更")
    finally:
        await pg_manager.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args().apply))
