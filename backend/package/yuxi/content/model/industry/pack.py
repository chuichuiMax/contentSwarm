"""Industry Pack 的纯领域协议、校验、生命周期和离线结构评测。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


PackStatus = Literal["draft", "validated", "canary", "published", "deprecated"]
CONTENT_TYPE_CODES = frozenset(f"CT0{index}" for index in range(1, 8))


class IndustryVariableMappingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_key: str = Field(min_length=1, max_length=120)
    variable_code: str = Field(min_length=1, max_length=80)
    transform_type: str = "identity"
    transform_config: dict[str, Any] = Field(default_factory=dict)
    required_by_content_types: list[str] = Field(default_factory=list)


class IndustryCombinationGroupSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    content_type_codes: list[str]
    combination_type: Literal["single", "double", "triple", "quadruple"]
    method_members: list[dict[str, Any]]
    title_formula_candidate_codes: list[str]
    body_formula_candidate_codes: list[str]
    required_variable_codes: list[str] = Field(default_factory=list)
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class IndustryGoldenSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    content_direction_code: str
    input_variables: dict[str, Any]
    expected_group_id: str


class IndustryNegativeExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    content_direction_code: str
    input_variables: dict[str, Any]
    expected_error_code: str


class IndustryPack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    slug: str
    version: int = Field(ge=1)
    schema_version: int = Field(ge=3)
    status: PackStatus
    name: str
    description: str = ""
    content_type_aliases: dict[str, str]
    variable_mappings: list[IndustryVariableMappingSpec]
    combination_groups: list[IndustryCombinationGroupSpec]
    lexicon_version_ids: list[str]
    knowledge_scope: list[str]
    evidence_policy: dict[str, Any]
    compliance_policy: dict[str, Any]
    persona_templates: list[dict[str, Any]]
    visual_policy: dict[str, Any]
    golden_samples: list[IndustryGoldenSample]
    negative_examples: list[IndustryNegativeExample]
    minimum_coverage: float = Field(ge=0, le=1)
    source_metadata: dict[str, Any]
    changelog: str
    rollback_target_version_id: str | None = None


def industry_pack_hash(pack: IndustryPack) -> str:
    payload = pack.model_dump(mode="json", exclude={"status", "rollback_target_version_id"})
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class IndustryPackCatalog:
    method_codes: frozenset[str]
    title_formula_codes: frozenset[str]
    body_formula_codes: frozenset[str]
    variable_codes: frozenset[str]


@dataclass(frozen=True, slots=True)
class IndustryPackValidation:
    errors: tuple[dict[str, str], ...]
    warnings: tuple[dict[str, str], ...]

    @property
    def valid(self) -> bool:
        return not self.errors


class IndustryPackLoader:
    """把持久化记录组装为稳定领域对象，不让领域规则依赖 ORM。"""

    @staticmethod
    def load_from_mapping(value: dict[str, Any]) -> IndustryPack:
        return IndustryPack.model_validate(value)

    @staticmethod
    def load(
        record: Any,
        *,
        variable_mappings: list[Any],
        combination_groups: list[dict[str, Any]],
    ) -> IndustryPack:
        return IndustryPack.model_validate(
            {
                "id": record.id,
                "slug": record.slug,
                "version": record.version,
                "schema_version": record.schema_version,
                "status": record.status,
                "name": record.name,
                "description": record.description or "",
                "content_type_aliases": record.content_type_aliases or {},
                "variable_mappings": [
                    {
                        "field_key": item.field_key,
                        "variable_code": item.variable_code,
                        "transform_type": item.transform_type,
                        "transform_config": item.transform_config or {},
                        "required_by_content_types": item.required_by_content_types or [],
                    }
                    for item in variable_mappings
                ],
                "combination_groups": combination_groups,
                "lexicon_version_ids": record.lexicon_version_ids or [],
                "knowledge_scope": record.knowledge_scope or [],
                "evidence_policy": record.evidence_policy or {},
                "compliance_policy": record.compliance_policy or {},
                "persona_templates": record.persona_templates or [],
                "visual_policy": record.visual_policy or {},
                "golden_samples": record.golden_samples or [],
                "negative_examples": record.negative_examples or [],
                "minimum_coverage": record.minimum_coverage or 0,
                "source_metadata": record.source_metadata or {},
                "changelog": record.changelog or "",
                "rollback_target_version_id": record.rollback_target_version_id,
            }
        )


class IndustryPackValidator:
    def validate(self, pack: IndustryPack, catalog: IndustryPackCatalog) -> IndustryPackValidation:
        errors: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []

        def error(code: str, message: str, path: str) -> None:
            errors.append({"code": code, "message": message, "path": path})

        alias_codes = set(pack.content_type_aliases)
        if alias_codes != CONTENT_TYPE_CODES or any(not value.strip() for value in pack.content_type_aliases.values()):
            error("PACK_DIRECTION_ALIASES_INVALID", "行业包必须完整映射 CT01～CT07", "content_type_aliases")

        fields = [item.field_key for item in pack.variable_mappings]
        if len(fields) != len(set(fields)):
            error("PACK_FIELD_MAPPING_DUPLICATED", "行业字段映射不能重复", "variable_mappings")
        for index, item in enumerate(pack.variable_mappings):
            if set(item.required_by_content_types) - CONTENT_TYPE_CODES:
                error(
                    "PACK_FIELD_DIRECTION_REFERENCE_INVALID",
                    "行业字段引用了无效内容方向",
                    f"variable_mappings.{index}.required_by_content_types",
                )
        unknown_variables = sorted(
            {item.variable_code for item in pack.variable_mappings} - set(catalog.variable_codes)
        )
        if unknown_variables:
            error(
                "PACK_VARIABLE_REFERENCE_INVALID",
                f"行业包引用了未知平台变量：{', '.join(unknown_variables)}",
                "variable_mappings",
            )
        raw_group_ids = [item.id for item in pack.combination_groups]
        group_ids = set(raw_group_ids)
        if not raw_group_ids:
            error("PACK_COMBINATION_GROUP_REQUIRED", "行业包至少需要一个组合组", "combination_groups")
        if len(raw_group_ids) != len(group_ids):
            error("PACK_COMBINATION_GROUP_DUPLICATED", "组合组 ID 不能重复", "combination_groups")
        group_by_id = {item.id: item for item in pack.combination_groups}
        covered_directions: set[str] = set()
        member_count_by_type = {"single": 1, "double": 2, "triple": 3, "quadruple": 4}
        for index, group in enumerate(pack.combination_groups):
            path = f"combination_groups.{index}"
            member_codes = {item.get("method_code") for item in group.method_members if item.get("method_code")}
            if member_codes - set(catalog.method_codes):
                error("PACK_METHOD_REFERENCE_INVALID", "组合组引用了未知创作手法", f"{path}.method_members")
            if set(group.content_type_codes) - CONTENT_TYPE_CODES or not group.content_type_codes:
                error("PACK_DIRECTION_REFERENCE_INVALID", "组合组内容方向无效", f"{path}.content_type_codes")
            else:
                covered_directions.update(group.content_type_codes)
            if len(group.method_members) != member_count_by_type[group.combination_type]:
                error(
                    "PACK_COMBINATION_MEMBER_COUNT_INVALID",
                    "组合类型与创作手法成员数不一致",
                    f"{path}.method_members",
                )
            if set(group.title_formula_candidate_codes) - set(catalog.title_formula_codes):
                error("PACK_TITLE_FORMULA_REFERENCE_INVALID", "标题候选池引用无效", path)
            if set(group.body_formula_candidate_codes) - set(catalog.body_formula_codes):
                error("PACK_BODY_FORMULA_REFERENCE_INVALID", "正文候选池引用无效", path)
            if not group.title_formula_candidate_codes or not group.body_formula_candidate_codes:
                error("PACK_FORMULA_POOL_EMPTY", "标题和正文候选池不能为空", path)
            if set(group.required_variable_codes) - set(catalog.variable_codes):
                error("PACK_GROUP_VARIABLE_REFERENCE_INVALID", "组合组引用了未知平台变量", path)
        if covered_directions != CONTENT_TYPE_CODES:
            error("PACK_GROUP_DIRECTION_COVERAGE_INVALID", "组合组必须覆盖 CT01～CT07", "combination_groups")

        sample_directions = {item.content_direction_code for item in pack.golden_samples}
        if sample_directions != CONTENT_TYPE_CODES:
            error("PACK_GOLDEN_COVERAGE_INVALID", "每个内容方向必须有黄金样本", "golden_samples")
        for sample in pack.golden_samples:
            if sample.expected_group_id not in group_ids:
                error("PACK_GOLDEN_GROUP_INVALID", "黄金样本引用了未知组合组", f"golden_samples.{sample.id}")
            elif sample.content_direction_code not in group_by_id[sample.expected_group_id].content_type_codes:
                error(
                    "PACK_GOLDEN_DIRECTION_MISMATCH",
                    "黄金样本的内容方向与期望组合组不一致",
                    f"golden_samples.{sample.id}",
                )
        if not pack.negative_examples:
            error("PACK_NEGATIVE_EXAMPLES_REQUIRED", "行业包必须提供反例", "negative_examples")
        elif set(item.content_direction_code for item in pack.negative_examples) != CONTENT_TYPE_CODES:
            error("PACK_NEGATIVE_COVERAGE_INVALID", "每个内容方向必须有反例", "negative_examples")
        if not pack.evidence_policy or not pack.compliance_policy:
            error("PACK_RISK_POLICY_REQUIRED", "行业包必须声明证据和合规政策", "evidence_policy")
        if not pack.persona_templates:
            error("PACK_PERSONA_REQUIRED", "行业包必须声明人设模板", "persona_templates")
        if not pack.visual_policy:
            error("PACK_VISUAL_POLICY_REQUIRED", "行业包必须声明视觉政策", "visual_policy")
        if not pack.source_metadata or not pack.changelog.strip():
            error("PACK_PROVENANCE_REQUIRED", "行业包必须声明来源和变更说明", "source_metadata")
        if not pack.lexicon_version_ids:
            warnings.append(
                {"code": "PACK_LEXICON_EMPTY", "message": "行业表达词库为空", "path": "lexicon_version_ids"}
            )
        return IndustryPackValidation(tuple(errors), tuple(warnings))


@dataclass(frozen=True, slots=True)
class IndustryPackEvaluation:
    metrics: dict[str, float]
    passed: bool


class IndustryPackEvaluator:
    """结构离线评测；运行态质量指标由审计事件汇总后写入新版本候选。"""

    def evaluate(self, pack: IndustryPack, validation: IndustryPackValidation) -> IndustryPackEvaluation:
        direction_coverage = len({item.content_direction_code for item in pack.golden_samples}) / 7
        referenced_groups = {item.expected_group_id for item in pack.golden_samples}
        group_coverage = len(referenced_groups) / max(len(pack.combination_groups), 1)
        cross_group_violation_rate = (
            0.0
            if all(
                item.expected_group_id in {group.id for group in pack.combination_groups}
                for item in pack.golden_samples
            )
            else 1.0
        )
        metrics = {
            "direction_coverage": round(direction_coverage, 4),
            "group_coverage": round(group_coverage, 4),
            "reference_error_rate": round(len(validation.errors) / max(len(pack.combination_groups), 1), 4),
            "cross_group_violation_rate": cross_group_violation_rate,
            "negative_example_coverage": 1.0 if pack.negative_examples else 0.0,
        }
        passed = validation.valid and direction_coverage >= pack.minimum_coverage and cross_group_violation_rate == 0
        return IndustryPackEvaluation(metrics=metrics, passed=passed)


class IndustryPackRegressionMetrics(BaseModel):
    """Canary 回归必须提交的全链路指标，所有比率都使用 0～1。"""

    model_config = ConfigDict(extra="forbid")

    rule_hit_rate: float = Field(ge=0, le=1)
    no_eligible_group_rate: float = Field(ge=0, le=1)
    evidence_missing_rate: float = Field(ge=0, le=1)
    cross_group_violation_rate: float = Field(ge=0, le=1)
    multi_formula_violation_rate: float = Field(ge=0, le=1)
    fact_citation_coverage: float = Field(ge=0, le=1)
    numeric_citation_coverage: float = Field(ge=0, le=1)
    deterministic_check_pass_rate: float = Field(ge=0, le=1)
    review_pass_rate: float = Field(ge=0, le=1)
    manual_reselection_rate: float = Field(ge=0, le=1)
    rework_rate: float = Field(ge=0, le=1)
    final_approval_rate: float = Field(ge=0, le=1)
    agent_success_rate: float = Field(ge=0, le=1)
    skill_success_rate: float = Field(ge=0, le=1)
    average_duration_ms: float = Field(ge=0)
    average_token_count: float = Field(ge=0)
    average_cost: float = Field(ge=0)
    cover_job_success_rate: float = Field(ge=0, le=1)
    visual_review_pass_rate: float = Field(ge=0, le=1)
    cover_manual_reselection_rate: float = Field(ge=0, le=1)


@dataclass(frozen=True, slots=True)
class IndustryPackRegressionEvaluation:
    metrics: dict[str, float]
    passed: bool
    failed_gates: tuple[dict[str, Any], ...]


class IndustryPackRegressionEvaluator:
    """将真实 canary 指标转换为发布门禁，不修改 Pack 或已发布权重。"""

    _MINIMUM_GATES = (
        "rule_hit_rate",
        "fact_citation_coverage",
        "numeric_citation_coverage",
        "deterministic_check_pass_rate",
        "review_pass_rate",
        "final_approval_rate",
        "agent_success_rate",
        "skill_success_rate",
        "cover_job_success_rate",
        "visual_review_pass_rate",
    )
    _MAXIMUM_FAILURE_GATES = ("no_eligible_group_rate", "evidence_missing_rate")
    _ZERO_VIOLATION_GATES = ("cross_group_violation_rate", "multi_formula_violation_rate")

    def evaluate(
        self,
        pack: IndustryPack,
        metrics: IndustryPackRegressionMetrics,
    ) -> IndustryPackRegressionEvaluation:
        values = metrics.model_dump()
        failed: list[dict[str, Any]] = []
        for name in self._MINIMUM_GATES:
            actual = values[name]
            if actual < pack.minimum_coverage:
                failed.append(
                    {
                        "metric": name,
                        "operator": ">=",
                        "threshold": pack.minimum_coverage,
                        "actual": actual,
                    }
                )
        maximum_failure_rate = 1 - pack.minimum_coverage
        for name in self._MAXIMUM_FAILURE_GATES:
            actual = values[name]
            if actual > maximum_failure_rate:
                failed.append(
                    {
                        "metric": name,
                        "operator": "<=",
                        "threshold": maximum_failure_rate,
                        "actual": actual,
                    }
                )
        for name in self._ZERO_VIOLATION_GATES:
            actual = values[name]
            if actual != 0:
                failed.append({"metric": name, "operator": "==", "threshold": 0.0, "actual": actual})
        return IndustryPackRegressionEvaluation(
            metrics=values,
            passed=not failed,
            failed_gates=tuple(failed),
        )


class IndustryPackPolicy:
    TRANSITIONS: dict[str, frozenset[str]] = {
        "draft": frozenset({"validated"}),
        "validated": frozenset({"draft", "canary"}),
        "canary": frozenset({"validated", "published"}),
        "published": frozenset({"deprecated"}),
        "deprecated": frozenset({"published"}),
    }

    @classmethod
    def assert_transition(cls, current: str, target: str) -> None:
        if target not in cls.TRANSITIONS.get(current, frozenset()):
            raise ValueError(f"Industry Pack 不允许从 {current} 晋级到 {target}")


__all__ = [
    "CONTENT_TYPE_CODES",
    "IndustryCombinationGroupSpec",
    "IndustryGoldenSample",
    "IndustryNegativeExample",
    "IndustryPack",
    "IndustryPackCatalog",
    "IndustryPackEvaluation",
    "IndustryPackEvaluator",
    "IndustryPackLoader",
    "IndustryPackPolicy",
    "IndustryPackRegressionEvaluation",
    "IndustryPackRegressionEvaluator",
    "IndustryPackRegressionMetrics",
    "IndustryPackValidation",
    "IndustryPackValidator",
    "IndustryVariableMappingSpec",
    "industry_pack_hash",
]
