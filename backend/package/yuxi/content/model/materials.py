"""标准化内容物料、需求清单、质量门和最终生产包。"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from yuxi.content.model.viral_assets import ReferenceCardV2
from yuxi.content.v3.title_formula_slots import process_title_options

MaterialType = Literal[
    "business_fact",
    "price_fact",
    "media_fact",
    "viral_reference",
    "business_rule",
    "compliance_rule",
    "style_reference",
]
MaterialSourceType = Literal[
    "manual_input",
    "business_record",
    "media",
    "knowledge_base",
    "human_confirmation",
    "external_api",
]
MaterialUsage = Literal["title", "body", "visual", "style_reference"]
MaterialRiskLevel = Literal["normal", "sensitive", "high_risk"]
MaterialVerifiedStatus = Literal["retrieved", "confirmed", "user_confirmed"]


class MaterialContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class MaterialSourceV2(MaterialContract):
    source_type: MaterialSourceType
    source_id: str = Field(min_length=1, max_length=255)
    source_version: str = Field(min_length=1, max_length=128)
    source_hash: str = Field(min_length=8, max_length=128)
    locator: str | None = Field(default=None, max_length=1000)


class MaterialGovernanceV2(MaterialContract):
    review_status: Literal["needs_review", "approved", "rejected", "invalidated"]
    verified_status: MaterialVerifiedStatus
    risk_level: MaterialRiskLevel = "normal"
    allowed_usage: tuple[MaterialUsage, ...] = Field(min_length=1)
    reviewed_by: str | None = Field(default=None, max_length=64)
    reviewed_at: datetime | None = None

    @model_validator(mode="after")
    def require_unique_usage(self):
        if len(self.allowed_usage) != len(set(self.allowed_usage)):
            raise ValueError("物料 allowed_usage 不能重复")
        return self


class MaterialScopeV2(MaterialContract):
    industry_slug: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=80)
    channel: str | None = Field(default=None, max_length=80)
    valid_from: datetime | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def require_valid_window(self):
        if self.valid_from and self.expires_at and self.valid_from >= self.expires_at:
            raise ValueError("物料有效期结束时间必须晚于开始时间")
        return self


class DerivedFactV1(MaterialContract):
    operation: Literal["multiply"]
    expression: str = Field(min_length=1, max_length=500)
    input_variable_codes: tuple[str, ...] = Field(min_length=2)
    disclaimer: str = Field(min_length=1, max_length=500)


class BusinessFactPayloadV2(MaterialContract):
    value: Any
    unit: str | None = Field(default=None, max_length=80)
    derivation: DerivedFactV1 | None = None

    @model_validator(mode="after")
    def require_non_empty_value(self):
        if self.value in (None, "", [], {}):
            raise ValueError("业务事实值不能为空")
        return self


class PriceFactPayloadV2(MaterialContract):
    quoted_value: str = Field(min_length=1, max_length=1000)
    amounts: tuple[float, ...] = Field(min_length=1)
    currency: Literal["CNY"] = "CNY"
    unit: str = Field(min_length=1, max_length=80)
    price_basis: Literal["standard_unit_price", "project_quote", "budget", "settlement"]
    scope: str = Field(min_length=1, max_length=500)
    city: str | None = Field(default=None, max_length=80)
    included_items: tuple[str, ...] = ()
    excluded_items: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_non_negative_amounts(self):
        if any(amount < 0 for amount in self.amounts):
            raise ValueError("报价金额不能为负数")
        return self


class MediaFactPayloadV2(BusinessFactPayloadV2):
    asset_id: str = Field(min_length=1, max_length=128)
    object_uri: str | None = Field(default=None, max_length=2000)


class ViralReferencePayloadV2(MaterialContract):
    reference_asset_id: str = Field(min_length=1, max_length=64)
    reference_card: ReferenceCardV2
    reference_blueprint: dict[str, Any] = Field(min_length=1)
    slot_mapping: dict[str, list[str]] = Field(default_factory=dict)


class RuleMaterialPayloadV2(MaterialContract):
    content: str = Field(min_length=1, max_length=20_000)
    integration_instruction: str | None = Field(default=None, max_length=2000)
    rule_kind: str | None = Field(default=None, max_length=120)


class StyleReferencePayloadV2(MaterialContract):
    content: str = Field(min_length=1, max_length=20_000)
    reference_role: str = Field(min_length=1, max_length=120)


MaterialPayloadV2 = (
    BusinessFactPayloadV2
    | PriceFactPayloadV2
    | MediaFactPayloadV2
    | ViralReferencePayloadV2
    | RuleMaterialPayloadV2
    | StyleReferencePayloadV2
)

_PAYLOAD_MODELS: dict[str, type[MaterialContract]] = {
    "business_fact": BusinessFactPayloadV2,
    "price_fact": PriceFactPayloadV2,
    "media_fact": MediaFactPayloadV2,
    "viral_reference": ViralReferencePayloadV2,
    "business_rule": RuleMaterialPayloadV2,
    "compliance_rule": RuleMaterialPayloadV2,
    "style_reference": StyleReferencePayloadV2,
}

_PRICE_VARIABLE_CODES = {
    "price",
    "unit_price",
    "budget",
    "cost",
    "labor_cost",
    "material_cost",
    "discount",
    "fee",
}

_FACT_BOUND_LEXICON_VARIABLE_CODES: dict[str, tuple[str, ...]] = {
    "title.positioning": ("product", "scene", "location"),
    "title.house_type": ("scene", "product"),
    "title.positive_result": ("result",),
    "body.old_house_pain": ("pain", "scene"),
    "body.renovation_advantage": ("advantages", "advantage", "result"),
    "persona.delivery_endorsement": ("persona_fact", "scene"),
    "persona.service_contrast": ("advantages", "advantage", "result"),
}

# 这些表达词在标准词库没有逐字命中时，可以直接使用对应的已审核事实原文。
# 回退变量比上面的宽松检索范围更窄，避免把普通场景误当痛点、把结果误当服务优势。
_FACT_BOUND_APPROVED_FACT_FALLBACK_VARIABLE_CODES: dict[str, tuple[str, ...]] = {
    "title.positive_result": ("result",),
    "body.old_house_pain": ("pain",),
    "body.renovation_advantage": ("advantages", "advantage"),
    "persona.delivery_endorsement": ("persona_fact", "scene"),
    "persona.service_contrast": ("advantages", "advantage"),
}

_TITLE_FORMULA_FACT_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "T01": ("result",),
    "FRT03": ("result",),
}

_BODY_FORMULA_FACT_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "C01": ("advantages",),
    "C02": ("pain", "advantages", "persona_fact"),
    "FRB01": ("advantages",),
    "FRB02": ("pain", "advantages", "persona_fact"),
    "FRB03": ("advantages",),
    "FRB09": ("advantages",),
}


class MaterialEnvelopeV2(MaterialContract):
    schema_version: Literal[2] = 2
    id: str = Field(min_length=1, max_length=64)
    material_type: MaterialType
    variable_codes: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    payload: MaterialPayloadV2
    source: MaterialSourceV2
    governance: MaterialGovernanceV2
    scope: MaterialScopeV2 = Field(default_factory=MaterialScopeV2)

    @model_validator(mode="before")
    @classmethod
    def parse_typed_payload(cls, value: Any):
        if not isinstance(value, dict):
            return value
        material_type = value.get("material_type")
        payload_model = _PAYLOAD_MODELS.get(str(material_type))
        if payload_model is not None and not isinstance(value.get("payload"), payload_model):
            value = dict(value)
            value["payload"] = payload_model.model_validate(value.get("payload") or {})
        return value

    @model_validator(mode="after")
    def validate_envelope(self):
        expected = _PAYLOAD_MODELS[self.material_type]
        if not isinstance(self.payload, expected):
            raise ValueError(f"{self.material_type} 的 payload 类型不正确")
        if len(self.variable_codes) != len(set(self.variable_codes)):
            raise ValueError("物料变量编码不能重复")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("物料 Evidence ID 不能重复")
        if self.material_type in {"viral_reference", "style_reference"} and self.variable_codes:
            raise ValueError("结构或风格参考不能声明业务事实变量")
        return self


class ProductionOrderV1(MaterialContract):
    schema_version: Literal[1] = 1
    task_id: str = Field(min_length=1, max_length=64)
    industry_slug: str = Field(min_length=1, max_length=80)
    content_type_code: Literal["CT01", "CT02", "CT03", "CT04", "CT05", "CT06", "CT07"]
    group_id: str = Field(min_length=1, max_length=64)
    rule_version_id: str = Field(min_length=1, max_length=64)
    policy_hash: str = Field(min_length=64, max_length=64)
    creation_method_codes: tuple[str, ...] = Field(min_length=1)
    title_formula_code: str = Field(min_length=1, max_length=32)
    body_formula_code: str = Field(min_length=1, max_length=32)
    selection_mode: Literal["fixed", "deterministic_policy", "operator_selected"]
    order_hash: str = Field(min_length=64, max_length=64)


class MaterialRequirementV1(MaterialContract):
    requirement_id: str = Field(min_length=1, max_length=128)
    variable_code: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    material_types: tuple[Literal["business_fact", "price_fact", "media_fact"], ...] = Field(min_length=1)
    value_type: str = Field(min_length=1, max_length=32)
    required: bool = True
    allowed_sources: tuple[MaterialSourceType, ...] = Field(min_length=1)
    allowed_usage: tuple[Literal["title", "body", "visual"], ...] = Field(min_length=1)
    review_policy: Literal["retrieved", "confirmed", "user_confirmed", "human_review"]
    risk_level: MaterialRiskLevel
    unit_schema: dict[str, Any] = Field(default_factory=dict)
    validation_schema: dict[str, Any] = Field(default_factory=dict)
    fallback_policy: Literal["block"] = "block"


class MaterialRequirementManifestV1(MaterialContract):
    schema_version: Literal[1] = 1
    order_hash: str = Field(min_length=64, max_length=64)
    content_type_code: Literal["CT01", "CT02", "CT03", "CT04", "CT05", "CT06", "CT07"]
    title_formula_code: str = Field(min_length=1)
    body_formula_code: str = Field(min_length=1)
    requirements: tuple[MaterialRequirementV1, ...]
    reference_required: bool = True
    manifest_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def require_unique_requirements(self):
        ids = [item.requirement_id for item in self.requirements]
        codes = [item.variable_code for item in self.requirements]
        if len(ids) != len(set(ids)) or len(codes) != len(set(codes)):
            raise ValueError("物料需求 ID 和变量编码必须唯一")
        return self


class MaterialBindingV1(MaterialContract):
    requirement_id: str
    material_ids: tuple[str, ...] = Field(min_length=1)


class MaterialGateIssueV1(MaterialContract):
    code: str
    message: str
    variable_code: str | None = None
    material_id: str | None = None


class MaterialQualityReportV1(MaterialContract):
    schema_version: Literal[1] = 1
    status: Literal["passed", "blocked"]
    manifest_hash: str = Field(min_length=64, max_length=64)
    materials_hash: str = Field(min_length=64, max_length=64)
    bindings: tuple[MaterialBindingV1, ...]
    missing_requirement_ids: tuple[str, ...]
    unapproved_variable_codes: tuple[str, ...]
    conflicting_variable_codes: tuple[str, ...]
    issues: tuple[MaterialGateIssueV1, ...]
    report_hash: str = Field(min_length=64, max_length=64)


class GenerationSlotV1(MaterialContract):
    """把冻结物料编译成生成阶段可逐项执行的写作槽位。"""

    slot_id: str = Field(min_length=1, max_length=120)
    target: Literal["title", "opening", "body", "closing", "topics", "global"]
    required: bool = True
    source_variable_codes: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    review_codes: tuple[str, ...] = ()
    instruction: str = Field(min_length=1, max_length=1000)
    acceptance: tuple[str, ...] = ()


class FrozenProductionPackV1(MaterialContract):
    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=64)
    task_id: str = Field(min_length=1, max_length=64)
    production_order: ProductionOrderV1
    material_manifest: MaterialRequirementManifestV1
    material_quality_report: MaterialQualityReportV1
    materials: tuple[MaterialEnvelopeV2, ...]
    strategy_snapshot: dict[str, Any] = Field(min_length=1)
    evidence_bundle_id: str = Field(min_length=1)
    evidence_bundle_version: int = Field(ge=1)
    evidence_bundle_hash: str = Field(min_length=64, max_length=64)
    formula_lexicon_bundle: dict[str, Any] = Field(min_length=1)
    formula_lexicon_bundle_hash: str = Field(min_length=64, max_length=64)
    reference_snapshot: dict[str, Any] = Field(min_length=1)
    expression_guidance: dict[str, Any] | None = None
    expression_policy: dict[str, Any] | None = None
    generation_slots: tuple[GenerationSlotV1, ...] = ()
    writing_request: str | None = Field(default=None, max_length=20_000)
    channel_profile: dict[str, Any]
    persona_profile: dict[str, Any]
    content_rule_bundle: dict[str, Any] = Field(min_length=1)
    compliance_policy_version_ids: tuple[str, ...] = ()
    production_pack_hash: str = Field(min_length=64, max_length=64)
    frozen_at: datetime

    @model_validator(mode="after")
    def verify_frozen_links_and_hash(self):
        if self.material_quality_report.status != "passed":
            raise ValueError("冻结生产包的物料质量报告必须通过")
        if self.material_manifest.order_hash != self.production_order.order_hash:
            raise ValueError("冻结生产包的订单与物料清单不一致")
        if self.material_quality_report.manifest_hash != self.material_manifest.manifest_hash:
            raise ValueError("冻结生产包的物料报告与清单不一致")
        materials_hash = _canonical_hash(
            [item.model_dump(mode="json") for item in sorted(self.materials, key=lambda item: item.id)]
        )
        if self.material_quality_report.materials_hash != materials_hash:
            raise ValueError("冻结生产包的物料与质量门快照不一致")
        if self.formula_lexicon_bundle.get("bundle_hash") != self.formula_lexicon_bundle_hash:
            raise ValueError("冻结生产包的公式词库 Hash 不一致")
        if (self.strategy_snapshot.get("title_formula") or {}).get("code") != self.production_order.title_formula_code:
            raise ValueError("冻结生产包的标题公式与生产订单不一致")
        if (self.strategy_snapshot.get("body_formula") or {}).get("code") != self.production_order.body_formula_code:
            raise ValueError("冻结生产包的正文公式与生产订单不一致")
        payload = self.model_dump(mode="json", exclude={"id", "production_pack_hash", "frozen_at"})
        # V1 生产包在 expression_policy 发布前没有该字段，继续允许历史任务
        # 校验原有 hash；新冻结的生产包始终写入非空策略并纳入 hash。
        if payload.get("expression_policy") is None:
            payload.pop("expression_policy", None)
        # 槽位契约从当前版本开始写入；历史生产包没有该字段时继续按旧 Hash 验证。
        if not self.generation_slots:
            payload.pop("generation_slots", None)
        if _canonical_hash(payload) != self.production_pack_hash:
            raise ValueError("冻结生产包 Hash 不一致")
        return self


def _canonical_hash(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def create_production_order(
    *,
    task_id: str,
    catalog: dict[str, Any],
    group_id: str,
    creation_method_codes: list[str] | tuple[str, ...],
    title_formula_code: str,
    body_formula_code: str,
) -> ProductionOrderV1:
    payload = {
        "schema_version": 1,
        "task_id": task_id,
        "industry_slug": catalog["industry_slug"],
        "content_type_code": catalog["direction_code"],
        "group_id": group_id,
        "rule_version_id": catalog["rule_version_id"],
        "policy_hash": catalog["policy_hash"],
        "creation_method_codes": list(creation_method_codes),
        "title_formula_code": title_formula_code,
        "body_formula_code": body_formula_code,
        "selection_mode": "fixed",
    }
    return ProductionOrderV1.model_validate({**payload, "order_hash": _canonical_hash(payload)})


def build_material_manifest(*, catalog: dict[str, Any], order: ProductionOrderV1) -> MaterialRequirementManifestV1:
    if order.rule_version_id != catalog.get("rule_version_id") or order.content_type_code != catalog.get(
        "direction_code"
    ):
        raise ValueError("生产订单与规则目录不一致")
    rules = [
        item
        for item in catalog.get("source_rules") or []
        if order.content_type_code in (item.get("content_type_codes") or [])
    ]
    if len(rules) != 1 or str(rules[0].get("id") or rules[0].get("code")) != order.group_id:
        raise ValueError("生产订单必须对应唯一组合规则")
    rule = rules[0]
    title = next(
        (item for item in catalog.get("title_formulas") or [] if item.get("code") == order.title_formula_code),
        None,
    )
    body = next(
        (item for item in catalog.get("content_formulas") or [] if item.get("code") == order.body_formula_code),
        None,
    )
    method_by_code = {item.get("code"): item for item in catalog.get("methods") or []}
    if title is None or body is None or any(code not in method_by_code for code in order.creation_method_codes):
        raise ValueError("生产订单引用了不存在或已停用的公式/手法")

    definitions = {item.get("code"): item for item in catalog.get("variables") or []}
    required_codes = set(rule.get("required_variable_codes") or [])
    title_slots = (title.get("source_content") or {}).get("slot_schema") or []
    title_alternative_groups: dict[str, set[str]] = {}
    if title_slots:
        grouped_title_codes: set[str] = set()
        for slot in title_slots:
            group_code = f"title:{slot.get('code')}"
            for code in slot.get("variable_codes") or []:
                normalized_code = str(code)
                grouped_title_codes.add(normalized_code)
                title_alternative_groups.setdefault(normalized_code, set()).add(group_code)
        required_codes.update(set(title.get("variable_schema") or []) - grouped_title_codes)
    else:
        required_codes.update(title.get("variable_schema") or [])
    # Formula codes are scoped by industry packs.  The decoration pack owns the
    # extra fact requirements below; a different pack may legitimately reuse a
    # code such as T01 with another schema.
    if catalog.get("industry_slug") == "decoration":
        required_codes.update(_TITLE_FORMULA_FACT_REQUIREMENTS.get(order.title_formula_code, ()))
        required_codes.update(_BODY_FORMULA_FACT_REQUIREMENTS.get(order.body_formula_code, ()))
    required_codes.update(body.get("required_variables") or [])
    for code in order.creation_method_codes:
        required_codes.update(method_by_code[code].get("variable_schema") or [])

    # 工长内容的人设不是生成后的润色项，而是进入生产线前必须准备好的事实。
    # persona_fact 锁定“我是谁/凭什么可信”；process 或优势事实锁定“我怎么做事”。
    # 仅对行业包实际发布的变量生效，避免把通用测试包或其他行业强行扩展。
    persona_alternative_groups: dict[str, set[str]] = {}
    if catalog.get("industry_slug") == "decoration" and "persona_fact" in definitions:
        required_codes.add("persona_fact")
        for code in ("process", "advantages", "advantage"):
            if code in definitions:
                persona_alternative_groups.setdefault(code, set()).add("persona:value")

    material_codes = required_codes | set(title_alternative_groups) | set(persona_alternative_groups)
    unknown = sorted(material_codes - set(definitions))
    if unknown:
        raise ValueError("生产物料清单引用未发布变量：" + "、".join(unknown))

    requirements: list[dict[str, Any]] = []
    for code in sorted(material_codes):
        definition = definitions[code]
        value_type = str(definition.get("value_type") or "string")
        sensitivity = str(definition.get("sensitivity") or "normal")
        evidence_policy = definition.get("evidence_policy") or {}
        usages = []
        for usage in definition.get("allowed_usages") or ["title", "body"]:
            normalized = "visual" if usage == "media" else usage
            if normalized in {"title", "body", "visual"} and normalized not in usages:
                usages.append(normalized)
        if not usages:
            usages = ["body"]
        allowed_sources = tuple(
            evidence_policy.get("allowed_sources")
            or ("manual_input", "business_record", "media", "knowledge_base", "human_confirmation", "external_api")
        )
        risk_level = sensitivity if sensitivity in {"normal", "sensitive", "high_risk"} else "normal"
        configured_review_policy = evidence_policy.get("review_policy")
        review_policy = (
            configured_review_policy
            if configured_review_policy in {"retrieved", "confirmed", "user_confirmed", "human_review"}
            else "human_review"
            if risk_level == "high_risk" or evidence_policy.get("required") is True
            else "retrieved"
        )
        unit_schema = definition.get("unit_schema") or {}
        if catalog.get("industry_slug") == "decoration" and code == "quantity" and not unit_schema:
            unit_schema = {"required": True, "allowed_units": ["㎡"]}
        requirements.append(
            {
                "requirement_id": f"variable:{code}",
                "variable_code": code,
                "material_types": (
                    ["price_fact"]
                    if value_type == "money" or code in _PRICE_VARIABLE_CODES
                    else ["business_fact", "media_fact", "price_fact"]
                    if code == "quote_type"
                    else ["business_fact", "media_fact"]
                ),
                "value_type": value_type,
                "required": code in required_codes,
                "allowed_sources": allowed_sources,
                "allowed_usage": usages,
                "review_policy": review_policy,
                "risk_level": risk_level,
                "unit_schema": unit_schema,
                "validation_schema": {
                    **(definition.get("validation_schema") or {}),
                    **(
                        {
                            "alternative_groups": sorted(
                                title_alternative_groups.get(code, set()) | persona_alternative_groups.get(code, set())
                            )
                        }
                        if title_alternative_groups.get(code) or persona_alternative_groups.get(code)
                        else {}
                    ),
                },
                "fallback_policy": "block",
            }
        )
    if (body.get("output_schema") or {}).get("deterministic_calculation_required") is True:
        requirements.append(
            {
                "requirement_id": "derived:calculated_total",
                "variable_code": "calculated_total",
                "material_types": ["business_fact"],
                "value_type": "number",
                "required": True,
                "allowed_sources": ["human_confirmation"],
                "allowed_usage": ["body"],
                "review_policy": "user_confirmed",
                "risk_level": "high_risk",
                "unit_schema": {"required": True, "allowed_units": ["元"]},
                "validation_schema": {"minimum": 0},
                "fallback_policy": "block",
            }
        )
    locked_quote_block = (body.get("output_schema") or {}).get("locked_quote_block_required") is True
    if order.body_formula_code == "FRB08" and not locked_quote_block:
        requirements.append(
            {
                "requirement_id": "formula:trade_breakdown",
                "variable_code": "trade_breakdown",
                "material_types": ["business_fact"],
                "value_type": "list",
                "required": True,
                "allowed_sources": ["manual_input", "business_record", "human_confirmation"],
                "allowed_usage": ["body"],
                "review_policy": "user_confirmed",
                "risk_level": "high_risk",
                "unit_schema": {},
                "validation_schema": {"minItems": 2},
                "fallback_policy": "block",
            }
        )
    if order.body_formula_code == "FRB09" and not locked_quote_block:
        requirements.append(
            {
                "requirement_id": "formula:labor_aux_breakdown",
                "variable_code": "labor_aux_breakdown",
                "material_types": ["business_fact"],
                "value_type": "object",
                "required": True,
                "allowed_sources": ["manual_input", "business_record", "human_confirmation"],
                "allowed_usage": ["body"],
                "review_policy": "user_confirmed",
                "risk_level": "high_risk",
                "unit_schema": {},
                "validation_schema": {},
                "fallback_policy": "block",
            }
        )
    payload = {
        "schema_version": 1,
        "order_hash": order.order_hash,
        "content_type_code": order.content_type_code,
        "title_formula_code": order.title_formula_code,
        "body_formula_code": order.body_formula_code,
        "requirements": requirements,
        "reference_required": True,
    }
    return MaterialRequirementManifestV1.model_validate({**payload, "manifest_hash": _canonical_hash(payload)})


def _material_variable_value(material: MaterialEnvelopeV2, variable_code: str) -> Any:
    del variable_code
    payload = material.payload
    if isinstance(payload, (BusinessFactPayloadV2, MediaFactPayloadV2)):
        return payload.value
    if isinstance(payload, PriceFactPayloadV2):
        return {
            "quoted_value": payload.quoted_value,
            "amounts": payload.amounts,
            "currency": payload.currency,
            "unit": payload.unit,
            "price_basis": payload.price_basis,
            "scope": payload.scope,
            "city": payload.city,
        }
    return payload.model_dump(mode="json")


def _normalized_fact_value(value: Any, value_type: str) -> Any:
    if value_type == "list":
        return value if isinstance(value, list) else [value]
    if value_type in {"number", "integer"} and isinstance(value, str):
        matched = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if matched:
            number = float(matched.group())
            return int(number) if value_type == "integer" and number.is_integer() else number
    return value


def _material_value_error(material: MaterialEnvelopeV2, requirement: MaterialRequirementV1) -> str | None:
    payload = material.payload
    if isinstance(payload, PriceFactPayloadV2):
        value: Any = (
            payload.price_basis
            if requirement.variable_code == "quote_type"
            else payload.amounts[0]
            if len(payload.amounts) == 1
            else list(payload.amounts)
        )
        unit = payload.unit
    elif isinstance(payload, (BusinessFactPayloadV2, MediaFactPayloadV2)):
        value = payload.value
        unit = payload.unit
    else:
        return "物料类型不能用于变量绑定"

    value_type = requirement.value_type
    valid_type = True
    if value_type in {"string", "duration"}:
        valid_type = isinstance(value, str) and bool(value.strip())
    elif value_type == "list":
        valid_type = isinstance(value, (list, tuple)) and bool(value)
    elif value_type == "number":
        valid_type = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif value_type == "integer":
        valid_type = isinstance(value, int) and not isinstance(value, bool)
    elif value_type == "boolean":
        valid_type = isinstance(value, bool)
    elif value_type == "object":
        valid_type = isinstance(value, dict) and bool(value)
    elif value_type == "money":
        valid_type = isinstance(payload, PriceFactPayloadV2) and bool(payload.amounts)
    if not valid_type:
        return f"值不符合 {value_type} 类型"

    unit_schema = requirement.unit_schema
    if unit_schema.get("required") is True and not unit:
        return "缺少必需单位"
    allowed_units = unit_schema.get("allowed_units") or unit_schema.get("units") or unit_schema.get("enum") or []
    if unit and allowed_units and unit not in allowed_units:
        return f"单位 {unit} 不在允许范围"

    schema = requirement.validation_schema
    if "enum" in schema and value not in schema["enum"]:
        return "值不在允许枚举范围"
    if isinstance(value, str):
        if isinstance(schema.get("minLength"), int) and len(value) < schema["minLength"]:
            return "文本短于最小长度"
        if isinstance(schema.get("maxLength"), int) and len(value) > schema["maxLength"]:
            return "文本超过最大长度"
        if schema.get("pattern") and re.fullmatch(str(schema["pattern"]), value) is None:
            return "文本不符合格式规则"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(schema.get("minimum"), (int, float)) and value < schema["minimum"]:
            return "数值低于最小值"
        if isinstance(schema.get("maximum"), (int, float)) and value > schema["maximum"]:
            return "数值超过最大值"
    if isinstance(value, (list, tuple)):
        if isinstance(schema.get("minItems"), int) and len(value) < schema["minItems"]:
            return "列表少于最小项数"
        if isinstance(schema.get("maxItems"), int) and len(value) > schema["maxItems"]:
            return "列表超过最大项数"
    if requirement.variable_code == "trade_breakdown":
        if not isinstance(value, (list, tuple)):
            return "工种报价必须是列表"
        trade_names: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                return "每条工种报价必须是对象"
            trade = str(item.get("trade") or "").strip()
            amount = item.get("amount")
            unit = str(item.get("unit") or "").strip()
            included_items = item.get("included_items")
            if (
                not trade
                or not isinstance(amount, (int, float))
                or isinstance(amount, bool)
                or amount <= 0
                or unit != "元"
            ):
                return "每条工种报价必须包含工种名称、正数金额和单位“元”"
            if (
                not isinstance(included_items, list)
                or not included_items
                or any(not str(included).strip() for included in included_items)
            ):
                return "每条工种报价必须包含至少一个包含项"
            trade_names.append(trade)
        if len(trade_names) != len(set(trade_names)):
            return "工种名称不能重复"
    if requirement.variable_code == "labor_aux_breakdown":
        if not isinstance(value, dict):
            return "人工辅材拆分必须是对象"
        labor_total = value.get("labor_total")
        auxiliary_total = value.get("auxiliary_total")
        breakdown_unit = str(value.get("unit") or "").strip()
        trades = value.get("trades")
        if (
            not isinstance(labor_total, (int, float))
            or isinstance(labor_total, bool)
            or labor_total <= 0
            or not isinstance(auxiliary_total, (int, float))
            or isinstance(auxiliary_total, bool)
            or auxiliary_total <= 0
            or breakdown_unit != "元"
        ):
            return "人工合计、辅材合计必须是正数且单位为“元”"
        if not isinstance(trades, list) or len(trades) < 2:
            return "人工辅材拆分至少需要两个工种"
        trade_names: list[str] = []
        labor_sum = 0.0
        auxiliary_sum = 0.0
        for item in trades:
            if not isinstance(item, dict):
                return "每条人工辅材工种拆分必须是对象"
            trade = str(item.get("trade") or "").strip()
            labor_amount = item.get("labor_amount")
            auxiliary_amount = item.get("auxiliary_amount")
            included_items = item.get("included_items")
            if (
                not trade
                or not isinstance(labor_amount, (int, float))
                or isinstance(labor_amount, bool)
                or labor_amount < 0
                or not isinstance(auxiliary_amount, (int, float))
                or isinstance(auxiliary_amount, bool)
                or auxiliary_amount < 0
                or labor_amount + auxiliary_amount <= 0
            ):
                return "每条工种拆分必须包含工种名称及非负人工、辅材金额"
            if (
                not isinstance(included_items, list)
                or not included_items
                or any(not str(included).strip() for included in included_items)
            ):
                return "每条人工辅材工种拆分必须包含至少一个施工范围"
            trade_names.append(trade)
            labor_sum += float(labor_amount)
            auxiliary_sum += float(auxiliary_amount)
        if len(trade_names) != len(set(trade_names)):
            return "人工辅材拆分的工种名称不能重复"
        if abs(labor_sum - float(labor_total)) >= 0.01 or abs(auxiliary_sum - float(auxiliary_total)) >= 0.01:
            return "各工种人工、辅材金额合计必须分别等于人工合计和辅材合计"
    return None


def standardize_evidence_materials(
    *,
    evidence_bundle: dict[str, Any],
    manifest: MaterialRequirementManifestV1,
    reference_snapshot: dict[str, Any] | None = None,
) -> tuple[MaterialEnvelopeV2, ...]:
    """把冻结 Evidence 转换为统一强类型物料；原始资料不得直接进入生成输入。"""

    requirements = {item.variable_code: item for item in manifest.requirements}
    evidence_items = [
        item
        for item in evidence_bundle.get("items") or []
        if isinstance(item, dict) and item.get("verified_status") != "rejected"
    ]
    materials: list[MaterialEnvelopeV2] = []

    price_items = [
        item
        for item in evidence_items
        if set(str(code) for code in item.get("variable_codes") or []) & _PRICE_VARIABLE_CODES & set(requirements)
    ]
    amount_items = list(price_items)
    if amount_items:
        values = [str(item.get("value") or "").strip() for item in price_items if str(item.get("value") or "").strip()]
        amounts = [
            float(value.replace(",", ""))
            for item in amount_items
            for value in re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", str(item.get("value") or ""))
        ]
        metadata = next((item.get("metadata") or {} for item in amount_items if item.get("metadata")), {})
        unit = str(metadata.get("unit") or "").strip()
        if not unit:
            for value in values:
                match = re.search(r"元(?:/[A-Za-z0-9\u4e00-\u9fff㎡²]+)?", value)
                if match:
                    unit = match.group()
                    break
        quote_text = " ".join(
            str(item.get("value") or "")
            for item in evidence_items
            if "quote_type" in set(item.get("variable_codes") or [])
        )
        basis = str(metadata.get("price_basis") or "")
        if basis not in {"standard_unit_price", "project_quote", "budget", "settlement"}:
            if "标准" in quote_text or "单价" in quote_text:
                basis = "standard_unit_price"
            elif "预算" in quote_text:
                basis = "budget"
            elif "结算" in quote_text:
                basis = "settlement"
            else:
                basis = "project_quote"
        product = next(
            (
                str(item.get("value") or "").strip()
                for item in evidence_items
                if "product" in set(item.get("variable_codes") or []) and str(item.get("value") or "").strip()
            ),
            "",
        )
        source_hash = _canonical_hash(
            [
                [item.get("id"), item.get("source_hash"), item.get("value")]
                for item in sorted(price_items, key=lambda value: str(value.get("id") or ""))
            ]
        )
        verified_status = (
            "user_confirmed"
            if all(item.get("verified_status") == "user_confirmed" for item in price_items)
            else "confirmed"
            if all(item.get("verified_status") in {"confirmed", "user_confirmed"} for item in price_items)
            else "retrieved"
        )
        material_codes = tuple(
            sorted(
                {
                    str(code)
                    for item in price_items
                    for code in item.get("variable_codes") or []
                    if str(code) in requirements
                }
            )
        )
        allowed_usage = tuple(
            sorted(
                {
                    str(usage)
                    for item in price_items
                    for usage in item.get("allowed_usage") or []
                    if usage in {"title", "body", "visual"}
                }
            )
        ) or ("body",)
        materials.append(
            MaterialEnvelopeV2.model_validate(
                {
                    "schema_version": 2,
                    "id": f"mat_{source_hash[:24]}",
                    "material_type": "price_fact",
                    "variable_codes": material_codes,
                    "evidence_ids": sorted(item_id for item in price_items if (item_id := str(item.get("id") or ""))),
                    "payload": {
                        "quoted_value": "；".join(values),
                        "amounts": amounts,
                        "currency": str(metadata.get("currency") or "CNY"),
                        "unit": unit,
                        "price_basis": basis,
                        "scope": str(metadata.get("scope") or product or "本次用户确认报价"),
                        "city": metadata.get("city"),
                        "included_items": metadata.get("included_items") or [],
                        "excluded_items": metadata.get("excluded_items") or [],
                    },
                    "source": {
                        "source_type": "human_confirmation" if len(price_items) > 1 else price_items[0]["source_type"],
                        "source_id": f"bundle:{evidence_bundle.get('id') or evidence_bundle.get('bundle_hash')}:price",
                        "source_version": str(evidence_bundle.get("version") or "1"),
                        "source_hash": source_hash,
                        "locator": ",".join(str(item.get("id") or "") for item in price_items),
                    },
                    "governance": {
                        "review_status": (
                            "approved" if verified_status in {"confirmed", "user_confirmed"} else "needs_review"
                        ),
                        "verified_status": verified_status,
                        "risk_level": "high_risk",
                        "allowed_usage": allowed_usage,
                    },
                    "scope": {"city": metadata.get("city")},
                }
            )
        )

    price_item_ids = {str(item.get("id") or "") for item in price_items}
    for item in evidence_items:
        item_id = str(item.get("id") or "")
        metadata = item.get("metadata") or {}
        if item_id in price_item_ids:
            continue
        variable_codes = tuple(
            sorted(str(code) for code in item.get("variable_codes") or [] if str(code) in requirements)
        )
        if variable_codes:
            requirement = requirements[variable_codes[0]]
            source_type = str(item.get("source_type") or "")
            material_type = "media_fact" if source_type == "media" else "business_fact"
            normalized_value = _normalized_fact_value(item.get("value"), requirement.value_type)
            unit = str(metadata.get("unit") or "").strip() or None
            if unit is None and isinstance(item.get("value"), str):
                unit_match = re.search(r"(?:元/㎡|元|万元|平方米|平米|㎡|m²|平|天|周|月|年|个|次|%)", item["value"])
                unit = unit_match.group() if unit_match else None
            if unit in {"平方米", "平米", "m²", "平"}:
                unit = "㎡"
            payload: dict[str, Any] = {
                "value": normalized_value,
                "unit": unit,
                "derivation": metadata.get("derivation"),
            }
            if material_type == "media_fact":
                payload.update(
                    asset_id=str(item.get("source_id") or item_id),
                    object_uri=metadata.get("object_uri"),
                )
            verified = str(item.get("verified_status") or "retrieved")
            risk = (
                "high_risk"
                if any(requirements[code].risk_level == "high_risk" for code in variable_codes)
                else str(item.get("risk_level") or "normal")
            )
            materials.append(
                MaterialEnvelopeV2.model_validate(
                    {
                        "schema_version": 2,
                        "id": f"mat_{item_id}"[:64],
                        "material_type": material_type,
                        "variable_codes": variable_codes,
                        "evidence_ids": [item_id],
                        "payload": payload,
                        "source": {
                            "source_type": source_type,
                            "source_id": str(item.get("source_id") or item_id),
                            "source_version": str(item.get("source_version") or "unknown"),
                            "source_hash": str(item.get("source_hash") or _canonical_hash(item.get("value"))),
                            "locator": metadata.get("locator"),
                        },
                        "governance": {
                            "review_status": "approved",
                            "verified_status": verified,
                            "risk_level": risk,
                            "allowed_usage": [
                                usage
                                for usage in item.get("allowed_usage") or []
                                if usage in {"title", "body", "visual", "style_reference"}
                            ],
                        },
                        "scope": {
                            "industry_slug": metadata.get("industry_slug"),
                            "city": metadata.get("city"),
                            "channel": metadata.get("channel"),
                        },
                    }
                )
            )
            continue

        if metadata.get("material_type") == "viral_example" and metadata.get("selected_reference") is True:
            if reference_snapshot is None:
                raise ValueError("选中的爆款 Evidence 缺少冻结参考快照")
            materials.append(
                MaterialEnvelopeV2.model_validate(
                    {
                        "schema_version": 2,
                        "id": f"mat_{item_id}"[:64],
                        "material_type": "viral_reference",
                        "variable_codes": [],
                        "evidence_ids": [item_id],
                        "payload": {
                            "reference_asset_id": reference_snapshot["id"],
                            "reference_card": reference_snapshot["reference_card"],
                            "reference_blueprint": reference_snapshot["reference_blueprint"],
                            "slot_mapping": reference_snapshot.get("slot_mapping") or {},
                        },
                        "source": {
                            "source_type": "knowledge_base",
                            "source_id": str(item.get("source_id") or reference_snapshot["id"]),
                            "source_version": str(item.get("source_version") or reference_snapshot["source_hash"]),
                            "source_hash": reference_snapshot["source_hash"],
                            "locator": reference_snapshot.get("locator"),
                        },
                        "governance": {
                            "review_status": "approved",
                            "verified_status": "confirmed",
                            "risk_level": "normal",
                            "allowed_usage": ["style_reference"],
                        },
                    }
                )
            )
        elif metadata.get("material_type") in {"platform_rule", "compliance_rule", "forbidden_terms"}:
            content = item.get("value")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, sort_keys=True)
            materials.append(
                MaterialEnvelopeV2.model_validate(
                    {
                        "schema_version": 2,
                        "id": f"mat_{item_id}"[:64],
                        "material_type": "compliance_rule",
                        "variable_codes": [],
                        "evidence_ids": [item_id],
                        "payload": {
                            "content": content,
                            "integration_instruction": metadata.get("integration_instruction"),
                            "rule_kind": metadata.get("rule_kind"),
                        },
                        "source": {
                            "source_type": str(item.get("source_type") or "knowledge_base"),
                            "source_id": str(item.get("source_id") or item_id),
                            "source_version": str(item.get("source_version") or "unknown"),
                            "source_hash": str(item.get("source_hash") or _canonical_hash(item.get("value"))),
                        },
                        "governance": {
                            "review_status": "approved",
                            "verified_status": str(item.get("verified_status") or "retrieved"),
                            "risk_level": str(item.get("risk_level") or "sensitive"),
                            "allowed_usage": [
                                usage
                                for usage in item.get("allowed_usage") or ["title", "body"]
                                if usage in {"title", "body", "visual", "style_reference"}
                            ],
                        },
                    }
                )
            )
        elif metadata.get("material_type") and set(item.get("allowed_usage") or []) & {"title", "body"}:
            content = item.get("value")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, sort_keys=True)
            materials.append(
                MaterialEnvelopeV2.model_validate(
                    {
                        "schema_version": 2,
                        "id": f"mat_{item_id}"[:64],
                        "material_type": "business_rule",
                        "variable_codes": [],
                        "evidence_ids": [item_id],
                        "payload": {
                            "content": content,
                            "integration_instruction": metadata.get("integration_instruction"),
                            "rule_kind": metadata.get("material_type"),
                        },
                        "source": {
                            "source_type": str(item.get("source_type") or "knowledge_base"),
                            "source_id": str(item.get("source_id") or item_id),
                            "source_version": str(item.get("source_version") or "unknown"),
                            "source_hash": str(item.get("source_hash") or _canonical_hash(item.get("value"))),
                        },
                        "governance": {
                            "review_status": "approved",
                            "verified_status": str(item.get("verified_status") or "retrieved"),
                            "risk_level": str(item.get("risk_level") or "normal"),
                            "allowed_usage": [
                                usage
                                for usage in item.get("allowed_usage") or []
                                if usage in {"title", "body", "visual", "style_reference"}
                            ],
                        },
                    }
                )
            )
        elif "style_reference" in set(item.get("allowed_usage") or []):
            materials.append(
                MaterialEnvelopeV2.model_validate(
                    {
                        "schema_version": 2,
                        "id": f"mat_{item_id}"[:64],
                        "material_type": "style_reference",
                        "variable_codes": [],
                        "evidence_ids": [item_id],
                        "payload": {
                            "content": str(item.get("value") or ""),
                            "reference_role": str(metadata.get("role") or metadata.get("material_type") or "style"),
                        },
                        "source": {
                            "source_type": str(item.get("source_type") or "knowledge_base"),
                            "source_id": str(item.get("source_id") or item_id),
                            "source_version": str(item.get("source_version") or "unknown"),
                            "source_hash": str(item.get("source_hash") or _canonical_hash(item.get("value"))),
                        },
                        "governance": {
                            "review_status": "approved",
                            "verified_status": str(item.get("verified_status") or "retrieved"),
                            "risk_level": str(item.get("risk_level") or "normal"),
                            "allowed_usage": ["style_reference"],
                        },
                    }
                )
            )
    return tuple(sorted(materials, key=lambda item: item.id))


def validate_material_gate(
    *,
    manifest: MaterialRequirementManifestV1,
    materials: list[MaterialEnvelopeV2] | tuple[MaterialEnvelopeV2, ...],
    now: datetime | None = None,
) -> MaterialQualityReportV1:
    checked_at = now or datetime.now(UTC)
    normalized = [
        item if isinstance(item, MaterialEnvelopeV2) else MaterialEnvelopeV2.model_validate(item) for item in materials
    ]
    ids = [item.id for item in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("标准物料 ID 不能重复")
    bindings: list[dict[str, Any]] = []
    missing: list[str] = []
    unapproved: set[str] = set()
    conflicts: set[str] = set()
    issues: list[dict[str, Any]] = []

    known_codes = {item.variable_code for item in manifest.requirements}
    for material in normalized:
        for code in set(material.variable_codes) - known_codes:
            issues.append(
                {
                    "code": "MATERIAL_VARIABLE_UNKNOWN",
                    "message": f"物料 {material.id} 引用了清单外变量 {code}",
                    "variable_code": code,
                    "material_id": material.id,
                }
            )

    for requirement in manifest.requirements:
        candidates = [
            material
            for material in normalized
            if requirement.variable_code in material.variable_codes
            and material.material_type in requirement.material_types
            and material.source.source_type in requirement.allowed_sources
            and set(material.governance.allowed_usage) & set(requirement.allowed_usage)
        ]
        if not candidates:
            if requirement.required:
                missing.append(requirement.requirement_id)
                issues.append(
                    {
                        "code": "MANIFEST_REQUIRED_SLOT_MISSING",
                        "message": f"缺少变量 {requirement.variable_code} 的标准物料",
                        "variable_code": requirement.variable_code,
                    }
                )
            continue
        approved = []
        for material in candidates:
            value_error = _material_value_error(material, requirement)
            if value_error:
                issues.append(
                    {
                        "code": "MATERIAL_VALUE_INVALID",
                        "message": f"变量 {requirement.variable_code} 的物料值无效：{value_error}",
                        "variable_code": requirement.variable_code,
                        "material_id": material.id,
                    }
                )
                continue
            expired = material.scope.expires_at is not None and material.scope.expires_at <= checked_at
            not_started = material.scope.valid_from is not None and material.scope.valid_from > checked_at
            verified = material.governance.verified_status
            verification_allowed = verified in {"confirmed", "user_confirmed"}
            if requirement.review_policy == "retrieved":
                verification_allowed = True
            elif requirement.review_policy == "confirmed":
                verification_allowed = verified in {"confirmed", "user_confirmed"}
            elif requirement.review_policy == "user_confirmed":
                verification_allowed = verified == "user_confirmed"
            if material.governance.review_status != "approved" or expired or not_started or not verification_allowed:
                unapproved.add(requirement.variable_code)
                issues.append(
                    {
                        "code": "MATERIAL_REVIEW_REQUIRED"
                        if not expired and not not_started
                        else "MATERIAL_SOURCE_STALE",
                        "message": f"变量 {requirement.variable_code} 的物料尚未通过审核或已失效",
                        "variable_code": requirement.variable_code,
                        "material_id": material.id,
                    }
                )
                continue
            if requirement.risk_level == "high_risk" and material.governance.risk_level != "high_risk":
                unapproved.add(requirement.variable_code)
                issues.append(
                    {
                        "code": "MATERIAL_RISK_INVALID",
                        "message": f"变量 {requirement.variable_code} 必须使用高风险治理物料",
                        "variable_code": requirement.variable_code,
                        "material_id": material.id,
                    }
                )
                continue
            approved.append(material)
        if not approved:
            continue
        verification_rank = {"retrieved": 0, "confirmed": 1, "user_confirmed": 2}
        highest_rank = max(verification_rank[item.governance.verified_status] for item in approved)
        approved = [item for item in approved if verification_rank[item.governance.verified_status] == highest_rank]
        canonical_values = {
            json.dumps(
                _material_variable_value(material, requirement.variable_code),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            for material in approved
        }
        # List variables are additive collections. Multiple approved sources
        # (for example separate knowledge-base chunks for ``advantages``) are
        # all valid bindings; scalar/object variables must still agree.
        if requirement.value_type != "list" and len(canonical_values) > 1:
            conflicts.add(requirement.variable_code)
            issues.append(
                {
                    "code": "MATERIAL_CONFLICT",
                    "message": f"变量 {requirement.variable_code} 存在多个不一致的已审核值",
                    "variable_code": requirement.variable_code,
                }
            )
            continue
        bindings.append(
            {
                "requirement_id": requirement.requirement_id,
                "material_ids": sorted(material.id for material in approved),
            }
        )

    bound_requirement_ids = {item["requirement_id"] for item in bindings}
    alternative_groups = {
        group
        for requirement in manifest.requirements
        for group in requirement.validation_schema.get("alternative_groups") or []
    }
    for group in sorted(alternative_groups):
        members = [
            requirement
            for requirement in manifest.requirements
            if group in (requirement.validation_schema.get("alternative_groups") or [])
        ]
        if any(requirement.requirement_id in bound_requirement_ids for requirement in members):
            continue
        missing.append(group)
        labels = "/".join(requirement.variable_code for requirement in members)
        group_label = group.removeprefix("title:").removeprefix("persona:")
        group_kind = "标题槽位" if group.startswith("title:") else "人设价值槽位"
        issues.append(
            {
                "code": "MANIFEST_ALTERNATIVE_GROUP_MISSING",
                "message": f"{group_kind} {group_label} 至少需要一项已审核物料：{labels}",
            }
        )

    trade_binding = next((item for item in bindings if item["requirement_id"] == "formula:trade_breakdown"), None)
    if trade_binding:
        bound_trade_material = next(
            material for material in normalized if material.id == trade_binding["material_ids"][0]
        )
        trade_value = bound_trade_material.payload.value
        trade_total = sum(float(item["amount"]) for item in trade_value)
        project_totals = [
            float(amount)
            for material in normalized
            if material.material_type == "price_fact"
            and "price" in material.variable_codes
            and material.governance.review_status == "approved"
            and material.payload.price_basis == "project_quote"
            for amount in material.payload.amounts
        ]
        if not project_totals or not any(abs(trade_total - total) < 0.01 for total in project_totals):
            conflicts.add("trade_breakdown")
            issues.append(
                {
                    "code": "TRADE_BREAKDOWN_SUM_MISMATCH",
                    "message": f"工种分项合计 {trade_total:g} 元与已确认项目总价不一致",
                    "variable_code": "trade_breakdown",
                    "material_id": bound_trade_material.id,
                }
            )

    labor_aux_binding = next(
        (item for item in bindings if item["requirement_id"] == "formula:labor_aux_breakdown"),
        None,
    )
    if labor_aux_binding:
        bound_breakdown = next(
            material for material in normalized if material.id == labor_aux_binding["material_ids"][0]
        )
        breakdown_value = bound_breakdown.payload.value
        breakdown_total = float(breakdown_value["labor_total"]) + float(breakdown_value["auxiliary_total"])
        project_totals = [
            float(amount)
            for material in normalized
            if material.material_type == "price_fact"
            and "price" in material.variable_codes
            and material.governance.review_status == "approved"
            and material.payload.price_basis == "project_quote"
            for amount in material.payload.amounts
        ]
        if not project_totals or not any(abs(breakdown_total - total) < 0.01 for total in project_totals):
            conflicts.add("labor_aux_breakdown")
            issues.append(
                {
                    "code": "LABOR_AUX_TOTAL_MISMATCH",
                    "message": f"人工辅材合计 {breakdown_total:g} 元与已确认项目总价不一致",
                    "variable_code": "labor_aux_breakdown",
                    "material_id": bound_breakdown.id,
                }
            )

    blocked = bool(missing or unapproved or conflicts or issues)
    payload = {
        "schema_version": 1,
        "status": "blocked" if blocked else "passed",
        "manifest_hash": manifest.manifest_hash,
        "materials_hash": _canonical_hash(
            [item.model_dump(mode="json") for item in sorted(normalized, key=lambda item: item.id)]
        ),
        "bindings": sorted(bindings, key=lambda item: item["requirement_id"]),
        "missing_requirement_ids": sorted(missing),
        "unapproved_variable_codes": sorted(unapproved),
        "conflicting_variable_codes": sorted(conflicts),
        "issues": issues,
    }
    return MaterialQualityReportV1.model_validate({**payload, "report_hash": _canonical_hash(payload)})


def _fact_phrase_values(value: Any) -> list[str]:
    if isinstance(value, str):
        normalized = value.strip(" \t\r\n，。；;")
        return [normalized] if normalized else []
    if isinstance(value, (list, tuple, set)):
        return list(dict.fromkeys(phrase for item in value for phrase in _fact_phrase_values(item)))
    return []


def build_formula_lexicon_constraints(
    *,
    materials: list[MaterialEnvelopeV2] | tuple[MaterialEnvelopeV2, ...] | list[dict[str, Any]],
    formula_lexicon_bundle: dict[str, Any],
    material_quality_report: MaterialQualityReportV1 | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把公式词库缩成可写候选；事实型标题词必须由已审核业务物料直接或确定性推导支撑。"""

    normalized = [
        item if isinstance(item, MaterialEnvelopeV2) else MaterialEnvelopeV2.model_validate(item) for item in materials
    ]
    report = (
        material_quality_report
        if isinstance(material_quality_report, MaterialQualityReportV1)
        else MaterialQualityReportV1.model_validate(material_quality_report)
        if material_quality_report
        else None
    )
    bound_material_ids = (
        {material_id for binding in report.bindings for material_id in binding.material_ids}
        if report
        else {material.id for material in normalized}
    )
    fact_sources_by_variable: dict[str, list[dict[str, Any]]] = {}
    for material in normalized:
        if material.id not in bound_material_ids:
            continue
        if material.material_type not in {"business_fact", "price_fact", "media_fact"}:
            continue
        if material.governance.review_status != "approved":
            continue
        payload = material.payload
        if isinstance(payload, (BusinessFactPayloadV2, MediaFactPayloadV2)):
            value: Any = payload.value
        elif isinstance(payload, PriceFactPayloadV2):
            value = {
                "quoted_value": payload.quoted_value,
                "scope": payload.scope,
                "city": payload.city,
            }
        else:  # pragma: no cover - material type narrowing already excludes rule payloads
            continue
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        for variable_code in material.variable_codes:
            fact_sources_by_variable.setdefault(variable_code, []).append(
                {
                    "material_id": material.id,
                    "evidence_ids": list(material.evidence_ids),
                    "rendered": rendered,
                    "phrases": _fact_phrase_values(value),
                }
            )

    constraints: dict[str, dict[str, list[str]]] = {"title": {}, "body": {}}
    fact_bindings: dict[str, dict[str, list[dict[str, Any]]]] = {"title": {}, "body": {}}
    unresolved: list[str] = []
    for scope in ("title", "body"):
        for entry in formula_lexicon_bundle.get(scope) or []:
            code = str(entry.get("code") or "").strip()
            terms = sorted(
                {
                    term.strip()
                    for chunk in entry.get("chunks") or []
                    for term in str(chunk).splitlines()
                    if term.strip()
                }
            )
            if not code:
                continue
            eligible = terms
            fact_variables = _FACT_BOUND_LEXICON_VARIABLE_CODES.get(code)
            if fact_variables:
                fact_text_by_variable = {
                    variable_code: "\n".join(
                        source["rendered"] for source in fact_sources_by_variable.get(variable_code, [])
                    )
                    for variable_code in fact_variables
                }
                fact_text = "\n".join(fact_text_by_variable.values())
                eligible = [term for term in terms if term in fact_text]
                term_sources: dict[str, list[tuple[str, dict[str, Any]]]] = {
                    term: [
                        (variable_code, source)
                        for variable_code in fact_variables
                        for source in fact_sources_by_variable.get(variable_code, [])
                        if term in source["rendered"]
                    ]
                    for term in eligible
                }
                resolution_type = "exact"
                if not eligible and code == "title.house_type":
                    house_type_facts = "\n".join(
                        fact_text_by_variable.get(variable_code, "") for variable_code in ("scene", "product")
                    )
                    derived_house_types = set(
                        re.findall(r"[一二两三四五六七八九十\d]+室(?:[一二两三四五六七八九十\d]+厅)?", house_type_facts)
                    )
                    eligible = sorted(derived_house_types)
                    term_sources = {
                        term: [
                            (variable_code, source)
                            for variable_code in ("scene", "product")
                            for source in fact_sources_by_variable.get(variable_code, [])
                            if term in source["rendered"]
                        ]
                        for term in eligible
                    }
                    resolution_type = "house_type_rule"
                if not eligible and code == "title.positioning" and fact_text_by_variable.get("location"):
                    eligible = [term for term in terms if term == "同城装修"]
                    term_sources = {
                        term: [("location", source) for source in fact_sources_by_variable.get("location", [])]
                        for term in eligible
                    }
                    resolution_type = "location_rule"
                fallback_variables = _FACT_BOUND_APPROVED_FACT_FALLBACK_VARIABLE_CODES.get(code, ())
                if not eligible and fallback_variables:
                    term_sources = {}
                    for variable_code in fallback_variables:
                        for source in fact_sources_by_variable.get(variable_code, []):
                            for phrase in source["phrases"]:
                                term_sources.setdefault(phrase, []).append((variable_code, source))
                    eligible = sorted(term_sources)
                    resolution_type = "approved_fact"
                if not eligible:
                    unresolved.append(code)
                fact_bindings[scope][code] = [
                    {
                        "term": term,
                        "resolution_type": resolution_type,
                        "variable_codes": sorted({variable_code for variable_code, _ in term_sources.get(term, [])}),
                        "material_ids": sorted({source["material_id"] for _, source in term_sources.get(term, [])}),
                        "evidence_ids": sorted(
                            {
                                evidence_id
                                for _, source in term_sources.get(term, [])
                                for evidence_id in source["evidence_ids"]
                            }
                        ),
                    }
                    for term in eligible
                ]
            constraints[scope][code] = eligible
    fact_bound_title_codes = sorted(code for code in constraints["title"] if code in _FACT_BOUND_LEXICON_VARIABLE_CODES)
    fact_bound_body_codes = sorted(code for code in constraints["body"] if code in _FACT_BOUND_LEXICON_VARIABLE_CODES)
    locked_selection = formula_lexicon_bundle.get("selection") or {}
    selected = {
        scope: {
            code: list(locked_selection.get(scope, {}).get(code) or [])
            for code in constraints[scope]
            if locked_selection.get(scope, {}).get(code)
        }
        for scope in ("title", "body")
    }
    invalid_selection = sorted(
        f"{scope}:{code}:{term}"
        for scope in ("title", "body")
        for code, terms in selected[scope].items()
        for term in terms
        if term not in constraints[scope].get(code, [])
    )
    return {
        "schema_version": 2,
        "title": constraints["title"],
        "body": constraints["body"],
        "fact_bindings": fact_bindings,
        "selection": selected,
        "invalid_locked_selection": invalid_selection,
        "fact_bound_title_codes": fact_bound_title_codes,
        "fact_bound_body_codes": fact_bound_body_codes,
        "unresolved_fact_bound_codes": sorted(unresolved),
    }


def select_formula_lexicon_terms(
    constraints: dict[str, Any],
    *,
    optional_title_codes: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """在冻结生产包前为每个公式词库确定唯一词条。

    优先短词可降低平台标题字数压力；同长时按字典序确保可重放。
    """

    selected: dict[str, dict[str, list[str]]] = {"title": {}, "body": {}}
    missing: list[str] = []
    for scope in ("title", "body"):
        for code, raw_terms in sorted((constraints.get(scope) or {}).items()):
            terms = sorted(
                {str(term).strip() for term in raw_terms if str(term).strip()},
                key=lambda term: (len(term), term),
            )
            if not terms:
                if scope == "title" and code in optional_title_codes:
                    continue
                missing.append(f"{scope}:{code}")
                continue
            selected[scope][code] = [terms[0]]
    if missing:
        raise ValueError("公式必选词库没有可冻结词条：" + "、".join(missing))
    payload = {
        "schema_version": 1,
        "policy": "shortest_then_lexicographic_v1",
        "title": selected["title"],
        "body": selected["body"],
    }
    return {**payload, "selection_hash": _canonical_hash(payload)}


def build_expression_policy(
    *,
    materials: list[MaterialEnvelopeV2] | tuple[MaterialEnvelopeV2, ...],
    strategy_snapshot: dict[str, Any],
    channel_profile: dict[str, Any],
) -> dict[str, Any]:
    """从冻结事实与渠道约束生成可执行的 Emoji 语义策略。"""

    emoji_allowed = (channel_profile.get("body_constraints") or {}).get("emoji_allowed") is not False
    variable_codes = {
        code
        for material in materials
        if material.material_type in {"business_fact", "price_fact", "media_fact"}
        for code in material.variable_codes
    }
    categories: list[dict[str, Any]] = []

    def add(code: str, semantic_role: str, target: str) -> None:
        categories.append({"code": code, "semantic_role": semantic_role, "target": target})

    if variable_codes & {"pain"}:
        add("risk_warning", "痛点、风险或避坑提醒", "紧邻真实问题或风险判断")
    if variable_codes & (_PRICE_VARIABLE_CODES | {"quantity", "title_price", "quote_block", "quote_type"}):
        add("verified_data", "已确认数据或报价口径", "紧邻有证据的数据、价格或报价说明")
    if "persona_fact" in variable_codes:
        add("identity_trust", "工长身份与可信依据", "紧邻身份、经验或可核验事实")
    if variable_codes & {"process"}:
        add("process_action", "施工过程或核对动作", "紧邻具体动作、步骤或检查点")
    if variable_codes & {"advantages", "advantage", "result"}:
        add("result_benefit", "服务价值或真实结果", "紧邻已审核优势、价值或结果")
    body_formula = strategy_snapshot.get("body_formula") or {}
    if (body_formula.get("output_schema") or {}).get("cta_required") is True:
        add("audience_action", "读者互动或下一步行动", "紧邻收藏、咨询或核对提醒")
    if variable_codes & {"product", "scene", "location"}:
        add("scene_context", "业务场景或空间信息", "紧邻真实业务、工地或空间场景")

    selected = categories[:3] if emoji_allowed else []
    payload = {
        "schema_version": 1,
        "policy_version": "semantic-expression-v1",
        "emoji_allowed": emoji_allowed,
        "minimum_semantic_categories": len(selected),
        "required_categories": selected,
        "placement_rule": "每类至少使用一个与目标句意一致的 Emoji，并紧邻所修饰内容",
        "restriction_rule": "不得用同义符号重复凑类，不得替代数字、单位、事实或标点",
    }
    return {**payload, "policy_hash": _canonical_hash(payload)}


def persona_opening_instruction(content_rule_bundle: dict[str, Any], *, has_locked_quote: bool) -> str:
    """生成槽位与模型视图共用最终稿窗口，避免报价组装挤出人设。"""
    policy = (content_rule_bundle.get("runtime_rules") or {}).get("viral-persona-author") or {}
    window = int(policy.get("opening_window_paragraphs", 2))
    if has_locked_quote and window == 2:
        return (
            "创作稿第一段必须自然完成身份、价值、证据三层；程序将在第一段后插入锁定报价块，"
            "最终正文前两个自然段包含该报价块，不能把人设留到创作稿第二段。"
        )
    return f"前 {window} 个自然段自然完成身份、价值、证据三层，说明我是谁、做什么、如何回应顾虑及可信依据。"


def _locked_title_slot_lines(
    materials: list[MaterialEnvelopeV2] | tuple[MaterialEnvelopeV2, ...],
    formula_lexicon_bundle: dict[str, Any],
) -> tuple[str, ...]:
    process_values = [
        phrase
        for material in materials
        if "process" in material.variable_codes
        for phrase in _fact_phrase_values(getattr(material.payload, "value", None))
    ]
    lines: list[str] = []
    if process_values:
        options = process_title_options(process_values)
        lines.append("工艺必须逐字出现其一：" + " / ".join(options) + "；标题字数紧时优先较短项，不要只写完整 HYB 编码")
    selected = ((formula_lexicon_bundle or {}).get("selection") or {}).get("title") or {}
    emotion_terms = [str(term).strip() for term in selected.get("title.oral_emotion") or [] if str(term).strip()]
    if emotion_terms:
        lines.append("仅词库来源的情绪槽位必须逐字写入：" + " / ".join(emotion_terms))
    return tuple(lines)


def compile_generation_slots(
    *,
    material_manifest: MaterialRequirementManifestV1,
    material_quality_report: MaterialQualityReportV1,
    materials: list[MaterialEnvelopeV2] | tuple[MaterialEnvelopeV2, ...],
    strategy_snapshot: dict[str, Any],
    expression_policy: dict[str, Any],
    channel_profile: dict[str, Any],
    content_rule_bundle: dict[str, Any],
    formula_lexicon_bundle: dict[str, Any] | None = None,
) -> tuple[GenerationSlotV1, ...]:
    """将物料、公式和审核契约编译为生成前的明确槽位。"""

    bound_ids_by_code = {
        requirement.variable_code: set(binding.material_ids)
        for requirement in material_manifest.requirements
        for binding in material_quality_report.bindings
        if requirement.requirement_id == binding.requirement_id
    }
    evidence_by_code: dict[str, list[str]] = {}
    for material in materials:
        for code in material.variable_codes:
            if material.id in bound_ids_by_code.get(code, set()):
                evidence_by_code.setdefault(code, []).extend(material.evidence_ids)

    def source_data(codes: set[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        variables = tuple(sorted(code for code in codes if code in evidence_by_code))
        evidence_ids = tuple(dict.fromkeys(item for code in variables for item in evidence_by_code.get(code, [])))
        return variables, evidence_ids

    def add(
        slots: list[GenerationSlotV1],
        *,
        slot_id: str,
        target: Literal["title", "opening", "body", "closing", "topics", "global"],
        source_codes: set[str] | None = None,
        review_codes: tuple[str, ...] = (),
        instruction: str,
        acceptance: tuple[str, ...],
        required: bool = True,
    ) -> None:
        variables, evidence_ids = source_data(source_codes or set())
        slots.append(
            GenerationSlotV1(
                slot_id=slot_id,
                target=target,
                required=required,
                source_variable_codes=variables,
                evidence_ids=evidence_ids,
                review_codes=review_codes,
                instruction=instruction,
                acceptance=acceptance,
            )
        )

    slots: list[GenerationSlotV1] = []
    title_formula = strategy_snapshot.get("title_formula") or {}
    body_formula = strategy_snapshot.get("body_formula") or {}
    title_codes = set(title_formula.get("variable_schema") or [])
    title_codes.update(
        code
        for slot in (title_formula.get("source_content") or {}).get("slot_schema") or []
        for code in slot.get("variable_codes") or []
    )
    body_codes = set(body_formula.get("required_variables") or [])
    body_codes.update(body_formula.get("variable_schema") or [])
    body_codes.update(
        code
        for method in strategy_snapshot.get("creation_method_definitions") or []
        for code in method.get("variable_schema") or []
    )
    required_manifest_codes = {item.variable_code for item in material_manifest.requirements if item.required}
    persona_codes = {
        item.variable_code
        for item in material_manifest.requirements
        if item.variable_code in evidence_by_code
        and (item.required or "persona:value" in (item.validation_schema.get("alternative_groups") or []))
    } & {"persona_fact", "advantage", "advantages", "process"}
    material_variable_codes = {code for material in materials for code in material.variable_codes}
    price_codes = (required_manifest_codes | material_variable_codes) & {
        "price",
        "unit_price",
        "budget",
        "cost",
        "labor_cost",
        "material_cost",
        "discount",
        "fee",
        "quote_type",
        "quantity",
        "title_price",
        "quote_block",
    }
    emoji_codes = (
        ("EMOJI_COVERAGE", "EMOJI_APPROPRIATENESS", "EMOJI_RESTRICTIONS")
        if expression_policy.get("emoji_allowed", True)
        else ()
    )
    from yuxi.content.v3.modular_rules import required_review_codes

    dynamic_review_codes = required_review_codes(
        {
            "production_pack": {
                "material_manifest": material_manifest.model_dump(mode="json"),
                "expression_policy": expression_policy,
            },
            "strategy_snapshot": strategy_snapshot,
            "evidence_bundle": {"items": [{"variable_codes": list(material.variable_codes)} for material in materials]},
            "content_brief": {},
        }
    )
    deterministic_review_codes = (
        "TITLE_TOO_LONG",
        "TITLE_TOO_SHORT",
        "CHANNEL_TITLE_LONG",
        "CHANNEL_TITLE_SHORT",
        "TITLE_PRODUCT_EVIDENCE_NOT_USED",
        "PERSONA_TONE_MISMATCH",
        "PERSONA_STYLE_MISMATCH",
        "MECHANICAL_META_EXPRESSION",
        "NATURAL_EXPRESSION",
        "FACT_NUMBER_WITHOUT_SOURCE",
        "NUMERIC_CLAIM_UNSUPPORTED",
        "EVIDENCE_REFERENCE_FORBIDDEN",
        "FACT_CHECK_FAILED",
        "FACT_INCONSISTENT",
        "CONTENT_REQUIRED_TERM_MISSING",
        "CONTENT_FORBIDDEN_TERM",
        "CONTENT_HIGH_RISK_CLAIM",
        "COMPLIANCE_RULE_MATCH",
        "CONTENT_STRATEGY_SNAPSHOT_MISSING",
        "BODY_PRODUCT_EVIDENCE_NOT_USED",
        "KNOWLEDGE_EVIDENCE_UNUSED",
        "KNOWLEDGE_PRICE_DETAIL_UNUSED",
        "DERIVED_CALCULATION_UNUSED",
        "TRADE_BREAKDOWN_UNUSED",
        "LABOR_AUX_BREAKDOWN_UNUSED",
        "BODY_LENGTH_OUT_OF_RANGE",
        "CHANNEL_BODY_LONG",
        "CHANNEL_BODY_SHORT",
        "BODY_FORMULA_MISMATCH",
        "CONTENT_STRUCTURE_MISMATCH",
        "LAYOUT_MARKDOWN_FORBIDDEN",
        "LAYOUT_PARAGRAPH_TOO_LONG",
        "CTA_TOO_DIRECT",
        "UNSAFE_AUTO_REPLACEMENT",
    )
    all_review_codes = tuple(dict.fromkeys((*dynamic_review_codes, *deterministic_review_codes)))

    add(
        slots,
        slot_id="review_contract",
        target="global",
        review_codes=all_review_codes,
        instruction="生成完成后逐项满足本生产包中的全部槽位和审核代码，不得只满足事实存在而遗漏表达关系。",
        acceptance=("所有必需槽位都有对应正文位置", "未新增生产包之外的事实或承诺"),
    )
    if title_codes:
        locked_title_lines = _locked_title_slot_lines(materials, formula_lexicon_bundle or {})
        title_instruction = "按锁定标题公式的槽位逐项成题；同一槽位内的变量或词库只需选择一个有证据的来源。"
        if locked_title_lines:
            title_instruction += "参考示例只学节奏和标点，不得用示例替换这些冻结词。" + "".join(
                f"{line}。" for line in locked_title_lines
            )
        add(
            slots,
            slot_id="title_formula",
            target="title",
            source_codes=title_codes,
            review_codes=(
                "TITLE_ALIGNMENT",
                "TITLE_FORMULA_MISMATCH",
                "TITLE_REQUIRED_FACT_MISSING",
                "TITLE_FACT_UNSUPPORTED",
            ),
            instruction=title_instruction,
            acceptance=(
                "标题使用已绑定事实",
                "标题与正文保持同一主题",
                "不新增数字或绝对化承诺",
                *locked_title_lines,
            ),
        )
    if body_codes or body_formula.get("structure_schema"):
        add(
            slots,
            slot_id="body_formula",
            target="body",
            source_codes=body_codes,
            review_codes=(
                "CREATION_TYPE_ALIGNMENT",
                "COMPOSITION_ALIGNMENT",
                "BODY_VALUE",
                "LAYOUT_READABILITY",
            ),
            instruction="按锁定正文公式和组成蓝图展开正文，每个层级都用对应事实或动作兑现，不照抄参考原文。",
            acceptance=("创作类型和层级组合一致", "正文提供明确阅读价值", "段落便于扫读"),
        )
    if "persona_fact" in persona_codes:
        add(
            slots,
            slot_id="persona_identity",
            target="opening",
            source_codes={"persona_fact"},
            review_codes=("PERSONA_OPENING", "PERSONA_GROUNDING"),
            instruction=persona_opening_instruction(
                content_rule_bundle,
                has_locked_quote=any(
                    "quote_block" in material.variable_codes
                    and material.payload.value.get("insertion_policy") == "after-opening-paragraph-v1"
                    for material in materials
                ),
            ),
            acceptance=("身份、业务和可信依据均来自对应 Evidence", "不写未经支持的多年经验或承诺"),
        )
    if persona_codes & {"advantage", "advantages", "process"}:
        add(
            slots,
            slot_id="persona_value",
            target="opening",
            source_codes=persona_codes & {"advantage", "advantages", "process"},
            review_codes=("PERSONA_OPENING", "PERSONA_GROUNDING", "PERSONA_CLOSING"),
            instruction="只选择与当前痛点最相关的 2～3 项优势，并在优势后说明各自解决的具体顾虑。",
            acceptance=("优势数量为 2～3 项", "每项优势都有对应用户顾虑或作用", "结尾不新增优势"),
        )

    for category in expression_policy.get("required_categories") or []:
        code = str(category.get("code") or "").strip()
        if not code:
            continue
        source_codes = {
            "identity_trust": "persona_fact",
            "process_action": "process",
            "result_benefit": "advantage",
            "verified_data": "price",
            "scene_context": "scene",
            "risk_warning": "pain",
            "audience_action": "",
        }
        source_code = source_codes.get(code, "")
        add(
            slots,
            slot_id=f"emoji:{code}",
            target="body",
            source_codes={source_code} if source_code else set(),
            review_codes=emoji_codes,
            instruction=f"在与“{category.get('semantic_role') or code}”对应的真实句子旁放置一个语义贴切的 Emoji。",
            acceptance=(str(category.get("target") or "紧邻对应语义"), "不使用 Emoji 替代数字、单位、事实或标点"),
        )

    price_context = bool(price_codes)
    if price_context:
        add(
            slots,
            slot_id="price_scope",
            target="body",
            source_codes=price_codes,
            review_codes=(
                "PRICE_SCOPE_ALIGNMENT",
                "PRICE_EVIDENCE_SCOPE_MISMATCH",
                "PRICE_CITY_MISMATCH",
                "PRICE_CITY_UNVERIFIED",
            ),
            instruction="严格按已确认价格类型、城市、单位和适用范围表达，不把参考单价写成无条件成交价。",
            acceptance=("价格口径、范围、城市和单位一致", "不自行计算未经确认的总价"),
        )

    topic_rules = (content_rule_bundle.get("runtime_rules") or {}).get("viral-topic-author") or {}
    if topic_rules:
        add(
            slots,
            slot_id="topics",
            target="topics",
            review_codes=(
                "TOPIC_ALIGNMENT",
                "TOPIC_COUNT_MISMATCH",
                "CHANNEL_TOPIC_COUNT",
                "TOPIC_DUPLICATED",
                "TOPIC_OUTSIDE_CANDIDATE_POOL",
            ),
            instruction="只从冻结话题候选池选择规定数量的话题，保持互不重复并与正文主题相关。",
            acceptance=(f"话题数量为 {int(topic_rules.get('topic_count') or 10)}", "话题来自冻结候选池"),
        )

    add(
        slots,
        slot_id="natural_expression",
        target="body",
        review_codes=(
            "NATURAL_EXPRESSION",
            "MECHANICAL_META_EXPRESSION",
            "PERSONA_TONE_MISMATCH",
            "PERSONA_STYLE_MISMATCH",
        ),
        instruction="使用自然口语完成事实表达，优先直接讲场景和动作，允许首先、其次等正常衔接词。",
        acceptance=("句子通顺、意思清楚、说话人一致", "轻微书面化或不够口语化仅给建议，不触发回修"),
    )
    add(
        slots,
        slot_id="platform_compliance",
        target="global",
        review_codes=(
            "PLATFORM_CTA",
            "CTA_TOO_DIRECT",
            "CONTENT_FORBIDDEN_TERM",
            "CONTENT_HIGH_RISK_CLAIM",
            "UNSAFE_AUTO_REPLACEMENT",
        ),
        instruction="遵守平台词汇、合规和 CTA 规则，不增加强引导或绝对化承诺。",
        acceptance=("不包含禁用词", "行动邀请克制且符合渠道"),
    )
    return tuple(slots)


def freeze_production_pack(
    *,
    task_id: str,
    production_order: ProductionOrderV1,
    material_manifest: MaterialRequirementManifestV1,
    material_quality_report: MaterialQualityReportV1,
    materials: list[MaterialEnvelopeV2] | tuple[MaterialEnvelopeV2, ...],
    strategy_snapshot: dict[str, Any],
    evidence_bundle: dict[str, Any],
    formula_lexicon_bundle: dict[str, Any],
    reference_snapshot: dict[str, Any],
    expression_guidance: dict[str, Any] | None,
    writing_request: str | None,
    channel_profile: dict[str, Any],
    persona_profile: dict[str, Any],
    content_rule_bundle: dict[str, Any],
    compliance_policy_version_ids: list[str] | tuple[str, ...],
) -> FrozenProductionPackV1:
    if not isinstance(production_order, ProductionOrderV1):
        production_order = ProductionOrderV1.model_validate(production_order)
    if not isinstance(material_manifest, MaterialRequirementManifestV1):
        material_manifest = MaterialRequirementManifestV1.model_validate(material_manifest)
    if not isinstance(material_quality_report, MaterialQualityReportV1):
        material_quality_report = MaterialQualityReportV1.model_validate(material_quality_report)
    if material_quality_report.status != "passed":
        raise ValueError("物料质量门未通过，不能冻结生产包")
    if material_manifest.order_hash != production_order.order_hash:
        raise ValueError("物料清单与生产订单不一致")
    if material_quality_report.manifest_hash != material_manifest.manifest_hash:
        raise ValueError("物料质量报告与清单不一致")
    if evidence_bundle.get("status") != "frozen" or not evidence_bundle.get("bundle_hash"):
        raise ValueError("生产包只能绑定已冻结 EvidenceBundle")
    if not formula_lexicon_bundle.get("bundle_hash"):
        raise ValueError("生产包缺少公式词库 Hash")
    if not content_rule_bundle.get("bundle_hash"):
        raise ValueError("生产包缺少冻结内容规则包")
    if not reference_snapshot.get("id") or not reference_snapshot.get("source_hash"):
        raise ValueError("生产包缺少已锁定参考版本")
    normalized_materials = tuple(
        sorted(
            (
                item if isinstance(item, MaterialEnvelopeV2) else MaterialEnvelopeV2.model_validate(item)
                for item in materials
            ),
            key=lambda item: item.id,
        )
    )
    revalidated_report = validate_material_gate(
        manifest=material_manifest,
        materials=normalized_materials,
    )
    if revalidated_report.report_hash != material_quality_report.report_hash:
        raise ValueError("标准物料在质量门通过后发生变化")
    if material_manifest.reference_required:
        references = [item for item in normalized_materials if item.material_type == "viral_reference"]
        if len(references) != 1:
            raise ValueError("生产包必须且只能绑定一篇已审核爆款参考")
    constraints = build_formula_lexicon_constraints(
        materials=normalized_materials,
        formula_lexicon_bundle=formula_lexicon_bundle,
        material_quality_report=material_quality_report,
    )
    frozen_formula_lexicon_bundle = deepcopy(formula_lexicon_bundle)
    from yuxi.content.v3.title_formula_slots import required_title_lexicon_codes

    title_formula = strategy_snapshot.get("title_formula") or {}
    title_lexicon_codes = {
        str(entry.get("code") or "")
        for entry in formula_lexicon_bundle.get("title") or []
        if str(entry.get("code") or "").strip()
    }
    optional_title_codes = frozenset(title_lexicon_codes - set(required_title_lexicon_codes(title_formula)))
    selection = select_formula_lexicon_terms(
        constraints,
        optional_title_codes=optional_title_codes,
    )
    frozen_formula_lexicon_bundle["selection"] = selection
    frozen_formula_lexicon_bundle["fact_bindings"] = {
        scope: {
            code: [
                binding
                for binding in (constraints.get("fact_bindings", {}).get(scope, {}).get(code) or [])
                if binding.get("term") in terms
            ]
            for code, terms in selection[scope].items()
            if code in _FACT_BOUND_LEXICON_VARIABLE_CODES
        }
        for scope in ("title", "body")
    }
    expression_policy = build_expression_policy(
        materials=normalized_materials,
        strategy_snapshot=strategy_snapshot,
        channel_profile=channel_profile,
    )
    generation_slots = compile_generation_slots(
        material_manifest=material_manifest,
        material_quality_report=material_quality_report,
        materials=normalized_materials,
        strategy_snapshot=strategy_snapshot,
        expression_policy=expression_policy,
        channel_profile=channel_profile,
        content_rule_bundle=content_rule_bundle,
        formula_lexicon_bundle=frozen_formula_lexicon_bundle,
    )
    payload = {
        "schema_version": 1,
        "task_id": task_id,
        "production_order": production_order.model_dump(mode="json"),
        "material_manifest": material_manifest.model_dump(mode="json"),
        "material_quality_report": material_quality_report.model_dump(mode="json"),
        "materials": [item.model_dump(mode="json") for item in normalized_materials],
        "strategy_snapshot": strategy_snapshot,
        "evidence_bundle_id": evidence_bundle.get("id"),
        "evidence_bundle_version": evidence_bundle.get("version"),
        "evidence_bundle_hash": evidence_bundle.get("bundle_hash"),
        "formula_lexicon_bundle": frozen_formula_lexicon_bundle,
        "formula_lexicon_bundle_hash": formula_lexicon_bundle.get("bundle_hash"),
        "reference_snapshot": reference_snapshot,
        "expression_guidance": expression_guidance,
        "expression_policy": expression_policy,
        "generation_slots": [item.model_dump(mode="json") for item in generation_slots],
        "writing_request": writing_request,
        "channel_profile": channel_profile,
        "persona_profile": persona_profile,
        "content_rule_bundle": content_rule_bundle,
        "compliance_policy_version_ids": sorted(set(compliance_policy_version_ids)),
    }
    pack_hash = _canonical_hash(payload)
    return FrozenProductionPackV1.model_validate(
        {
            **payload,
            "id": f"cpp_{pack_hash[:24]}",
            "production_pack_hash": pack_hash,
            "frozen_at": datetime.now(UTC),
        }
    )


__all__ = [
    "FrozenProductionPackV1",
    "GenerationSlotV1",
    "MaterialEnvelopeV2",
    "MaterialGateIssueV1",
    "MaterialQualityReportV1",
    "MaterialRequirementManifestV1",
    "MaterialRequirementV1",
    "ProductionOrderV1",
    "build_formula_lexicon_constraints",
    "build_expression_policy",
    "compile_generation_slots",
    "build_material_manifest",
    "create_production_order",
    "freeze_production_pack",
    "select_formula_lexicon_terms",
    "standardize_evidence_materials",
    "validate_material_gate",
]
