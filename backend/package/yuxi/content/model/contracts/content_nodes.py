"""V3 Agent 节点的版本化输入、输出契约与一次性提交器。"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.tools import StructuredTool, ToolException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LockedVersionsV1(StrictContract):
    industry_pack_version_id: str
    channel_profile_version_id: str
    persona_profile_version_id: str | None
    rule_version_id: str
    title_formula_code: str | None
    body_formula_code: str | None
    artifact_version_id: str | None


class ContentAgentNodeInputV1(StrictContract):
    task_id: str
    parent_run_id: str
    node_id: str
    attempt: int = Field(ge=1)
    content_brief: dict[str, Any]
    runtime_config_snapshot: dict[str, Any]
    match_decision_snapshot: dict[str, Any]
    formula_selection_snapshot: dict[str, Any]
    evidence_bundle: dict[str, Any]
    evidence_bundle_hash: str
    locked_versions: LockedVersionsV1
    locked_values: dict[str, Any]
    node_responsibility: str
    prohibited_actions: list[str]
    output_json_schema: dict[str, Any]


class ContentAgentNodeInputV2(StrictContract):
    """Agent 可见的最小节点输入；治理锁与授权范围仅保留在服务端。"""

    task_id: str
    parent_run_id: str
    node_id: str
    attempt: int = Field(ge=1)
    input_contract: str
    input_snapshot_hash: str
    payload: dict[str, Any]
    runtime_config_snapshot: dict[str, Any]
    node_responsibility: str
    prohibited_actions: list[str]
    output_json_schema: dict[str, Any]


class AnalyzeContentValueInputV1(StrictContract):
    content_brief: dict[str, Any] = Field(min_length=1)
    evidence_bundle: dict[str, Any] = Field(min_length=1)
    content_type: dict[str, Any]
    industry_pack: dict[str, Any]
    channel_profile: dict[str, Any]


class AnalyzeAndSelectDirectionInputV1(AnalyzeContentValueInputV1):
    pass


class SelectCreationStrategyInputV1(StrictContract):
    rule_version_id: str
    content_brief: dict[str, Any] = Field(min_length=1)
    evidence_bundle: dict[str, Any] = Field(min_length=1)
    content_type: dict[str, Any]
    industry_pack: dict[str, Any]
    channel_profile: dict[str, Any]


class SelectContentDirectionInputV1(StrictContract):
    content_brief: dict[str, Any] = Field(min_length=1)
    value_analysis: dict[str, Any] = Field(min_length=1)
    content_angles: list[dict[str, Any]] = Field(min_length=1)
    evidence_bundle: dict[str, Any] = Field(min_length=1)
    content_type: dict[str, Any]
    industry_pack: dict[str, Any]
    channel_profile: dict[str, Any]


class ExplainStrategyInputV1(StrictContract):
    rule_version_id: str
    content_brief: dict[str, Any] = Field(min_length=1)
    value_analysis: dict[str, Any] = Field(min_length=1)
    selected_angle: dict[str, Any] = Field(min_length=1)
    match_decision_snapshot: dict[str, Any] = Field(min_length=1)
    evidence_bundle: dict[str, Any] = Field(min_length=1)


class CollectMissingEvidenceInputV1(StrictContract):
    rule_version_id: str
    content_brief: dict[str, Any] = Field(min_length=1)
    selected_angle: dict[str, Any] = Field(min_length=1)
    match_decision_snapshot: dict[str, Any] = Field(min_length=1)
    formula_candidate_pool: dict[str, Any] = Field(min_length=1)
    strategy_explanation: dict[str, Any] = Field(min_length=1)
    evidence_bundle: dict[str, Any] = Field(min_length=1)


class CollectMissingEvidenceInputV2(StrictContract):
    rule_version_id: str
    content_brief: dict[str, Any] = Field(min_length=1)
    selected_angle: dict[str, Any] = Field(min_length=1)
    match_decision_snapshot: dict[str, Any] = Field(min_length=1)
    formula_candidate_pool: dict[str, Any] = Field(min_length=1)
    evidence_gap_analysis: dict[str, Any] = Field(min_length=1)
    evidence_bundle: dict[str, Any] = Field(min_length=1)


class CollectSelectedStrategyEvidenceInputV1(StrictContract):
    rule_version_id: str
    content_brief: dict[str, Any] = Field(min_length=1)
    strategy_selection: dict[str, Any] = Field(min_length=1)
    strategy_snapshot: dict[str, Any] = Field(min_length=1)
    evidence_gap_analysis: dict[str, Any] = Field(min_length=1)
    evidence_bundle: dict[str, Any] = Field(min_length=1)
    runtime_config_snapshot: dict[str, Any]


class CollectBusinessRuleEvidenceInputV1(CollectSelectedStrategyEvidenceInputV1):
    pass


class CollectPriceEvidenceInputV1(StrictContract):
    content_brief: dict[str, Any] = Field(min_length=1)
    strategy_snapshot: dict[str, Any] = Field(min_length=1)
    evidence_gap_analysis: dict[str, Any] = Field(min_length=1)
    runtime_config_snapshot: dict[str, Any]


class CollectComplianceEvidenceInputV1(CollectSelectedStrategyEvidenceInputV1):
    pass


class CollectViralCandidatesInputV1(CollectSelectedStrategyEvidenceInputV1):
    pass


class SelectViralReferenceInputV1(StrictContract):
    content_brief: dict[str, Any] = Field(min_length=1)
    strategy_snapshot: dict[str, Any] = Field(min_length=1)
    runtime_config_snapshot: dict[str, Any]
    viral_candidate_collection: dict[str, Any]


class RankFormulaCandidatesInputV1(CollectMissingEvidenceInputV1):
    pass


class RankFormulaCandidatesInputV2(StrictContract):
    rule_version_id: str
    content_brief: dict[str, Any] = Field(min_length=1)
    selected_angle: dict[str, Any] = Field(min_length=1)
    match_decision_snapshot: dict[str, Any] = Field(min_length=1)
    formula_candidate_pool: dict[str, Any] = Field(min_length=1)
    evidence_bundle: dict[str, Any] = Field(min_length=1)


class StrategySnapshotV1(StrictContract):
    content_direction: str = Field(min_length=1)
    selected_group_id: str = Field(min_length=1)
    creation_methods: list[str] = Field(min_length=1)
    creation_method_definitions: list[dict[str, Any]] = Field(min_length=1)
    title_formula: dict[str, Any] = Field(min_length=1)
    body_formula: dict[str, Any] = Field(min_length=1)
    rule_version_id: str = Field(min_length=1)
    match_snapshot_id: str = Field(min_length=1)
    formula_snapshot_id: str = Field(min_length=1)
    snapshot_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def verify_snapshot_hash(self) -> StrategySnapshotV1:
        payload = self.model_dump(mode="json", exclude={"snapshot_hash"})
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if self.snapshot_hash != expected:
            raise ValueError("StrategySnapshot hash 与锁定内容不一致")
        return self


class ProductMaterialRequirementV1(StrictContract):
    requirement_id: str = Field(min_length=1)
    material_type: Literal["product_profile", "price", "case_proof", "brand", "viral_example"]
    variable_codes: list[str]
    target_usages: list[Literal["title", "body", "style_reference"]] = Field(min_length=1)
    required: bool
    query_hint: str = Field(min_length=1)
    risk_level: Literal["normal", "sensitive", "high_risk"]


class ProductMaterialRequirementsV1(StrictContract):
    strategy_snapshot_hash: str = Field(min_length=64, max_length=64)
    required_variable_codes: list[str]
    requirements: list[ProductMaterialRequirementV1] = Field(min_length=1)


class CollectStrategyProductEvidenceInputV1(StrictContract):
    content_brief: dict[str, Any] = Field(min_length=1)
    strategy_snapshot: StrategySnapshotV1
    product_material_requirements: ProductMaterialRequirementsV1
    evidence_bundle: dict[str, Any] = Field(min_length=1)
    channel_profile: dict[str, Any]


class TitleEvidenceRequirementV1(StrictContract):
    slot: str = Field(min_length=1)
    required: bool
    evidence_ids: list[str] = Field(min_length=1)
    integration_instruction: str = Field(min_length=1)


class ProductEvidencePackV1(StrictContract):
    strategy_snapshot_hash: str = Field(min_length=64, max_length=64)
    evidence_bundle_id: str = Field(min_length=1)
    evidence_bundle_version: int = Field(ge=1)
    evidence_bundle_hash: str = Field(min_length=64, max_length=64)
    slot_mappings: list[dict[str, Any]]
    unresolved_questions: list[str]
    pack_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def verify_pack_hash(self) -> ProductEvidencePackV1:
        payload = self.model_dump(mode="json", exclude={"pack_hash"})
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if self.pack_hash != expected:
            raise ValueError("ProductEvidencePack hash 与冻结内容不一致")
        return self


class ProductEvidenceBoundInputV1(StrictContract):
    content_brief: dict[str, Any] = Field(min_length=1)
    strategy_snapshot: StrategySnapshotV1
    product_evidence_pack: ProductEvidencePackV1
    evidence_bundle: dict[str, Any] = Field(min_length=1)
    channel_profile: dict[str, Any]
    persona_profile: dict[str, Any]

    @model_validator(mode="after")
    def verify_product_evidence_locks(self) -> ProductEvidenceBoundInputV1:
        if self.product_evidence_pack.strategy_snapshot_hash != self.strategy_snapshot.snapshot_hash:
            raise ValueError("产品证据快照不属于当前 StrategySnapshot")
        if self.product_evidence_pack.evidence_bundle_hash != self.evidence_bundle.get("bundle_hash"):
            raise ValueError("产品证据快照与当前 EvidenceBundle 不一致")
        return self


class GenerateTitleCandidatesInputV1(ProductEvidenceBoundInputV1):
    title_evidence_requirements: list[TitleEvidenceRequirementV1]
    title_validation_report: dict[str, Any] | None = None

    @model_validator(mode="after")
    def verify_title_evidence_requirements(self) -> GenerateTitleCandidatesInputV1:
        expected = sorted(
            (
                str(mapping.get("slot") or ""),
                bool(mapping.get("required")),
                tuple(sorted(str(item) for item in mapping.get("evidence_ids") or [])),
            )
            for mapping in self.product_evidence_pack.slot_mappings
            if mapping.get("target_usage") == "title"
        )
        actual = sorted(
            (item.slot, item.required, tuple(sorted(item.evidence_ids))) for item in self.title_evidence_requirements
        )
        if actual != expected:
            raise ValueError("标题资料要求与 ProductEvidencePack 的标题槽位不一致")
        return self


class SelectTitleInputV1(ProductEvidenceBoundInputV1):
    title_candidates: list[dict[str, Any]] = Field(min_length=1)
    title_validation_report: dict[str, Any] = Field(min_length=1)

    @model_validator(mode="after")
    def require_selectable_candidate(self) -> SelectTitleInputV1:
        if not any(item.get("selectable") is True for item in self.title_candidates):
            raise ValueError("标题选择节点至少需要一个通过确定性校验的候选")
        return self


class BuildOutlineInputV1(ProductEvidenceBoundInputV1):
    selected_title: dict[str, Any] = Field(min_length=1)


class GenerateBodyInputV1(BuildOutlineInputV1):
    content_outline: dict[str, Any] = Field(min_length=1)


class PersonaStylePolishInputV1(GenerateBodyInputV1):
    content_draft: dict[str, Any] = Field(min_length=1)


class GenerateContentInputV1(StrictContract):
    content_brief: dict[str, Any] = Field(min_length=1)
    strategy_snapshot: StrategySnapshotV1
    formula_lexicon_bundle: dict[str, Any] = Field(min_length=1)
    evidence_bundle: dict[str, Any] = Field(min_length=1)
    channel_profile: dict[str, Any]
    persona_profile: dict[str, Any]
    runtime_config_snapshot: dict[str, Any]
    validation_report: dict[str, Any] | None = None
    review_report: dict[str, Any] | None = None
    selected_title: dict[str, Any] | None = None
    content_outline: dict[str, Any] | None = None
    content_draft: dict[str, Any] | None = None

    @model_validator(mode="after")
    def verify_formula_lexicon_bundle(self) -> GenerateContentInputV1:
        bundle = self.formula_lexicon_bundle
        title_formula_code = self.strategy_snapshot.title_formula.get("code")
        body_formula_code = self.strategy_snapshot.body_formula.get("code")
        if bundle.get("title_formula_code") != title_formula_code:
            raise ValueError("标题词库包必须匹配锁定标题公式")
        if bundle.get("body_formula_code") != body_formula_code:
            raise ValueError("正文词库包必须匹配锁定正文公式")
        if title_formula_code in {f"T{index:02d}" for index in range(1, 8)} and body_formula_code in {
            f"C{index:02d}" for index in range(1, 5)
        }:
            if bundle.get("required") is not True:
                raise ValueError("装修标题和正文公式必须经过必选词库加载路径")
        if bundle.get("required") is True:
            for scope in ("title", "body"):
                entries = bundle.get(scope)
                if not isinstance(entries, list) or not entries:
                    raise ValueError(f"装修内容生成缺少必选{scope}词库")
                incomplete = any(
                    not item.get("knowledge_base_id") or not item.get("file_id") or not item.get("chunks")
                    for item in entries
                )
                if incomplete:
                    raise ValueError(f"装修内容生成的必选{scope}词库未完整加载")
            if not bundle.get("bundle_hash"):
                raise ValueError("装修内容生成的必选词库包缺少 hash")
        return self


class SemanticReviewInputV1(StrictContract):
    content_brief: dict[str, Any] = Field(min_length=1)
    strategy_snapshot: StrategySnapshotV1
    selected_title: dict[str, Any] = Field(min_length=1)
    content_outline: dict[str, Any] = Field(min_length=1)
    content_draft: dict[str, Any] = Field(min_length=1)
    validation_report: dict[str, Any] = Field(min_length=1)
    channel_result: dict[str, Any] = Field(min_length=1)
    persona_diff: dict[str, Any] | None = None
    evidence_bundle: dict[str, Any] = Field(min_length=1)


class PlanVisualsInputV1(StrictContract):
    selected_title: dict[str, Any] = Field(min_length=1)
    content_draft: dict[str, Any] = Field(min_length=1)
    strategy_snapshot: StrategySnapshotV1
    evidence_bundle: dict[str, Any] = Field(min_length=1)
    media_evidence_items: list[dict[str, Any]]
    artifact_version: dict[str, Any] = Field(min_length=1)
    channel_profile: dict[str, Any]
    runtime_config_snapshot: dict[str, Any] = Field(default_factory=dict)

    @field_validator("media_evidence_items")
    @classmethod
    def remove_non_evidence_names(cls, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                key: value
                for key, value in item.items()
                if key not in {"display_name", "file_name", "original_file_name"}
            }
            for item in items
        ]


class SubmitCoverJobInputV1(StrictContract):
    visual_plan: dict[str, Any] = Field(min_length=1)
    artifact_version: dict[str, Any] = Field(min_length=1)
    media_evidence_items: list[dict[str, Any]]

    @field_validator("media_evidence_items")
    @classmethod
    def remove_non_evidence_names(cls, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return PlanVisualsInputV1.remove_non_evidence_names(items)


class VisualReviewInputV1(StrictContract):
    selected_title: dict[str, Any] = Field(min_length=1)
    content_draft: dict[str, Any] = Field(min_length=1)
    visual_plan: dict[str, Any] = Field(min_length=1)
    cover_job: dict[str, Any] = Field(min_length=1)
    cover_assets: list[dict[str, Any]] = Field(min_length=1)
    evidence_bundle: dict[str, Any] = Field(min_length=1)


INPUT_CONTRACT_REGISTRY: dict[str, type[StrictContract]] = {
    model.__name__: model
    for model in (
        AnalyzeContentValueInputV1,
        AnalyzeAndSelectDirectionInputV1,
        SelectCreationStrategyInputV1,
        SelectContentDirectionInputV1,
        ExplainStrategyInputV1,
        CollectMissingEvidenceInputV1,
        CollectMissingEvidenceInputV2,
        CollectSelectedStrategyEvidenceInputV1,
        CollectBusinessRuleEvidenceInputV1,
        CollectPriceEvidenceInputV1,
        CollectComplianceEvidenceInputV1,
        CollectViralCandidatesInputV1,
        SelectViralReferenceInputV1,
        RankFormulaCandidatesInputV1,
        RankFormulaCandidatesInputV2,
        CollectStrategyProductEvidenceInputV1,
        GenerateTitleCandidatesInputV1,
        SelectTitleInputV1,
        BuildOutlineInputV1,
        GenerateBodyInputV1,
        PersonaStylePolishInputV1,
        GenerateContentInputV1,
        SemanticReviewInputV1,
        PlanVisualsInputV1,
        SubmitCoverJobInputV1,
        VisualReviewInputV1,
    )
}


class DirectionCandidateV1(StrictContract):
    direction_code: Literal["CT01", "CT02", "CT03", "CT04", "CT05", "CT06", "CT07"]
    reason: str
    evidence_ids: list[str]


class ContentValueResultV1(StrictContract):
    value_points: list[str] = Field(min_length=1)
    direction_candidates: list[DirectionCandidateV1] = Field(min_length=1)
    reasoning: str
    evidence_ids: list[str]


class ContentDirectionDecisionResultV1(ContentValueResultV1):
    selected_direction_code: Literal["CT01", "CT02", "CT03", "CT04", "CT05", "CT06", "CT07"]
    selection_reason: str = Field(min_length=1)
    selection_evidence_ids: list[str]


class CreationStrategySelectionResultV1(StrictContract):
    selected_direction_code: Literal["CT01", "CT02", "CT03", "CT04", "CT05", "CT06", "CT07"]
    selected_group_id: str = Field(min_length=1)
    creation_method_codes: list[str] = Field(min_length=1)
    title_formula_code: str = Field(min_length=1)
    body_formula_code: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence_ids: list[str]


class DirectionSelectionResultV1(StrictContract):
    direction_code: Literal["CT01", "CT02", "CT03", "CT04", "CT05", "CT06", "CT07"]
    reason: str = Field(min_length=1)
    evidence_ids: list[str]


class StrategyExplanationResultV1(StrictContract):
    locked_group_id: str
    explanation: str
    risks: list[str]
    evidence_ids: list[str]


class EvidenceDraftV1(StrictContract):
    id: str
    variable_codes: list[str]
    value: Any
    source_type: Literal["manual_input", "business_record", "media", "knowledge_base", "human_confirmation"]
    source_id: str
    source_version: str
    verified_status: Literal["retrieved", "confirmed", "user_confirmed"]
    allowed_usage: list[Literal["title", "body", "visual", "style_reference"]]
    risk_level: Literal["normal", "sensitive", "high_risk"]
    source_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceCollectionResultV1(StrictContract):
    evidence_items: list[EvidenceDraftV1]
    citations: list[str]
    unresolved_questions: list[str]


class BusinessRuleEvidenceCollectionResultV1(EvidenceCollectionResultV1):
    pass


class PriceEvidenceCollectionResultV1(EvidenceCollectionResultV1):
    pass


class ComplianceEvidenceCollectionResultV1(EvidenceCollectionResultV1):
    pass


class ViralCandidateCollectionResultV1(EvidenceCollectionResultV1):
    pass


class ViralReferenceSelectionResultV1(StrictContract):
    selected_candidate_id: str | None = None
    selection_reason: str = Field(min_length=1)
    selection_basis: dict[str, Any] = Field(default_factory=dict)
    reference_blueprint: dict[str, Any] | None = None
    unresolved_questions: list[str]

    @model_validator(mode="after")
    def require_complete_selection(self) -> ViralReferenceSelectionResultV1:
        if self.selected_candidate_id and not self.reference_blueprint:
            raise ValueError("选中爆款候选时必须提交结构蓝图")
        if not self.selected_candidate_id and (self.selection_basis or self.reference_blueprint):
            raise ValueError("未选爆款候选时不得提交选择依据或结构蓝图")
        return self


class FormulaSlotEvidenceMappingV1(StrictContract):
    slot: str = Field(min_length=1)
    target_usage: Literal["title", "body", "style_reference"]
    evidence_ids: list[str] = Field(min_length=1)
    integration_instruction: str = Field(min_length=1)


class ProductEvidenceCollectionResultV1(StrictContract):
    evidence_items: list[EvidenceDraftV1]
    citations: list[str]
    slot_mappings: list[FormulaSlotEvidenceMappingV1]
    unresolved_questions: list[str]


class RankedFormulaV1(StrictContract):
    formula_code: str
    reason: str


class FormulaRankingResultV1(StrictContract):
    title_rankings: list[RankedFormulaV1] = Field(min_length=1)
    body_rankings: list[RankedFormulaV1] = Field(min_length=1)


class TitleCandidateV1(StrictContract):
    id: str
    text: str
    formula_code: str
    evidence_ids: list[str]
    reason: str


class TitleCandidatesResultV1(StrictContract):
    candidates: list[TitleCandidateV1] = Field(min_length=2)
    selected_title_formula_code: str
    evidence_ids: list[str]


class OutlineSectionV1(StrictContract):
    section_id: str
    goal: str
    evidence_ids: list[str]


class TitleSelectionResultV1(StrictContract):
    selected_title_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class OutlineResultV1(StrictContract):
    body_formula_code: str
    sections: list[OutlineSectionV1] = Field(min_length=1)
    variant_key: str | None = None


class ParagraphEvidenceV1(StrictContract):
    paragraph_id: str
    evidence_ids: list[str]


class LexiconUsageV1(StrictContract):
    code: str = Field(min_length=1)
    selected_terms: list[str] = Field(min_length=1)


class ContentDraftResultV1(StrictContract):
    body: str
    topics: list[str]
    paragraph_evidence: list[ParagraphEvidenceV1]
    body_formula_code: str
    lexicon_usage: list[LexiconUsageV1] = Field(default_factory=list)


class GeneratedTitleV1(StrictContract):
    text: str = Field(min_length=1)
    formula_code: str = Field(min_length=1)
    evidence_ids: list[str]
    lexicon_usage: list[LexiconUsageV1] = Field(default_factory=list)


class GeneratedContentResultV1(StrictContract):
    title: GeneratedTitleV1
    outline: OutlineResultV1
    draft: ContentDraftResultV1


class PreservedFactCheckV1(StrictContract):
    evidence_id: str
    preserved: bool


class PersonaPolishResultV1(StrictContract):
    polished_body: str
    change_summary: list[str]
    preserved_fact_checks: list[PreservedFactCheckV1]


class ReviewCheckV1(StrictContract):
    code: str
    status: Literal["passed", "warning", "blocked"]
    location: str = "content"
    message: str
    suggestion: str = ""
    evidence_ids: list[str]


class ContentReviewResultV1(StrictContract):
    status: Literal["passed", "warning", "blocked"]
    checks: list[ReviewCheckV1]
    evidence_conflicts: list[str]

    @model_validator(mode="after")
    def verify_aggregate_status(self) -> ContentReviewResultV1:
        expected = (
            "blocked"
            if any(item.status == "blocked" for item in self.checks)
            else "warning"
            if any(item.status == "warning" for item in self.checks)
            else "passed"
        )
        if self.status != expected:
            raise ValueError("审核汇总状态必须与 checks 中最严重状态一致")
        return self


class VisualSizeV1(StrictContract):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class SafeAreaV1(StrictContract):
    top: int = Field(ge=0)
    right: int = Field(ge=0)
    bottom: int = Field(ge=0)
    left: int = Field(ge=0)


class VisualPlanResultV1(StrictContract):
    size: VisualSizeV1
    safe_area: SafeAreaV1
    text: list[str]
    source_asset_ids: list[str]
    mode: Literal["template", "generated", "mixed"]
    risks: list[str]
    artifact_version_id: str
    evidence_ids: list[str]


class CoverJobSubmissionResultV1(StrictContract):
    cover_job_id: str
    plan_hash: str
    source_asset_ids: list[str]


class AssetReviewV1(StrictContract):
    asset_id: str
    status: Literal["passed", "warning", "blocked"]
    issues: list[str]


class VisualReviewResultV1(StrictContract):
    assets: list[AssetReviewV1] = Field(min_length=1)
    status: Literal["passed", "warning", "blocked"]
    recommended_asset_id: str | None


CONTRACT_REGISTRY: dict[str, type[StrictContract]] = {
    model.__name__: model
    for model in (
        ContentValueResultV1,
        ContentDirectionDecisionResultV1,
        CreationStrategySelectionResultV1,
        DirectionSelectionResultV1,
        StrategyExplanationResultV1,
        EvidenceCollectionResultV1,
        BusinessRuleEvidenceCollectionResultV1,
        PriceEvidenceCollectionResultV1,
        ComplianceEvidenceCollectionResultV1,
        ViralCandidateCollectionResultV1,
        ViralReferenceSelectionResultV1,
        ProductEvidenceCollectionResultV1,
        FormulaRankingResultV1,
        TitleCandidatesResultV1,
        TitleSelectionResultV1,
        OutlineResultV1,
        ContentDraftResultV1,
        GeneratedContentResultV1,
        PersonaPolishResultV1,
        ContentReviewResultV1,
        VisualPlanResultV1,
        CoverJobSubmissionResultV1,
        VisualReviewResultV1,
    )
}


class ContractDomainValidationError(ValueError):
    def __init__(self, code: str, field_path: str, message: str):
        super().__init__(message)
        self.code = code
        self.field_path = field_path


def _extract_supported_numbers(value: Any) -> set[str]:
    if value is None or isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        return {str(value)}
    if isinstance(value, str):
        return set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", value))
    if isinstance(value, dict):
        return set().union(*(_extract_supported_numbers(item) for item in value.values()))
    if isinstance(value, (list, tuple, set)):
        return set().union(*(_extract_supported_numbers(item) for item in value))
    return set()


@dataclass(frozen=True, slots=True)
class ContractDomainContext:
    locked_group_id: str | None = None
    title_formula_pool: frozenset[str] = frozenset()
    body_formula_pool: frozenset[str] = frozenset()
    locked_title_formula_code: str | None = None
    locked_body_formula_code: str | None = None
    locked_body_formula_sections: tuple[str, ...] = ()
    locked_body_calling_section_ids: tuple[str, ...] = ()
    allowed_body_variant_keys: frozenset[str] = frozenset()
    required_title_lexicon_codes: frozenset[str] = frozenset()
    allowed_body_lexicon_codes: frozenset[str] = frozenset()
    body_variant_lexicon_codes: dict[str, frozenset[str]] = field(default_factory=dict)
    allowed_evidence_by_usage: dict[str, frozenset[str]] = field(default_factory=dict)
    allowed_asset_ids: frozenset[str] = frozenset()
    required_source_asset_ids: tuple[str, ...] = ()
    locked_title: str | None = None
    artifact_version_id: str | None = None
    visual_plan_hash: str | None = None
    allowed_numbers: frozenset[str] = frozenset()
    allowed_numbers_by_usage: dict[str, frozenset[str]] = field(default_factory=dict)
    product_material_slots: frozenset[str] = frozenset()
    product_material_slot_usages: dict[str, frozenset[str]] = field(default_factory=dict)
    product_material_slot_types: dict[str, str] = field(default_factory=dict)
    creation_mode: Literal["original", "viral_rewrite"] = "original"
    selected_viral_reference_ids: tuple[str, ...] = ()
    viral_candidate_ids: frozenset[str] = frozenset()
    visual_text_max_chars: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_node_input(cls, node_input: ContentAgentNodeInputV1) -> ContractDomainContext:
        return cls.from_governance(
            match_decision_snapshot=node_input.match_decision_snapshot,
            formula_selection_snapshot=node_input.formula_selection_snapshot,
            evidence_bundle=node_input.evidence_bundle,
            locked_versions=node_input.locked_versions,
            locked_values=node_input.locked_values,
        )

    @classmethod
    def from_governance(
        cls,
        *,
        match_decision_snapshot: dict[str, Any],
        formula_selection_snapshot: dict[str, Any],
        evidence_bundle: dict[str, Any],
        locked_versions: LockedVersionsV1 | dict[str, Any],
        locked_values: dict[str, Any],
        product_material_requirements: dict[str, Any] | None = None,
        strategy_snapshot: dict[str, Any] | None = None,
        viral_candidate_collection: dict[str, Any] | None = None,
    ) -> ContractDomainContext:
        versions = (
            locked_versions
            if isinstance(locked_versions, LockedVersionsV1)
            else LockedVersionsV1.model_validate(locked_versions)
        )
        evidence_by_usage: dict[str, set[str]] = {
            "title": set(),
            "body": set(),
            "visual": set(),
            "style_reference": set(),
            "any": set(),
        }
        allowed_numbers: set[str] = set()
        allowed_numbers_by_usage: dict[str, set[str]] = {
            "title": set(),
            "body": set(),
            "visual": set(),
            "style_reference": set(),
        }
        for item in evidence_bundle.get("items") or []:
            if not isinstance(item, dict) or not item.get("id") or item.get("verified_status") == "rejected":
                continue
            evidence_id = str(item["id"])
            evidence_by_usage["any"].add(evidence_id)
            for usage in item.get("allowed_usage") or []:
                if usage in evidence_by_usage:
                    evidence_by_usage[usage].add(evidence_id)
            item_numbers = _extract_supported_numbers(item.get("value"))
            item_numbers.update(str(number) for number in item.get("numbers") or [])
            allowed_numbers.update(item_numbers)
            for usage in item.get("allowed_usage") or []:
                if usage in allowed_numbers_by_usage:
                    allowed_numbers_by_usage[usage].update(item_numbers)
        formula = formula_selection_snapshot
        match = match_decision_snapshot
        locks = locked_values
        material_requirements = [
            requirement
            for requirement in (product_material_requirements or {}).get("requirements") or []
            if isinstance(requirement, dict) and requirement.get("requirement_id")
        ]
        return cls(
            locked_group_id=match.get("selected_group_id") or formula.get("combination_group_id"),
            title_formula_pool=frozenset(
                match.get("eligible_title_formula_codes") or formula.get("eligible_title_formula_codes") or []
            ),
            body_formula_pool=frozenset(
                match.get("eligible_body_formula_codes") or formula.get("eligible_body_formula_codes") or []
            ),
            locked_title_formula_code=(formula.get("selected_title_formula_code") or versions.title_formula_code),
            locked_body_formula_code=(formula.get("selected_body_formula_code") or versions.body_formula_code),
            locked_body_formula_sections=tuple(
                ((strategy_snapshot or {}).get("body_formula") or {}).get("structure_schema") or []
            ),
            locked_body_calling_section_ids=tuple(
                section["id"]
                for section in (
                    (((strategy_snapshot or {}).get("body_formula") or {}).get("body_calling") or {}).get("sections")
                    or []
                )
            ),
            allowed_body_variant_keys=frozenset(
                variant["id"]
                for variant in (
                    (((strategy_snapshot or {}).get("body_formula") or {}).get("body_calling") or {}).get("variants")
                    or []
                )
            ),
            required_title_lexicon_codes=frozenset(
                ((strategy_snapshot or {}).get("title_formula") or {}).get("lexicon_codes") or []
            ),
            allowed_body_lexicon_codes=frozenset(
                ((((strategy_snapshot or {}).get("body_formula") or {}).get("body_calling") or {}).get("lexicon_calls"))
                or []
            ),
            body_variant_lexicon_codes={
                variant["id"]: frozenset(variant.get("lexicon_calls") or [])
                for variant in (
                    (((strategy_snapshot or {}).get("body_formula") or {}).get("body_calling") or {}).get("variants")
                    or []
                )
            },
            allowed_evidence_by_usage={key: frozenset(value) for key, value in evidence_by_usage.items()},
            allowed_asset_ids=frozenset(locks.get("source_asset_ids") or []),
            required_source_asset_ids=tuple(locks.get("required_source_asset_ids") or []),
            locked_title=locks.get("selected_title"),
            artifact_version_id=versions.artifact_version_id,
            visual_plan_hash=locks.get("visual_plan_hash"),
            allowed_numbers=frozenset(allowed_numbers),
            allowed_numbers_by_usage={key: frozenset(value) for key, value in allowed_numbers_by_usage.items()},
            product_material_slots=frozenset(requirement["requirement_id"] for requirement in material_requirements),
            product_material_slot_usages={
                requirement["requirement_id"]: frozenset(requirement.get("target_usages") or [])
                for requirement in material_requirements
            },
            product_material_slot_types={
                requirement["requirement_id"]: str(requirement.get("material_type") or requirement["requirement_id"])
                for requirement in material_requirements
            },
            creation_mode="viral_rewrite" if locks.get("creation_mode") == "viral_rewrite" else "original",
            selected_viral_reference_ids=tuple(
                str(item["id"])
                for item in evidence_bundle.get("items") or []
                if isinstance(item, dict)
                and item.get("id")
                and item.get("metadata", {}).get("material_type") == "viral_example"
                and item.get("metadata", {}).get("selected_reference") is True
            ),
            visual_text_max_chars={
                str(key): int(value)
                for key, value in (locks.get("visual_text_max_chars") or {}).items()
                if str(key) in {"title", "subtitle", "body_excerpt"} and isinstance(value, int) and value > 0
            },
            viral_candidate_ids=frozenset(
                str(item["id"])
                for item in (viral_candidate_collection or {}).get("evidence_items") or []
                if isinstance(item, dict) and item.get("id")
            ),
        )


def get_contract_model(name: str) -> type[StrictContract]:
    model = CONTRACT_REGISTRY.get(name)
    if model is None:
        raise ContractDomainValidationError("contract_unknown", "output_contract", f"未知输出契约: {name}")
    return model


def get_input_contract_model(name: str) -> type[StrictContract]:
    model = INPUT_CONTRACT_REGISTRY.get(name)
    if model is None:
        raise ContractDomainValidationError("input_contract_unknown", "input_contract", f"未知输入契约: {name}")
    return model


def _require_member(value: str, allowed: frozenset[str], field_path: str) -> None:
    if value not in allowed:
        raise ContractDomainValidationError("unknown_id", field_path, f"{field_path} 不在锁定候选范围内: {value}")


def _require_equal(value: str | None, locked: str | None, field_path: str) -> None:
    if not locked or value != locked:
        raise ContractDomainValidationError("locked_value_changed", field_path, f"{field_path} 必须等于锁定值")


def _validate_evidence_ids(ids: list[str], usage: str, context: ContractDomainContext, field_path: str) -> None:
    allowed = context.allowed_evidence_by_usage.get(usage, frozenset())
    unknown = sorted(set(ids) - set(allowed))
    if unknown:
        raise ContractDomainValidationError(
            "evidence_forbidden",
            field_path,
            f"{field_path} 引用了未授权 Evidence ID: {', '.join(unknown)}",
        )


def _validate_outline_calling_contract(result: OutlineResultV1, context: ContractDomainContext) -> None:
    expected_ids = context.locked_body_calling_section_ids
    if expected_ids:
        actual_ids = tuple(section.section_id for section in result.sections)
        if actual_ids != expected_ids:
            raise ContractDomainValidationError(
                "body_calling_section_order_invalid",
                "sections",
                f"正文大纲必须按锁定调用规则输出段落: {', '.join(expected_ids)}",
            )
    if context.allowed_body_variant_keys:
        if result.variant_key not in context.allowed_body_variant_keys:
            raise ContractDomainValidationError(
                "body_calling_variant_invalid",
                "variant_key",
                "正文公式要求从允许的单一维度中选择一个 variant_key",
            )
    elif result.variant_key is not None:
        raise ContractDomainValidationError(
            "body_calling_variant_unexpected",
            "variant_key",
            "当前正文公式不允许选择额外反差维度",
        )


def _validate_formula_lexicon_usage(result: GeneratedContentResultV1, context: ContractDomainContext) -> None:
    title_codes = {item.code for item in result.title.lexicon_usage}
    if context.required_title_lexicon_codes and title_codes != context.required_title_lexicon_codes:
        raise ContractDomainValidationError(
            "title_formula_lexicon_usage_incomplete",
            "title.lexicon_usage",
            "标题必须使用锁定公式对应的全部必选词库",
        )

    body_codes = {item.code for item in result.draft.lexicon_usage}
    if context.allowed_body_lexicon_codes:
        unexpected = body_codes - context.allowed_body_lexicon_codes
        if unexpected:
            raise ContractDomainValidationError(
                "body_formula_lexicon_usage_invalid",
                "draft.lexicon_usage",
                f"正文使用了锁定公式之外的词库: {', '.join(sorted(unexpected))}",
            )
        variant_codes = (
            set().union(*context.body_variant_lexicon_codes.values()) if context.body_variant_lexicon_codes else set()
        )
        required_codes = set(context.allowed_body_lexicon_codes) - variant_codes
        if result.outline.variant_key:
            required_codes.update(context.body_variant_lexicon_codes.get(result.outline.variant_key, frozenset()))
        elif not context.body_variant_lexicon_codes:
            required_codes = set(context.allowed_body_lexicon_codes)
        if not required_codes.issubset(body_codes):
            missing = sorted(required_codes - body_codes)
            raise ContractDomainValidationError(
                "body_formula_lexicon_usage_incomplete",
                "draft.lexicon_usage",
                f"正文缺少锁定公式要求的词库调用: {', '.join(missing)}",
            )


def _validate_numbers(text: str, context: ContractDomainContext, field_path: str, usage: str) -> None:
    # 行首顺序编号只是结构导航，不是事实数字。正文中的其他数字仍必须有证据。
    factual_text = re.sub(r"(?m)^\s*\d{1,2}[.、）)]\s*", "", text)
    numbers = set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", factual_text))
    allowed = context.allowed_numbers_by_usage.get(usage, context.allowed_numbers)
    unknown = sorted(numbers - set(allowed))
    if unknown:
        raise ContractDomainValidationError(
            "unsupported_number", field_path, f"{field_path} 含有无证据数字: {', '.join(unknown)}"
        )


def validate_content_node_result(
    contract_name: str,
    payload: dict[str, Any],
    context: ContractDomainContext,
) -> StrictContract:
    result = get_contract_model(contract_name).model_validate(payload)
    if isinstance(result, ContentValueResultV1):
        _validate_evidence_ids(result.evidence_ids, "any", context, "evidence_ids")
        for index, item in enumerate(result.direction_candidates):
            _validate_evidence_ids(item.evidence_ids, "any", context, f"direction_candidates.{index}.evidence_ids")
        if isinstance(result, ContentDirectionDecisionResultV1):
            candidate_codes = {item.direction_code for item in result.direction_candidates}
            _require_member(result.selected_direction_code, frozenset(candidate_codes), "selected_direction_code")
            _validate_evidence_ids(result.selection_evidence_ids, "any", context, "selection_evidence_ids")
    elif isinstance(result, DirectionSelectionResultV1):
        _validate_evidence_ids(result.evidence_ids, "any", context, "evidence_ids")
    elif isinstance(result, CreationStrategySelectionResultV1):
        _validate_evidence_ids(result.evidence_ids, "any", context, "evidence_ids")
    elif isinstance(result, StrategyExplanationResultV1):
        _require_equal(result.locked_group_id, context.locked_group_id, "locked_group_id")
        _validate_evidence_ids(result.evidence_ids, "any", context, "evidence_ids")
    elif isinstance(result, EvidenceCollectionResultV1):
        new_ids = [item.id for item in result.evidence_items]
        if len(new_ids) != len(set(new_ids)):
            raise ContractDomainValidationError("duplicate_id", "evidence_items", "新 Evidence ID 不能重复")
        reused_ids = sorted(set(new_ids) & set(context.allowed_evidence_by_usage.get("any", frozenset())))
        if reused_ids:
            raise ContractDomainValidationError(
                "evidence_id_reused",
                "evidence_items",
                f"已有 Evidence ID 只能直接引用，不能作为新证据重复提交: {', '.join(reused_ids)}",
            )
        for index, item in enumerate(result.evidence_items):
            material_type = item.metadata.get("material_type")
            if material_type == "price" and item.risk_level != "high_risk":
                raise ContractDomainValidationError(
                    "price_governance_invalid",
                    f"evidence_items.{index}",
                    "价格资料必须标记 high_risk",
                )
            if material_type == "viral_example" and (
                item.allowed_usage != ["style_reference"]
                or item.metadata.get("usage_mode") != "structure_reference_only"
            ):
                raise ContractDomainValidationError(
                    "viral_example_usage_invalid",
                    f"evidence_items.{index}",
                    "爆款样例只能以 structure_reference_only 用于样式参考",
                )
            article_usages = set(item.allowed_usage) & {"title", "body"}
            if (
                item.source_type == "knowledge_base"
                and article_usages
                and material_type not in {"viral_example", "platform_rule", "compliance_rule", "forbidden_term"}
            ):
                if item.metadata.get("writing_ready") is not True:
                    raise ContractDomainValidationError(
                        "knowledge_evidence_not_writing_ready",
                        f"evidence_items.{index}.metadata.writing_ready",
                        "知识库事实必须先确认可直接写入锁定公式，才能作为标题或正文证据",
                    )
                if not str(item.metadata.get("integration_instruction") or "").strip():
                    raise ContractDomainValidationError(
                        "knowledge_evidence_integration_missing",
                        f"evidence_items.{index}.metadata.integration_instruction",
                        "知识库事实必须说明具体写入方式",
                    )
                if not str(item.metadata.get("relevance_reason") or "").strip():
                    raise ContractDomainValidationError(
                        "knowledge_evidence_relevance_missing",
                        f"evidence_items.{index}.metadata.relevance_reason",
                        "知识库事实必须说明与当前主题和写作逻辑的直接关系",
                    )
                if "title" in article_usages:
                    _require_equal(
                        item.metadata.get("title_formula_code"),
                        context.locked_title_formula_code,
                        f"evidence_items.{index}.metadata.title_formula_code",
                    )
                if "body" in article_usages:
                    _require_equal(
                        item.metadata.get("body_formula_code"),
                        context.locked_body_formula_code,
                        f"evidence_items.{index}.metadata.body_formula_code",
                    )
                    section = str(item.metadata.get("formula_section") or "").strip()
                    if not section:
                        raise ContractDomainValidationError(
                            "knowledge_evidence_section_missing",
                            f"evidence_items.{index}.metadata.formula_section",
                            "正文知识库事实必须绑定正文公式中的具体段落",
                        )
                    if context.locked_body_formula_sections:
                        _require_member(
                            section,
                            context.locked_body_formula_sections,
                            f"evidence_items.{index}.metadata.formula_section",
                        )
        if isinstance(result, BusinessRuleEvidenceCollectionResultV1):
            forbidden_types = {"price", "viral_example", "forbidden_term", "compliance_rule"}
            invalid = [
                item.id for item in result.evidence_items if item.metadata.get("material_type") in forbidden_types
            ]
            if invalid:
                raise ContractDomainValidationError(
                    "business_evidence_scope_invalid",
                    "evidence_items",
                    f"业务与规则调研不得提交价格、封禁词或爆款资料: {', '.join(invalid)}",
                )
        elif isinstance(result, PriceEvidenceCollectionResultV1):
            invalid = [item.id for item in result.evidence_items if item.metadata.get("material_type") != "price"]
            if invalid:
                raise ContractDomainValidationError(
                    "price_evidence_scope_invalid",
                    "evidence_items",
                    f"价格调研只能提交价格资料: {', '.join(invalid)}",
                )
        elif isinstance(result, ComplianceEvidenceCollectionResultV1):
            invalid = [
                item.id
                for item in result.evidence_items
                if item.metadata.get("rule_kind") != "forbidden_replacement_map"
            ]
            if invalid:
                raise ContractDomainValidationError(
                    "compliance_evidence_scope_invalid",
                    "evidence_items",
                    f"封禁词调研只能提交问题词替换表: {', '.join(invalid)}",
                )
        elif isinstance(result, ViralCandidateCollectionResultV1):
            invalid = [
                item.id
                for item in result.evidence_items
                if item.metadata.get("material_type") != "viral_example"
                or item.metadata.get("selected_reference") is True
            ]
            if invalid:
                raise ContractDomainValidationError(
                    "viral_candidate_scope_invalid",
                    "evidence_items",
                    f"爆款候选调研只能提交尚未选定的爆款样例: {', '.join(invalid)}",
                )
        viral_references = [
            item
            for item in result.evidence_items
            if item.metadata.get("material_type") == "viral_example" and item.metadata.get("selected_reference") is True
        ]
        is_final_collection = result.__class__ is EvidenceCollectionResultV1
        if is_final_collection and context.creation_mode == "viral_rewrite":
            if len(viral_references) != 1:
                raise ContractDomainValidationError(
                    "viral_reference_required",
                    "evidence_items",
                    "爆款仿写模式必须且只能选择一篇爆款参考",
                )
            blueprint = viral_references[0].metadata.get("reference_blueprint")
            required_blueprint_fields = {
                "title_pattern",
                "title_slot_sequence",
                "opening_hook",
                "content_block_sequence",
                "narrative_structure",
                "paragraph_rhythm",
                "list_pattern",
                "emoji_pattern",
                "interaction_style",
            }
            if not isinstance(blueprint, dict) or not required_blueprint_fields.issubset(blueprint):
                raise ContractDomainValidationError(
                    "viral_reference_blueprint_invalid",
                    "evidence_items",
                    "选中的爆款参考缺少完整结构蓝图",
                )
            if not isinstance(blueprint.get("title_slot_sequence"), list) or not blueprint["title_slot_sequence"]:
                raise ContractDomainValidationError(
                    "viral_reference_blueprint_invalid",
                    "evidence_items",
                    "爆款结构蓝图缺少可执行的标题槽位顺序",
                )
            if not isinstance(blueprint.get("content_block_sequence"), list) or not blueprint["content_block_sequence"]:
                raise ContractDomainValidationError(
                    "viral_reference_blueprint_invalid",
                    "evidence_items",
                    "爆款结构蓝图缺少动态正文信息块顺序",
                )
            list_pattern = blueprint.get("list_pattern")
            if not isinstance(list_pattern, dict) or list_pattern.get("type") not in {
                "none",
                "numbered",
                "emoji",
                "bulleted",
                "mixed",
            }:
                raise ContractDomainValidationError(
                    "viral_reference_blueprint_invalid",
                    "evidence_items",
                    "爆款结构蓝图必须声明真实列表类型",
                )
            selection_basis = viral_references[0].metadata.get("selection_basis")
            if not isinstance(selection_basis, dict) or not {
                "input_variable_paths",
                "matched_dimensions",
                "structure_fillability",
                "candidate_comparison",
            }.issubset(selection_basis):
                raise ContractDomainValidationError(
                    "viral_reference_selection_invalid",
                    "evidence_items",
                    "爆款参考缺少基于当前输入变量的可追溯选择依据",
                )
            if (
                not isinstance(selection_basis.get("input_variable_paths"), list)
                or not selection_basis["input_variable_paths"]
            ):
                raise ContractDomainValidationError(
                    "viral_reference_selection_invalid",
                    "evidence_items",
                    "爆款参考选择必须记录实际使用的输入变量路径",
                )
            fillability = selection_basis.get("structure_fillability")
            if not isinstance(fillability, dict) or fillability.get("unfilled_required_slots") != []:
                raise ContractDomainValidationError(
                    "viral_reference_unfillable",
                    "evidence_items",
                    "选中的爆款结构仍有当前输入无法承接的关键槽位",
                )
            if (
                not isinstance(selection_basis.get("candidate_comparison"), list)
                or not selection_basis["candidate_comparison"]
            ):
                raise ContractDomainValidationError(
                    "viral_reference_selection_invalid",
                    "evidence_items",
                    "爆款参考选择必须保留候选比较结论",
                )
        elif is_final_collection and viral_references:
            raise ContractDomainValidationError(
                "viral_reference_forbidden",
                "evidence_items",
                "原创模式不得选用爆款参考",
            )
    elif isinstance(result, ViralReferenceSelectionResultV1):
        if context.creation_mode == "original":
            if result.selected_candidate_id is not None:
                raise ContractDomainValidationError(
                    "viral_reference_forbidden",
                    "selected_candidate_id",
                    "原创模式不得选择爆款参考",
                )
        else:
            if not result.selected_candidate_id:
                raise ContractDomainValidationError(
                    "viral_reference_required",
                    "selected_candidate_id",
                    "爆款仿写模式必须从候选中选择一篇可填充参考",
                )
            _require_member(result.selected_candidate_id, context.viral_candidate_ids, "selected_candidate_id")
            blueprint = result.reference_blueprint or {}
            required_blueprint_fields = {
                "title_pattern",
                "title_slot_sequence",
                "opening_hook",
                "content_block_sequence",
                "narrative_structure",
                "paragraph_rhythm",
                "list_pattern",
                "emoji_pattern",
                "interaction_style",
            }
            if not required_blueprint_fields.issubset(blueprint):
                raise ContractDomainValidationError(
                    "viral_reference_blueprint_invalid",
                    "reference_blueprint",
                    "选中的爆款参考缺少完整结构蓝图",
                )
            if not blueprint.get("title_slot_sequence") or not blueprint.get("content_block_sequence"):
                raise ContractDomainValidationError(
                    "viral_reference_blueprint_invalid",
                    "reference_blueprint",
                    "爆款结构蓝图缺少标题槽位或正文信息块顺序",
                )
            if (blueprint.get("list_pattern") or {}).get("type") not in {
                "none",
                "numbered",
                "emoji",
                "bulleted",
                "mixed",
            }:
                raise ContractDomainValidationError(
                    "viral_reference_blueprint_invalid",
                    "reference_blueprint.list_pattern",
                    "爆款结构蓝图必须声明真实列表类型",
                )
            selection_basis = result.selection_basis
            if not {
                "input_variable_paths",
                "matched_dimensions",
                "structure_fillability",
                "candidate_comparison",
            }.issubset(selection_basis):
                raise ContractDomainValidationError(
                    "viral_reference_selection_invalid",
                    "selection_basis",
                    "爆款参考缺少基于当前输入变量的可追溯选择依据",
                )
            if not selection_basis.get("input_variable_paths") or not selection_basis.get("candidate_comparison"):
                raise ContractDomainValidationError(
                    "viral_reference_selection_invalid",
                    "selection_basis",
                    "爆款参考选择必须记录输入变量和候选比较结论",
                )
            if (selection_basis.get("structure_fillability") or {}).get("unfilled_required_slots") != []:
                raise ContractDomainValidationError(
                    "viral_reference_unfillable",
                    "selection_basis.structure_fillability",
                    "选中的爆款结构仍有当前输入无法承接的关键槽位",
                )
    elif isinstance(result, ProductEvidenceCollectionResultV1):
        new_ids = [item.id for item in result.evidence_items]
        if len(new_ids) != len(set(new_ids)):
            raise ContractDomainValidationError("duplicate_id", "evidence_items", "新产品 Evidence ID 不能重复")
        reused_ids = sorted(set(new_ids) & set(context.allowed_evidence_by_usage.get("any", frozenset())))
        if reused_ids:
            raise ContractDomainValidationError(
                "evidence_id_reused",
                "evidence_items",
                f"已有 Evidence ID 只能在 slot_mappings 中引用，不能作为新产品证据重复提交: {', '.join(reused_ids)}",
            )
        new_by_id = {item.id: item for item in result.evidence_items}
        available_ids = set(context.allowed_evidence_by_usage.get("any", frozenset())) | set(new_ids)
        for index, mapping in enumerate(result.slot_mappings):
            _require_member(mapping.slot, context.product_material_slots, f"slot_mappings.{index}.slot")
            allowed_target_usages = context.product_material_slot_usages.get(mapping.slot, frozenset())
            if allowed_target_usages and mapping.target_usage not in allowed_target_usages:
                raise ContractDomainValidationError(
                    "product_slot_usage_invalid",
                    f"slot_mappings.{index}.target_usage",
                    f"资料槽位 {mapping.slot} 只允许用于: {', '.join(sorted(allowed_target_usages))}",
                )
            unknown_ids = sorted(set(mapping.evidence_ids) - available_ids)
            if unknown_ids:
                raise ContractDomainValidationError(
                    "evidence_forbidden",
                    f"slot_mappings.{index}.evidence_ids",
                    f"公式槽位引用了未知 Evidence ID: {', '.join(unknown_ids)}",
                )
            forbidden_ids = [
                evidence_id
                for evidence_id in mapping.evidence_ids
                if (
                    mapping.target_usage not in new_by_id[evidence_id].allowed_usage
                    if evidence_id in new_by_id
                    else evidence_id not in context.allowed_evidence_by_usage.get(mapping.target_usage, frozenset())
                )
            ]
            if forbidden_ids:
                raise ContractDomainValidationError(
                    "product_evidence_usage_invalid",
                    f"slot_mappings.{index}.evidence_ids",
                    f"公式槽位引用了未授权用于 {mapping.target_usage} 的 Evidence ID: {', '.join(forbidden_ids)}",
                )
            expected_material_type = context.product_material_slot_types.get(mapping.slot)
            mismatched_ids = [
                evidence_id
                for evidence_id in mapping.evidence_ids
                if evidence_id in new_by_id
                and new_by_id[evidence_id].metadata.get("material_type")
                and new_by_id[evidence_id].metadata.get("material_type") != expected_material_type
            ]
            if mismatched_ids:
                raise ContractDomainValidationError(
                    "product_evidence_material_mismatch",
                    f"slot_mappings.{index}.evidence_ids",
                    f"资料槽位 {mapping.slot} 引用了其他资料类型的 Evidence ID: {', '.join(mismatched_ids)}",
                )
        for index, item in enumerate(result.evidence_items):
            material_type = item.metadata.get("material_type")
            if material_type == "price":
                if item.risk_level != "high_risk":
                    raise ContractDomainValidationError(
                        "price_governance_invalid",
                        f"evidence_items.{index}",
                        "价格资料必须标记 high_risk",
                    )
            if material_type == "viral_example" and (
                item.allowed_usage != ["style_reference"]
                or item.metadata.get("usage_mode") != "structure_reference_only"
            ):
                raise ContractDomainValidationError(
                    "viral_example_usage_invalid",
                    f"evidence_items.{index}",
                    "爆款样例只能以 structure_reference_only 用于样式参考",
                )
    elif isinstance(result, FormulaRankingResultV1):
        for index, item in enumerate(result.title_rankings):
            _require_member(item.formula_code, context.title_formula_pool, f"title_rankings.{index}.formula_code")
        for index, item in enumerate(result.body_rankings):
            _require_member(item.formula_code, context.body_formula_pool, f"body_rankings.{index}.formula_code")
    elif isinstance(result, TitleCandidatesResultV1):
        _require_equal(
            result.selected_title_formula_code,
            context.locked_title_formula_code,
            "selected_title_formula_code",
        )
        _validate_evidence_ids(result.evidence_ids, "title", context, "evidence_ids")
        for index, item in enumerate(result.candidates):
            _require_equal(item.formula_code, context.locked_title_formula_code, f"candidates.{index}.formula_code")
            _validate_evidence_ids(item.evidence_ids, "title", context, f"candidates.{index}.evidence_ids")
            _validate_numbers(item.text, context, f"candidates.{index}.text", "title")
    elif isinstance(result, OutlineResultV1):
        _require_equal(result.body_formula_code, context.locked_body_formula_code, "body_formula_code")
        _validate_outline_calling_contract(result, context)
        for index, item in enumerate(result.sections):
            _validate_evidence_ids(item.evidence_ids, "body", context, f"sections.{index}.evidence_ids")
    elif isinstance(result, ContentDraftResultV1):
        _require_equal(result.body_formula_code, context.locked_body_formula_code, "body_formula_code")
        for index, item in enumerate(result.paragraph_evidence):
            _validate_evidence_ids(item.evidence_ids, "body", context, f"paragraph_evidence.{index}.evidence_ids")
        _validate_numbers("\n".join([result.body, *result.topics]), context, "body", "body")
    elif isinstance(result, GeneratedContentResultV1):
        if context.creation_mode == "viral_rewrite":
            if len(context.selected_viral_reference_ids) != 1:
                raise ContractDomainValidationError(
                    "viral_reference_not_frozen",
                    "evidence_bundle",
                    "爆款仿写模式生成前必须冻结唯一爆款结构参考",
                )
        _validate_formula_lexicon_usage(result, context)
        _require_equal(result.title.formula_code, context.locked_title_formula_code, "title.formula_code")
        _validate_evidence_ids(result.title.evidence_ids, "title", context, "title.evidence_ids")
        _validate_numbers(result.title.text, context, "title.text", "title")
        _require_equal(result.outline.body_formula_code, context.locked_body_formula_code, "outline.body_formula_code")
        _validate_outline_calling_contract(result.outline, context)
        for index, item in enumerate(result.outline.sections):
            _validate_evidence_ids(item.evidence_ids, "body", context, f"outline.sections.{index}.evidence_ids")
        _require_equal(result.draft.body_formula_code, context.locked_body_formula_code, "draft.body_formula_code")
        for index, item in enumerate(result.draft.paragraph_evidence):
            _validate_evidence_ids(item.evidence_ids, "body", context, f"draft.paragraph_evidence.{index}.evidence_ids")
        _validate_numbers("\n".join([result.draft.body, *result.draft.topics]), context, "draft.body", "body")
    elif isinstance(result, PersonaPolishResultV1):
        for index, item in enumerate(result.preserved_fact_checks):
            _validate_evidence_ids([item.evidence_id], "body", context, f"preserved_fact_checks.{index}.evidence_id")
            if not item.preserved:
                raise ContractDomainValidationError(
                    "fact_changed", f"preserved_fact_checks.{index}.preserved", "人设润色不得改变事实"
                )
        _validate_numbers(result.polished_body, context, "polished_body", "body")
    elif isinstance(result, ContentReviewResultV1):
        for index, item in enumerate(result.checks):
            _validate_evidence_ids(item.evidence_ids, "any", context, f"checks.{index}.evidence_ids")
    elif isinstance(result, VisualPlanResultV1):
        _require_equal(result.artifact_version_id, context.artifact_version_id, "artifact_version_id")
        if context.required_source_asset_ids and tuple(result.source_asset_ids) != context.required_source_asset_ids:
            raise ContractDomainValidationError(
                "visual_source_locked",
                "source_asset_ids",
                "视觉方案必须且只能使用任务已锁定的图库图片",
            )
        for index, asset_id in enumerate(result.source_asset_ids):
            _require_member(asset_id, context.allowed_asset_ids, f"source_asset_ids.{index}")
        _validate_evidence_ids(result.evidence_ids, "visual", context, "evidence_ids")
        _validate_numbers("\n".join(result.text), context, "text", "visual")
        role_indexes = {"title": 0, "subtitle": 1, "body_excerpt": 1}
        for role, max_chars in context.visual_text_max_chars.items():
            index = role_indexes[role]
            if index < len(result.text) and len(result.text[index]) > max_chars:
                raise ContractDomainValidationError(
                    "visual_text_too_long",
                    f"text.{index}",
                    f"封面{role}最多 {max_chars} 个字符，请缩短后重新提交视觉方案",
                )
    elif isinstance(result, CoverJobSubmissionResultV1):
        _require_equal(result.plan_hash, context.visual_plan_hash, "plan_hash")
        if context.required_source_asset_ids and tuple(result.source_asset_ids) != context.required_source_asset_ids:
            raise ContractDomainValidationError(
                "visual_source_locked",
                "source_asset_ids",
                "封面任务必须使用任务已锁定的图库图片",
            )
        for index, asset_id in enumerate(result.source_asset_ids):
            _require_member(asset_id, context.allowed_asset_ids, f"source_asset_ids.{index}")
    elif isinstance(result, VisualReviewResultV1):
        for index, item in enumerate(result.assets):
            _require_member(item.asset_id, context.allowed_asset_ids, f"assets.{index}.asset_id")
        if result.recommended_asset_id is not None:
            _require_member(result.recommended_asset_id, context.allowed_asset_ids, "recommended_asset_id")
    return result


@dataclass(slots=True)
class ContentNodeResultCollector:
    contract_name: str
    domain_context: ContractDomainContext
    runtime_context: Any
    submission_count: int = 0
    result: dict[str, Any] | None = None

    def _with_verified_knowledge_provenance(self, payload: dict[str, Any]) -> dict[str, Any]:
        evidence_contracts = {
            "EvidenceCollectionResultV1",
            "ProductEvidenceCollectionResultV1",
            "BusinessRuleEvidenceCollectionResultV1",
            "PriceEvidenceCollectionResultV1",
            "ComplianceEvidenceCollectionResultV1",
            "ViralCandidateCollectionResultV1",
        }
        if self.contract_name not in evidence_contracts:
            return payload

        normalized = deepcopy(payload)
        normalized["evidence_items"] = [
            item.model_dump(mode="python") if isinstance(item, EvidenceDraftV1) else item
            for item in normalized.get("evidence_items") or []
        ]
        retrieved = getattr(self.runtime_context, "_content_retrieved_knowledge_results", {}) or {}
        for index, item in enumerate(normalized.get("evidence_items") or []):
            if item.get("source_type") != "knowledge_base":
                continue
            source_id = str(item.get("source_id") or "")
            matches = retrieved.get(source_id) or []
            if len(matches) != 1:
                raise ContractDomainValidationError(
                    "knowledge_source_unknown",
                    f"evidence_items.{index}.source_id",
                    "知识库 Evidence 的 source_id 必须等于本节点唯一检索结果 ID",
                )
            item["metadata"] = {**(item.get("metadata") or {}), **matches[0]["metadata"]}
        return normalized

    async def submit(self, **payload: Any) -> dict[str, Any]:
        from yuxi.services.run_queue_service import append_content_runtime_event

        event_payload = {
            "tool_name": "submit_content_node_result",
            "output_contract": self.contract_name,
        }
        await append_content_runtime_event(
            self.runtime_context,
            "content.tool.called",
            event_payload,
        )
        try:
            if self.submission_count:
                raise ContractDomainValidationError(
                    "duplicate_submission", "submit_content_node_result", "Agent 节点结果只能提交一次"
                )
            if self.contract_name == "CoverJobSubmissionResultV1":
                created_submission = getattr(self.runtime_context, "_content_cover_job_submission", None)
                if created_submission is None:
                    raise ContractDomainValidationError(
                        "cover_job_not_created",
                        "cover_job_id",
                        "必须先成功创建 CoverJob，不能提交未创建的任务",
                    )
                if payload != created_submission:
                    raise ContractDomainValidationError(
                        "cover_job_submission_mismatch",
                        "cover_job_id",
                        "封面任务结果必须原样使用创建工具返回值",
                    )
            required = set(getattr(self.runtime_context, "_required_skill_closure", []) or [])
            activated = set(getattr(self.runtime_context, "_activated_required_skills", []) or [])
            if not required.issubset(activated):
                raise ContractDomainValidationError(
                    "required_skill_not_activated", "required_skills", "未激活全部必需 Skills，禁止提交"
                )
            required_knowledge_names = {
                "EvidenceCollectionResultV1": {"价格库", "品牌知识库", "平台规则", "爆款库"},
                "BusinessRuleEvidenceCollectionResultV1": {"品牌知识库", "平台规则"},
                "PriceEvidenceCollectionResultV1": {"价格库"},
                "ComplianceEvidenceCollectionResultV1": {"封禁词库"},
                "ViralCandidateCollectionResultV1": {"爆款库"},
            }.get(self.contract_name)
            if required_knowledge_names is not None:
                visible = {
                    str(item.get("kb_id") or ""): str(item.get("name") or "")
                    for item in (getattr(self.runtime_context, "_visible_knowledge_bases", []) or [])
                    if isinstance(item, dict) and item.get("kb_id")
                }
                required_knowledge_bases = {
                    kb_id for kb_id, name in visible.items() if name in required_knowledge_names
                }
                queried = set(getattr(self.runtime_context, "_content_queried_knowledge_bases", set()) or set())
                missing_queries = sorted(required_knowledge_bases - queried)
                if missing_queries:
                    missing_names = [visible[kb_id] for kb_id in missing_queries]
                    raise ContractDomainValidationError(
                        "required_knowledge_not_queried",
                        "submit_content_node_result",
                        f"创作取材前必须检索已授权的必需知识库: {', '.join(missing_names)}",
                    )
            validated = validate_content_node_result(
                self.contract_name,
                self._with_verified_knowledge_provenance(payload),
                self.domain_context,
            )
            self.result = validated.model_dump(mode="json")
            self.submission_count += 1
        except Exception as exc:
            failure_payload = {**event_payload, "error_type": type(exc).__name__}
            if isinstance(exc, ContractDomainValidationError):
                failure_payload.update(
                    {
                        "error_code": exc.code,
                        "error_field_path": exc.field_path,
                        "message": str(exc),
                    }
                )
            await append_content_runtime_event(
                self.runtime_context,
                "content.tool.failed",
                failure_payload,
            )
            raise
        await append_content_runtime_event(
            self.runtime_context,
            "content.tool.completed",
            event_payload,
        )
        return {"accepted": True, "contract": self.contract_name}

    def finalize(self) -> dict[str, Any]:
        if self.submission_count != 1 or self.result is None:
            raise ContractDomainValidationError(
                "result_not_submitted", "submit_content_node_result", "Agent 未通过结构化结果工具提交结果"
            )
        return self.result


def build_content_result_tool(collector: ContentNodeResultCollector) -> StructuredTool:
    async def submit_content_node_result(**payload: Any) -> dict[str, Any]:
        try:
            return await collector.submit(**payload)
        except ContractDomainValidationError as exc:
            raise ToolException(f"结果未通过业务校验，请修正后重新提交：{exc}") from exc

    def validation_error_message(exc: ValidationError) -> str:
        first = exc.errors()[0] if exc.errors() else {}
        field_path = ".".join(str(item) for item in first.get("loc") or [])
        message = str(first.get("msg") or "结果结构不符合契约")
        return f"结果未通过结构校验，请修正后重新提交：{field_path} {message}".strip()

    return StructuredTool.from_function(
        coroutine=submit_content_node_result,
        name="submit_content_node_result",
        description=(
            f"仅用于提交当前节点的 {collector.contract_name} 结果。只接受一次有效结果；"
            "校验失败时必须根据工具提示修正后重新提交。"
        ),
        args_schema=get_contract_model(collector.contract_name),
        infer_schema=False,
        handle_tool_error=True,
        handle_validation_error=validation_error_message,
    )


__all__ = [
    "CONTRACT_REGISTRY",
    "INPUT_CONTRACT_REGISTRY",
    "ContentAgentNodeInputV1",
    "ContentAgentNodeInputV2",
    "ContentNodeResultCollector",
    "ContractDomainContext",
    "ContractDomainValidationError",
    "ProductEvidenceCollectionResultV1",
    "ProductEvidencePackV1",
    "ProductMaterialRequirementsV1",
    "StrategySnapshotV1",
    "build_content_result_tool",
    "get_contract_model",
    "get_input_contract_model",
    "validate_content_node_result",
]
