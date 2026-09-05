"""contentSwarm V3 平台规则、工作流和行业包种子。"""

from __future__ import annotations

from copy import deepcopy

from sqlalchemy import exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content.catalog import (
    CONTENT_TYPES,
    INDUSTRY_CONFIG,
    VARIABLES,
    XHS_CHANNEL_VERSION_ID,
    content_form_fields,
)
from yuxi.content.model.workflows.definition import workflow_definition_hash
from yuxi.content.rules import BODY_FORMULAS, INDUSTRIES, METHODS, TITLE_FORMULAS
from yuxi.content.v3.fixtures import load_decoration_matrix
from yuxi.content.v3.workflow import PLATFORM_WORKFLOW_V3_ID, WORKFLOW_V3
from yuxi.storage.postgres.models_content import (
    ContentCombinationRule,
    ContentFormula,
    ContentNodeRun,
    ContentRuleVersion,
    ContentTask,
    ContentTypeDefinition,
    ContentWorkflowVersion,
    CreationMethod,
    IndustryContentPackVersion,
    IndustryTemplateVersion,
    IndustryVariableMapping,
    TitleFormula,
    VariableDefinition,
)
from yuxi.utils.datetime_utils import utc_now_naive

PLATFORM_RULE_V3_ID = "content-rules-platform-v3"
DECORATION_INDUSTRY_PACK_V3_ID = "industry-pack-decoration-v3"


async def ensure_content_v3_seed_data(db: AsyncSession) -> None:
    """幂等导入并发布 V3 单轨生产配置。"""

    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": "yuxi_content_seed_v3"},
    )
    existing = await db.execute(select(ContentRuleVersion.id).where(ContentRuleVersion.id == PLATFORM_RULE_V3_ID))
    if existing.scalar_one_or_none():
        await _ensure_workflow_v3(db)
        await _ensure_all_industry_packs_v3(db)
        await _activate_v3_seed_data(db)
        await db.commit()
        return

    fixture = load_decoration_matrix()
    now = utc_now_naive()
    db.add(
        ContentRuleVersion(
            id=PLATFORM_RULE_V3_ID,
            tenant_id=None,
            version=3,
            status="draft",
            changelog="V3：四层组合组、独立标题/正文候选池和可审计匹配决策",
            created_by="system",
            created_at=now,
        )
    )
    await db.flush()

    for order, item in enumerate(METHODS, 1):
        db.add(
            CreationMethod(
                id=f"method-{item['code'].lower()}-v3",
                version_id=PLATFORM_RULE_V3_ID,
                sort_order=order,
                enabled=True,
                **deepcopy(item),
            )
        )
    for order, item in enumerate(TITLE_FORMULAS, 1):
        db.add(
            TitleFormula(
                id=f"title-{item['code'].lower()}-v3",
                version_id=PLATFORM_RULE_V3_ID,
                sort_order=order,
                enabled=True,
                **deepcopy(item),
            )
        )
    for order, original in enumerate(BODY_FORMULAS, 1):
        item = deepcopy(original)
        item["industry_aliases"] = {}
        db.add(
            ContentFormula(
                id=f"body-{item['code'].lower()}-v3",
                version_id=PLATFORM_RULE_V3_ID,
                sort_order=order,
                enabled=True,
                **item,
            )
        )
    for order, item in enumerate(CONTENT_TYPES, 1):
        db.add(
            ContentTypeDefinition(
                id=f"content-type-{item['code'].lower()}-v3",
                version_id=PLATFORM_RULE_V3_ID,
                sort_order=order,
                enabled=True,
                **deepcopy(item),
            )
        )
    for order, (code, name, value_type, sensitivity, evidence_required) in enumerate(VARIABLES, 1):
        db.add(
            VariableDefinition(
                id=f"variable-{code}-v3",
                rule_version_id=PLATFORM_RULE_V3_ID,
                code=code,
                name=name,
                value_type=value_type,
                unit_schema={},
                evidence_policy={"required": evidence_required},
                sensitivity=sensitivity,
                allowed_usages=["title", "body", "topic", "media"],
                validation_schema={},
                enabled=True,
                sort_order=order,
            )
        )

    group_ids: list[str] = []
    direction_aliases: dict[str, str] = {}
    for row_number, group in enumerate(fixture["groups"], 1):
        direction = group["content_direction"]
        group_ids.append(group["code"])
        direction_aliases[direction["code"]] = direction["name"]
        db.add(
            ContentCombinationRule(
                id=group["code"],
                version_id=PLATFORM_RULE_V3_ID,
                schema_version=3,
                content_goal=None,
                content_goal_codes=[],
                content_type_codes=[direction["code"]],
                industry_scope=["decoration"],
                channel_scope=[],
                narrative_axis_codes=[],
                methods=[],
                method_members=deepcopy(group["method_members"]),
                combination_type=group["combination_type"],
                title_formula_codes=[],
                title_pattern_codes=[],
                title_formula_candidate_codes=deepcopy(group["title_formula_candidate_codes"]),
                content_formula_code=None,
                body_pattern_codes=[],
                body_formula_candidate_codes=deepcopy(group["body_formula_candidate_codes"]),
                required_variable_codes=[],
                required_evidence_types=[],
                compatibility="compatible",
                priority=1000 - row_number,
                conditions={},
                hard_conditions={"single_narrative_axis": True, "unsupported_numbers": "block"},
                score_weights={"variable_coverage": 2, "evidence_coverage": 3},
                fallback_rule_id=None,
                scenario_description=group["scenario_description"],
                source_metadata={**deepcopy(fixture["source"]), **deepcopy(group["source_metadata"])},
                recommendation_reason=group["scenario_description"],
            )
        )

    db.add(
        IndustryContentPackVersion(
            id=DECORATION_INDUSTRY_PACK_V3_ID,
            slug="decoration",
            tenant_id=None,
            version=3,
            schema_version=3,
            status="draft",
            name="装修与家居 V3",
            description="基于飞书全层级匹配总表导入的装修四层组合组",
            content_type_aliases=direction_aliases,
            variable_schema=[],
            lexicon_version_ids=[f"lexicon-decoration-{index:02d}-v1" for index in range(1, 35)],
            pattern_ids=[],
            combination_overrides=group_ids,
            persona_templates=[],
            knowledge_scope=[],
            evidence_policy={"unsupported_numbers": "block", "unsupported_claims": "block"},
            review_policy={"single_narrative_axis": True, "human_formula_selection": True},
            created_by="system",
            created_at=now,
        )
    )
    await _ensure_workflow_v3(db)
    await _ensure_all_industry_packs_v3(db)
    await _activate_v3_seed_data(db)
    await db.commit()


async def _ensure_workflow_v3(db: AsyncSession) -> None:
    workflow = await db.get(ContentWorkflowVersion, PLATFORM_WORKFLOW_V3_ID)
    if workflow is not None:
        previous_hash = workflow.definition_hash
        if not _upgrade_system_workflow_v3(workflow):
            return
        tasks_without_node_runs = list(
            (
                await db.execute(
                    select(ContentTask).where(
                        ContentTask.workflow_version_id == PLATFORM_WORKFLOW_V3_ID,
                        ~exists(select(ContentNodeRun.id).where(ContentNodeRun.task_id == ContentTask.id)),
                    )
                )
            ).scalars()
        )
        for task in tasks_without_node_runs:
            if task.workflow_definition_hash not in {None, previous_hash}:
                continue
            task.workflow_definition_hash = workflow.definition_hash
            task.runtime_config_snapshot_json = {
                **(task.runtime_config_snapshot_json or {}),
                "workflow_definition_hash": workflow.definition_hash,
            }
        return
    now = utc_now_naive()
    db.add(
        ContentWorkflowVersion(
            id=PLATFORM_WORKFLOW_V3_ID,
            slug="enterprise-content",
            tenant_id=None,
            version=13,
            schema_version=3,
            status="draft",
            definition_json=deepcopy(WORKFLOW_V3),
            definition_hash=workflow_definition_hash(WORKFLOW_V3),
            input_schema={"type": "ContentBrief", "version": 3},
            output_schema={"type": "ContentArtifact", "version": 3},
            created_by="system",
            created_at=now,
        )
    )


def _upgrade_system_workflow_v3(workflow: ContentWorkflowVersion) -> bool:
    """同步平台保留 ID 的系统工作流，并迁移尚未产生节点记录的任务。"""

    if workflow.created_by != "system":
        return False
    expected_definition = deepcopy(WORKFLOW_V3)
    expected_hash = workflow_definition_hash(expected_definition)
    if (
        workflow.definition_json == expected_definition
        and workflow.definition_hash == expected_hash
        and int(getattr(workflow, "version", 0) or 0) == 13
    ):
        return False
    workflow.version = 13
    workflow.schema_version = 3
    workflow.definition_json = expected_definition
    workflow.definition_hash = expected_hash
    workflow.input_schema = {"type": "ContentBrief", "version": 3}
    workflow.output_schema = {"type": "ContentArtifact", "version": 3}
    return True


GENERIC_DIRECTION_FORMULAS = {
    "CT01": ("M04", ["T01", "T03", "T05"], ["C02", "C04"]),
    "CT02": ("M01", ["T01", "T02", "T04", "T06", "T07"], ["C01", "C02"]),
    "CT03": ("M02", ["T02", "T04", "T05"], ["C02", "C03"]),
    "CT04": ("M03", ["T01", "T03", "T05", "T07"], ["C01", "C03", "C04"]),
    "CT05": ("M03", ["T01", "T03", "T05", "T07"], ["C01", "C03", "C04"]),
    "CT06": ("M03", ["T01", "T03", "T05", "T07"], ["C01", "C03", "C04"]),
    "CT07": ("M04", ["T01", "T03", "T05", "T06"], ["C01", "C02", "C04"]),
}


def _pack_samples(slug: str, group_by_direction: dict[str, str]) -> tuple[list[dict], list[dict]]:
    golden = [
        {
            "id": f"{slug}-{code.lower()}-golden-v3",
            "content_direction_code": code,
            "input_variables": {"sample_source": "platform-v3-seed"},
            "expected_group_id": group_by_direction[code],
        }
        for code in sorted(group_by_direction)
    ]
    negative = [
        {
            "id": f"{slug}-{code.lower()}-missing-variable-v3",
            "content_direction_code": code,
            "input_variables": {},
            "expected_error_code": "MISSING_REQUIRED_VARIABLE",
        }
        for code in sorted(group_by_direction)
    ]
    return golden, negative


async def _ensure_all_industry_packs_v3(db: AsyncSession) -> None:
    """把 seed 中现有行业迁移为独立 V3 Pack，不复用装修组合组。"""

    platform_rules = await db.get(ContentRuleVersion, PLATFORM_RULE_V3_ID)
    if platform_rules is None:
        raise RuntimeError("V3 平台规则版本不存在")
    rules_mutable = platform_rules.status == "draft"
    source_by_slug = {item["slug"]: item for item in INDUSTRIES}
    fixture = load_decoration_matrix()
    decoration_groups = fixture["groups"]
    decoration_group_by_direction: dict[str, str] = {}
    for group in decoration_groups:
        decoration_group_by_direction.setdefault(group["content_direction"]["code"], group["code"])

    now = utc_now_naive()
    for slug, config in INDUSTRY_CONFIG.items():
        source = source_by_slug[slug]
        aliases = {item["code"]: alias for item, alias in zip(CONTENT_TYPES, config["aliases"], strict=True)}
        if slug == "decoration":
            group_ids = [item["code"] for item in decoration_groups]
            group_by_direction = decoration_group_by_direction
            lexicon_ids = [f"lexicon-decoration-{index:02d}-v1" for index in range(1, 35)]
        else:
            group_ids = []
            group_by_direction = {}
            for content_type in CONTENT_TYPES:
                code = content_type["code"]
                group_id = f"{slug}-{code.lower()}-platform-single-v3"
                group_ids.append(group_id)
                group_by_direction[code] = group_id
                if await db.get(ContentCombinationRule, group_id) is None:
                    if not rules_mutable:
                        raise RuntimeError(f"已发布 V3 规则缺少行业组合组: {group_id}")
                    method, title_codes, body_codes = GENERIC_DIRECTION_FORMULAS[code]
                    db.add(
                        ContentCombinationRule(
                            id=group_id,
                            version_id=PLATFORM_RULE_V3_ID,
                            schema_version=3,
                            content_goal=None,
                            content_goal_codes=[],
                            content_type_codes=[code],
                            industry_scope=[slug],
                            channel_scope=[],
                            narrative_axis_codes=[],
                            methods=[],
                            method_members=[{"method_code": method, "role": "primary", "order": 1}],
                            combination_type="single",
                            title_formula_codes=[],
                            title_pattern_codes=[],
                            title_formula_candidate_codes=title_codes,
                            content_formula_code=None,
                            body_pattern_codes=[],
                            body_formula_candidate_codes=body_codes,
                            required_variable_codes=content_type["required_variable_codes"],
                            required_evidence_types=[],
                            compatibility="compatible",
                            priority=700 - int(code[-1]),
                            conditions={},
                            hard_conditions={"single_narrative_axis": True, "unsupported_numbers": "block"},
                            score_weights={"variable_coverage": 2, "evidence_coverage": 3},
                            fallback_rule_id=None,
                            scenario_description=aliases[code],
                            source_metadata={
                                "source": "platform-v3-seed",
                                "industry": slug,
                                "platform_formula_catalog": "v3",
                            },
                            recommendation_reason=aliases[code],
                        )
                    )
            lexicon_ids = [f"lexicon-{slug}-core-v1"]

        golden, negative = _pack_samples(slug, group_by_direction)
        pack_id = f"industry-pack-{slug}-v3"
        pack = await db.get(IndustryContentPackVersion, pack_id)
        pack_mutable = pack is None or pack.status == "draft"
        values = {
            "schema_version": 3,
            "content_type_aliases": aliases,
            "lexicon_version_ids": lexicon_ids,
            "combination_overrides": group_ids,
            "persona_templates": [
                {
                    "name": config["persona"],
                    "identity": config["persona"],
                    "service_boundaries": [],
                }
            ],
            "knowledge_scope": [],
            "evidence_policy": {
                "price": "confirm",
                "number": "confirm",
                "result": "confirm",
                "unsupported_claims": "block",
            },
            "review_policy": {"single_narrative_axis": True, "human_formula_selection": True},
            "compliance_policy": {
                "unsupported_numbers": "block",
                "unsupported_promises": "block",
            },
            "visual_policy": {
                "styles": ["documentary", "clean-editorial"],
                "allowed_brand_asset_types": ["logo", "product", "environment"],
                "require_source_asset_provenance": True,
            },
            "golden_samples": golden,
            "negative_examples": negative,
            "minimum_coverage": 1.0,
            "source_metadata": {
                "source": "platform-v3-seed",
                "review_required_before_publish": False,
            },
            "changelog": "V3：行业方向、变量、候选池、证据、合规、视觉与评测协议",
        }
        if pack is None:
            pack = IndustryContentPackVersion(
                id=pack_id,
                slug=slug,
                tenant_id=None,
                version=3,
                status="draft",
                name=f"{source['name']} V3",
                description=source["description"],
                variable_schema=[],
                pattern_ids=[],
                created_by="system",
                created_at=now,
                **values,
            )
            db.add(pack)
        elif pack.status == "draft":
            for key, value in values.items():
                setattr(pack, key, value)

        for field_key, _label, variable_code in config["fields"]:
            mapping_id = f"mapping-v3-{slug}-{field_key}"
            if await db.get(IndustryVariableMapping, mapping_id) is None:
                if not pack_mutable:
                    raise RuntimeError(f"已发布 Industry Pack 缺少变量映射: {mapping_id}")
                db.add(
                    IndustryVariableMapping(
                        id=mapping_id,
                        industry_pack_version_id=pack_id,
                        field_key=field_key,
                        variable_code=variable_code,
                        transform_type="identity",
                        transform_config={},
                        required_by_content_types=(
                            ["CT01", "CT02", "CT03", "CT05"] if variable_code in {"product", "process"} else []
                        ),
                    )
                )


async def _activate_v3_seed_data(db: AsyncSession) -> None:
    """发布系统 V3 配置，并让新任务只从 V3 行业模板进入。"""

    now = utc_now_naive()
    rules = await db.get(ContentRuleVersion, PLATFORM_RULE_V3_ID)
    workflow = await db.get(ContentWorkflowVersion, PLATFORM_WORKFLOW_V3_ID)
    if rules is None or workflow is None:
        raise RuntimeError("V3 平台规则或工作流缺失")
    rules.status = "published"
    rules.published_at = rules.published_at or now
    workflow.status = "published"
    workflow.schema_version = 3
    workflow.published_at = workflow.published_at or now

    packs = list(
        (
            await db.execute(
                select(IndustryContentPackVersion).where(
                    IndustryContentPackVersion.schema_version == 3,
                    IndustryContentPackVersion.created_by == "system",
                )
            )
        ).scalars()
    )
    packs_by_slug = {pack.slug: pack for pack in packs}
    for pack in packs:
        pack.status = "published"
        pack.published_at = pack.published_at or now

    industries = {item["slug"]: item for item in INDUSTRIES}
    for slug, config in INDUSTRY_CONFIG.items():
        if slug not in packs_by_slug:
            raise RuntimeError(f"V3 行业包缺失: {slug}")
        template_id = f"industry-{slug}-v3"
        template = await db.get(IndustryTemplateVersion, template_id)
        source = industries[slug]
        values = {
            "status": "published",
            "name": source["name"],
            "description": source["description"],
            "icon": source["icon"],
            "quick_form_schema": content_form_fields(config, pro=False),
            "pro_form_schema": content_form_fields(config, pro=True),
            "default_goal": source["default_goal"],
            "default_strategy": {
                "content_type_code": "CT01",
                "channel_profile_version_id": XHS_CHANNEL_VERSION_ID,
            },
            "default_knowledge_scope": [],
            "default_workflow_version_id": PLATFORM_WORKFLOW_V3_ID,
            "review_policy": {
                "require_sources_for_numbers": True,
                "block_unsupported_effect_claims": True,
                "human_title_selection": False,
                "agent_title_selection": True,
                "single_narrative_axis": True,
            },
            "published_at": now,
        }
        if template is None:
            template = IndustryTemplateVersion(
                id=template_id,
                slug=slug,
                tenant_id=None,
                version=3,
                created_by="system",
                created_at=now,
                **values,
            )
            db.add(template)
        elif template.created_by == "system":
            for key, value in values.items():
                setattr(template, key, value)

        older_templates = list(
            (
                await db.execute(
                    select(IndustryTemplateVersion).where(
                        IndustryTemplateVersion.slug == slug,
                        IndustryTemplateVersion.id != template_id,
                        IndustryTemplateVersion.status == "published",
                    )
                )
            ).scalars()
        )
        for older in older_templates:
            older.status = "superseded"


__all__ = [
    "DECORATION_INDUSTRY_PACK_V3_ID",
    "PLATFORM_RULE_V3_ID",
    "PLATFORM_WORKFLOW_V3_ID",
    "ensure_content_v3_seed_data",
]
