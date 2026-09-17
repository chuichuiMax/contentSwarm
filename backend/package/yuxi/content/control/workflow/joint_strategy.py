"""Agent + Skill 联合决策的候选装配与结果锁定，保持历史工作流语义。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from sqlalchemy import select

from yuxi.content.control.strategy.recommend_v3 import StrategyPreviewActor
from yuxi.content.infrastructure.postgres.strategy_preview_repository import PostgresStrategyPreviewRepository
from yuxi.content.model.contracts.joint_strategy import StrategySnapshotV2, validate_joint_strategy
from yuxi.services.content_viral_assets import (
    check_asset_source,
    preparation_skill_hash,
    require_asset,
    search_ready_viral_assets,
)
from yuxi.storage.postgres.models_business import User


async def prepare_strategy_candidates(*, db, state, node_run_id):
    del node_run_id
    from yuxi.content.v3.joint_workflow import BLUEPRINT_FIRST_WORKFLOW_IDS

    auto_direction = state["runtime_config_snapshot"].get("workflow_version_id") in BLUEPRINT_FIRST_WORKFLOW_IDS
    user = (await db.execute(select(User).where(User.uid == state["uid"], User.is_deleted == 0))).scalar_one()
    result = await PostgresStrategyPreviewRepository(db).load_candidates(
        task_id=state["task_id"],
        actor=StrategyPreviewActor(uid=user.uid, role=user.role, tenant_id=str(user.department_id)),
        auto_direction=auto_direction,
    )
    catalog = result["strategy_candidates"]
    candidates = deepcopy(catalog)
    # 在线决策只看适用条件和公式说明；完整规则保留在锁定目录中供创作使用。
    candidates.pop("source_rules")
    for section in ("title_formulas", "content_formulas", "methods"):
        for item in candidates[section]:
            for metadata_key in ("id", "sort_order", "enabled"):
                item.pop(metadata_key, None)
            item.pop("reference_examples", None)
            source = item.get("source_content")
            if isinstance(source, dict):
                source.pop("source", None)
                source.pop("reference_examples", None)
    if auto_direction:
        fact_values = {
            f"content_brief.form_values.{key}": value
            for key, value in (state["content_brief"].get("form_values") or {}).items()
            if value not in (None, "", [], {})
        }
        fact_values.update(
            {
                f"evidence_bundle.items.{index}.value": item["value"]
                for index, item in enumerate(state["evidence_bundle"].get("items", []))
                if item.get("value") not in (None, "", [], {})
                and item.get("evidence_type", item.get("type")) != "style_reference"
                and (item.get("metadata") or {}).get("material_type") != "viral_example"
            }
        )
        candidates["available_input_paths"] = list(fact_values)
    queries = []
    references = []
    if state["runtime_config_snapshot"].get("creation_mode") == "viral_rewrite":
        brief = state["content_brief"]
        query = " ".join(
            str(brief.get(key) or "")
            for key in ("topic", "project_name", "content_goal", "audience", "form_values", "business_variables")
        )
        if auto_direction:
            # 检索输入保留用户事实，排除字段名和运行元数据；语义判断由选择 Agent 完成。
            query = " ".join(dict.fromkeys(str(value) for value in fact_values.values()))
        queries = [query]
        references = await search_ready_viral_assets(
            db,
            user,
            industry_slug=catalog["industry_slug"],
            query=query,
            limit=catalog["reference_candidate_limit"],
            include_structure=auto_direction,
            content_type_code=catalog.get("direction_code"),
        )
        if catalog.get("direction_code") and not references:
            from yuxi.content.control.errors import ContentApplicationError

            raise ContentApplicationError(
                "CONTENT_REFERENCE_TYPE_MISSING",
                f"未找到创作类型 {catalog['direction_code']} 对应的已准备爆款，"
                "请在有权限访问的知识库中绑定该创作类型，并准备同类型参考文章",
                "invalid",
            )
    return {
        "strategy_catalog": catalog,
        "strategy_candidates": candidates,
        "reference_candidates": references,
        "reference_search_queries": queries,
    }


async def lock_joint_strategy(*, db, state, node_run_id):
    from yuxi.content.control.workflow.deterministic_node import _available_variable_codes
    from yuxi.content.v3.body_calling import get_decoration_body_calling
    from yuxi.content.v3.body_calling import get_decoration_body_calling_source
    from yuxi.content.v3.formula_lexicons import get_formula_lexicon_requirements

    del node_run_id
    inputs = {
        key: state[key]
        for key in (
            "content_brief",
            "evidence_bundle",
            "strategy_candidates",
            "reference_candidates",
            "runtime_config_snapshot",
        )
    }
    result = validate_joint_strategy(state["joint_strategy_decision"], inputs)
    decision = result.strategy
    if decision.status != "selected" or result.reference.status in {"needs_input", "no_candidate"}:
        raise ValueError("；".join([*decision.unresolved_questions, *result.reference.unresolved_questions]))
    catalog = state["strategy_catalog"]
    title = deepcopy(next(item for item in catalog["title_formulas"] if item["code"] == decision.title_formula_code))
    body = deepcopy(next(item for item in catalog["content_formulas"] if item["code"] == decision.body_formula_code))
    methods = [
        deepcopy(next(item for item in catalog["methods"] if item["code"] == code))
        for code in decision.creation_method_codes
    ]
    direction_blueprint = None
    if decision.industry_slug == "decoration":
        direction_rules = [
            item for item in catalog["source_rules"] if decision.direction_code in item.get("content_type_codes", [])
        ]
        blueprints = [
            deepcopy(item.get("source_metadata", {}).get("composition_blueprint"))
            for item in direction_rules
            if item.get("source_metadata", {}).get("composition_blueprint")
        ]
        if len(direction_rules) != 1 or len(blueprints) != 1:
            raise ValueError("装修一级内容方向必须且只能绑定一套层级与词组组合")
        direction_blueprint = blueprints[0]
    if decision.industry_slug == "decoration":
        lexicons = get_formula_lexicon_requirements(title["code"], body["code"])
        title["lexicon_codes"] = [item["code"] for item in lexicons["title"]]
        calling = get_decoration_body_calling(body["code"])
        if calling:
            calling["composition_blueprint"] = deepcopy(direction_blueprint)
            sections = body.get("structure_schema") or []
            calling["sections"] = [
                {
                    **(
                        calling["sections"][index]
                        if index < len(calling["sections"])
                        else {"id": f"section_{index + 1}", "lexicon_calls": [], "fact_source": "evidence"}
                    ),
                    "name": text.split("：", 1)[0],
                    "instruction": text,
                    "fill_rule": text,
                }
                for index, text in enumerate(sections)
            ]
            body["body_calling"] = calling
            body["composition_blueprint"] = deepcopy(direction_blueprint)
            calling["formula_name"] = body["name"]
            calling["reference_examples"] = body.get("reference_examples") or []
            body["body_calling_source"] = get_decoration_body_calling_source(body["code"])
            body["structure_schema"] = [section["name"] for section in calling["sections"]]
    reference_snapshot = None
    viral_collection = {"evidence_items": [], "citations": [], "unresolved_questions": []}
    selection = {"selected_candidate_id": None, "selection_reason": result.reference.reason, "unresolved_questions": []}
    if result.reference.status == "selected":
        user = (await db.execute(select(User).where(User.uid == state["uid"], User.is_deleted == 0))).scalar_one()
        asset = await require_asset(db, user, result.reference.selected_asset_id)
        if (
            asset.status != "ready"
            or asset.source_hash != result.reference.source_hash
            or not await check_asset_source(db, asset)
        ):
            raise ValueError("选中的参考原文已失效，请重新选择")
        if asset.preparation_skill_hash != preparation_skill_hash():
            raise ValueError("参考准备标准已变化，请重新准备")
        reference_snapshot = {
            "id": asset.id,
            "article_id": asset.article_id,
            "kb_id": asset.kb_id,
            "file_id": asset.file_id,
            "locator": asset.source_json["locator"],
            "source_hash": asset.source_hash,
            "preparation_skill_hash": asset.preparation_skill_hash,
            "reference_card": next(
                item["reference_card"] for item in state["reference_candidates"] if item["id"] == asset.id
            ),
            "reference_blueprint": asset.prepared_json["reference_blueprint"],
            "slot_mapping": result.reference.slot_mapping,
        }
        source_id = f"{asset.kb_id}/{asset.file_id}/{asset.source_json['locator']}"
        # 创作证据只带抽象蓝图，原文与锚点留在可追溯资产快照中。
        viral_collection["evidence_items"] = [
            {
                "id": asset.id,
                "variable_codes": [],
                "value": "已准备的单篇结构参考",
                "source_type": "knowledge_base",
                "source_id": source_id,
                "source_version": asset.source_hash,
                "source_hash": asset.source_hash,
                "verified_status": "retrieved",
                "allowed_usage": ["style_reference"],
                "risk_level": "normal",
                "metadata": {
                    "material_type": "viral_example",
                    "asset_id": asset.id,
                    "usage_mode": "structure_reference_only",
                },
            }
        ]
        selected_assessment = next(item for item in result.reference.assessments if item.candidate_id == asset.id)
        # 将已核验的新决策投影为现有创作契约；不再评分，也不补造旧式选择结果。
        selection.update(
            selected_candidate_id=asset.id,
            selection_basis={
                "schema_version": 2,
                "input_variable_paths": selected_assessment.input_paths,
                "matched_dimensions": selected_assessment.dimensions,
                "structure_fillability": {
                    "filled_slots": result.reference.slot_mapping,
                    "unfilled_required_slots": [],
                },
                "candidate_comparison": [item.model_dump() for item in result.reference.assessments],
                "prepared_reference_decision": result.reference.model_dump(),
            },
            reference_blueprint=asset.prepared_json["reference_blueprint"],
        )
    payload = {
        "schema_version": 2,
        "industry_slug": decision.industry_slug,
        "strategy_mode": decision.strategy_mode,
        "content_direction": decision.direction_code,
        "creation_methods": decision.creation_method_codes,
        "creation_method_definitions": methods,
        "title_formula": title,
        "body_formula": body,
        "rule_version_id": decision.rule_version_id,
        "policy_hash": decision.policy_hash,
        "decision": result.model_dump(mode="json", exclude={"price_research_questions"}),
        "reference_snapshot": reference_snapshot,
    }
    if direction_blueprint is not None:
        payload["direction_blueprint"] = direction_blueprint
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["snapshot_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
    snapshot = StrategySnapshotV2.model_validate(payload).model_dump(mode="json")
    required = set(title.get("variable_schema") or []) | set(body.get("required_variables") or [])
    required.update(variable for method in methods for variable in method.get("variable_schema") or [])
    missing = sorted(required - _available_variable_codes(state))
    return {
        "strategy_snapshot": snapshot,
        "formula_selection_snapshot": {
            "schema_version": 2,
            "selected_title_formula_code": title["code"],
            "selected_body_formula_code": body["code"],
            "rule_version_id": decision.rule_version_id,
            "selected_by": "agent",
            "decision": decision.model_dump(),
        },
        "evidence_gap_analysis": {
            "has_missing": bool(missing),
            "missing_variable_codes": missing,
            "missing_evidence_types": [],
            "target_formula_pair": {"title_formula_code": title["code"], "body_formula_code": body["code"]},
        },
        "viral_candidate_collection": viral_collection,
        "viral_reference_selection": selection,
    }
