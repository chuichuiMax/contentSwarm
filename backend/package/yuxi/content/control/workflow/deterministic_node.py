from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content.control.evidence import EvidenceApplicationService
from yuxi.content.control.strategy.recommend_v3 import StrategyPreviewActor
from yuxi.content.infrastructure.postgres.decision_snapshot_repository import PostgresDecisionSnapshotRepository
from yuxi.content.infrastructure.postgres.strategy_preview_repository import PostgresStrategyPreviewRepository
from yuxi.content.model.contracts import ContractDomainContext, StrategySnapshotV1, validate_content_node_result
from yuxi.content.model.evidence import (
    EvidenceBundleV1,
    EvidenceGovernanceError,
    EvidenceItemV1,
    freeze_evidence_bundle,
    next_evidence_bundle_version,
)
from yuxi.content.model.formulas.selector import (
    FormulaCandidateDefinition,
    FormulaCandidatePool,
    FormulaSelectionRequest,
    FormulaSelector,
)
from yuxi.content.model.rules.engine import CombinationMatcher, MatchRequest
from yuxi.content.rules import brief_variable_map
from yuxi.content.validation import ComplianceEngine, validate_numeric_evidence_coverage
from yuxi.content.validators import validate_content
from yuxi.content.v3.body_calling import SOURCE_METADATA as BODY_CALLING_SOURCE
from yuxi.content.v3.body_calling import get_decoration_body_calling
from yuxi.content.v3.formula_lexicons import get_formula_lexicon_requirements
from yuxi.services.run_queue_service import append_run_stream_event
from yuxi.storage.postgres.models_content import ContentFormula, ContentTask, CreationMethod, TitleFormula
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeFile

_SLOT_REQUIRED_VARIABLES = {
    "product_profile": {"product", "advantages", "pain_points"},
    "price": {"price", "budget", "cost", "discount", "fee"},
    "case_proof": {"number", "result", "scene", "location"},
    "brand": {"brand_name"},
}


def _display_business_value(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip() if value not in (None, "") else ""


def _brief_source_path(brief: dict[str, Any], key: str) -> str:
    for section_name in ("business_variables", "form_values"):
        section = brief.get(section_name)
        if isinstance(section, dict) and _display_business_value(section.get(key)):
            return f"{section_name}.{key}"
    return key


def _derive_scene_evidence(task_id: str, brief: dict[str, Any]) -> EvidenceItemV1 | None:
    variables = brief_variable_map(brief)
    if _display_business_value(variables.get("scene")):
        return None

    audience = _display_business_value(variables.get("audience"))
    project_key = "project_type" if _display_business_value(variables.get("project_type")) else "product"
    project = _display_business_value(variables.get(project_key))
    area = _display_business_value(variables.get("area"))
    pain_key = next(
        (key for key in ("owner_pain", "pain_points", "pain") if _display_business_value(variables.get(key))),
        "",
    )
    pain = _display_business_value(variables.get(pain_key))
    if not audience or not project or not pain:
        return None

    parts = [("目标人群", audience), ("项目", project)]
    source_fields = [_brief_source_path(brief, "audience"), _brief_source_path(brief, project_key)]
    if area:
        parts.append(("面积", area))
        source_fields.append(_brief_source_path(brief, "area"))
    parts.append(("业务痛点", pain))
    source_fields.append(_brief_source_path(brief, pain_key))
    value = "；".join(f"{label}：{content}" for label, content in parts)
    source_hash = hashlib.sha256(
        json.dumps([task_id, "scene", source_fields, value], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return EvidenceItemV1(
        id=f"ev_{source_hash[:16]}",
        variable_codes=("scene",),
        value=value,
        source_type="manual_input",
        source_id="derived_scene_from_business_brief",
        source_version="brief-v1",
        verified_status="user_confirmed",
        allowed_usage=("body", "visual"),
        source_hash=source_hash,
        metadata={"derived_from_fields": source_fields},
    )


def _available_variable_codes(state: dict[str, Any]) -> set[str]:
    available = {
        key
        for key, value in brief_variable_map(state.get("content_brief") or {}).items()
        if value not in (None, "", [], {})
    }
    for item in (state.get("evidence_bundle") or {}).get("items") or []:
        available.update(str(code) for code in item.get("variable_codes") or [] if code)
    return available


def _annotate_product_slot_requirements(
    *,
    slot_mappings: list[dict[str, Any]],
    material_requirements: dict[str, Any],
    strategy_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    requirement_by_id = {
        item["requirement_id"]: item
        for item in material_requirements.get("requirements") or []
        if item.get("requirement_id")
    }
    title_variables = set((strategy_snapshot.get("title_formula") or {}).get("variable_schema") or [])
    body_variables = set((strategy_snapshot.get("body_formula") or {}).get("required_variables") or [])
    body_variables.update(
        variable
        for method in strategy_snapshot.get("creation_method_definitions") or []
        for variable in method.get("variable_schema") or []
    )
    variables_by_usage = {"title": title_variables, "body": body_variables}
    has_case_result_mapping = {
        usage: any(
            mapping.get("slot") == "case_proof"
            and mapping.get("target_usage") == usage
            and "result" in set((requirement_by_id.get("case_proof") or {}).get("variable_codes") or [])
            for mapping in slot_mappings
        )
        for usage in variables_by_usage
    }

    annotated = []
    for mapping in slot_mappings:
        requirement = requirement_by_id.get(mapping.get("slot"))
        if requirement is None:
            required = bool(mapping.get("required", True))
        elif not requirement.get("required") or mapping.get("target_usage") == "style_reference":
            required = False
        else:
            target_usage = str(mapping.get("target_usage") or "")
            relevant_variables = variables_by_usage.get(target_usage, set())
            requirement_variables = set(requirement.get("variable_codes") or [])
            slot = str(mapping.get("slot") or "")
            required_variables = _SLOT_REQUIRED_VARIABLES.get(slot, requirement_variables)
            required = (
                bool(relevant_variables & requirement_variables & required_variables)
                if relevant_variables and requirement_variables
                else True
            )
            if (
                slot == "product_profile"
                and not required
                and "result" in relevant_variables & requirement_variables
                and not has_case_result_mapping.get(target_usage)
            ):
                required = True
        annotated.append({**mapping, "required": required})
    return annotated


def _title_evidence_requirements(slot_mappings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "slot": mapping["slot"],
            "required": bool(mapping.get("required")),
            "evidence_ids": list(mapping.get("evidence_ids") or []),
            "integration_instruction": str(mapping.get("integration_instruction") or "按槽位证据生成标题"),
        }
        for mapping in slot_mappings
        if mapping.get("target_usage") == "title"
    ]


class V3DeterministicNodeHandler:
    async def execute(
        self,
        *,
        db: AsyncSession,
        node: dict[str, Any],
        state: dict[str, Any],
        node_run_id: str,
    ) -> dict[str, Any]:
        handlers = {
            "compile_runtime_snapshot": self._compile_runtime_snapshot,
            "ingest_real_materials": self._ingest_real_materials,
            "normalize_evidence": self._normalize_evidence,
            "select_creation_strategy": self._select_creation_strategy,
            "lock_creation_strategy": self._lock_creation_strategy,
            "load_formula_lexicons": self._load_formula_lexicons,
            "merge_research_evidence": self._merge_research_evidence,
            "match_combination_group": self._match_combination_group,
            "resolve_formula_requirements": self._resolve_formula_requirements,
            "freeze_evidence_bundle": self._freeze_evidence_bundle,
            "prepare_formula_selection": self._prepare_formula_selection,
            "resolve_product_material_requirements": self._resolve_product_material_requirements,
            "freeze_product_evidence_bundle": self._freeze_product_evidence_bundle,
            "validate_title_candidates": self._validate_title_candidates,
            "adapt_to_channel": self._adapt_to_channel,
            "deterministic_validate": self._deterministic_validate,
            "package_for_distribution": self._package_for_distribution,
        }
        handler = handlers.get(node["id"])
        if handler is None:
            raise ValueError(f"未注册的 V3 固定节点: {node['id']}")
        return await handler(db=db, state=state, node_run_id=node_run_id)

    @staticmethod
    async def _select_creation_strategy(*, db: AsyncSession, state: dict[str, Any], node_run_id: str) -> dict[str, Any]:
        del node_run_id
        task = await db.get(ContentTask, state["task_id"])
        if task is None:
            raise ValueError("内容任务不存在")
        context = await PostgresStrategyPreviewRepository(db).load_context(
            task_id=task.id,
            actor=StrategyPreviewActor(
                uid=state["uid"],
                role="superadmin" if task.created_by != state["uid"] else "user",
                tenant_id=task.tenant_id,
            ),
            requested_content_direction_code=None,
        )
        if context is None:
            raise ValueError("无权访问内容任务")
        if not context.content_direction_code:
            raise ValueError("内容任务缺少内容方向")
        decision = CombinationMatcher().match(
            list(context.groups),
            MatchRequest(
                content_direction_code=context.content_direction_code,
                industry_slug=context.industry_slug,
                channel_code=context.channel_code,
                content_goal_code=context.content_goal_code,
                narrative_axis_code=context.narrative_axis_code,
                available_variable_codes=context.available_variable_codes,
                available_evidence_types=context.available_evidence_types,
            ),
        )
        if decision.status != "matched" or not decision.eligible_groups:
            raise ValueError("固定规则没有匹配到可用的内容策略")
        selected = decision.eligible_groups[0]
        group = next(item for item in context.groups if item.code == selected.group_code)
        title_code = selected.title_formula_candidate_codes[0]
        body_code = selected.body_formula_candidate_codes[0]
        method_codes = [item.method_code for item in group.method_members]
        evidence_ids = [
            str(item.get("id")) for item in (state.get("evidence_bundle") or {}).get("items") or [] if item.get("id")
        ]
        reason = f"固定规则按优先级与变量、证据覆盖度选择 {group.code}，并锁定公式 {title_code} + {body_code}"
        selection = {
            "selected_direction_code": context.content_direction_code,
            "selected_group_id": group.code,
            "creation_method_codes": method_codes,
            "title_formula_code": title_code,
            "body_formula_code": body_code,
            "reason": reason,
            "evidence_ids": evidence_ids,
        }
        return {
            "selected_angle": {
                "direction_code": context.content_direction_code,
                "reason": reason,
                "evidence_ids": evidence_ids,
                "selected_by": "deterministic",
            },
            "strategy_selection": selection,
        }

    @staticmethod
    async def _lock_creation_strategy(*, db: AsyncSession, state: dict[str, Any], node_run_id: str) -> dict[str, Any]:
        selection = state.get("strategy_selection") or {}
        direction = str(selection.get("selected_direction_code") or "")
        task = await db.get(ContentTask, state["task_id"])
        if task is None or not direction:
            raise ValueError("固定规则未选出内容方向")
        context = await PostgresStrategyPreviewRepository(db).load_context(
            task_id=task.id,
            actor=StrategyPreviewActor(
                uid=state["uid"],
                role="superadmin" if task.created_by != state["uid"] else "user",
                tenant_id=task.tenant_id,
            ),
            requested_content_direction_code=direction,
        )
        if context is None:
            raise ValueError("无权访问内容任务")
        match = CombinationMatcher().match(
            list(context.groups),
            MatchRequest(
                content_direction_code=direction,
                industry_slug=context.industry_slug,
                channel_code=context.channel_code,
                content_goal_code=context.content_goal_code,
                narrative_axis_code=context.narrative_axis_code,
                available_variable_codes=context.available_variable_codes,
                available_evidence_types=context.available_evidence_types,
            ),
        )
        selected_group_id = str(selection.get("selected_group_id") or "")
        match = match.with_selected_group(selected_group_id)
        group = next(item for item in context.groups if item.code == selected_group_id)
        method_codes = [item.method_code for item in group.method_members]
        if selection.get("creation_method_codes") != method_codes:
            raise ValueError("固定规则选择的创作手法与组合组不一致")
        title_code = str(selection.get("title_formula_code") or "")
        body_code = str(selection.get("body_formula_code") or "")
        if title_code not in group.title_formula_candidate_codes or body_code not in group.body_formula_candidate_codes:
            raise ValueError("固定规则选择了组合组外的标题或正文公式")

        title_formula = (
            await db.execute(
                select(TitleFormula).where(
                    TitleFormula.version_id == context.rule_version_id,
                    TitleFormula.code == title_code,
                    TitleFormula.enabled.is_(True),
                )
            )
        ).scalar_one_or_none()
        body_formula = (
            await db.execute(
                select(ContentFormula).where(
                    ContentFormula.version_id == context.rule_version_id,
                    ContentFormula.code == body_code,
                    ContentFormula.enabled.is_(True),
                )
            )
        ).scalar_one_or_none()
        methods = list(
            (
                await db.execute(
                    select(CreationMethod).where(
                        CreationMethod.version_id == context.rule_version_id,
                        CreationMethod.code.in_(method_codes),
                        CreationMethod.enabled.is_(True),
                    )
                )
            ).scalars()
        )
        if title_formula is None or body_formula is None or {item.code for item in methods} != set(method_codes):
            raise ValueError("固定规则选择的手法或公式不存在或已停用")

        snapshots = PostgresDecisionSnapshotRepository(db)
        match_snapshot = await snapshots.save_match_decision(
            task_id=task.id,
            content_run_id=state["run_id"],
            node_run_id=node_run_id,
            rule_version_id=context.rule_version_id,
            industry_pack_version_id=context.industry_pack_version_id,
            channel_profile_version_id=context.channel_profile_version_id,
            decision=match,
            selected_by="deterministic",
        )
        required_variables = set(title_formula.variable_schema or []) | set(body_formula.required_variables or [])
        required_variables.update(variable for method in methods for variable in (method.variable_schema or []))
        formula_decision = FormulaSelector().select(
            FormulaCandidatePool(
                combination_group_id=group.code,
                rule_version_id=context.rule_version_id,
                title_formula_codes=tuple(group.title_formula_candidate_codes),
                body_formula_codes=tuple(group.body_formula_candidate_codes),
            ),
            [
                FormulaCandidateDefinition(
                    code=code,
                    kind=kind,
                    rule_version_id=context.rule_version_id,
                )
                for kind, codes in (
                    ("title", group.title_formula_candidate_codes),
                    ("body", group.body_formula_candidate_codes),
                )
                for code in codes
            ],
            FormulaSelectionRequest(
                available_variable_codes=frozenset(required_variables),
                agent_title_ranking=(title_code,),
                agent_body_ranking=(body_code,),
            ),
        )
        if formula_decision.status != "selected":
            raise ValueError("固定规则选择的标题/正文公式对不可用")
        formula_snapshot = await snapshots.save_formula_selection(
            task_id=task.id,
            content_run_id=state["run_id"],
            node_run_id=node_run_id,
            match_snapshot_id=match_snapshot.id,
            rule_version_id=context.rule_version_id,
            evidence_bundle_hash=str((state.get("evidence_bundle") or {}).get("bundle_hash") or ""),
            decision=formula_decision,
            selected_by="deterministic",
            delegated_agent_run_id=(state.get("delegated_agent_runs") or {}).get("select_creation_strategy"),
        )
        method_by_code = {item.code: item for item in methods}
        body_calling = get_decoration_body_calling(body_formula.code) if context.industry_slug == "decoration" else None
        formula_lexicons = (
            get_formula_lexicon_requirements(title_formula.code, body_formula.code)
            if context.industry_slug == "decoration"
            else None
        )
        body_structure = (
            [section["name"] for section in body_calling["sections"]]
            if body_calling is not None
            else body_formula.structure_schema or []
        )
        strategy_payload = {
            "content_direction": direction,
            "selected_group_id": group.code,
            "creation_methods": method_codes,
            "creation_method_definitions": [
                {
                    "code": method_by_code[code].code,
                    "name": method_by_code[code].name,
                    "method_type": method_by_code[code].method_type,
                    "principle": method_by_code[code].principle,
                    "suitable_scenes": method_by_code[code].suitable_scenes or [],
                    "sentence_patterns": method_by_code[code].sentence_patterns or [],
                    "variable_schema": method_by_code[code].variable_schema or [],
                    "risk_rules": method_by_code[code].risk_rules or [],
                }
                for code in method_codes
            ],
            "title_formula": {
                "code": title_formula.code,
                "name": title_formula.name,
                "core_goal": title_formula.core_goal,
                "reference_examples": title_formula.reference_examples or [],
                "variable_schema": title_formula.variable_schema or [],
                "compatible_methods": title_formula.compatible_methods or [],
                "risk_rules": title_formula.risk_rules or [],
                "lexicon_codes": (
                    [item["code"] for item in formula_lexicons["title"]] if formula_lexicons is not None else []
                ),
            },
            "body_formula": {
                "code": body_formula.code,
                "name": body_formula.name,
                "structure_schema": body_structure,
                "reference_examples": (
                    body_calling["reference_examples"]
                    if body_calling is not None
                    else body_formula.reference_examples or []
                ),
                "required_variables": body_formula.required_variables or [],
                "output_schema": body_formula.output_schema or {},
                "compatible_methods": body_formula.compatible_methods or [],
                "risk_rules": body_formula.risk_rules or [],
                "body_calling": body_calling,
                "body_calling_source": BODY_CALLING_SOURCE if body_calling is not None else None,
            },
            "rule_version_id": context.rule_version_id,
            "match_snapshot_id": match_snapshot.id,
            "formula_snapshot_id": formula_snapshot.id,
        }
        canonical = json.dumps(strategy_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        strategy_payload["snapshot_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
        strategy_snapshot = StrategySnapshotV1.model_validate(strategy_payload).model_dump(mode="json")
        await append_run_stream_event(
            state["run_id"],
            "content.strategy.locked",
            {
                "task_id": state["task_id"],
                "node_id": "lock_creation_strategy",
                "strategy_snapshot": strategy_snapshot,
            },
            thread_id=state["task_id"],
        )
        missing = sorted(required_variables - _available_variable_codes(state))
        match_payload = match.to_dict()
        match_payload.update(
            {
                "id": match_snapshot.id,
                "selected_group_id": group.code,
                "eligible_title_formula_codes": list(group.title_formula_candidate_codes),
                "eligible_body_formula_codes": list(group.body_formula_candidate_codes),
            }
        )
        formula_payload = formula_decision.to_dict()
        formula_payload["id"] = formula_snapshot.id
        return {
            "match_decision_snapshot": match_payload,
            "formula_selection_snapshot": formula_payload,
            "strategy_snapshot": strategy_snapshot,
            "formula_candidate_pool": {
                "combination_group_id": group.code,
                "title_formula_codes": list(group.title_formula_candidate_codes),
                "body_formula_codes": list(group.body_formula_candidate_codes),
            },
            "evidence_gap_analysis": {
                "has_missing": bool(missing),
                "missing_variable_codes": missing,
                "missing_evidence_types": [],
                "target_formula_pair": {"title_formula_code": title_code, "body_formula_code": body_code},
            },
        }

    @staticmethod
    async def _load_formula_lexicons(*, db: AsyncSession, state: dict[str, Any], node_run_id: str) -> dict[str, Any]:
        del node_run_id
        strategy = state.get("strategy_snapshot") or {}
        title_formula_code = str((strategy.get("title_formula") or {}).get("code") or "")
        body_formula_code = str((strategy.get("body_formula") or {}).get("code") or "")
        industry_pack_id = str(
            state.get("industry_pack_version_id")
            or (state.get("runtime_config_snapshot") or {}).get("industry_pack_version_id")
            or (state.get("industry_pack") or {}).get("id")
            or ""
        )
        if industry_pack_id != "industry-pack-decoration-v3":
            return {
                "formula_lexicon_bundle": {
                    "required": False,
                    "title_formula_code": title_formula_code,
                    "body_formula_code": body_formula_code,
                    "title": [],
                    "body": [],
                }
            }

        requirements = get_formula_lexicon_requirements(title_formula_code, body_formula_code)
        loaded: dict[str, list[dict[str, Any]]] = {"title": [], "body": []}
        for scope in ("title", "body"):
            for requirement in requirements[scope]:
                rows = list(
                    (
                        await db.execute(
                            select(KnowledgeBase, KnowledgeFile, KnowledgeChunk)
                            .join(KnowledgeFile, KnowledgeFile.kb_id == KnowledgeBase.kb_id)
                            .join(KnowledgeChunk, KnowledgeChunk.file_id == KnowledgeFile.file_id)
                            .where(
                                KnowledgeBase.name == requirement["knowledge_base_name"],
                                KnowledgeFile.filename == requirement["filename"],
                                KnowledgeFile.status == "indexed",
                            )
                            .order_by(KnowledgeChunk.chunk_index)
                        )
                    ).all()
                )
                if not rows:
                    raise ValueError(
                        f"锁定公式 {title_formula_code}/{body_formula_code} 的必需词库不可用: "
                        f"{requirement['knowledge_base_name']}/{requirement['filename']}"
                    )
                knowledge_base, knowledge_file, _ = rows[0]
                loaded[scope].append(
                    {
                        **requirement,
                        "knowledge_base_id": knowledge_base.kb_id,
                        "file_id": knowledge_file.file_id,
                        "chunks": [row[2].content for row in rows if row[2].content.strip()],
                    }
                )

        payload = {
            "required": True,
            "title_formula_code": title_formula_code,
            "body_formula_code": body_formula_code,
            "title": loaded["title"],
            "body": loaded["body"],
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload["bundle_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        await append_run_stream_event(
            state["run_id"],
            "content.formula_lexicons.loaded",
            {
                "task_id": state["task_id"],
                "node_id": "load_formula_lexicons",
                "title_formula_code": title_formula_code,
                "body_formula_code": body_formula_code,
                "title_lexicons": [item["filename"] for item in loaded["title"]],
                "body_lexicons": [item["filename"] for item in loaded["body"]],
                "bundle_hash": payload["bundle_hash"],
            },
            thread_id=state["task_id"],
        )
        return {"formula_lexicon_bundle": payload}

    @staticmethod
    async def _compile_runtime_snapshot(*, db: AsyncSession, state: dict[str, Any], node_run_id: str) -> dict[str, Any]:
        del node_run_id
        from yuxi.repositories.content_repository import ContentRepository

        task = await db.get(ContentTask, state["task_id"])
        if task is None:
            raise ValueError("内容任务不存在")
        if not task.workflow_definition_hash:
            raise ValueError("V3 任务未锁定工作流定义 hash")
        repo = ContentRepository(db)
        template = await repo.get_template(task.industry_template_version_id)
        industry_slug = template.slug if template else None
        industry_pack = next(
            (item for item in await repo.list_industry_packs() if item["id"] == task.industry_pack_version_id),
            {},
        )
        channel_profile = next(
            (item for item in await repo.list_channel_profiles() if item["id"] == task.channel_profile_version_id),
            {},
        )
        policies = [
            item
            for item in await repo.list_compliance_policies()
            if item["scope_type"] == "platform"
            or (item["scope_type"] == "channel" and item["scope_id"] == channel_profile.get("code"))
            or (item["scope_type"] == "industry" and item["scope_id"] == industry_slug)
            or (item["scope_type"] == "enterprise" and item["tenant_id"] == task.tenant_id)
        ]
        runtime = {
            **(state.get("runtime_config_snapshot") or {}),
            "schema_version": 3,
            "workflow_version_id": task.workflow_version_id,
            "workflow_definition_hash": task.workflow_definition_hash,
            "rule_version_id": task.rule_version_id,
            "industry_pack_version_id": task.industry_pack_version_id,
            "persona_profile_version_id": task.persona_profile_version_id,
            "channel_profile_version_id": task.channel_profile_version_id,
            "compliance_policy_version_ids": [item["id"] for item in policies],
        }
        task.runtime_config_snapshot_json = runtime
        return {
            "schema_version": 3,
            "runtime_config_snapshot": runtime,
            "industry_pack": industry_pack,
            "channel_profile": channel_profile,
            "compliance_policies": policies,
            "state_version": int(state.get("state_version") or 0) + 1,
            "task_mode": task.mode,
        }

    @staticmethod
    async def _ingest_real_materials(*, db: AsyncSession, state: dict[str, Any], node_run_id: str) -> dict[str, Any]:
        del db, node_run_id
        usable = [
            item
            for item in state.get("media_evidence_items") or []
            if item.get("verified_status") != "rejected" and item.get("privacy_status") == "approved"
        ]
        return {"media_evidence_items": usable}

    async def _normalize_evidence(self, *, db: AsyncSession, state: dict[str, Any], node_run_id: str) -> dict[str, Any]:
        del node_run_id
        existing = state.get("evidence_bundle") or {}
        if existing.get("status") == "frozen" and existing.get("bundle_hash"):
            return {"evidence_bundle": existing}
        items: list[EvidenceItemV1] = []
        for key, value in brief_variable_map(state["content_brief"]).items():
            if value in (None, "", [], {}):
                continue
            source_hash = hashlib.sha256(
                json.dumps([state["task_id"], key, value], ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            items.append(
                EvidenceItemV1(
                    id=f"ev_{source_hash[:16]}",
                    variable_codes=(key,),
                    value=value,
                    source_type="manual_input",
                    source_id=f"field_{key}",
                    source_version="brief-v1",
                    verified_status="user_confirmed",
                    allowed_usage=("title", "body", "visual"),
                    source_hash=source_hash,
                )
            )
        derived_scene = _derive_scene_evidence(state["task_id"], state["content_brief"])
        if derived_scene is not None:
            items.append(derived_scene)
        for media in state.get("media_evidence_items") or []:
            value = media.get("extracted_text") or media.get("confirmed_facts") or ""
            if not value:
                continue
            items.append(
                EvidenceItemV1(
                    id=str(media["id"]),
                    variable_codes=tuple(
                        str(item.get("variable_code"))
                        for item in media.get("confirmed_facts") or []
                        if item.get("variable_code")
                    ),
                    value=value,
                    source_type="media",
                    source_id=str(media.get("attachment_id") or media["id"]),
                    source_version=str(media.get("parser_version") or media.get("source_hash") or "unknown"),
                    verified_status="confirmed",
                    allowed_usage=tuple(media.get("allowed_usage") or ["body", "visual"]),
                    source_hash=str(media.get("source_hash") or hashlib.sha256(str(value).encode()).hexdigest()),
                    metadata={"object_uri": media.get("object_uri")},
                )
            )
        bundle = freeze_evidence_bundle(task_id=state["task_id"], version=1, items=items)
        await EvidenceApplicationService(db).persist_frozen_bundle(
            bundle,
            run_id=state["run_id"],
            thread_id=state["task_id"],
            added_evidence_ids=tuple(item.id for item in items),
        )
        return {"evidence_bundle": bundle.model_dump(mode="json")}

    @staticmethod
    async def _match_combination_group(*, db: AsyncSession, state: dict[str, Any], node_run_id: str) -> dict[str, Any]:
        selected = state.get("selected_angle") or {}
        direction = selected.get("direction_code") or selected.get("content_direction_code")
        task = await db.get(ContentTask, state["task_id"])
        if task is None or not direction:
            raise ValueError("匹配组合组前必须锁定内容方向")
        context = await PostgresStrategyPreviewRepository(db).load_context(
            task_id=task.id,
            actor=StrategyPreviewActor(
                uid=state["uid"],
                role="superadmin" if task.created_by != state["uid"] else "user",
                tenant_id=task.tenant_id,
            ),
            requested_content_direction_code=direction,
        )
        if context is None:
            raise ValueError("无权访问内容任务")
        decision = CombinationMatcher().match(
            list(context.groups),
            MatchRequest(
                content_direction_code=context.content_direction_code,
                industry_slug=context.industry_slug,
                channel_code=context.channel_code,
                content_goal_code=context.content_goal_code,
                narrative_axis_code=context.narrative_axis_code,
                available_variable_codes=context.available_variable_codes,
                available_evidence_types=context.available_evidence_types,
            ),
        )
        if decision.status != "matched" or not decision.selected_group_code:
            raise ValueError("没有组合组通过固定硬约束")
        snapshot = await PostgresDecisionSnapshotRepository(db).save_match_decision(
            task_id=task.id,
            content_run_id=state["run_id"],
            node_run_id=node_run_id,
            rule_version_id=context.rule_version_id,
            industry_pack_version_id=context.industry_pack_version_id,
            channel_profile_version_id=context.channel_profile_version_id,
            decision=decision,
            selected_by="deterministic",
        )
        payload = decision.to_dict()
        payload["id"] = snapshot.id
        payload["selected_group_id"] = decision.selected_group_code
        selected_group = next(
            item for item in decision.eligible_groups if item.group_code == decision.selected_group_code
        )
        payload["eligible_title_formula_codes"] = list(selected_group.title_formula_candidate_codes)
        payload["eligible_body_formula_codes"] = list(selected_group.body_formula_candidate_codes)
        title_formulas = list(
            (
                await db.execute(
                    select(TitleFormula).where(
                        TitleFormula.version_id == context.rule_version_id,
                        TitleFormula.code.in_(selected_group.title_formula_candidate_codes),
                        TitleFormula.enabled.is_(True),
                    )
                )
            ).scalars()
        )
        body_formulas = list(
            (
                await db.execute(
                    select(ContentFormula).where(
                        ContentFormula.version_id == context.rule_version_id,
                        ContentFormula.code.in_(selected_group.body_formula_candidate_codes),
                        ContentFormula.enabled.is_(True),
                    )
                )
            ).scalars()
        )
        if len(title_formulas) != len(selected_group.title_formula_candidate_codes) or len(body_formulas) != len(
            selected_group.body_formula_candidate_codes
        ):
            raise ValueError("组合组引用了不存在或已停用的公式")
        available_variables = _available_variable_codes(state)
        title_missing = {
            formula.code: set(str(code) for code in formula.variable_schema or [] if code) - available_variables
            for formula in title_formulas
        }
        body_missing = {
            formula.code: set(str(code) for code in formula.required_variables or [] if code) - available_variables
            for formula in body_formulas
        }
        closest_pair = min(
            (
                (title_code, body_code, title_missing[title_code] | body_missing[body_code])
                for title_code in selected_group.title_formula_candidate_codes
                for body_code in selected_group.body_formula_candidate_codes
            ),
            key=lambda item: (len(item[2]), item[0], item[1]),
        )
        missing_variables = sorted(closest_pair[2])
        formula_candidate_pool = {
            "combination_group_id": decision.selected_group_code,
            "title_formula_codes": list(selected_group.title_formula_candidate_codes),
            "body_formula_codes": list(selected_group.body_formula_candidate_codes),
        }
        evidence_gap_analysis = {
            "has_missing": bool(missing_variables),
            "missing_variable_codes": missing_variables,
            "missing_evidence_types": [],
            "target_formula_pair": {
                "title_formula_code": closest_pair[0],
                "body_formula_code": closest_pair[1],
            },
        }
        await append_run_stream_event(
            state["run_id"],
            "content.rule.matched",
            {
                "task_id": task.id,
                "node_id": "match_combination_group",
                "selected_group_id": decision.selected_group_code,
                "eligible_group_ids": [item.group_code for item in decision.eligible_groups],
            },
            thread_id=task.id,
        )
        return {
            "match_decision_snapshot": payload,
            "formula_candidate_pool": formula_candidate_pool,
            "evidence_gap_analysis": evidence_gap_analysis,
        }

    @staticmethod
    async def _resolve_formula_requirements(
        *, db: AsyncSession, state: dict[str, Any], node_run_id: str
    ) -> dict[str, Any]:
        del db, node_run_id
        match = state.get("match_decision_snapshot") or {}
        return {
            "formula_candidate_pool": {
                "combination_group_id": match.get("selected_group_id"),
                "title_formula_codes": match.get("eligible_title_formula_codes") or [],
                "body_formula_codes": match.get("eligible_body_formula_codes") or [],
            }
        }

    async def _freeze_evidence_bundle(
        self, *, db: AsyncSession, state: dict[str, Any], node_run_id: str
    ) -> dict[str, Any]:
        del node_run_id
        current = EvidenceBundleV1.model_validate(state["evidence_bundle"])
        collection = state.get("evidence_collection") or {}
        additions = [EvidenceItemV1.model_validate(item) for item in collection.get("evidence_items") or []]
        current_by_id = {item.id: item for item in current.items}
        new_additions: list[EvidenceItemV1] = []
        for item in additions:
            existing = current_by_id.get(item.id)
            if existing is None:
                new_additions.append(item)
                continue
            existing_payload = existing.model_dump(mode="json", exclude={"created_at", "metadata"})
            addition_payload = item.model_dump(mode="json", exclude={"created_at", "metadata"})
            if existing_payload != addition_payload:
                raise EvidenceGovernanceError(
                    "evidence_id_conflict",
                    f"Evidence ID {item.id} 已存在但内容不一致",
                )
        additions = new_additions
        if not additions:
            return {"evidence_bundle": current.model_dump(mode="json")}
        bundle = next_evidence_bundle_version(
            current,
            additions=additions,
            citations=[*current.citations, *({"source_id": item} for item in collection.get("citations") or [])],
        )
        await EvidenceApplicationService(db).persist_frozen_bundle(
            bundle,
            run_id=state["run_id"],
            thread_id=state["task_id"],
            added_evidence_ids=tuple(item.id for item in additions),
        )
        return {"evidence_bundle": bundle.model_dump(mode="json")}

    @staticmethod
    async def _merge_research_evidence(*, db: AsyncSession, state: dict[str, Any], node_run_id: str) -> dict[str, Any]:
        del db, node_run_id
        collections = [
            state.get("business_rule_evidence_collection") or {},
            state.get("price_evidence_collection") or {},
            state.get("compliance_evidence_collection") or {},
        ]
        evidence_items = [item for collection in collections for item in collection.get("evidence_items") or []]
        excluded_high_risk = [
            item
            for item in evidence_items
            if item.get("risk_level") == "high_risk" and item.get("verified_status") != "user_confirmed"
        ]
        evidence_items = [item for item in evidence_items if item not in excluded_high_risk]
        selection = state.get("viral_reference_selection") or {}
        candidate_id = selection.get("selected_candidate_id")
        candidates = (state.get("viral_candidate_collection") or {}).get("evidence_items") or []
        if candidate_id:
            candidate = next((item for item in candidates if item.get("id") == candidate_id), None)
            if candidate is None:
                raise ValueError("爆款选择结果不属于当前候选集合")
            evidence_items.append(
                {
                    **candidate,
                    "value": "已选爆款的抽象结构参考",
                    "metadata": {
                        **(candidate.get("metadata") or {}),
                        "selected_reference": True,
                        "selection_reason": selection.get("selection_reason"),
                        "selection_basis": selection.get("selection_basis") or {},
                        "reference_blueprint": selection.get("reference_blueprint") or {},
                    },
                }
            )

        citations = list(
            dict.fromkeys(
                str(citation)
                for collection in collections
                for citation in collection.get("citations") or []
                if citation
            )
        )
        excluded_source_ids = {str(item.get("source_id")) for item in excluded_high_risk if item.get("source_id")}
        citations = [citation for citation in citations if citation not in excluded_source_ids]
        if candidate_id:
            selected_candidate = next(item for item in candidates if item.get("id") == candidate_id)
            if selected_candidate.get("source_id"):
                citations.append(str(selected_candidate["source_id"]))
                citations = list(dict.fromkeys(citations))
        unresolved_questions = [
            str(question)
            for collection in [*collections, selection]
            for question in collection.get("unresolved_questions") or []
            if question
        ]
        unresolved_questions.extend(
            f"外部高风险资料“{item.get('value') or item.get('id')}”未经用户确认，本次首稿已自动排除"
            for item in excluded_high_risk
        )
        task = state.get("runtime_config_snapshot") or {}
        formula = state.get("formula_selection_snapshot") or {}
        result = validate_content_node_result(
            "EvidenceCollectionResultV1",
            {
                "evidence_items": evidence_items,
                "citations": citations,
                "unresolved_questions": unresolved_questions,
            },
            ContractDomainContext.from_governance(
                match_decision_snapshot=state.get("match_decision_snapshot") or {},
                formula_selection_snapshot=formula,
                evidence_bundle=state.get("evidence_bundle") or {},
                locked_versions={
                    "industry_pack_version_id": str(task.get("industry_pack_version_id") or ""),
                    "channel_profile_version_id": str(task.get("channel_profile_version_id") or ""),
                    "persona_profile_version_id": task.get("persona_profile_version_id"),
                    "rule_version_id": str(task.get("rule_version_id") or state.get("rule_version_id") or ""),
                    "title_formula_code": formula.get("selected_title_formula_code"),
                    "body_formula_code": formula.get("selected_body_formula_code"),
                    "artifact_version_id": None,
                },
                locked_values={"creation_mode": task.get("creation_mode", "original")},
                strategy_snapshot=state.get("strategy_snapshot") or {},
                viral_candidate_collection=state.get("viral_candidate_collection") or {},
            ),
        )
        return {"evidence_collection": result.model_dump(mode="json")}

    @staticmethod
    async def _prepare_formula_selection(
        *, db: AsyncSession, state: dict[str, Any], node_run_id: str
    ) -> dict[str, Any]:
        del node_run_id
        pool = dict(state.get("formula_candidate_pool") or {})
        title_codes = tuple(pool.get("title_formula_codes") or ())
        body_codes = tuple(pool.get("body_formula_codes") or ())
        if not title_codes or not body_codes:
            raise ValueError("公式候选池不能为空")
        title_formulas = list(
            (
                await db.execute(
                    select(TitleFormula).where(
                        TitleFormula.version_id == state["rule_version_id"],
                        TitleFormula.code.in_(title_codes),
                        TitleFormula.enabled.is_(True),
                    )
                )
            ).scalars()
        )
        body_formulas = list(
            (
                await db.execute(
                    select(ContentFormula).where(
                        ContentFormula.version_id == state["rule_version_id"],
                        ContentFormula.code.in_(body_codes),
                        ContentFormula.enabled.is_(True),
                    )
                )
            ).scalars()
        )
        available_variables = _available_variable_codes(state)
        valid_title_codes = [
            code
            for code in title_codes
            if any(
                formula.code == code and set(formula.variable_schema or []).issubset(available_variables)
                for formula in title_formulas
            )
        ]
        valid_body_codes = [
            code
            for code in body_codes
            if any(
                formula.code == code and set(formula.required_variables or []).issubset(available_variables)
                for formula in body_formulas
            )
        ]
        valid_pairs = [
            {"title_formula_code": title_code, "body_formula_code": body_code}
            for title_code in valid_title_codes
            for body_code in valid_body_codes
        ]
        if not valid_pairs:
            unresolved = (state.get("evidence_collection") or {}).get("unresolved_questions") or []
            details = f"；未解决问题：{'、'.join(unresolved)}" if unresolved else ""
            raise ValueError(f"补充证据后仍没有有效标题/正文公式对{details}")
        return {
            "formula_candidate_pool": {
                **pool,
                "title_formula_codes": valid_title_codes,
                "body_formula_codes": valid_body_codes,
                "valid_formula_pairs": valid_pairs,
                "valid_formula_pair_count": len(valid_pairs),
            }
        }

    @staticmethod
    async def _resolve_product_material_requirements(
        *, db: AsyncSession, state: dict[str, Any], node_run_id: str
    ) -> dict[str, Any]:
        del db, node_run_id
        strategy = state.get("strategy_snapshot") or {}
        snapshot_hash = str(strategy.get("snapshot_hash") or "")
        if not snapshot_hash:
            raise ValueError("解析产品资料需求前必须锁定 StrategySnapshot")

        title_variables = list((strategy.get("title_formula") or {}).get("variable_schema") or [])
        body_variables = list((strategy.get("body_formula") or {}).get("required_variables") or [])
        method_variables = [
            variable
            for method in strategy.get("creation_method_definitions") or []
            for variable in method.get("variable_schema") or []
        ]
        variable_codes = list(dict.fromkeys([*title_variables, *body_variables, *method_variables]))
        variable_set = set(variable_codes)
        body_formula_code = str((strategy.get("body_formula") or {}).get("code") or "")

        requirements = [
            {
                "requirement_id": "product_profile",
                "material_type": "product_profile",
                "variable_codes": sorted(variable_set & {"product", "advantages", "result", "pain_points"}),
                "target_usages": ["title", "body"],
                "required": bool(variable_set & {"product", "advantages", "result"}),
                "query_hint": "检索当前公司正式产品或服务介绍、适用人群、核心卖点、解决的问题和使用边界",
                "risk_level": "normal",
            },
            {
                "requirement_id": "price",
                "material_type": "price",
                "variable_codes": sorted(variable_set & {"price", "budget", "cost", "discount", "fee"}),
                "target_usages": ["title", "body"],
                "required": bool(variable_set & {"price", "budget", "cost", "discount", "fee"}),
                "query_hint": "检索仍在有效期内的正式价格、报价范围、费用口径、适用区域与生效日期",
                "risk_level": "high_risk",
            },
            {
                "requirement_id": "case_proof",
                "material_type": "case_proof",
                "variable_codes": sorted(variable_set & {"number", "result", "scene", "location"}),
                "target_usages": ["title", "body"],
                "required": body_formula_code == "C02" or bool(variable_set & {"number", "result"}),
                "query_hint": "检索可公开使用的真实案例、结果数字、使用场景、地域与客户问题，不得拼接不同案例",
                "risk_level": "sensitive",
            },
            {
                "requirement_id": "brand",
                "material_type": "brand",
                "variable_codes": sorted(variable_set & {"brand_name", "audience"}),
                "target_usages": ["title", "body"],
                "required": body_formula_code == "C04" or "brand_name" in variable_set,
                "query_hint": "检索正式品牌称谓、品牌定位、服务对象、价值主张、禁用词和承诺边界",
                "risk_level": "normal",
            },
            {
                "requirement_id": "viral_example",
                "material_type": "viral_example",
                "variable_codes": [],
                "target_usages": ["style_reference"],
                "required": False,
                "query_hint": "检索同方向爆款样例，仅提取标题结构、叙事节奏和表达模式，禁止复制事实、数字和原句",
                "risk_level": "normal",
            },
        ]
        if not any(item["required"] for item in requirements):
            requirements[0]["required"] = True
        return {
            "product_material_requirements": {
                "strategy_snapshot_hash": snapshot_hash,
                "required_variable_codes": variable_codes,
                "requirements": requirements,
            }
        }

    async def _freeze_product_evidence_bundle(
        self, *, db: AsyncSession, state: dict[str, Any], node_run_id: str
    ) -> dict[str, Any]:
        del node_run_id
        current = EvidenceBundleV1.model_validate(state["evidence_bundle"])
        requirements = state.get("product_material_requirements") or {}
        collection = state.get("product_evidence_collection") or {}
        additions = [EvidenceItemV1.model_validate(item) for item in collection.get("evidence_items") or []]
        current_by_id = {item.id: item for item in current.items}
        new_additions: list[EvidenceItemV1] = []
        for item in additions:
            existing = current_by_id.get(item.id)
            if existing is None:
                new_additions.append(item)
            # 兼容已通过旧版契约的节点结果：同 ID 只能引用冻结证据，Agent 重复提交的副本一律不参与合并。

        evidence_by_id = {**current_by_id, **{item.id: item for item in new_additions}}
        requirement_by_id = {
            item["requirement_id"]: item
            for item in requirements.get("requirements") or []
            if item.get("requirement_id")
        }
        slot_mappings = list(collection.get("slot_mappings") or [])
        mapped_required = {item.get("slot") for item in slot_mappings if item.get("evidence_ids")}
        missing_required = sorted(
            requirement_id
            for requirement_id, requirement in requirement_by_id.items()
            if requirement.get("required") and requirement_id not in mapped_required
        )
        if missing_required:
            raise EvidenceGovernanceError(
                "required_product_evidence_missing",
                f"锁定公式所需产品资料尚未补齐: {', '.join(missing_required)}",
            )

        for mapping in slot_mappings:
            requirement = requirement_by_id.get(mapping.get("slot"))
            if requirement is None:
                raise EvidenceGovernanceError("product_slot_unknown", f"未知产品资料槽位: {mapping.get('slot')}")
            target_usage = str(mapping.get("target_usage") or "")
            if target_usage not in requirement.get("target_usages", []):
                raise EvidenceGovernanceError(
                    "product_slot_usage_invalid",
                    f"槽位 {mapping['slot']} 不允许用于 {target_usage}",
                )
            for evidence_id in mapping.get("evidence_ids") or []:
                evidence = evidence_by_id.get(evidence_id)
                if evidence is None or target_usage not in evidence.allowed_usage:
                    raise EvidenceGovernanceError(
                        "product_evidence_usage_invalid",
                        f"Evidence {evidence_id} 不允许用于槽位 {mapping['slot']} 的 {target_usage}",
                    )

        if new_additions:
            bundle = next_evidence_bundle_version(
                current,
                additions=new_additions,
                citations=[
                    *current.citations,
                    *({"source_id": item} for item in collection.get("citations") or []),
                ],
            )
            await EvidenceApplicationService(db).persist_frozen_bundle(
                bundle,
                run_id=state["run_id"],
                thread_id=state["task_id"],
                added_evidence_ids=tuple(item.id for item in new_additions),
            )
        else:
            bundle = current

        annotated_slot_mappings = _annotate_product_slot_requirements(
            slot_mappings=slot_mappings,
            material_requirements=requirements,
            strategy_snapshot=state.get("strategy_snapshot") or {},
        )
        pack_payload = {
            "strategy_snapshot_hash": requirements.get("strategy_snapshot_hash"),
            "evidence_bundle_id": bundle.id,
            "evidence_bundle_version": bundle.version,
            "evidence_bundle_hash": bundle.bundle_hash,
            "slot_mappings": annotated_slot_mappings,
            "unresolved_questions": collection.get("unresolved_questions") or [],
        }
        canonical = json.dumps(pack_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        pack_payload["pack_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return {
            "evidence_bundle": bundle.model_dump(mode="json"),
            "product_evidence_pack": pack_payload,
            "title_evidence_requirements": _title_evidence_requirements(annotated_slot_mappings),
        }

    @staticmethod
    async def _validate_title_candidates(
        *, db: AsyncSession, state: dict[str, Any], node_run_id: str
    ) -> dict[str, Any]:
        del db, node_run_id
        candidates = state.get("title_candidates") or []
        locked = (state.get("formula_selection_snapshot") or {}).get("selected_title_formula_code")
        if len(candidates) < 2 or any(item.get("formula_code") != locked for item in candidates):
            raise ValueError("标题候选必须全部使用同一锁定标题公式")
        product_pack = dict(state.get("product_evidence_pack") or {})
        annotated_slot_mappings = _annotate_product_slot_requirements(
            slot_mappings=list(product_pack.get("slot_mappings") or []),
            material_requirements=state.get("product_material_requirements") or {},
            strategy_snapshot=state.get("strategy_snapshot") or {},
        )
        product_pack["slot_mappings"] = annotated_slot_mappings
        if product_pack.get("pack_hash"):
            canonical = json.dumps(
                {key: value for key, value in product_pack.items() if key != "pack_hash"},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            product_pack["pack_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        report = []
        title_mappings = [
            item for item in annotated_slot_mappings if item.get("target_usage") == "title" and item.get("required")
        ]
        for item in candidates:
            text = str(item.get("text") or "")
            numeric = validate_numeric_evidence_coverage(text, state["evidence_bundle"])
            length_ok = 1 <= len(text) <= 60
            cited = set(item.get("evidence_ids") or [])
            missing_product_mappings = [
                mapping for mapping in title_mappings if not cited.intersection(mapping.get("evidence_ids") or [])
            ]
            checks = list(numeric["checks"])
            if not length_ok:
                checks.append(
                    {
                        "code": "TITLE_TOO_SHORT" if not text else "TITLE_TOO_LONG",
                        "level": "error",
                        "location": "title",
                        "message": "标题不能为空" if not text else "标题超过 60 个字符",
                        "evidence_ids": [],
                        "suggestion": "重新生成符合 1～60 个字符限制的标题",
                    }
                )
            for mapping in missing_product_mappings:
                evidence_ids = list(mapping.get("evidence_ids") or [])
                checks.append(
                    {
                        "code": "TITLE_PRODUCT_EVIDENCE_NOT_USED",
                        "level": "error",
                        "location": "title",
                        "message": f"标题未引用必填产品资料槽位：{mapping['slot']}",
                        "evidence_ids": evidence_ids,
                        "suggestion": (
                            f"按要求“{mapping.get('integration_instruction') or '植入对应资料'}”，"
                            f"并在候选 evidence_ids 中加入 {', '.join(evidence_ids)}"
                        ),
                    }
                )
            status = (
                "passed" if numeric["status"] == "passed" and length_ok and not missing_product_mappings else "blocked"
            )
            report.append(
                {
                    "id": item["id"],
                    "text": text,
                    "status": status,
                    "missing_required_slots": [mapping["slot"] for mapping in missing_product_mappings],
                    "checks": checks,
                }
            )
        status_by_id = {item["id"]: item["status"] for item in report}
        return {
            "title_candidates": [{**item, "selectable": status_by_id[item["id"]] != "blocked"} for item in candidates],
            "title_validation_report": {
                "status": "blocked" if all(item["status"] == "blocked" for item in report) else "passed",
                "items": report,
            },
            "product_evidence_pack": product_pack,
            "title_evidence_requirements": _title_evidence_requirements(annotated_slot_mappings),
        }

    @staticmethod
    async def _adapt_to_channel(*, db: AsyncSession, state: dict[str, Any], node_run_id: str) -> dict[str, Any]:
        del db, node_run_id
        draft = dict(state.get("content_draft") or {})
        title = dict(state.get("selected_title") or {})
        result = ComplianceEngine().validate_and_adapt(
            title=title.get("text", ""),
            body=draft.get("body", ""),
            topics=draft.get("topics") or [],
            channel_profile=state.get("channel_profile") or {},
            policies=state.get("compliance_policies") or [],
        )
        title["text"] = result["title"]
        draft["body"] = result["body"]
        draft["topics"] = result["topics"]
        return {"selected_title": title, "content_draft": draft, "channel_result": result}

    @staticmethod
    async def _deterministic_validate(*, db: AsyncSession, state: dict[str, Any], node_run_id: str) -> dict[str, Any]:
        del db, node_run_id
        draft = state.get("content_draft") or {}
        body = draft.get("body", "")
        report = validate_content(
            title=(state.get("selected_title") or {}).get("text", ""),
            body=body,
            topics=draft.get("topics") or [],
            brief=state["content_brief"],
            evidence_bundle=state["evidence_bundle"],
            strategy={
                "methods": (state.get("strategy_snapshot") or {}).get("creation_methods"),
                "title_formula_code": ((state.get("strategy_snapshot") or {}).get("title_formula") or {}).get("code"),
                "body_formula_code": ((state.get("strategy_snapshot") or {}).get("body_formula") or {}).get("code"),
            },
        )
        if not 200 <= len(body) <= 650:
            report["checks"].append(
                {
                    "code": "BODY_LENGTH_OUT_OF_RANGE",
                    "level": "error",
                    "location": "body",
                    "message": "正文目标长度必须为 200～650 字",
                    "evidence_ids": [],
                }
            )
            report["status"] = "blocked"
        mechanical_markers = (
            "旧况很典型",
            "关键数据先摊开",
            "先说背景",
            "再看过程",
            "最后看结果",
            "下面来说",
            "接下来看看",
        )
        matched_mechanical_markers = [marker for marker in mechanical_markers if marker in body]
        if matched_mechanical_markers:
            report["checks"].append(
                {
                    "code": "MECHANICAL_META_EXPRESSION",
                    "level": "error",
                    "location": "body",
                    "message": "正文包含暴露写作步骤的报幕式元话术",
                    "evidence_ids": [],
                    "matched_terms": matched_mechanical_markers,
                }
            )
            report["status"] = "blocked"
        used_body_evidence = {
            evidence_id
            for paragraph in draft.get("paragraph_evidence") or []
            for evidence_id in paragraph.get("evidence_ids") or []
        }
        knowledge_body_evidence = {
            str(item["id"])
            for item in (state.get("evidence_bundle") or {}).get("items") or []
            if item.get("source_type") == "knowledge_base"
            and "body" in (item.get("allowed_usage") or [])
            and item.get("metadata", {}).get("material_type")
            not in {"viral_example", "platform_rule", "compliance_rule", "forbidden_terms"}
        }
        if knowledge_body_evidence and not used_body_evidence.intersection(knowledge_body_evidence):
            report["checks"].append(
                {
                    "code": "KNOWLEDGE_EVIDENCE_UNUSED",
                    "level": "error",
                    "location": "body",
                    "message": "已取得可用于正文的业务知识证据，但正文未引用",
                    "evidence_ids": sorted(knowledge_body_evidence),
                }
            )
            report["status"] = "blocked"
        used_price_evidence = [
            item
            for item in (state.get("evidence_bundle") or {}).get("items") or []
            if str(item.get("id") or "") in used_body_evidence
            and item.get("source_type") == "knowledge_base"
            and item.get("metadata", {}).get("material_type") == "price"
        ]
        concrete_price_values: set[str] = set()
        for evidence in used_price_evidence:
            value = evidence.get("value")
            if isinstance(value, dict):
                price_items = value.get("items") or []
                if isinstance(price_items, list):
                    concrete_price_values.update(
                        str(price_item["price"])
                        for price_item in price_items
                        if isinstance(price_item, dict) and price_item.get("price") is not None
                    )
            elif isinstance(value, str):
                concrete_price_values.update(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", value))
        if (
            used_price_evidence
            and concrete_price_values
            and not any(price_value in body for price_value in concrete_price_values)
        ):
            report["checks"].append(
                {
                    "code": "KNOWLEDGE_PRICE_DETAIL_UNUSED",
                    "level": "error",
                    "location": "body",
                    "message": "正文引用了知识库价格证据，但没有写出其中任何具体项目价格",
                    "evidence_ids": sorted(str(item["id"]) for item in used_price_evidence),
                }
            )
            report["status"] = "blocked"
        missing_product_slots = [
            mapping["slot"]
            for mapping in (state.get("product_evidence_pack") or {}).get("slot_mappings") or []
            if mapping.get("target_usage") == "body"
            and mapping.get("required", True)
            and not used_body_evidence.intersection(mapping.get("evidence_ids") or [])
        ]
        if missing_product_slots:
            report["checks"].append(
                {
                    "code": "BODY_PRODUCT_EVIDENCE_NOT_USED",
                    "level": "error",
                    "location": "body",
                    "message": f"正文未植入已映射的产品资料: {', '.join(missing_product_slots)}",
                    "evidence_ids": [],
                }
            )
            report["status"] = "blocked"
        return {"validation_report": report}

    @staticmethod
    async def _package_for_distribution(*, db: AsyncSession, state: dict[str, Any], node_run_id: str) -> dict[str, Any]:
        del db, node_run_id
        if not state.get("artifact_id"):
            raise ValueError("发布打包前必须保存 Artifact")
        version = state.get("artifact_version") or {}
        if not version.get("id") or not version.get("cover_asset_id"):
            raise ValueError("发布打包前必须锁定同时包含文案与封面的 ArtifactVersion")
        return {
            "distribution_package": {
                "artifact_id": state["artifact_id"],
                "artifact_version_id": version["id"],
                "cover_asset_id": version["cover_asset_id"],
                "selected_cover": state.get("selected_cover"),
                "evidence_bundle_hash": (state.get("evidence_bundle") or {}).get("bundle_hash"),
                "formula_selection_snapshot_id": (state.get("formula_selection_snapshot") or {}).get("id"),
                "runtime_config_snapshot": state.get("runtime_config_snapshot") or {},
            }
        }


__all__ = ["V3DeterministicNodeHandler"]
