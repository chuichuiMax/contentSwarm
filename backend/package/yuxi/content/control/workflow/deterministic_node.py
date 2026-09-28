from __future__ import annotations

import asyncio
import hashlib
import json
import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content.model.raw_reference import is_raw_reference, topic_validation_checks
from yuxi.content.control.errors import ContentApplicationError
from yuxi.content.control.evidence import EvidenceApplicationService
from yuxi.content.control.strategy.recommend_v3 import StrategyPreviewActor
from yuxi.content.industry_matrix import resolve_industry_formula
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
from yuxi.content.model.locked_blocks import (
    QUOTE_RENDER_POLICIES,
    compose_after_opening_paragraph,
    extract_locked_quote_block,
    quote_body_limits,
)
from yuxi.content.model.materials import (
    MaterialRequirementManifestV1,
    ProductionOrderV1,
    build_formula_lexicon_constraints,
    freeze_production_pack,
    standardize_evidence_materials,
    validate_material_gate,
)
from yuxi.content.model.rules.engine import CombinationMatcher, MatchRequest
from yuxi.content.rules import brief_variable_map, canonical_brief_facts
from yuxi.content.v3.modular_rules import SINGLE_BLUEPRINT_WORKFLOW_IDS
from yuxi.content.v3.body_calling import get_decoration_body_calling, get_decoration_body_calling_source
from yuxi.content.v3.formula_lexicons import get_formula_lexicon_requirements
from yuxi.content.v3.title_formula_slots import (
    enrich_decoration_title_formula,
    required_title_lexicon_codes,
    title_formula_slot_schema,
)
from yuxi.content.validation import ComplianceEngine, validate_numeric_evidence_coverage
from yuxi.content.validators import validate_content, validate_modular_content
from yuxi.services.run_queue_service import append_run_stream_event
from yuxi.storage.postgres.models_content import ContentFormula, ContentTask, CreationMethod, TitleFormula
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeFile

_SLOT_REQUIRED_VARIABLES = {
    "product_profile": {"product", "advantages", "pain_points"},
    "price": {"price", "budget", "cost", "discount", "fee"},
    "case_proof": {"number", "result", "scene", "location"},
    "brand": {"brand_name"},
}
_TRUSTED_QUOTE_SNAPSHOT_KEY = "trusted_external_material_snapshot"
_TRUSTED_QUOTE_VARIABLES = {"title_price", "title_price_label", "quote_block", "quote_type"}


def _trusted_quote_evidence_items(snapshot: dict[str, Any], *, content_type_code: str) -> list[EvidenceItemV1]:
    """把服务端当家快照重新验签并转换为高风险已确认 Evidence。"""

    if (
        snapshot.get("schema_version") != 1
        or snapshot.get("source") != "dangjia"
        or snapshot.get("content_type_code") != content_type_code
    ):
        raise ContentApplicationError(
            "TRUSTED_QUOTE_SNAPSHOT_INVALID",
            "当家报价可信快照的版本、来源或创作类型不匹配",
            "invalid",
        )
    serial_no = str(snapshot.get("serial_no") or "").strip()
    quote_type = str(snapshot.get("quote_type") or "").strip()
    title_price = snapshot.get("title_price") or {}
    quote_block = snapshot.get("quote_block") or {}
    label = str(title_price.get("label") or "").strip()
    display_text = str(title_price.get("display_text") or "").strip()
    original_content = quote_block.get("original_content")
    expected_content_hash = quote_block.get("content_hash")
    if (
        not serial_no
        or quote_type not in {"standard_unit_price", "project_quote"}
        or not label
        or not display_text
        or not isinstance(original_content, str)
        or not original_content
        or quote_block.get("render_policy") not in QUOTE_RENDER_POLICIES
        or quote_block.get("insertion_policy") != "after-opening-paragraph-v1"
    ):
        raise ContentApplicationError(
            "TRUSTED_QUOTE_SNAPSHOT_INVALID",
            "当家报价可信快照缺少必需字段或使用了未发布策略",
            "invalid",
        )
    content_hash = hashlib.sha256(original_content.encode("utf-8")).hexdigest()
    if content_hash != expected_content_hash:
        raise ContentApplicationError(
            "LOCKED_QUOTE_BLOCK_INTEGRITY_FAILED",
            "当家报价原文与冻结 Hash 不一致",
            "invalid",
        )

    def item(
        variable_code: str,
        value: Any,
        *,
        allowed_usage: tuple[str, ...],
        source_hash: str,
        metadata: dict[str, Any],
    ) -> EvidenceItemV1:
        source_id = f"dangjia:{serial_no}:{variable_code.replace('_', '-')}"
        evidence_key = f"{source_id}:{source_hash}"
        # 新排版对应独立 Evidence，避免同一报价原文与历史冻结值发生 ID 冲突。
        if variable_code == "quote_block" and value["render_policy"] != "semicolon-lines-v1":
            evidence_key += f":{value['render_policy']}"
        evidence_hash = hashlib.sha256(evidence_key.encode()).hexdigest()
        return EvidenceItemV1(
            id=f"ev_{evidence_hash[:24]}",
            variable_codes=(variable_code,),
            value=value,
            source_type="business_record",
            source_id=source_id,
            source_version=serial_no,
            verified_status="user_confirmed",
            allowed_usage=allowed_usage,
            risk_level="high_risk",
            source_hash=source_hash,
            metadata=metadata,
        )

    title_price_hash = hashlib.sha256(
        json.dumps([label, display_text], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    label_hash = hashlib.sha256(label.encode("utf-8")).hexdigest()
    quote_type_hash = hashlib.sha256(quote_type.encode("utf-8")).hexdigest()
    return [
        item(
            "title_price",
            display_text,
            allowed_usage=("title",),
            source_hash=title_price_hash,
            metadata={"label": label, "source_path": "requirementType.prices[0].titlePrice.displayText"},
        ),
        item(
            "title_price_label",
            label,
            allowed_usage=("title", "body"),
            source_hash=label_hash,
            metadata={"source_path": "requirementType.prices[0].titlePrice.label"},
        ),
        item(
            "quote_type",
            quote_type,
            allowed_usage=("body",),
            source_hash=quote_type_hash,
            metadata={"source_path": "requirementType.prices[0].format"},
        ),
        item(
            "quote_block",
            {
                "original_content": original_content,
                "content_hash": content_hash,
                "render_policy": quote_block["render_policy"],
                "insertion_policy": "after-opening-paragraph-v1",
            },
            allowed_usage=("body",),
            source_hash=content_hash,
            metadata={
                "quote_format": snapshot.get("quote_format"),
                "source_path": "requirementType.prices[0].content",
                "immutable": True,
            },
        ),
    ]


def _display_business_value(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip() if value not in (None, "") else ""


def _required_title_fact_options(
    brief: dict[str, Any],
    strategy_snapshot: dict[str, Any],
    production_pack: dict[str, Any] | None = None,
) -> dict[str, tuple[str, ...]]:
    """返回可逐字校验的标题槽位；同一槽位内的变量和词库按 one-of 解释。"""

    policy = ((production_pack or {}).get("content_rule_bundle") or {}).get("single_blueprint") or {}
    if policy.get("writing_mode") in {"direct_reference", "raw_reference_text"}:
        return {}
    variables = brief_variable_map(brief)
    for material in (production_pack or {}).get("materials") or []:
        value = (material.get("payload") or {}).get("value")
        for code in material.get("variable_codes") or []:
            if value not in (None, "", [], {}):
                variables.setdefault(str(code), value)

    formula = enrich_decoration_title_formula(strategy_snapshot.get("title_formula") or {})
    location = _display_business_value(variables.get("location"))

    def raw_values(value: Any) -> list[str]:
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        rendered = _display_business_value(value)
        return [rendered] if rendered else []

    slots = title_formula_slot_schema(formula)
    variable_codes = list(formula.get("variable_schema") or [])
    for slot in slots:
        for code in slot.get("variable_codes") or []:
            if code not in variable_codes:
                variable_codes.append(code)

    options_by_variable: dict[str, list[str]] = {}
    for code in variable_codes:
        values = raw_values(variables.get(code))
        if not values:
            continue
        options: list[str] = []
        if code == "location":
            for value in values:
                options.append(value)
                city_match = re.search(r"(?:^|省|自治区)([^省市区县]{2,})市", value)
                district_match = re.search(r"市([^省市区县]{2,}[区县])", value)
                if city_match:
                    city = city_match.group(1)
                    options.extend((f"{city}市", city))
                    if district_match:
                        options.append(f"{city}{district_match.group(1)}")
                if district_match:
                    options.append(district_match.group(1))
        elif code == "product":
            if formula.get("code") in {"FRT13", "FRT19"}:
                options.extend(
                    part.strip() for value in values for part in re.split(r"[、，,；;]", value) if part.strip()
                )
            for value in values:
                options.append(value)
                normalized = value
                for token in (location, "同城", "装修"):
                    if token:
                        normalized = normalized.replace(token, "")
                normalized = normalized.strip(" -—·，,：:")
                if normalized:
                    options.append(normalized)
                    shortened = normalized
                    for suffix in ("服务", "施工", "人工", "改造"):
                        if shortened.endswith(suffix) and len(shortened) > len(suffix):
                            shortened = shortened[: -len(suffix)]
                    if shortened:
                        options.append(shortened)
        elif code in {"inspection", "kickoff"}:
            options.extend(values)
            pattern = r"巡检|巡查" if code == "inspection" else r"开工"
            options.extend(term for value in values for term in re.findall(pattern, value))
        elif code in {"craft_count", "craft_duration"}:
            options.extend(values)
            unit = r"(?:道|步|项|个)" if code == "craft_count" else r"(?:小时|天|周|个月|月)"
            options.extend(
                term
                for value in values
                for term in re.findall(r"(?:\d+(?:\.\d+)?|[一二两三四五六七八九十百]+)" + unit, value)
            )
        elif code == "title_price":
            options.extend(values)
        elif code in {"quantity", "price"}:
            options.extend(
                number for value in values for number in re.findall(r"\d+(?:\.\d+)?", value.replace(",", ""))
            )
        elif code == "persona_fact":
            options.extend(
                identity
                for value in values
                for identity in re.findall(r"(?:装修)?工长|设计师|项目经理|监理|施工负责人", value)
            )
            if any("工长" in value for value in values):
                options.append("工长")
            if not options or formula.get("code") not in {"FRT12", "FRT13", "FRT14"}:
                options.extend(
                    fact
                    for value in values
                    for fact in re.findall(
                        r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百]+)(?:年|岁|个|位|次)", value
                    )
                )
        else:
            options.extend(values)
        if options:
            options_by_variable[code] = list(dict.fromkeys(options))

    lexicon_bundle = (production_pack or {}).get("formula_lexicon_bundle") or {}
    selected_title_terms = (lexicon_bundle.get("selection") or {}).get("title") or {}
    fact_bindings = (lexicon_bundle.get("fact_bindings") or {}).get("title") or {}
    for lexicon_code, bindings in fact_bindings.items():
        selected = set(selected_title_terms.get(lexicon_code) or [])
        for binding in bindings or []:
            term = str(binding.get("term") or "").strip()
            if not term or term not in selected:
                continue
            for variable_code in binding.get("variable_codes") or []:
                options_by_variable.setdefault(str(variable_code), []).append(term)

    title_policy = ((production_pack or {}).get("content_rule_bundle") or {}).get("single_blueprint") or {}
    if title_policy.get("title_styles", {}).get(strategy_snapshot.get("content_direction")) == "local_labor_standard":
        return {"location（地域）": tuple(dict.fromkeys(options_by_variable["location"]))}
    if not slots:
        return {
            code: tuple(dict.fromkeys(options_by_variable.get(code) or []))
            for code in formula.get("variable_schema") or []
            if options_by_variable.get(code)
        }

    required: dict[str, tuple[str, ...]] = {}
    for slot in slots:
        variable_codes = [str(code) for code in slot.get("variable_codes") or []]
        lexicon_codes = [str(code) for code in slot.get("lexicon_codes") or []]
        options = [option for code in variable_codes for option in options_by_variable.get(code) or []]
        options.extend(
            str(term).strip()
            for code in lexicon_codes
            for term in selected_title_terms.get(code) or []
            if str(term).strip()
        )
        slot_code = str(slot.get("code") or "").strip()
        label = str(slot.get("label") or slot_code).strip()
        source_codes = "/".join([*variable_codes, *lexicon_codes])
        key = (
            f"{slot_code}[{source_codes}]（{label}，任选一项）"
            if len(variable_codes) + len(lexicon_codes) > 1
            else f"{slot_code}（{label}）"
        )
        required[key] = tuple(dict.fromkeys(options))
    return required


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


def _derive_formula_calculation_evidence(state: dict[str, Any]) -> EvidenceItemV1 | None:
    requirements = {
        str(item.get("variable_code") or "")
        for item in (state.get("material_manifest") or {}).get("requirements") or []
    }
    if "calculated_total" not in requirements:
        return None
    variables = brief_variable_map(state.get("content_brief") or {})
    quote_type = str(variables.get("quote_type") or "")
    quantity_text = str(variables.get("quantity") or "").replace(",", "")
    price_text = str(variables.get("price") or "").replace(",", "")
    quantity_match = re.search(r"\d+(?:\.\d+)?", quantity_text)
    price_match = re.search(r"\d+(?:\.\d+)?", price_text)
    is_project_quote = quote_type == "project_quote" or "项目" in quote_type
    if not is_project_quote or not quantity_match or not price_match or not re.search(r"元\s*/\s*㎡", price_text):
        return None
    try:
        quantity = Decimal(quantity_match.group())
        unit_price = Decimal(price_match.group())
    except InvalidOperation:
        return None
    total = quantity * unit_price

    def display(value: Decimal) -> str:
        rendered = format(value.normalize(), "f")
        return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered

    quantity_value = display(quantity)
    unit_price_value = display(unit_price)
    total_value = display(total)
    expression = f"{quantity_value}㎡×{unit_price_value}元/㎡={total_value}元"
    source_hash = hashlib.sha256(
        json.dumps(
            [state["task_id"], "calculated_total", quantity_value, unit_price_value, total_value, quote_type],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return EvidenceItemV1(
        id=f"ev_{source_hash[:16]}",
        variable_codes=("calculated_total",),
        value=int(total) if total == total.to_integral_value() else float(total),
        source_type="human_confirmation",
        source_id="formula:FRB07:quantity_x_unit_price",
        source_version="deterministic-calculation-v1",
        verified_status="user_confirmed",
        allowed_usage=("body",),
        risk_level="high_risk",
        source_hash=source_hash,
        metadata={
            "unit": "元",
            "material_type": "derived_calculation",
            "derivation": {
                "operation": "multiply",
                "expression": expression,
                "input_variable_codes": ["quantity", "price"],
                "disclaimer": "同一项目口径的程序计算参考，不等同于最终成交或结算金额。",
            },
        },
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


def _expression_search_context(state: dict[str, Any]) -> list[str]:
    variables = brief_variable_map(state.get("content_brief") or {})
    values = []
    for key in ("user_request", "scene", "project_type", "product", "pain", "owner_pain", "city", "area"):
        value = _display_business_value(variables.get(key))
        if value:
            values.append(value[:160])
    strategy = state.get("strategy_snapshot") or {}
    for item in (strategy.get("body_formula") or {}, strategy.get("title_formula") or {}):
        value = _display_business_value(item.get("name"))
        if value:
            values.append(value)
    return list(dict.fromkeys(values))


def _advantage_formula_section(strategy_snapshot: dict[str, Any]) -> str:
    sections = list((strategy_snapshot.get("body_formula") or {}).get("structure_schema") or [])
    for terms in (("优势", "服务", "做事"), ("人设", "身份")):
        for section in sections:
            if any(term in str(section) for term in terms):
                return str(section)
    if not sections:
        raise ValueError("正文公式缺少可绑定优势资料的段落")
    return str(sections[-1])


async def load_expression_knowledge(state: dict[str, Any]) -> dict[str, Any]:
    """并行检索 V5 绑定资料，并将事实与纯表达参考分开冻结。"""

    blueprint_policy = ((state.get("runtime_config_snapshot") or {}).get("content_rule_bundle") or {}).get(
        "single_blueprint"
    ) or {}
    if blueprint_policy.get("skip_expression_knowledge"):
        return {"evidence_items": [], "citations": [], "expression_guidance": None}
    policy = (state.get("runtime_config_snapshot") or {}).get("expression_knowledge_policy") or {}
    sources = policy.get("sources") or []
    if not sources:
        return {"evidence_items": [], "citations": [], "expression_guidance": None}

    from yuxi import knowledge_base

    accessible_payload = await knowledge_base.get_databases_by_uid(state["uid"])
    accessible = accessible_payload.get("databases") or []
    by_name: dict[str, list[dict[str, Any]]] = {}
    for item in accessible:
        by_name.setdefault(str(item.get("name") or ""), []).append(item)
    retrievers = knowledge_base.get_retrievers()
    search_context = _expression_search_context(state)

    async def retrieve(source: dict[str, Any]) -> dict[str, Any]:
        name = str(source.get("name") or "").strip()
        matches = by_name.get(name) or []
        if len(matches) != 1:
            raise ValueError(f"表达资料库“{name}”必须存在且当前用户只能访问一个同名库")
        kb_id = str(matches[0].get("kb_id") or "")
        target = retrievers.get(kb_id)
        if target is None:
            raise ValueError(f"表达资料库“{name}”尚未加载检索器")
        query = " ".join(
            dict.fromkeys(
                [
                    *(str(item).strip() for item in source.get("query_terms") or [] if str(item).strip()),
                    *search_context,
                ]
            )
        )[:800]
        output = await target["retriever"](query)
        if not isinstance(output, dict) or not isinstance(output.get("results"), list):
            raise ValueError(f"表达资料库“{name}”返回了无效检索结果")
        max_chunks = int(source.get("max_chunks") or 2)
        max_chars = int(source.get("max_chars_per_chunk") or 1200)
        chunks = []
        for item in output["results"][:max_chunks]:
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            metadata = dict(item.get("metadata") or {})
            chunks.append(
                {
                    "chunk_id": str(item.get("id") or ""),
                    "file_id": str(item.get("file_id") or ""),
                    "document_name": str(metadata.get("source") or item.get("file_id") or ""),
                    "content": content[:max_chars],
                }
            )
        if policy.get("required") is True and not chunks:
            raise ValueError(f"表达资料库“{name}”没有召回可用内容")
        return {
            "kb_id": kb_id,
            "name": name,
            "role": str(source.get("role") or ""),
            "usage": str(source.get("usage") or "style_reference"),
            "query": query,
            "chunks": chunks,
        }

    loaded = await asyncio.gather(*(retrieve(source) for source in sources))
    evidence_items: list[dict[str, Any]] = []
    citations: list[str] = []
    style_sources: list[dict[str, Any]] = []
    strategy_snapshot = state.get("strategy_snapshot") or {}
    body_formula_code = str((strategy_snapshot.get("body_formula") or {}).get("code") or "")
    formula_section = _advantage_formula_section(strategy_snapshot)
    for source in loaded:
        if source["usage"] == "body_evidence" and not (
            (state.get("runtime_config_snapshot") or {}).get("content_rule_bundle") or {}
        ).get("single_blueprint"):
            for chunk in source["chunks"]:
                source_hash = hashlib.sha256(chunk["content"].encode("utf-8")).hexdigest()
                evidence_key = f"{source['kb_id']}:{chunk['chunk_id']}:{source_hash}"
                evidence_id = f"ev_{hashlib.sha256(evidence_key.encode()).hexdigest()[:16]}"
                evidence_items.append(
                    {
                        "id": evidence_id,
                        "variable_codes": ["advantages"],
                        "value": chunk["content"],
                        "source_type": "knowledge_base",
                        "source_id": chunk["chunk_id"],
                        "source_version": source_hash,
                        "verified_status": "retrieved",
                        "allowed_usage": ["body"],
                        "risk_level": "normal",
                        "source_hash": source_hash,
                        "metadata": {
                            "material_type": "brand_fact",
                            "knowledge_base_id": source["kb_id"],
                            "knowledge_base_name": source["name"],
                            "document_id": chunk["file_id"],
                            "document_name": chunk["document_name"],
                            "chunk_id": chunk["chunk_id"],
                            "writing_ready": True,
                            "integration_instruction": (
                                "只选择与本次场景和核心痛点最相关的二至三项有据优势；"
                                "正文前两个自然段用真实身份、做事特点和可信依据建立身份、价值、证据三层人设，"
                                f"再在“{formula_section}”按需展开，不得机械罗列或扩大为承诺"
                            ),
                            "relevance_reason": "用户指定该知识库用于创作时提炼当前工长的真实优势",
                            "body_formula_code": body_formula_code,
                            "formula_section": formula_section,
                            "persona_opening_layers": ["identity", "value", "evidence"],
                            "persona_opening_window_paragraphs": 2,
                            "advantage_selection": {
                                "min": 2,
                                "max": 3,
                                "match_current_pain": True,
                            },
                        },
                    }
                )
                citations.append(chunk["chunk_id"])
        else:
            style_sources.append(source)

    guidance = {
        "schema_version": 1,
        "sources": style_sources,
        "usage_rules": [
            "只改变措辞、语气、句式、换行、清单与 Emoji，不把表达样例当作本次业务事实",
            "不得复制资料中的人物、城市、数字、报价、项目经历、客户反馈或承诺",
            "与锁定爆款结构、公式、渠道或用户要求冲突时，服从锁定规则和真实 Evidence",
        ],
    }
    canonical = json.dumps(guidance, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    guidance["snapshot_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    await append_run_stream_event(
        state["run_id"],
        "content.expression_knowledge.loaded",
        {
            "task_id": state["task_id"],
            "snapshot_hash": guidance["snapshot_hash"],
            "sources": [
                {
                    "knowledge_base_id": item["kb_id"],
                    "name": item["name"],
                    "role": item["role"],
                    "count": len(item["chunks"]),
                }
                for item in loaded
            ],
        },
        thread_id=state["task_id"],
    )
    return {"evidence_items": evidence_items, "citations": citations, "expression_guidance": guidance}


class V3DeterministicNodeHandler:
    async def execute(
        self,
        *,
        db: AsyncSession,
        node: dict[str, Any],
        state: dict[str, Any],
        node_run_id: str,
    ) -> dict[str, Any]:
        if node["id"] == "merge_strategy_prices":
            collection = state["strategy_price_evidence_collection"]
            result = await self._freeze_evidence_bundle(
                db=db,
                state={**state, "evidence_collection": collection},
                node_run_id=node_run_id,
            )
            candidates = dict(state["strategy_candidates"])
            candidates["available_input_paths"] = list(
                dict.fromkeys(
                    [
                        *(candidates.get("available_input_paths") or []),
                        *(
                            f"evidence_bundle.items.{index}.value"
                            for index, item in enumerate(result["evidence_bundle"]["items"])
                            if item.get("value") not in (None, "", [], {})
                            and (item.get("metadata") or {}).get("material_type") != "viral_example"
                        ),
                    ]
                )
            )
            return {**result, "strategy_candidates": candidates}
        if node["id"] == "prepare_strategy_candidates":
            from yuxi.content.control.workflow.joint_strategy import prepare_strategy_candidates

            return await prepare_strategy_candidates(db=db, state=state, node_run_id=node_run_id)
        if node["id"] == "prepare_creation_plan_inputs":
            from yuxi.content.control.workflow.creation_plan import prepare_creation_plan_inputs

            return await prepare_creation_plan_inputs(db=db, state=state, node_run_id=node_run_id)
        if node["id"] == "build_creation_plan":
            from yuxi.content.control.workflow.creation_plan import build_creation_plan

            return await build_creation_plan(db=db, state=state, node_run_id=node_run_id)
        if node["id"] == "merge_extracted_creation_facts":
            from yuxi.content.control.workflow.creation_plan import merge_extracted_creation_facts

            return await merge_extracted_creation_facts(db=db, state=state, node_run_id=node_run_id)
        if node["id"] == "lock_creation_strategy" and state.get("joint_strategy_decision"):
            from yuxi.content.control.workflow.joint_strategy import lock_joint_strategy

            return await lock_joint_strategy(db=db, state=state, node_run_id=node_run_id)
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
            "validate_material_gate": self._validate_material_gate,
            "freeze_production_pack": self._freeze_production_pack,
            "compose_locked_quote_block": self._compose_locked_quote_block,
            "validate_composed_content": self._validate_composed_content,
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
    async def _validate_material_gate(*, db: AsyncSession, state: dict[str, Any], node_run_id: str) -> dict[str, Any]:
        del db, node_run_id
        try:
            manifest = MaterialRequirementManifestV1.model_validate(state["material_manifest"])
            reference_snapshot = (state.get("strategy_snapshot") or {}).get("reference_snapshot") or {}
            materials = standardize_evidence_materials(
                evidence_bundle=state["evidence_bundle"],
                manifest=manifest,
                reference_snapshot=reference_snapshot,
            )
            report = validate_material_gate(manifest=manifest, materials=materials)
        except (KeyError, ValueError) as exc:
            raise ContentApplicationError("MATERIAL_SCHEMA_INVALID", str(exc), "invalid") from exc
        if report.status != "passed":
            first = report.issues[0] if report.issues else None
            raise ContentApplicationError(
                first.code if first else "MATERIAL_GATE_BLOCKED",
                first.message if first else "标准物料质量门未通过",
                "invalid",
            )
        try:
            locked_quote = extract_locked_quote_block(
                {
                    "materials": [item.model_dump(mode="json") for item in materials],
                    "channel_profile": state.get("channel_profile") or {},
                }
            )
        except ValueError as exc:
            raise ContentApplicationError("LOCKED_QUOTE_BLOCK_INTEGRITY_FAILED", str(exc), "invalid") from exc
        if locked_quote is not None:
            limits = quote_body_limits(
                {
                    "channel_profile": state.get("channel_profile") or {},
                    "content_rule_bundle": (state.get("runtime_config_snapshot") or {}).get("content_rule_bundle")
                    or {},
                },
                locked_quote["rendered_content"],
            )
            if limits["creative_body_max_chars"] < limits["creative_body_min_chars"]:
                raise ContentApplicationError(
                    "LOCKED_QUOTE_BLOCK_TOO_LONG",
                    f"报价原文超过渠道正文容量，无法保留至少 {limits['creative_body_min_chars']} 字的创作空间",
                    "invalid",
                )
        references = [item for item in materials if item.material_type == "viral_reference"]
        if len(references) != 1:
            raise ContentApplicationError(
                "REFERENCE_MATERIAL_INVALID",
                "冻结生产包前必须且只能有一篇已审核爆款参考",
                "invalid",
            )
        lexicon_constraints = build_formula_lexicon_constraints(
            materials=materials,
            formula_lexicon_bundle=state.get("formula_lexicon_bundle") or {},
            material_quality_report=report,
        )
        strategy_title_formula = (state.get("strategy_snapshot") or {}).get("title_formula") or {}
        title_lexicon_codes = {
            str(entry.get("code") or "")
            for entry in (state.get("formula_lexicon_bundle") or {}).get("title") or []
            if str(entry.get("code") or "").strip()
        }
        required_title_codes = required_title_lexicon_codes(strategy_title_formula)
        single_blueprint = ((state.get("runtime_config_snapshot") or {}).get("content_rule_bundle") or {}).get(
            "single_blueprint"
        )
        unresolved = [
            code
            for code in lexicon_constraints["unresolved_fact_bound_codes"]
            if code in required_title_codes or (not single_blueprint and code not in title_lexicon_codes)
        ]
        if unresolved:
            raise ContentApplicationError(
                "MATERIAL_LEXICON_GROUNDING_MISSING",
                "公式必选词库没有已审核事实支撑：" + "、".join(unresolved),
                "invalid",
            )
        return {
            "standardized_materials": [item.model_dump(mode="json") for item in materials],
            "material_quality_report": report.model_dump(mode="json"),
        }

    @staticmethod
    async def _freeze_production_pack(*, db: AsyncSession, state: dict[str, Any], node_run_id: str) -> dict[str, Any]:
        del db, node_run_id
        try:
            runtime = deepcopy(state.get("runtime_config_snapshot") or {})
            bundle = runtime.get("content_rule_bundle") or {}
            platform = (bundle.get("runtime_rules") or {}).get("viral-platform-expression") or {}
            lexicon_name = platform.get("forbidden_knowledge_base") or (bundle.get("single_blueprint") or {}).get(
                "forbidden_knowledge_base"
            )
            if lexicon_name:
                from yuxi.content.model.forbidden_words import replace_forbidden_words
                from yuxi.services.content_forbidden_words_service import load_forbidden_words

                snapshot = await load_forbidden_words(state["uid"], lexicon_name)
                platform = bundle["runtime_rules"]["viral-platform-expression"]
                platform["forbidden_lexicon"] = snapshot
                platform["forbidden_replacements"] = {
                    term: alternatives[0] for term, alternatives in snapshot["alternatives"].items() if alternatives
                }
                bundle["topic_candidates"] = list(
                    dict.fromkeys(
                        replace_forbidden_words(topic, platform["forbidden_replacements"])
                        for topic in bundle.get("topic_candidates") or []
                        if not any(term in topic for term, values in snapshot["alternatives"].items() if not values)
                    )
                )
                bundle["bundle_hash"] = hashlib.sha256(
                    json.dumps(
                        {key: value for key, value in bundle.items() if key != "bundle_hash"},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
            brief = state.get("content_brief") or {}
            writing_request = (
                _display_business_value(brief.get("user_request") or brief_variable_map(brief).get("user_request"))
                or None
            )
            if (runtime.get("content_rule_bundle", {}).get("single_blueprint") or {}).get(
                "full_context_repair"
            ) and writing_request:
                writing_request = writing_request.removesuffix("；标题价格与报价明细已作为锁定事实保存。")
            pack = freeze_production_pack(
                task_id=state["task_id"],
                production_order=ProductionOrderV1.model_validate(state["production_order"]),
                material_manifest=MaterialRequirementManifestV1.model_validate(state["material_manifest"]),
                material_quality_report=state["material_quality_report"],
                materials=state["standardized_materials"],
                strategy_snapshot=state["strategy_snapshot"],
                evidence_bundle=state["evidence_bundle"],
                formula_lexicon_bundle=state["formula_lexicon_bundle"],
                reference_snapshot=state["strategy_snapshot"]["reference_snapshot"],
                expression_guidance=state.get("expression_guidance"),
                writing_request=writing_request,
                channel_profile=state.get("channel_profile") or {},
                persona_profile=state.get("persona_profile") or {},
                content_rule_bundle=runtime.get("content_rule_bundle") or {},
                compliance_policy_version_ids=runtime.get("compliance_policy_version_ids") or [],
            )
        except (KeyError, ValueError) as exc:
            raise ContentApplicationError("PRODUCTION_PACK_INVALID", str(exc), "invalid") from exc
        return {"production_pack": pack.model_dump(mode="json"), "runtime_config_snapshot": runtime}

    @staticmethod
    async def _compose_locked_quote_block(
        *, db: AsyncSession, state: dict[str, Any], node_run_id: str
    ) -> dict[str, Any]:
        del db, node_run_id
        production_pack = state.get("production_pack") or {}
        try:
            locked_quote = extract_locked_quote_block(production_pack)
        except ValueError as exc:
            raise ContentApplicationError("LOCKED_QUOTE_BLOCK_INTEGRITY_FAILED", str(exc), "invalid") from exc
        creative_draft = deepcopy(state.get("creative_content_draft") or state.get("content_draft") or {})
        creative_hash = hashlib.sha256(
            json.dumps(creative_draft, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if locked_quote is None or is_raw_reference(production_pack):
            return {
                "creative_content_draft": creative_draft,
                "content_draft": creative_draft,
                "creative_draft_hash": creative_hash,
                "final_draft_hash": creative_hash,
                "locked_block_composition": {"status": "not_applicable"},
            }
        draft = deepcopy(creative_draft)
        creative_body = str(draft.get("body") or "")
        rendered = locked_quote["rendered_content"]
        original = locked_quote["original_content"]
        if rendered in creative_body or original in creative_body:
            raise ContentApplicationError(
                "LOCKED_QUOTE_BLOCK_INTEGRITY_FAILED",
                "创作稿包含应由程序插入的锁定报价块",
                "invalid",
            )
        limits = quote_body_limits(production_pack, rendered)
        if not limits["creative_body_min_chars"] <= len(creative_body) <= limits["creative_body_max_chars"]:
            raise ContentApplicationError(
                "LOCKED_QUOTE_BLOCK_LENGTH_INVALID",
                "创作正文长度未给锁定报价块预留足够渠道容量",
                "invalid",
            )
        try:
            if draft.get("blueprint_content"):
                composed_body = "\n\n".join(
                    rendered if b["kind"] == "quote_ref" else b["text"] for b in draft["blueprint_content"]["blocks"]
                )
            else:
                composed_body = compose_after_opening_paragraph(creative_body, rendered)
        except ValueError as exc:
            raise ContentApplicationError("LOCKED_QUOTE_BLOCK_INTEGRITY_FAILED", str(exc), "invalid") from exc
        draft["body"] = composed_body
        paragraph_evidence = list(draft.get("paragraph_evidence") or [])
        paragraph_evidence.append(
            {
                "paragraph_id": "locked_quote_block",
                "evidence_ids": locked_quote["evidence_ids"],
            }
        )
        draft["paragraph_evidence"] = paragraph_evidence
        final_hash = hashlib.sha256(
            json.dumps(draft, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        channel_result = deepcopy(state.get("channel_result") or {})
        if channel_result:
            channel_result["body"] = composed_body
            if locked_quote.get("replacement_diffs"):
                channel_result.setdefault("replacement_diffs", []).extend(locked_quote["replacement_diffs"])
        return {
            "creative_content_draft": creative_draft,
            "content_draft": draft,
            "creative_draft_hash": creative_hash,
            "final_draft_hash": final_hash,
            **({"channel_result": channel_result} if channel_result else {}),
            "locked_block_composition": {
                "status": "composed",
                "block_id": "quote_block",
                "material_id": locked_quote["material_id"],
                "content_hash": locked_quote["content_hash"],
                "rendered_char_count": len(rendered),
                "creative_body_hash": hashlib.sha256(creative_body.encode("utf-8")).hexdigest(),
                "final_body_hash": hashlib.sha256(composed_body.encode("utf-8")).hexdigest(),
                "insertion_policy": "agent-ordered-reference-v1"
                if draft.get("blueprint_content")
                else locked_quote["insertion_policy"],
            },
        }

    @staticmethod
    async def _validate_composed_content(
        *, db: AsyncSession, state: dict[str, Any], node_run_id: str
    ) -> dict[str, Any]:
        del db, node_run_id
        production_pack = state.get("production_pack") or {}
        try:
            locked_quote = extract_locked_quote_block(production_pack)
        except ValueError as exc:
            raise ContentApplicationError("LOCKED_QUOTE_BLOCK_INTEGRITY_FAILED", str(exc), "invalid") from exc
        if locked_quote is None or is_raw_reference(production_pack):
            return {"composed_content_validation_report": {"status": "not_applicable", "checks": []}}
        draft = state.get("content_draft") or {}
        body = str(draft.get("body") or "")
        draft_hash = hashlib.sha256(
            json.dumps(draft, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        composition = state.get("locked_block_composition") or {}
        rendered = locked_quote["rendered_content"]
        checks: list[dict[str, Any]] = []
        if (
            composition.get("status") != "composed"
            or composition.get("content_hash") != locked_quote["content_hash"]
            or composition.get("final_body_hash") != hashlib.sha256(body.encode("utf-8")).hexdigest()
            or state.get("final_draft_hash") != draft_hash
            or body.count(rendered) != 1
        ):
            checks.append(
                {
                    "code": "LOCKED_QUOTE_BLOCK_INTEGRITY_FAILED",
                    "level": "error",
                    "message": "最终正文没有且仅有一个完整的锁定报价块",
                }
            )
        limits = quote_body_limits(production_pack, rendered)
        if len(body) > limits["final_body_max_chars"]:
            checks.append(
                {
                    "code": "CHANNEL_BODY_LONG",
                    "level": "error",
                    "message": f"合成后正文超过渠道上限 {limits['final_body_max_chars']} 字",
                }
            )
        compliance = ComplianceEngine().validate_and_adapt(
            title=str((state.get("selected_title") or {}).get("text") or ""),
            body=body,
            topics=list(draft.get("topics") or []),
            channel_profile=state.get("channel_profile") or {},
            policies=state.get("compliance_policies") or [],
        )
        if compliance["status"] == "blocked" or compliance["replacement_diffs"]:
            checks.append(
                {
                    "code": "COMPOSED_CONTENT_COMPLIANCE_FAILED",
                    "level": "error",
                    "message": "合成后全文命中阻断或自动替换规则；锁定报价原文禁止自动修改",
                }
            )
        platform = (production_pack.get("content_rule_bundle", {}).get("runtime_rules") or {}).get(
            "viral-platform-expression", {}
        )
        combined = "\n".join(
            [str((state.get("selected_title") or {}).get("text") or ""), body, *draft.get("topics", [])]
        )
        residual = [term for term in platform.get("forbidden_lexicon", {}).get("alternatives", {}) if term in combined]
        if residual:
            checks.append(
                {
                    "code": "CONTENT_FORBIDDEN_TERM",
                    "level": "error",
                    "message": "合成后仍有封禁词：" + "、".join(residual),
                }
            )
        if checks:
            raise ContentApplicationError(checks[0]["code"], checks[0]["message"], "invalid")
        return {
            "composed_content_validation_report": {
                "status": "passed",
                "checks": [],
                "content_hash": locked_quote["content_hash"],
                "final_body_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            }
        }

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
        direction_blueprint = deepcopy(group.source_metadata.get("composition_blueprint"))
        if context.industry_slug == "decoration" and not direction_blueprint:
            raise ValueError("装修一级内容方向缺少层级与词组组合")
        body_calling = get_decoration_body_calling(body_formula.code) if context.industry_slug == "decoration" else None
        formula_lexicons = (
            get_formula_lexicon_requirements(title_formula.code, body_formula.code)
            if context.industry_slug == "decoration"
            else None
        )
        # 已导入原文的版本以可编辑规则为准，同时保留词库调用与段落标识。
        if body_calling is not None and body_formula.source_content:
            body_calling["composition_blueprint"] = deepcopy(direction_blueprint)
            body_calling["sections"] = [
                {
                    **(
                        body_calling["sections"][index]
                        if index < len(body_calling["sections"])
                        else {
                            "id": f"section_{index + 1}",
                            "lexicon_calls": [],
                            "fact_source": "evidence",
                        }
                    ),
                    "name": paragraph.split("：", 1)[0],
                    "instruction": paragraph,
                    "fill_rule": paragraph,
                }
                for index, paragraph in enumerate(body_formula.structure_schema)
            ]
            body_calling["formula_name"] = body_formula.name
            body_calling["reference_examples"] = body_formula.reference_examples
        body_structure = (
            [section["name"] for section in body_calling["sections"]]
            if body_calling is not None
            else body_formula.structure_schema or []
        )
        title_formula_payload = {
            "code": title_formula.code,
            "name": title_formula.name,
            "core_goal": title_formula.core_goal,
            "source_content": title_formula.source_content or {},
            "reference_examples": title_formula.reference_examples or [],
            "variable_schema": title_formula.variable_schema or [],
            "compatible_methods": title_formula.compatible_methods or [],
            "risk_rules": title_formula.risk_rules or [],
            "lexicon_codes": (
                [item["code"] for item in formula_lexicons["title"]] if formula_lexicons is not None else []
            ),
        }
        if context.industry_slug == "decoration":
            title_formula_payload = enrich_decoration_title_formula(title_formula_payload)
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
            "title_formula": title_formula_payload,
            "body_formula": {
                "code": body_formula.code,
                "name": body_formula.name,
                "source_content": body_formula.source_content or {},
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
                "composition_blueprint": deepcopy(direction_blueprint),
                "body_calling_source": (
                    get_decoration_body_calling_source(body_formula.code) if body_calling is not None else None
                ),
            },
            "rule_version_id": context.rule_version_id,
            "match_snapshot_id": match_snapshot.id,
            "formula_snapshot_id": formula_snapshot.id,
        }
        if direction_blueprint is not None:
            strategy_payload["direction_blueprint"] = direction_blueprint
        for section in ("title_formula", "body_formula"):
            strategy_payload[section] = resolve_industry_formula(
                strategy_payload[section], industry_slug=context.industry_slug, scenario=group.scenario_description
            )
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
        if not industry_pack_id.startswith("industry-pack-decoration-v"):
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
            (
                item
                for item in await repo.list_industry_packs(published_only=False)
                if item["id"] == task.industry_pack_version_id
            ),
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
        from yuxi.content.v3.modular_rules import MODULAR_WORKFLOW_IDS, build_modular_rule_bundle

        if task.workflow_version_id in MODULAR_WORKFLOW_IDS:
            runtime["content_rule_bundle"] = build_modular_rule_bundle(
                state.get("content_brief") or {},
                single_blueprint=task.workflow_version_id in SINGLE_BLUEPRINT_WORKFLOW_IDS,
            )
        workflow = await repo.get_workflow(task.workflow_version_id)
        expression_policy = (
            deepcopy((workflow.definition_json or {}).get("expression_knowledge_policy")) if workflow else None
        )
        if expression_policy:
            runtime["expression_knowledge_policy"] = expression_policy
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
        items: list[EvidenceItemV1] = []
        single_blueprint = bool(
            (state.get("runtime_config_snapshot", {}).get("content_rule_bundle") or {}).get("single_blueprint")
        )
        for key, value, variable_codes in canonical_brief_facts(
            state["content_brief"], derive_numbers=not single_blueprint
        ):
            if value in (None, "", [], {}):
                continue
            source_hash = hashlib.sha256(
                json.dumps([state["task_id"], key, value], ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            items.append(
                EvidenceItemV1(
                    id=f"ev_{source_hash[:16]}",
                    variable_codes=variable_codes,
                    value=value,
                    source_type="manual_input",
                    source_id=f"field_{key}",
                    source_version="brief-v1",
                    verified_status="user_confirmed",
                    allowed_usage=("title", "body", "visual"),
                    source_hash=source_hash,
                    metadata={
                        "locator": (state["content_brief"].get("fact_source_paths") or {}).get(
                            key, f"content_brief.{key}"
                        )
                    },
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
        trusted_snapshot = (state.get("runtime_config_snapshot") or {}).get(_TRUSTED_QUOTE_SNAPSHOT_KEY)
        if trusted_snapshot is not None:
            trusted_items = _trusted_quote_evidence_items(
                trusted_snapshot,
                content_type_code=str((state.get("content_brief") or {}).get("content_type_code") or ""),
            )
            items = [item for item in items if not set(item.variable_codes).intersection(_TRUSTED_QUOTE_VARIABLES)]
            items.extend(trusted_items)
        evidence_service = EvidenceApplicationService(db)
        items = await evidence_service.canonicalize_existing_items(items)
        latest = await evidence_service.get_latest_frozen_bundle(state["task_id"])

        def same_fact_identity(left: EvidenceItemV1, right: EvidenceItemV1) -> bool:
            return (
                left.id == right.id
                and left.variable_codes == right.variable_codes
                and left.value == right.value
                and left.source_type == right.source_type
                and left.verified_status == right.verified_status
                and left.allowed_usage == right.allowed_usage
                and left.risk_level == right.risk_level
                and left.source_hash == right.source_hash
            )

        if latest is not None:
            latest_by_id = {item.id: item for item in latest.items}
            items = [
                latest_by_id[item.id]
                if item.id in latest_by_id and same_fact_identity(latest_by_id[item.id], item)
                else item
                for item in items
            ]

        def item_signature(item: EvidenceItemV1) -> str:
            return json.dumps(
                item.model_dump(mode="json", exclude={"created_at"}),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )

        if latest is not None and sorted(map(item_signature, latest.items)) == sorted(map(item_signature, items)):
            return {"evidence_bundle": latest.model_dump(mode="json")}
        bundle = freeze_evidence_bundle(
            task_id=state["task_id"],
            version=(latest.version + 1) if latest else 1,
            items=items,
            supersedes_id=latest.id if latest else None,
        )
        await evidence_service.persist_frozen_bundle(
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
        derived_calculation = _derive_formula_calculation_evidence(state)
        if derived_calculation is not None:
            additions.append(derived_calculation)
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
        evidence_service = EvidenceApplicationService(db)
        additions = await evidence_service.canonicalize_existing_items(new_additions)
        if not additions:
            return {"evidence_bundle": current.model_dump(mode="json")}
        bundle = next_evidence_bundle_version(
            current,
            additions=additions,
            citations=[*current.citations, *({"source_id": item} for item in collection.get("citations") or [])],
        )
        await evidence_service.persist_frozen_bundle(
            bundle,
            run_id=state["run_id"],
            thread_id=state["task_id"],
            added_evidence_ids=tuple(item.id for item in additions),
        )
        return {"evidence_bundle": bundle.model_dump(mode="json")}

    @staticmethod
    async def _merge_research_evidence(*, db: AsyncSession, state: dict[str, Any], node_run_id: str) -> dict[str, Any]:
        del db, node_run_id
        expression_materials = await load_expression_knowledge(state)
        collections = [
            state.get("business_rule_evidence_collection") or {},
            state.get("price_evidence_collection") or {},
            state.get("compliance_evidence_collection") or {},
            {
                "evidence_items": expression_materials["evidence_items"],
                "citations": expression_materials["citations"],
                "unresolved_questions": [],
            },
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

        # A failed run may already have frozen deterministic KB/reference
        # evidence. Retrying the same task should reuse an identical item
        # instead of submitting its ID as new evidence again.
        current_items = {
            item.id: item
            for item in (
                EvidenceItemV1.model_validate(raw) for raw in (state.get("evidence_bundle") or {}).get("items") or []
            )
        }
        new_evidence_items: list[dict[str, Any]] = []
        reused_reference_ids: set[str] = set()
        for raw in evidence_items:
            candidate = EvidenceItemV1.model_validate(raw)
            existing = current_items.get(candidate.id)
            if existing is None:
                new_evidence_items.append(raw)
                continue
            existing_payload = existing.model_dump(mode="json", exclude={"created_at", "metadata"})
            candidate_payload = candidate.model_dump(mode="json", exclude={"created_at", "metadata"})
            if existing_payload != candidate_payload:
                raise EvidenceGovernanceError(
                    "evidence_id_conflict",
                    f"Evidence ID {candidate.id} 已存在但内容不一致",
                )
            if (
                candidate.metadata.get("material_type") == "viral_example"
                and candidate.metadata.get("selected_reference") is True
            ):
                # The final collection contract still needs to validate the
                # one selected reference. The freeze node will drop this
                # identical copy instead of persisting it again.
                new_evidence_items.append(raw)
                reused_reference_ids.add(candidate.id)
        evidence_items = new_evidence_items
        validation_evidence_bundle = deepcopy(state.get("evidence_bundle") or {})
        if reused_reference_ids:
            validation_evidence_bundle["items"] = [
                item
                for item in validation_evidence_bundle.get("items") or []
                if str(item.get("id") or "") not in reused_reference_ids
            ]

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
        if task.get("creation_mode") != "viral_rewrite":
            raise ValueError("内容证据合并只支持爆款仿写")
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
                evidence_bundle=validation_evidence_bundle,
                locked_versions={
                    "industry_pack_version_id": str(task.get("industry_pack_version_id") or ""),
                    "channel_profile_version_id": str(task.get("channel_profile_version_id") or ""),
                    "persona_profile_version_id": task.get("persona_profile_version_id"),
                    "rule_version_id": str(task.get("rule_version_id") or state.get("rule_version_id") or ""),
                    "title_formula_code": formula.get("selected_title_formula_code"),
                    "body_formula_code": formula.get("selected_body_formula_code"),
                    "artifact_version_id": None,
                },
                locked_values={"creation_mode": "viral_rewrite"},
                strategy_snapshot=state.get("strategy_snapshot") or {},
                viral_candidate_collection=state.get("viral_candidate_collection") or {},
            ),
        )
        output = {"evidence_collection": result.model_dump(mode="json")}
        if expression_materials["expression_guidance"] is not None:
            output["expression_guidance"] = expression_materials["expression_guidance"]
        return output

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

        evidence_service = EvidenceApplicationService(db)
        new_additions = await evidence_service.canonicalize_existing_items(new_additions)

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
            await evidence_service.persist_frozen_bundle(
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
        blueprint = deepcopy(draft.get("blueprint_content"))
        policies = state.get("compliance_policies") or []
        block_results = []
        if blueprint:
            # 有序块是创作正文的唯一来源，合规替换直接落到块上，不能在报价组装时恢复旧文字。
            for block in blueprint["blocks"]:
                if block["kind"] == "text":
                    adapted = ComplianceEngine().validate_and_adapt(
                        title="", body=block["text"], topics=[], channel_profile={}, policies=policies
                    )
                    block["text"] = adapted["body"]
                    for item in [*adapted["checks"], *adapted["replacement_diffs"]]:
                        item["location"] = block["id"]
                    block_results.append(adapted)
            draft["body"] = "\n\n".join(b["text"] for b in blueprint["blocks"] if b["kind"] == "text")
            title_result = ComplianceEngine().validate_and_adapt(
                title=title.get("text", ""), body="", topics=[], channel_profile={}, policies=policies
            )
            title["text"] = title_result["title"]
            block_results.append(title_result)
        channel_profile = deepcopy(state.get("channel_profile") or {})
        blueprint_policy = ((state.get("production_pack") or {}).get("content_rule_bundle") or {}).get(
            "single_blueprint"
        ) or {}
        if blueprint_policy.get("full_context_repair"):
            channel_profile.setdefault("body_constraints", {})["min_length"] = 0
        result = ComplianceEngine().validate_and_adapt(
            title=title.get("text", ""),
            body=draft.get("body", ""),
            topics=draft.get("topics") or [],
            channel_profile=channel_profile,
            policies=[] if blueprint or is_raw_reference(state.get("production_pack") or {}) else policies,
        )
        if is_raw_reference(state.get("production_pack") or {}):
            # 直接仿写保留模型全文；渠道容量提示留给发布前处理，不自动改写。
            for check in result["checks"]:
                if check["code"].startswith("CHANNEL_"):
                    check["level"] = "warning"
            result["status"] = "warning" if result["checks"] else "passed"
        for adapted in block_results:
            result["checks"].extend(adapted["checks"])
            result["replacement_diffs"].extend(adapted["replacement_diffs"])
        if block_results:
            result["status"] = (
                "blocked"
                if any(c["level"] == "error" for c in result["checks"])
                else "warning"
                if result["checks"] or result["replacement_diffs"]
                else "passed"
            )
        rule_bundle = (
            (state.get("production_pack") or {}).get("content_rule_bundle")
            or (state.get("runtime_config_snapshot") or {}).get("content_rule_bundle")
            or {}
        )
        platform_rules = (rule_bundle.get("runtime_rules") or {}).get("viral-platform-expression") or {}
        from yuxi.content.model.forbidden_words import replace_forbidden_words

        replacements = platform_rules.get("forbidden_replacements") or {}
        for location in ("title", "body", "topics"):
            before = result[location]
            after = (
                [replace_forbidden_words(topic, replacements) for topic in before]
                if location == "topics"
                else replace_forbidden_words(before, replacements)
            )
            if after != before:
                result[location] = after
                result["replacement_diffs"].append(
                    {
                        "location": location,
                        "before": before,
                        "after": after,
                        "rule_id": (platform_rules.get("forbidden_lexicon") or {}).get("snapshot_hash")
                        or "viral-platform-expression.v1",
                    }
                )
        if blueprint:
            blueprint["title"]["text"] = result["title"]
            blueprint["topics"] = result["topics"]
            for block in blueprint["blocks"]:
                if block["kind"] == "text":
                    block["text"] = replace_forbidden_words(block["text"], replacements)
            draft["blueprint_content"] = blueprint
        title["text"] = result["title"]
        draft["body"] = result["body"]
        draft["topics"] = result["topics"]
        draft_hash = hashlib.sha256(
            json.dumps(draft, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return {
            "selected_title": title,
            "creative_content_draft": draft,
            "content_draft": draft,
            "creative_draft_hash": draft_hash,
            "channel_result": result,
        }

    @staticmethod
    async def _deterministic_validate(*, db: AsyncSession, state: dict[str, Any], node_run_id: str) -> dict[str, Any]:
        del db, node_run_id
        draft = state.get("content_draft") or {}
        body = draft.get("body", "")
        production_pack = state.get("production_pack") or {}
        blueprint_policy = production_pack.get("content_rule_bundle", {}).get("single_blueprint") or {}
        from yuxi.content.model.forbidden_words import contains_frozen_term
        from yuxi.content.model.single_blueprint import title_publication_year

        report = validate_content(
            title=(state.get("selected_title") or {}).get("text", ""),
            body=body,
            topics=draft.get("topics") or [],
            brief=state["content_brief"],
            evidence_bundle=state["evidence_bundle"],
            title_publication_year=title_publication_year(production_pack),
            strategy={
                "methods": (state.get("strategy_snapshot") or {}).get("creation_methods"),
                "title_formula_code": ((state.get("strategy_snapshot") or {}).get("title_formula") or {}).get("code"),
                "body_formula_code": ((state.get("strategy_snapshot") or {}).get("body_formula") or {}).get("code"),
            },
        )
        if is_raw_reference(production_pack):
            report["checks"].extend((state.get("channel_result") or {}).get("checks") or [])
            report["checks"].extend(topic_validation_checks(draft.get("topics") or []))
            platform = production_pack["content_rule_bundle"]["runtime_rules"]["viral-platform-expression"]
            combined = "\n".join([(state.get("selected_title") or {}).get("text", ""), body, *draft.get("topics", [])])
            for term in (platform.get("forbidden_lexicon") or {}).get("alternatives", {}):
                if term in combined:
                    report["checks"].append(
                        {
                            "code": "CONTENT_FORBIDDEN_TERM",
                            "level": "error",
                            "location": "content",
                            "message": f"封禁词替换后仍有残留：{term}",
                            "evidence_ids": [],
                        }
                    )
            report["status"] = (
                "blocked"
                if any(c["level"] == "error" for c in report["checks"])
                else "warning"
                if report["checks"]
                else "passed"
            )
            return {"validation_report": report}
        locked_quote = extract_locked_quote_block(production_pack) if production_pack else None
        body_minimum = (production_pack.get("content_rule_bundle", {}).get("single_blueprint") or {}).get(
            "creative_min_chars", 200
        )
        body_maximum = blueprint_policy.get("creative_max_chars", 650) or production_pack["channel_profile"][
            "body_constraints"
        ].get("max_length", 1000)
        if locked_quote is not None:
            limits = quote_body_limits(production_pack, locked_quote["rendered_content"])
            body_minimum = limits["creative_body_min_chars"]
            body_maximum = limits["creative_body_max_chars"]
        if not body_minimum <= len(body) <= body_maximum:
            report["checks"].append(
                {
                    "code": "BODY_LENGTH_OUT_OF_RANGE",
                    "level": "error",
                    "location": "body",
                    "message": f"正文目标长度必须为 {body_minimum}～{body_maximum} 字",
                    "evidence_ids": [],
                }
            )
            report["status"] = "blocked"
        if production_pack:
            title = str((state.get("selected_title") or {}).get("text") or "")
            title_for_numbers = title.replace(",", "")
            strategy_snapshot = production_pack.get("strategy_snapshot") or state.get("strategy_snapshot") or {}
            replacements = (
                ((production_pack.get("content_rule_bundle") or {}).get("runtime_rules") or {})
                .get("viral-platform-expression", {})
                .get("forbidden_replacements", {})
            )
            missing_title_facts = {
                code: options
                for code, options in _required_title_fact_options(
                    state["content_brief"], strategy_snapshot, production_pack
                ).items()
                if not any(
                    contains_frozen_term(title_for_numbers, option.replace(",", ""), replacements) for option in options
                )
            }
            if missing_title_facts:
                descriptions = [
                    f"{code}（可接受：{'/'.join(options)}）" for code, options in missing_title_facts.items()
                ]
                report["checks"].append(
                    {
                        "code": "TITLE_REQUIRED_FACT_MISSING",
                        "level": "error",
                        "location": "title",
                        "message": "标题缺少锁定公式必填事实槽位：" + "、".join(descriptions),
                        "evidence_ids": [],
                    }
                )
                report["status"] = "blocked"
        channel_checks = list((state.get("channel_result") or {}).get("checks") or [])
        if channel_checks:
            report["checks"].extend(channel_checks)
            if any(item.get("level") == "error" for item in channel_checks):
                report["status"] = "blocked"
        rule_bundle = (state.get("runtime_config_snapshot") or {}).get("content_rule_bundle") or {}
        if rule_bundle:
            modular_checks = validate_modular_content(
                title=(state.get("selected_title") or {}).get("text", ""),
                body=body,
                topics=draft.get("topics") or [],
                draft=draft,
                brief=state["content_brief"],
                evidence_bundle=state["evidence_bundle"],
                rule_bundle=rule_bundle,
            )
            report["checks"].extend(modular_checks)
            if any(item["level"] == "error" for item in modular_checks):
                report["status"] = "blocked"
            elif report["status"] == "passed" and any(item["level"] == "warning" for item in modular_checks):
                report["status"] = "warning"
        else:
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
        derived_calculations = [
            material
            for material in production_pack.get("materials") or []
            if "calculated_total" in (material.get("variable_codes") or [])
        ]
        for material in derived_calculations:
            payload = material.get("payload") or {}
            derivation = payload.get("derivation") or {}
            expression = str(derivation.get("expression") or "").strip()
            evidence_ids = {str(item) for item in material.get("evidence_ids") or []}
            normalized_body = re.sub(r"\s+", "", body).replace("x", "×").replace("X", "×").replace("*", "×")
            normalized_expression = re.sub(r"\s+", "", expression).replace("x", "×").replace("X", "×").replace("*", "×")
            accepted_expressions = {normalized_expression} if normalized_expression else set()
            expression_match = re.fullmatch(
                r"(?P<quantity>\d+(?:\.\d+)?)㎡×(?P<unit_price>\d+(?:\.\d+)?)元/㎡=(?P<total>\d+(?:\.\d+)?)元",
                normalized_expression,
            )
            if expression_match:
                accepted_expressions.add(
                    f"{expression_match.group('unit_price')}元/㎡×"
                    f"{expression_match.group('quantity')}㎡={expression_match.group('total')}元"
                )
            calculation_used = any(candidate in normalized_body for candidate in accepted_expressions)
            if not calculation_used or not (evidence_ids & used_body_evidence):
                report["checks"].append(
                    {
                        "code": "DERIVED_CALCULATION_UNUSED",
                        "level": "error",
                        "location": "body",
                        "message": f"正文必须逐字写入并引用程序校验结果：{expression}",
                        "evidence_ids": sorted(evidence_ids),
                    }
                )
                report["status"] = "blocked"
        trade_breakdowns = [
            material
            for material in production_pack.get("materials") or []
            if "trade_breakdown" in (material.get("variable_codes") or [])
        ]
        for material in trade_breakdowns:
            value = (material.get("payload") or {}).get("value") or []
            evidence_ids = {str(item) for item in material.get("evidence_ids") or []}
            missing_items = [
                str(item.get("trade") or "")
                for item in value
                if str(item.get("trade") or "") not in body
                or str(item.get("amount") or "") not in body.replace(",", "")
                or not any(str(included) in body for included in item.get("included_items") or [])
            ]
            if missing_items or not (evidence_ids & used_body_evidence):
                report["checks"].append(
                    {
                        "code": "TRADE_BREAKDOWN_UNUSED",
                        "level": "error",
                        "location": "body",
                        "message": "正文必须逐项写入并引用已确认的工种金额与至少一个包含项："
                        + "、".join(missing_items or ["缺少分项 Evidence 引用"]),
                        "evidence_ids": sorted(evidence_ids),
                    }
                )
                report["status"] = "blocked"
        labor_aux_breakdowns = [
            material
            for material in production_pack.get("materials") or []
            if "labor_aux_breakdown" in (material.get("variable_codes") or [])
        ]
        for material in labor_aux_breakdowns:
            value = (material.get("payload") or {}).get("value") or {}
            evidence_ids = {str(item) for item in material.get("evidence_ids") or []}
            body_without_commas = body.replace(",", "")
            required_totals = [
                ("人工合计", value.get("labor_total")),
                ("辅材合计", value.get("auxiliary_total")),
            ]
            missing_items = [
                label
                for label, amount in required_totals
                if label not in body or str(amount or "") not in body_without_commas
            ]
            for item in value.get("trades") or []:
                trade = str(item.get("trade") or "")
                required_amounts = [
                    amount
                    for amount in (item.get("labor_amount"), item.get("auxiliary_amount"))
                    if isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount > 0
                ]
                if (
                    trade not in body
                    or any(str(amount) not in body_without_commas for amount in required_amounts)
                    or not any(str(included) in body for included in item.get("included_items") or [])
                ):
                    missing_items.append(trade)
            if missing_items or not (evidence_ids & used_body_evidence):
                report["checks"].append(
                    {
                        "code": "LABOR_AUX_BREAKDOWN_UNUSED",
                        "level": "error",
                        "location": "body",
                        "message": "正文必须写入并引用已确认的人工、辅材合计及各工种拆分："
                        + "、".join(missing_items or ["缺少拆分 Evidence 引用"]),
                        "evidence_ids": sorted(evidence_ids),
                    }
                )
                report["status"] = "blocked"
        if production_pack:
            bound_material_ids = {
                str(material_id)
                for binding in (production_pack.get("material_quality_report") or {}).get("bindings") or []
                for material_id in binding.get("material_ids") or []
            }
            knowledge_body_evidence = {
                str(evidence_id)
                for material in production_pack.get("materials") or []
                if str(material.get("id") or "") in bound_material_ids
                and (material.get("source") or {}).get("source_type") == "knowledge_base"
                and "body" in ((material.get("governance") or {}).get("allowed_usage") or [])
                for evidence_id in material.get("evidence_ids") or []
            }
        else:
            knowledge_body_evidence = {
                str(item["id"])
                for item in (state.get("evidence_bundle") or {}).get("items") or []
                if item.get("source_type") == "knowledge_base"
                and "body" in (item.get("allowed_usage") or [])
                and item.get("metadata", {}).get("material_type")
                not in {"viral_example", "platform_rule", "compliance_rule", "forbidden_terms"}
            }
        if (
            not blueprint_policy
            and knowledge_body_evidence
            and not used_body_evidence.intersection(knowledge_body_evidence)
        ):
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
        if not blueprint_policy and missing_product_slots:
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
        if blueprint_policy.get("full_context_repair"):
            # 对最终可见全文检查词语；锁定报价尚未插入时也不能漏检或误报。
            combined = "\n".join(
                [
                    (state.get("selected_title") or {}).get("text", ""),
                    body,
                    *(draft.get("topics") or []),
                    locked_quote["rendered_content"] if locked_quote else "",
                ]
            )
            report["checks"] = [c for c in report["checks"] if c["code"] != "CONTENT_REQUIRED_TERM_MISSING"]
            for code, terms, should_contain in (
                ("CONTENT_REQUIRED_TERM_MISSING", state["content_brief"].get("required_terms") or [], True),
                ("CONTENT_FORBIDDEN_TERM", state["content_brief"].get("forbidden_terms") or [], False),
            ):
                for term in terms:
                    if term and (term in combined) != should_contain:
                        report["checks"].append(
                            {
                                "code": code,
                                "level": "error",
                                "location": "content",
                                "message": f"最终稿{'缺少要求词' if should_contain else '含禁用词'}：{term}",
                                "evidence_ids": [],
                            }
                        )
            if locked_quote and not blueprint_policy.get("allow_price_anchor"):
                amounts = set(re.findall(r"\d+(?:\.\d+)?(?=\s*元)", locked_quote["original_content"].replace(",", "")))
                for block in draft.get("blueprint_content", {}).get("blocks", []):
                    if block["kind"] != "text":
                        continue
                    repeated = amounts & set(re.findall(r"\d+(?:\.\d+)?(?=\s*元)", block["text"].replace(",", "")))
                    if repeated:
                        report["checks"].append(
                            {
                                "code": "QUOTE_AMOUNT_REPEATED",
                                "level": "error",
                                "location": block["id"],
                                "message": "原创正文重复报价金额：" + "、".join(sorted(repeated)),
                                "evidence_ids": [],
                            }
                        )
            report["status"] = (
                "blocked"
                if any(c["level"] == "error" for c in report["checks"])
                else "warning"
                if report["checks"]
                else "passed"
            )
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
