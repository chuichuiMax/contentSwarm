"""将已批准发布的规则矩阵编译为行业包引用版本；不变更行业业务政策。"""

import uuid
from copy import deepcopy
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content.control.industry.pack import ValidateIndustryPackHandler
from yuxi.repositories.content_repository import ContentRepository
from yuxi.storage.postgres.models_content import IndustryContentPackVersion, IndustryVariableMapping
from yuxi.utils.datetime_utils import utc_now_naive


async def sync_industry_pack_bindings(db: AsyncSession, *, bundle: dict[str, Any], uid: str) -> list[str]:
    """仅复制已发布行业包的业务配置。与矩阵发布同事务提交，旧任务的两个版本均不改写。"""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('content_industry_rule_binding'))"))
    repo = ContentRepository(db)
    rule_id = bundle["version"]["id"]
    all_packs = list(
        (
            await db.execute(
                select(IndustryContentPackVersion)
                .where(IndustryContentPackVersion.schema_version == 3, IndustryContentPackVersion.tenant_id.is_(None))
                .order_by(IndustryContentPackVersion.version.desc())
            )
        ).scalars()
    )
    published = {}
    for pack in all_packs:
        if pack.status == "published":
            published.setdefault(pack.slug, pack)
    synced = []
    for slug, parent in published.items():
        bound = next(
            (
                item
                for item in all_packs
                if item.slug == slug
                and (item.source_metadata or {}).get("rule_version_id") == rule_id
                and item.status in {"published", "deprecated"}
            ),
            None,
        )
        if bound is None:
            groups = [
                item
                for item in bundle["combination_rules"]
                if not item.get("industry_scope") or slug in item["industry_scope"]
            ]
            version = max(item.version for item in all_packs if item.slug == slug) + 1
            excluded = {
                "id",
                "version",
                "status",
                "name",
                "content_type_aliases",
                "combination_overrides",
                "golden_samples",
                "source_metadata",
                "changelog",
                "rollback_target_version_id",
                "evaluation_report",
                "created_by",
                "created_at",
                "published_at",
            }
            values = {
                column.name: deepcopy(getattr(parent, column.name))
                for column in IndustryContentPackVersion.__table__.columns
                if column.name not in excluded
            }
            aliases = deepcopy(parent.content_type_aliases or {})
            for group in groups:
                if len(group["content_type_codes"]) == 1 and group["source_metadata"].get("content_direction_name"):
                    aliases[group["content_type_codes"][0]] = group["source_metadata"]["content_direction_name"]
            bound = IndustryContentPackVersion(
                **values,
                id=f"industry-pack-{slug}-v{version}",
                version=version,
                status="draft",
                name=f"{parent.name.rsplit(' V', 1)[0]} V{version}",
                content_type_aliases=aliases,
                combination_overrides=[item["id"] for item in groups],
                golden_samples=[
                    {
                        "id": f"reference-{index + 1}",
                        "content_direction_code": direction,
                        "input_variables": {"validation_kind": "combination_reference", "rule_version_id": rule_id},
                        "expected_group_id": group["id"],
                    }
                    for index, (group, direction) in enumerate(
                        (group, direction) for group in groups for direction in group["content_type_codes"]
                    )
                ],
                source_metadata={
                    **deepcopy(parent.source_metadata or {}),
                    "rule_version_id": rule_id,
                    "derived_from_pack_id": parent.id,
                    "update_kind": "rule_binding_sync",
                    "business_config_version_id": (parent.source_metadata or {}).get(
                        "business_config_version_id", parent.id
                    ),
                },
                changelog=f"同步 {rule_id} 的 {len(groups)} 组矩阵引用与结构样本，沿用 {parent.id} 的行业业务配置",
                rollback_target_version_id=parent.id,
                evaluation_report={},
                created_by=uid,
                created_at=utc_now_naive(),
            )
            db.add(bound)
            await db.flush()
            mappings = await repo.list_industry_variable_mappings(parent.id)
            for mapping in mappings:
                db.add(
                    IndustryVariableMapping(
                        id=f"ivm_{uuid.uuid4().hex}",
                        industry_pack_version_id=bound.id,
                        **{
                            column.name: deepcopy(getattr(mapping, column.name))
                            for column in IndustryVariableMapping.__table__.columns
                            if column.name not in {"id", "industry_pack_version_id"}
                        },
                    )
                )
            report = ValidateIndustryPackHandler().execute(
                record=bound, variable_mappings=mappings, combination_groups=groups, rule_bundle=bundle
            )
            if not report["validation"]["valid"] or not report["evaluation"]["passed"]:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": {
                            "code": "INDUSTRY_RULE_BINDING_INVALID",
                            "message": f"{slug} 行业包与矩阵不一致",
                            "report": report,
                        }
                    },
                )
            # 这里只记录结构校验，不复制或伪造其他版本的 canary 全链路评测。
            bound.evaluation_report = report
            bound.published_at = utc_now_naive()
        for old in all_packs:
            if old.slug == slug and old.status == "published" and old.id != bound.id:
                old.status = "deprecated"
        bound.status = "published"
        synced.append(bound.id)
    await repo.track(
        "content_industry_rule_bindings_synced",
        uid=uid,
        properties={"rule_version_id": rule_id, "industry_pack_ids": synced},
    )
    await db.flush()
    return synced
