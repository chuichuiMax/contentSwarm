"""生成前确定并冻结创作计划；在线链路不调用策略选择模型。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from sqlalchemy import select

from yuxi.content.control.errors import ContentApplicationError
from yuxi.content.model.contracts.joint_strategy import StrategySnapshotV2
from yuxi.content.model.materials import (
    MaterialRequirementManifestV1,
    _canonical_hash,
    build_material_manifest,
    create_production_order,
)
from yuxi.content.v3.body_calling import get_decoration_body_calling, get_decoration_body_calling_source
from yuxi.content.v3.formula_lexicons import get_formula_lexicon_requirements
from yuxi.content.v3.foreman_composition import lock_evidence_composition
from yuxi.services.content_viral_assets import check_asset_source, preparation_skill_hash, require_asset
from yuxi.services.run_queue_service import append_run_stream_event
from yuxi.storage.postgres.models_business import User

_PRICE_VARIABLES = {
    "price",
    "unit_price",
    "budget",
    "cost",
    "labor_cost",
    "material_cost",
    "discount",
    "fee",
}
_REFERENCE_VARIABLE_ALIASES = {
    "price": ("title_price",),
    "trade_breakdown": ("quote_block",),
    "labor_aux_breakdown": ("quote_block",),
}
_LOCKED_QUOTE_PLAN_VARIABLES = {"quote_block", "title_price", "title_price_label"}
_PLANNER_VERSION = "deterministic_creation_plan_v1"


def _fact_values(fact_index: dict[str, Any], code: str) -> list[Any]:
    return [item.get("value") for item in (fact_index.get("facts") or {}).get(code, {}).get("values") or []]


def _is_budget_quote(fact_index: dict[str, Any]) -> bool:
    """生产资料包装出的预算价不走当家锁定报价块。"""

    available = set(fact_index.get("available_variable_codes") or [])
    quote_types = [str(value).strip().lower() for value in _fact_values(fact_index, "quote_type") if value not in (None, "")]
    if any(value == "budget" or "预算" in value for value in quote_types):
        return True
    return "price" in available and not available.intersection({"quote_block", "title_price"})


def _plan_available_codes(fact_index: dict[str, Any]) -> set[str]:
    available = set(fact_index.get("available_variable_codes") or [])
    if _is_budget_quote(fact_index) and "price" in available:
        available.update({"title_price", "quote_type"})
    return available


def _drop_budget_locked_quote_requirements(missing: set[str], fact_index: dict[str, Any]) -> set[str]:
    if not _is_budget_quote(fact_index):
        return missing
    missing -= {"quote_block", "title_price_label"}
    if "price" in set(fact_index.get("available_variable_codes") or []):
        missing.discard("title_price")
        missing.discard("quote_type")
    return missing


def _relax_budget_quote_manifest(
    manifest: MaterialRequirementManifestV1, fact_index: dict[str, Any]
) -> MaterialRequirementManifestV1:
    if not _is_budget_quote(fact_index):
        return manifest
    optional = {"quote_block", "title_price_label"}
    if "price" in set(fact_index.get("available_variable_codes") or []):
        optional.update({"title_price", "quote_type"})
    payload = manifest.model_dump(mode="json")
    payload.pop("manifest_hash", None)
    payload["requirements"] = [
        {**item, "required": False} if item.get("variable_code") in optional else item
        for item in payload.get("requirements") or []
    ]
    return MaterialRequirementManifestV1.model_validate({**payload, "manifest_hash": _canonical_hash(payload)})


def _missing_title_formula_variables(title: dict[str, Any], available: set[str]) -> set[str]:
    """返回标题公式缺失事实；同一槽位内的变量按 one-of 解释。"""

    variable_schema = [str(code) for code in title.get("variable_schema") or []]
    slots = (title.get("source_content") or {}).get("slot_schema") or []
    if not slots:
        return set(variable_schema) - available

    grouped_codes: set[str] = set()
    missing: set[str] = set()
    for slot in slots:
        candidates = [str(code) for code in slot.get("variable_codes") or []]
        grouped_codes.update(candidates)
        if not candidates or any(code in available for code in candidates):
            continue
        preferred = next((code for code in variable_schema if code in candidates), candidates[0])
        missing.add(preferred)
    missing.update(set(variable_schema) - grouped_codes - available)
    return missing


def build_fact_index(content_brief: dict[str, Any], evidence_bundle: dict[str, Any]) -> dict[str, Any]:
    """把本次简报与已冻结 Evidence 归一成变量到可引用路径的稳定索引。"""

    facts: dict[str, dict[str, Any]] = {}
    brief_values: dict[str, list[Any]] = {}

    def add(code: str, path: str, value: Any, *, evidence: bool) -> None:
        if not code or value in (None, "", [], {}):
            return
        entry = facts.setdefault(code, {"paths": [], "evidence_paths": [], "values": []})
        if path not in entry["paths"]:
            entry["paths"].append(path)
        if evidence and path not in entry["evidence_paths"]:
            entry["evidence_paths"].append(path)
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        if all(item["canonical"] != canonical for item in entry["values"]):
            entry["values"].append({"canonical": canonical, "value": value})

    for section_name in ("business_variables", "form_values", "brand", "persona"):
        section = content_brief.get(section_name)
        if not isinstance(section, dict):
            continue
        for code, value in section.items():
            # 简报在进入运行前由用户确认，并会在 normalize_evidence 中冻结为 Evidence。
            add(str(code), f"content_brief.{section_name}.{code}", value, evidence=True)
            if value not in (None, "", [], {}):
                brief_values.setdefault(str(code), []).append(value)
    for code, value in content_brief.items():
        if isinstance(value, dict):
            continue
        add(str(code), f"content_brief.{code}", value, evidence=True)
        if value not in (None, "", [], {}):
            brief_values.setdefault(str(code), []).append(value)

    for index, item in enumerate(evidence_bundle.get("items") or []):
        if not isinstance(item, dict) or item.get("verified_status") == "rejected":
            continue
        if (
            item.get("evidence_type") == "style_reference"
            or item.get("type") == "style_reference"
            or (item.get("metadata") or {}).get("material_type") == "viral_example"
        ):
            continue
        for code in item.get("variable_codes") or []:
            add(str(code), f"evidence_bundle.items.{index}.value", item.get("value"), evidence=True)

    conflicts = []
    for code, values in brief_values.items():
        canonical = {
            json.dumps(
                value[0] if isinstance(value, list) and len(value) == 1 else value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for value in values
        }
        if len(canonical) > 1:
            conflicts.append(code)
    return {
        "facts": facts,
        "available_variable_codes": sorted(facts),
        "conflicting_variable_codes": sorted(conflicts),
    }


def _resolve_rule_and_formulas(
    catalog: dict[str, Any], fact_index: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    direction = str(catalog.get("direction_code") or "")
    if not direction:
        raise ContentApplicationError("CONTENT_PLAN_TYPE_MISSING", "请先选择本次创作类型", "invalid")
    rules = [rule for rule in catalog.get("source_rules") or [] if direction in rule.get("content_type_codes", [])]
    if len(rules) != 1:
        raise ContentApplicationError(
            "CONTENT_PLAN_RULE_MISSING",
            f"创作类型 {direction} 必须且只能配置一条已发布组合规则，当前为 {len(rules)} 条",
            "conflict",
        )
    rule = rules[0]
    method_codes = [
        str(item.get("method_code"))
        for item in rule.get("method_members") or []
        if isinstance(item, dict) and item.get("method_code")
    ] or [str(code) for code in rule.get("methods") or [] if code]
    methods_by_code = {item["code"]: item for item in catalog.get("methods") or []}
    if not method_codes or any(code not in methods_by_code for code in method_codes):
        raise ContentApplicationError(
            "CONTENT_PLAN_CONFIGURATION_INVALID", "组合规则引用了不存在或已停用的创作手法", "conflict"
        )
    methods = [deepcopy(methods_by_code[code]) for code in method_codes]
    available = _plan_available_codes(fact_index)

    def choose_locked(section: str, codes: list[str]) -> dict[str, Any] | None:
        by_code = {item["code"]: item for item in catalog.get(section) or []}
        if not codes:
            return None
        item = by_code.get(codes[0])
        if item is None:
            return None
        compatible = set(item.get("compatible_methods") or [])
        if compatible and not set(method_codes) <= compatible:
            return None
        return deepcopy(item)

    title_codes = list(rule.get("title_formula_candidate_codes") or [])
    body_codes = list(rule.get("body_formula_candidate_codes") or [])
    # 生产订单先锁定规则声明的默认公式（候选第一项），资料缺失只能补料，不能静默换公式。
    title = choose_locked("title_formulas", title_codes)
    body = choose_locked("content_formulas", body_codes)
    required = set(rule.get("required_variable_codes") or [])
    required.update(variable for method in methods for variable in method.get("variable_schema") or [])
    if body is not None:
        required.update(body.get("required_variables") or [])
    missing = required - available
    if title is not None:
        missing.update(_missing_title_formula_variables(title, available))
    if title is not None and body is not None:
        # 缺口分析也必须覆盖物料门追加的人设等要求；临时订单只用于编译，不写入任务。
        order = create_production_order(
            task_id="creation-plan-gap-analysis",
            catalog=catalog,
            group_id=str(rule.get("id") or rule.get("code")),
            creation_method_codes=[item["code"] for item in methods],
            title_formula_code=title["code"],
            body_formula_code=body["code"],
        )
        try:
            manifest = build_material_manifest(catalog=catalog, order=order)
        except ValueError as exc:
            raise ContentApplicationError("CONTENT_PLAN_CONFIGURATION_INVALID", str(exc), "conflict") from exc
        # derived 物料由后续程序计算，不能要求事实抽取 Agent 提交。
        missing.update(
            item.variable_code
            for item in manifest.requirements
            if item.required and not item.requirement_id.startswith("derived:") and item.variable_code not in available
        )
        groups: dict[str, list[str]] = {}
        for item in manifest.requirements:
            for group in item.validation_schema.get("alternative_groups") or []:
                groups.setdefault(group, []).append(item.variable_code)
        for candidates in groups.values():
            if not (available | missing).intersection(candidates):
                missing.add(candidates[0])
    missing = _drop_budget_locked_quote_requirements(missing, fact_index)
    return rule, title, body, methods, sorted(missing)


def compile_production_order_and_manifest(
    *, task_id: str, catalog: dict[str, Any], fact_index: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    rule, title, body, methods, _missing = _resolve_rule_and_formulas(catalog, fact_index)
    if title is None or body is None:
        raise ContentApplicationError(
            "CONTENT_PLAN_CONFIGURATION_INVALID",
            "组合规则的默认标题公式或正文公式不存在、已停用或与创作手法不兼容",
            "conflict",
        )
    order = create_production_order(
        task_id=task_id,
        catalog=catalog,
        group_id=str(rule.get("id") or rule.get("code")),
        creation_method_codes=[item["code"] for item in methods],
        title_formula_code=title["code"],
        body_formula_code=body["code"],
    )
    try:
        manifest = _relax_budget_quote_manifest(
            build_material_manifest(catalog=catalog, order=order), fact_index
        )
    except ValueError as exc:
        raise ContentApplicationError("CONTENT_PLAN_CONFIGURATION_INVALID", str(exc), "conflict") from exc
    return order.model_dump(mode="json"), manifest.model_dump(mode="json")


def map_reference_slots(
    card: dict[str, Any], fact_index: dict[str, Any], *, mapped_facts_only: bool, minimum: int
) -> dict[str, Any] | None:
    if card.get("schema_version") != 2:
        return None
    facts = fact_index["facts"]
    mapping: dict[str, list[str]] = {}
    missing_required: list[str] = []
    mapped_required = 0
    for slot in card.get("required_slots") or []:
        codes = list(slot.get("variable_codes") or [])
        source_key = "evidence_paths" if slot.get("evidence_required", True) else "paths"
        resolved_codes: list[tuple[str, str]] = []
        for code in codes:
            candidates = (code, *_REFERENCE_VARIABLE_ALIASES.get(code, ()))
            resolved = next(
                (candidate for candidate in candidates if (facts.get(candidate) or {}).get(source_key)), None
            )
            if resolved:
                resolved_codes.append((code, resolved))
        available = [code for code, _resolved in resolved_codes]
        matched = len(available) == len(codes) if slot.get("match_mode", "all") == "all" else bool(available)
        slot_key = str(slot.get("slot_key") or "")
        if matched and slot_key:
            mapping[slot_key] = list(
                dict.fromkeys(path for _code, resolved in resolved_codes for path in facts[resolved][source_key])
            )
            if slot.get("required"):
                mapped_required += 1
        elif slot.get("required"):
            missing_required.append(slot_key or str(slot.get("name") or "未命名槽位"))
    if mapped_facts_only:
        if len(mapping) < minimum:
            return None
    elif missing_required:
        return None
    return {
        "slot_mapping": mapping,
        "mapped_required_count": mapped_required,
        "mapped_count": len(mapping),
        "omitted_required_slots": missing_required,
    }


def rank_reference_candidates(
    candidates: list[dict[str, Any]],
    *,
    direction_code: str,
    fact_index: dict[str, Any],
    runtime_config_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    policy = (
        ((runtime_config_snapshot.get("content_rule_bundle") or {}).get("runtime_rules") or {})
        .get("viral-author-core", {})
        .get("reference_policy", {})
    )
    mapped_facts_only = policy.get("required_slot_mode") == "mapped_facts_only"
    minimum = int(policy.get("minimum_mapped_slots") or 1)

    def context_match_count(card: dict[str, Any]) -> int:
        count = 0
        for field in ("audience", "scene", "goal", "channel"):
            expected = str(card.get(field) or "").strip().lower()
            if not expected:
                continue
            values = [
                str(item["value"]).strip().lower() for item in (fact_index["facts"].get(field) or {}).get("values", [])
            ]
            if any(value and (value in expected or expected in value) for value in values):
                count += 1
        return count

    ranked = []
    for candidate in candidates:
        card = candidate.get("reference_card") or {}
        if card.get("content_type_code") != direction_code:
            continue
        mapped = map_reference_slots(
            card,
            fact_index,
            mapped_facts_only=mapped_facts_only,
            minimum=minimum,
        )
        if mapped is None:
            continue
        ranked.append({**candidate, **mapped, "context_match_count": context_match_count(card)})
    ranked.sort(
        key=lambda item: (
            -item["mapped_required_count"],
            -item["mapped_count"],
            -int(item.get("retrieval_score") or 0),
            -item["context_match_count"],
            item["id"],
        )
    )
    return ranked


def analyze_plan_gaps(
    *,
    catalog: dict[str, Any],
    references: list[dict[str, Any]],
    content_brief: dict[str, Any],
    evidence_bundle: dict[str, Any],
    runtime_config_snapshot: dict[str, Any],
) -> dict[str, Any]:
    fact_index = build_fact_index(content_brief, evidence_bundle)
    rule, title, body, methods, missing = _resolve_rule_and_formulas(catalog, fact_index)
    required_evidence = set(rule.get("required_evidence_types") or [])
    available_evidence = {
        str(item.get("evidence_type") or item.get("type") or item.get("source_type") or "")
        for item in evidence_bundle.get("items") or []
        if isinstance(item, dict) and item.get("verified_status") != "rejected"
    }
    missing_evidence = sorted(required_evidence - available_evidence)
    ranked = rank_reference_candidates(
        references,
        direction_code=str(catalog.get("direction_code") or ""),
        fact_index=fact_index,
        runtime_config_snapshot=runtime_config_snapshot,
    )
    price_missing = sorted(set(missing) & _PRICE_VARIABLES)
    variables_by_code = {str(item.get("code") or ""): item for item in catalog.get("variables") or []}
    return {
        "has_missing": bool(
            missing
            or missing_evidence
            or fact_index["conflicting_variable_codes"]
            or title is None
            or body is None
            or not ranked
        ),
        "missing_variable_codes": missing,
        "missing_variable_definitions": [
            {
                "code": code,
                "name": str(variables_by_code.get(code, {}).get("name") or code),
                "value_type": str(variables_by_code.get(code, {}).get("value_type") or "string"),
            }
            for code in missing
        ],
        "missing_evidence_types": missing_evidence,
        "conflicting_variable_codes": fact_index["conflicting_variable_codes"],
        "title_formula_available": title is not None,
        "body_formula_available": body is not None,
        "eligible_reference_ids": [item["id"] for item in ranked],
        "price_research_questions": [f"请检索与本项目口径一致的 {code} 价格证据" for code in price_missing],
        "group_id": str(rule.get("id") or rule.get("code") or ""),
        "creation_method_codes": [item["code"] for item in methods],
    }


async def prepare_creation_plan_inputs(*, db, state: dict[str, Any], node_run_id: str) -> dict[str, Any]:
    from yuxi.content.control.workflow.joint_strategy import prepare_strategy_candidates

    try:
        result = await prepare_strategy_candidates(db=db, state=state, node_run_id=node_run_id)
    except ContentApplicationError as exc:
        if exc.code != "CONTENT_REFERENCE_TYPE_MISSING":
            raise
        result = {
            "strategy_catalog": {},
            "strategy_candidates": {},
            "reference_candidates": [],
            "reference_search_queries": [],
        }
        # 重新装配时仍需规则目录；参考缺失会在计划分析中给出专用错误。
        from yuxi.content.control.strategy.recommend_v3 import StrategyPreviewActor
        from yuxi.content.infrastructure.postgres.strategy_preview_repository import PostgresStrategyPreviewRepository

        user = (await db.execute(select(User).where(User.uid == state["uid"], User.is_deleted == 0))).scalar_one()
        loaded = await PostgresStrategyPreviewRepository(db).load_candidates(
            task_id=state["task_id"],
            actor=StrategyPreviewActor(uid=user.uid, role=user.role, tenant_id=str(user.department_id)),
            auto_direction=False,
        )
        result["strategy_catalog"] = loaded["strategy_candidates"]
        result["strategy_candidates"] = deepcopy(loaded["strategy_candidates"])
    catalog = result["strategy_catalog"]
    fact_index = build_fact_index(state["content_brief"], state["evidence_bundle"])
    selected_catalog = lock_evidence_composition(catalog, fact_index, seed=state["task_id"])
    gap = analyze_plan_gaps(
        catalog=selected_catalog,
        references=result["reference_candidates"],
        content_brief=state["content_brief"],
        evidence_bundle=state["evidence_bundle"],
        runtime_config_snapshot=state["runtime_config_snapshot"],
    )
    selection_pending = any(
        (rule.get("source_metadata") or {}).get("formula_selection_policy") == "evidence_composition_v1"
        and not (rule.get("source_metadata") or {}).get("locked_composition")
        for rule in catalog.get("source_rules") or []
    )
    if selection_pending:
        # 新组合先摘录候选事实，再按实际摘录结果选式。候选缺失不等于必需物料缺失。
        candidate_codes = set()
        for title in catalog["title_formulas"]:
            candidate_codes.update(title.get("variable_schema") or [])
        for body in catalog["content_formulas"]:
            candidate_codes.update(body.get("required_variables") or [])
            for component in (body.get("source_content") or {}).get("component_pool") or []:
                candidate_codes.update(component["required_variables"])
        candidate_codes -= set(fact_index["available_variable_codes"])
        candidate_codes -= set(gap["missing_variable_codes"])
        gap["candidate_variable_codes"] = sorted(candidate_codes)
        gap["candidate_variable_definitions"] = [
            {"code": item["code"], "name": item["name"], "value_type": item["value_type"]}
            for item in catalog["variables"]
            if item["code"] in candidate_codes
        ]
        gap["selection_pending"] = True
        return {**result, "production_order": {}, "material_manifest": {}, "creation_plan_gap_analysis": gap}
    order, manifest = compile_production_order_and_manifest(
        task_id=state["task_id"],
        catalog=catalog,
        fact_index=fact_index,
    )
    return {**result, "production_order": order, "material_manifest": manifest, "creation_plan_gap_analysis": gap}


async def preview_creation_plan(*, db, user, task_id: str) -> dict[str, Any]:
    """在启动运行前只读预检规则、事实和已审核参考，不创建 checkpoint。"""

    from yuxi.content.control.strategy.recommend_v3 import StrategyPreviewActor
    from yuxi.content.infrastructure.postgres.strategy_preview_repository import PostgresStrategyPreviewRepository
    from yuxi.repositories.content_repository import ContentRepository
    from yuxi.services.content_viral_assets import search_ready_viral_assets

    repo = ContentRepository(db)
    task = await repo.get_task_for_user(task_id, user)
    if task is None:
        raise ContentApplicationError("CONTENT_TASK_NOT_FOUND", "内容任务不存在", "not_found")
    if not task.brief_json:
        raise ContentApplicationError("CONTENT_BRIEF_REQUIRED", "请先形成事实简报", "conflict")
    loaded = await PostgresStrategyPreviewRepository(db).load_candidates(
        task_id=task_id,
        actor=StrategyPreviewActor(
            uid=str(user.uid),
            role=user.role,
            tenant_id=str(user.department_id) if user.department_id is not None else None,
        ),
        auto_direction=False,
    )
    catalog = loaded["strategy_candidates"]
    query = " ".join(
        str(value)
        for section in (task.brief_json.get("form_values") or {}, task.brief_json.get("business_variables") or {})
        for value in section.values()
        if value not in (None, "", [], {})
    )
    references = await search_ready_viral_assets(
        db,
        user,
        industry_slug=catalog["industry_slug"],
        query=query,
        limit=catalog["reference_candidate_limit"],
        include_structure=True,
        content_type_code=catalog.get("direction_code"),
    )
    evidence_bundle = task.evidence_json or {"items": []}
    runtime = deepcopy(task.runtime_config_snapshot_json or {})
    if not runtime.get("content_rule_bundle"):
        from yuxi.content.v3.modular_rules import build_modular_rule_bundle

        runtime["content_rule_bundle"] = build_modular_rule_bundle(task.brief_json)
    fact_index = build_fact_index(task.brief_json, evidence_bundle)
    catalog = lock_evidence_composition(catalog, fact_index, seed=task_id)
    rule, title, body, methods, _missing = _resolve_rule_and_formulas(catalog, fact_index)
    gaps = analyze_plan_gaps(
        catalog=catalog,
        references=references,
        content_brief=task.brief_json,
        evidence_bundle=evidence_bundle,
        runtime_config_snapshot=runtime,
    )
    ranked = rank_reference_candidates(
        references,
        direction_code=str(catalog.get("direction_code") or ""),
        fact_index=fact_index,
        runtime_config_snapshot=runtime,
    )
    selected_reference = ranked[0] if ranked else None
    production_order, material_manifest = compile_production_order_and_manifest(
        task_id=task_id,
        catalog=catalog,
        fact_index=fact_index,
    )
    extractable_from_request = bool(str(task.brief_json.get("user_request") or "").strip())
    extraction_can_complete_formulas = False
    if extractable_from_request and gaps["missing_variable_codes"]:
        projected_brief = deepcopy(task.brief_json)
        projected_values = dict(projected_brief.get("form_values") or {})
        projected_values.update({code: "待从用户原文抽取" for code in gaps["missing_variable_codes"]})
        projected_brief["form_values"] = projected_values
        projected_index = build_fact_index(projected_brief, evidence_bundle)
        _, projected_title, projected_body, _, _ = _resolve_rule_and_formulas(catalog, projected_index)
        extraction_can_complete_formulas = projected_title is not None and projected_body is not None
    has_type_compatible_reference = any(
        (item.get("reference_card") or {}).get("schema_version") == 2
        and (item.get("reference_card") or {}).get("content_type_code") == catalog.get("direction_code")
        for item in references
    )
    blocking_gaps = bool(
        gaps["missing_evidence_types"]
        or gaps["conflicting_variable_codes"]
        or (not gaps["title_formula_available"] and not extraction_can_complete_formulas)
        or (not gaps["body_formula_available"] and not extraction_can_complete_formulas)
        or (not gaps["eligible_reference_ids"] and not (extractable_from_request and has_type_compatible_reference))
        or (gaps["missing_variable_codes"] and not extractable_from_request)
    )
    return {
        "plan": {
            "content_type_code": catalog.get("direction_code"),
            "rule_version_id": catalog.get("rule_version_id"),
            "group_id": str(rule.get("id") or rule.get("code") or ""),
            "creation_methods": [{"code": item["code"], "name": item.get("name", item["code"])} for item in methods],
            "title_formula": ({"code": title["code"], "name": title.get("name", title["code"])} if title else None),
            "body_formula": ({"code": body["code"], "name": body.get("name", body["code"])} if body else None),
            "reference": (
                {
                    "id": selected_reference["id"],
                    "title": selected_reference.get("title"),
                    "mapped_slots": sorted(selected_reference["slot_mapping"]),
                }
                if selected_reference
                else None
            ),
        },
        "production_order": production_order,
        "material_manifest": material_manifest,
        "gaps": gaps,
        "can_generate": not blocking_gaps,
        "requires_fact_extraction": bool(gaps["missing_variable_codes"] and extractable_from_request),
    }


async def merge_extracted_creation_facts(*, db, state: dict[str, Any], node_run_id: str) -> dict[str, Any]:
    """只接受用户原文逐字可验证的抽取结果，并冻结为本次 Evidence。"""

    from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler

    input_gap = state.get("creation_plan_gap_analysis") or {}
    requested = set(input_gap.get("missing_variable_codes") or []) | set(
        input_gap.get("candidate_variable_codes") or []
    )
    extracted = state.get("extracted_creation_facts") or {"facts": []}
    list_variable_codes = {
        str(item.get("code") or "")
        for item in (state.get("strategy_catalog") or {}).get("variables") or []
        if item.get("value_type") == "list"
    }

    def strings(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [text for item in value.values() for text in strings(item)]
        if isinstance(value, list):
            return [text for item in value for text in strings(item)]
        return []

    source_texts = strings(state["content_brief"])
    additions = []
    seen_codes = set()
    seen_facts = set()
    for fact in extracted.get("facts") or []:
        code = str(fact.get("variable_code") or "")
        value = str(fact.get("value") or "").strip()
        quote = str(fact.get("source_quote") or "").strip()
        if code not in requested:
            raise ContentApplicationError(
                "CONTENT_PLAN_CONFIGURATION_INVALID", f"事实抽取提交了未请求的变量：{code}", "invalid"
            )
        if (code, quote) in seen_facts or (code in seen_codes and code not in list_variable_codes):
            raise ContentApplicationError(
                "CONTENT_PLAN_CONFIGURATION_INVALID", f"事实抽取重复提交了单值变量或相同事实：{code}", "invalid"
            )
        if not quote or value != quote or not any(quote in text for text in source_texts):
            raise ContentApplicationError(
                "CONTENT_PLAN_CONFIGURATION_INVALID", f"变量 {code} 的抽取值不是用户原文逐字引用", "invalid"
            )
        seen_codes.add(code)
        seen_facts.add((code, quote))
        source_hash = hashlib.sha256(
            json.dumps([state["task_id"], code, quote], ensure_ascii=False).encode()
        ).hexdigest()
        additions.append(
            {
                "id": f"ev_{source_hash[:16]}",
                "variable_codes": [code],
                "value": value,
                "source_type": "manual_input",
                "source_id": "extracted_from_user_request",
                "source_version": "brief-v1",
                "verified_status": "user_confirmed",
                "allowed_usage": ["title", "body", "visual"],
                "risk_level": "high_risk" if code in _PRICE_VARIABLES else "normal",
                "source_hash": source_hash,
                "metadata": {"extraction_mode": "verbatim", "source_quote": quote},
            }
        )
    frozen = await V3DeterministicNodeHandler()._freeze_evidence_bundle(
        db=db,
        state={
            **state,
            "evidence_collection": {
                "evidence_items": additions,
                "citations": [],
                "unresolved_questions": [],
            },
        },
        node_run_id=node_run_id,
    )
    catalog = state["strategy_catalog"]
    selection_updates = {}
    if input_gap.get("selection_pending"):
        fact_index = build_fact_index(state["content_brief"], frozen["evidence_bundle"])
        catalog = lock_evidence_composition(catalog, fact_index, seed=state["task_id"])
        order, manifest = compile_production_order_and_manifest(
            task_id=state["task_id"],
            catalog=catalog,
            fact_index=fact_index,
        )
        selection_updates = {
            "strategy_catalog": catalog,
            "strategy_candidates": deepcopy(catalog),
            "production_order": order,
            "material_manifest": manifest,
        }
    gap = analyze_plan_gaps(
        catalog=catalog,
        references=state.get("reference_candidates") or [],
        content_brief=state["content_brief"],
        evidence_bundle=frozen["evidence_bundle"],
        runtime_config_snapshot=state["runtime_config_snapshot"],
    )
    return {**frozen, **selection_updates, "creation_plan_gap_analysis": gap}


async def build_creation_plan(*, db, state: dict[str, Any], node_run_id: str) -> dict[str, Any]:
    del node_run_id
    await append_run_stream_event(
        state["run_id"],
        "content.plan.started",
        {"task_id": state["task_id"], "content_type_code": (state.get("strategy_catalog") or {}).get("direction_code")},
        thread_id=state["task_id"],
    )
    try:
        return await _build_creation_plan(db=db, state=state)
    except ContentApplicationError as exc:
        await append_run_stream_event(
            state["run_id"],
            "content.plan.blocked",
            {"task_id": state["task_id"], "error_code": exc.code},
            thread_id=state["task_id"],
        )
        raise


async def _build_creation_plan(*, db, state: dict[str, Any]) -> dict[str, Any]:
    catalog = state["strategy_catalog"]
    fact_index = build_fact_index(state["content_brief"], state["evidence_bundle"])
    if fact_index["conflicting_variable_codes"]:
        raise ContentApplicationError(
            "CONTENT_PLAN_FACT_CONFLICT",
            "以下业务字段存在冲突值：" + "、".join(fact_index["conflicting_variable_codes"]),
            "invalid",
        )
    rule, title, body, methods, missing = _resolve_rule_and_formulas(catalog, fact_index)
    if missing:
        code = (
            "CONTENT_PLAN_PRICE_RECOVERY_REQUIRED" if set(missing) <= _PRICE_VARIABLES else "CONTENT_PLAN_INPUT_MISSING"
        )
        raise ContentApplicationError(code, "缺少创作计划必需字段：" + "、".join(missing), "invalid")
    if title is None:
        raise ContentApplicationError(
            "CONTENT_PLAN_TITLE_FORMULA_MISSING", "没有标题公式能使用当前已确认资料", "conflict"
        )
    if body is None:
        raise ContentApplicationError(
            "CONTENT_PLAN_CONFIGURATION_INVALID", "没有正文公式能使用当前已确认资料", "conflict"
        )
    required_evidence = set(rule.get("required_evidence_types") or [])
    available_evidence = {
        str(item.get("evidence_type") or item.get("type") or item.get("source_type") or "")
        for item in state["evidence_bundle"].get("items") or []
        if isinstance(item, dict) and item.get("verified_status") != "rejected"
    }
    missing_evidence = sorted(required_evidence - available_evidence)
    if missing_evidence:
        raise ContentApplicationError(
            "CONTENT_PLAN_EVIDENCE_MISSING",
            "缺少创作计划必需 Evidence：" + "、".join(missing_evidence),
            "invalid",
        )
    direction = str(catalog["direction_code"])
    ranked = rank_reference_candidates(
        state.get("reference_candidates") or [],
        direction_code=direction,
        fact_index=fact_index,
        runtime_config_snapshot=state["runtime_config_snapshot"],
    )
    if not ranked:
        raise ContentApplicationError(
            "CONTENT_PLAN_REFERENCE_MISSING", f"没有创作类型 {direction} 的可填充已审核爆款", "conflict"
        )
    selected = ranked[0]
    user = (await db.execute(select(User).where(User.uid == state["uid"], User.is_deleted == 0))).scalar_one()
    asset = await require_asset(db, user, selected["id"])
    card = (asset.prepared_json or {}).get("reference_card") or {}
    if (
        asset.status != "ready"
        or asset.source_hash != selected["source_hash"]
        or asset.preparation_skill_hash != preparation_skill_hash()
        or card.get("schema_version") != 2
        or card.get("content_type_code") != direction
        or not await check_asset_source(db, asset)
    ):
        raise ContentApplicationError(
            "CONTENT_PLAN_REFERENCE_INVALID", "选中的爆款版本、类型、准备标准或原文已失效", "conflict"
        )

    method_codes = [item["code"] for item in methods]
    production_order, material_manifest = compile_production_order_and_manifest(
        task_id=state["task_id"], catalog=catalog, fact_index=fact_index
    )
    existing_order = state.get("production_order")
    if existing_order and existing_order.get("order_hash") != production_order["order_hash"]:
        raise ContentApplicationError("PRODUCTION_ORDER_CHANGED", "生产订单在备料过程中发生变化", "conflict")
    existing_manifest = state.get("material_manifest")
    if existing_manifest and existing_manifest.get("manifest_hash") != material_manifest["manifest_hash"]:
        raise ContentApplicationError("MATERIAL_MANIFEST_CHANGED", "物料清单在备料过程中发生变化", "conflict")
    direction_blueprint = deepcopy((rule.get("source_metadata") or {}).get("composition_blueprint"))
    if catalog["industry_slug"] == "decoration" and not direction_blueprint:
        raise ContentApplicationError(
            "CONTENT_PLAN_CONFIGURATION_INVALID", "装修组合规则缺少层级与词组组合", "conflict"
        )
    if catalog["industry_slug"] == "decoration":
        lexicons = get_formula_lexicon_requirements(title["code"], body["code"])
        title["lexicon_codes"] = [item["code"] for item in lexicons["title"]]
        calling = get_decoration_body_calling(body["code"])
        if calling:
            calling["composition_blueprint"] = deepcopy(direction_blueprint)
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
                for index, text in enumerate(body.get("structure_schema") or [])
            ]
            calling["formula_name"] = body["name"]
            calling["reference_examples"] = body.get("reference_examples") or []
            body["body_calling"] = calling
            body["composition_blueprint"] = deepcopy(direction_blueprint)
            body["body_calling_source"] = get_decoration_body_calling_source(body["code"])
            body["structure_schema"] = [section["name"] for section in calling["sections"]]

    decision = {
        "schema_version": 1,
        "selection_mode": "deterministic",
        "status": "selected",
        "industry_slug": catalog["industry_slug"],
        "direction_code": direction,
        "group_id": str(rule.get("id") or rule.get("code")),
        "rule_version_id": catalog["rule_version_id"],
        "title_formula_code": title["code"],
        "body_formula_code": body["code"],
        "creation_method_codes": method_codes,
        "reference_asset_id": asset.id,
        "reference_source_hash": asset.source_hash,
        "slot_mapping": selected["slot_mapping"],
        "selection_trace": [
            f"按创作类型 {direction} 命中唯一组合规则 {rule.get('id') or rule.get('code')}",
            f"按规则选式策略锁定标题公式 {title['code']}，备料后不更换公式",
            f"锁定正文公式 {body['code']} 与手法 {','.join(method_codes)}",
            f"按槽位覆盖、检索相关度和资产 ID 稳定排序选择 {asset.id}",
        ],
    }
    reference_snapshot = {
        "id": asset.id,
        "article_id": asset.article_id,
        "kb_id": asset.kb_id,
        "file_id": asset.file_id,
        "locator": asset.source_json["locator"],
        "source_hash": asset.source_hash,
        "preparation_skill_hash": asset.preparation_skill_hash,
        "reference_card": card,
        "reference_blueprint": asset.prepared_json["reference_blueprint"],
        "slot_mapping": selected["slot_mapping"],
    }
    payload = {
        "schema_version": 2,
        "planner_version": _PLANNER_VERSION,
        "input_snapshot_hash": hashlib.sha256(
            json.dumps(
                {
                    "content_brief": state["content_brief"],
                    "evidence_bundle_hash": state["evidence_bundle"].get("bundle_hash"),
                    "facts": fact_index["facts"],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest(),
        "industry_slug": catalog["industry_slug"],
        "strategy_mode": catalog["strategy_mode"],
        "content_direction": direction,
        "creation_methods": method_codes,
        "creation_method_definitions": methods,
        "title_formula": title,
        "body_formula": body,
        "rule_version_id": catalog["rule_version_id"],
        "policy_hash": catalog["policy_hash"],
        "decision": decision,
        "reference_snapshot": reference_snapshot,
    }
    if direction_blueprint is not None:
        payload["direction_blueprint"] = direction_blueprint
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["snapshot_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
    snapshot = StrategySnapshotV2.model_validate(payload).model_dump(mode="json")
    source_id = f"{asset.kb_id}/{asset.file_id}/{asset.source_json['locator']}"
    viral_collection = {
        "evidence_items": [
            {
                "id": asset.id,
                "variable_codes": [],
                "value": "已准备并审核的单篇结构参考",
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
        ],
        "citations": [],
        "unresolved_questions": [],
    }
    input_variable_paths = list(dict.fromkeys(path for paths in selected["slot_mapping"].values() for path in paths))
    selection = {
        "selected_candidate_id": asset.id,
        "selection_reason": decision["selection_trace"][-1],
        "selection_basis": {
            "schema_version": 2,
            "input_variable_paths": input_variable_paths,
            "matched_dimensions": {slot_key: "matched" for slot_key in selected["slot_mapping"]},
            "structure_fillability": {
                "required_variable_kinds": list(selected["slot_mapping"]),
                "available_variable_paths": input_variable_paths,
                "unfilled_required_slots": [],
                "omitted_reference_slots": selected["omitted_required_slots"],
            },
            "candidate_comparison": [
                {
                    "source_id": item["id"],
                    "decision": "selected" if item["id"] == asset.id else "eligible",
                    "reason": "按槽位覆盖、检索相关度和资产 ID 的稳定顺序排序",
                }
                for item in ranked
            ],
            "slot_mapping": selected["slot_mapping"],
            "omitted_reference_slots": selected["omitted_required_slots"],
            "selection_trace": decision["selection_trace"],
        },
        "reference_blueprint": asset.prepared_json["reference_blueprint"],
        "unresolved_questions": [],
    }
    formula_snapshot = {
        "schema_version": 2,
        "combination_group_id": decision["group_id"],
        "selected_title_formula_code": title["code"],
        "selected_body_formula_code": body["code"],
        "eligible_title_formula_codes": list(rule.get("title_formula_candidate_codes") or []),
        "eligible_body_formula_codes": list(rule.get("body_formula_candidate_codes") or []),
        "rule_version_id": catalog["rule_version_id"],
        "selected_by": "deterministic",
        "decision": decision,
    }
    result = {
        "production_order": production_order,
        "material_manifest": material_manifest,
        "strategy_selection": decision,
        "match_decision_snapshot": {
            "selected_group_id": decision["group_id"],
            "eligible_title_formula_codes": formula_snapshot["eligible_title_formula_codes"],
            "eligible_body_formula_codes": formula_snapshot["eligible_body_formula_codes"],
            "selection_mode": "deterministic",
        },
        "strategy_snapshot": snapshot,
        "formula_selection_snapshot": formula_snapshot,
        "evidence_gap_analysis": {
            "has_missing": False,
            "missing_variable_codes": [],
            "missing_evidence_types": [],
            "target_formula_pair": {"title_formula_code": title["code"], "body_formula_code": body["code"]},
        },
        "viral_candidate_collection": viral_collection,
        "viral_reference_selection": selection,
    }
    await append_run_stream_event(
        state["run_id"],
        "content.plan.completed",
        {
            "task_id": state["task_id"],
            "content_type_code": direction,
            "rule_version_id": catalog["rule_version_id"],
            "title_formula_code": title["code"],
            "body_formula_code": body["code"],
            "reference_asset_id": asset.id,
            "mapped_slot_count": len(selected["slot_mapping"]),
            "plan_hash": snapshot["snapshot_hash"],
        },
        thread_id=state["task_id"],
    )
    return result
