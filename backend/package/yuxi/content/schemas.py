from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from yuxi.content.model.industry.pack import IndustryPackRegressionMetrics

ContentMode = Literal["quick", "pro"]
CreationMode = Literal["original", "viral_rewrite"]


class ContentTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    industry_template_id: str
    mode: ContentMode = "quick"
    creation_mode: CreationMode = "original"
    content_goal: str | None = None
    content_type_code: str | None = Field(default=None, pattern=r"^CT0[1-7]$")
    persona_profile_version_id: str | None = None
    channel_profile_version_id: str | None = None
    name: str | None = None
    project_id: str | None = None


class ContentTaskUpdate(BaseModel):
    name: str | None = None
    content_goal: str | None = None
    content_type_code: str | None = Field(default=None, pattern=r"^CT0[1-7]$")
    persona_profile_version_id: str | None = None
    channel_profile_version_id: str | None = None
    mode: ContentMode | None = None


class ContentVisualMaterialSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_item_id: str | None = Field(default=None, min_length=1, max_length=64)
    poster_template_id: str | None = Field(default=None, min_length=1, max_length=64)
    hycanvas_template_id: str | None = Field(
        default=None,
        pattern=(
            r"^(?:xiaohongshu-[a-z0-9-]+|"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$"
        ),
    )


class ContentBriefPayload(BaseModel):
    brand: dict[str, Any] = Field(default_factory=dict)
    audience: list[str] = Field(default_factory=list)
    business_variables: dict[str, Any] = Field(default_factory=dict)
    persona: dict[str, Any] = Field(default_factory=dict)
    required_terms: list[str] = Field(default_factory=list)
    forbidden_terms: list[str] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    locked_fields: list[str] = Field(default_factory=list)
    form_values: dict[str, Any] = Field(default_factory=dict)
    material_confirmations: list[dict[str, Any]] = Field(default_factory=list)
    visual_material: ContentVisualMaterialSelection | None = None


class ContentBriefSave(BaseModel):
    brief: ContentBriefPayload


class ContentOCRCorrection(BaseModel):
    corrected_text: str = Field(max_length=200_000)


class ContentRunCreate(BaseModel):
    request_id: str
    model_spec: str | None = None


class ContentRunResume(BaseModel):
    request_id: str
    resume: dict[str, Any]


class ContentNodeRetry(BaseModel):
    request_id: str
    node_id: str | None = None
    model_spec: str | None = None


class ContentArtifactUpdate(BaseModel):
    title: str
    body: str
    topics: list[str] = Field(default_factory=list)


class ContentArtifactAIEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    instruction: str = Field(min_length=1, max_length=4000)
    expected_version: int = Field(ge=1)
    model_spec: str | None = Field(default=None, max_length=255)


class ContentArtifactAIEditOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=100_000)
    topics: list[str] = Field(default_factory=list, max_length=30)


class ContentArtifactReview(BaseModel):
    model_spec: str | None = None


class ContentArtifactRegenerate(BaseModel):
    request_id: str
    scope: Literal["titles", "body", "topics", "all"] = "body"
    model_spec: str | None = None


class ContentFinalizeRequest(BaseModel):
    note: str | None = None


class XiaohongshuAccountCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    display_name: str = Field(min_length=1, max_length=120)


class XiaohongshuAccountUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None


class XiaohongshuDistributionCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    request_id: str = Field(min_length=8, max_length=120)
    account_ids: list[str] = Field(min_length=1, max_length=20)
    mode: Literal["draft", "publish"] = "draft"
    title: str | None = Field(default=None, min_length=1, max_length=20)
    body: str | None = Field(default=None, min_length=1, max_length=1000)
    topics: list[str] | None = Field(default=None, max_length=10)
    confirm_publish: bool = False


class XiaohongshuBrowserOpen(BaseModel):
    target: Literal["home", "drafts"] = "home"


class XiaohongshuBrowserAction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    action: Literal["click", "type", "keypress", "scroll"]
    x: float | None = Field(default=None, ge=0, le=4000)
    y: float | None = Field(default=None, ge=0, le=4000)
    text: str | None = Field(default=None, max_length=2000)
    key: str | None = Field(default=None, max_length=32)
    delta_y: int | None = Field(default=None, ge=-2000, le=2000)


class RuleVersionAction(BaseModel):
    note: str | None = None


class IndustryPackTransitionRequest(BaseModel):
    target_status: Literal["validated", "canary", "published", "deprecated"]
    note: str | None = None


class IndustryPackRegressionSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_run_ids: list[str] = Field(min_length=1)
    sample_count: int = Field(ge=1)
    metrics: IndustryPackRegressionMetrics
    candidate_recommendations: list[dict[str, Any]] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=1000)


class RuleInputBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class RuleDraftCreate(RuleInputBase):
    source_version_id: str
    changelog: str = Field(default="", max_length=1000)


class CreationMethodInput(RuleInputBase):
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=80)
    method_type: Literal["core", "enhancer"] = "core"
    principle: str = Field(min_length=1, max_length=4000)
    suitable_scenes: list[str] = Field(default_factory=list)
    sentence_patterns: list[str] = Field(default_factory=list)
    tag_schema: dict[str, Any] = Field(default_factory=dict)
    variable_schema: list[str] = Field(default_factory=list)
    risk_rules: list[str] = Field(default_factory=list)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0)


class TitleFormulaInput(RuleInputBase):
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=120)
    suitable_scenes: list[str] = Field(default_factory=list)
    core_goal: str = Field(min_length=1, max_length=4000)
    reference_examples: list[str] = Field(default_factory=list)
    variable_schema: list[str] = Field(default_factory=list)
    compatible_methods: list[str] = Field(default_factory=list)
    risk_rules: list[str] = Field(default_factory=list)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0)


class ContentFormulaInput(RuleInputBase):
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=120)
    industry_aliases: dict[str, str] = Field(default_factory=dict)
    compatible_methods: list[str] = Field(default_factory=list)
    suitable_scenes: list[str] = Field(default_factory=list)
    business_pains: list[str] = Field(default_factory=list)
    structure_schema: list[str] = Field(default_factory=list)
    reference_examples: list[str] = Field(default_factory=list)
    required_variables: list[str] = Field(default_factory=list)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    risk_rules: list[str] = Field(default_factory=list)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0)


class MethodMemberInput(RuleInputBase):
    method_code: str = Field(min_length=1, max_length=32)
    role: Literal["primary", "supporting"]
    order: int = Field(ge=1, le=4)


class CombinationRuleInput(RuleInputBase):
    schema_version: Literal[3] = 3
    content_goal_codes: list[str] = Field(default_factory=list)
    content_type_codes: list[str] = Field(default_factory=list)
    industry_scope: list[str] = Field(default_factory=list)
    channel_scope: list[str] = Field(default_factory=list)
    narrative_axis_codes: list[str] = Field(default_factory=list)
    combination_type: Literal["single", "double", "triple", "quadruple"]
    method_members: list[MethodMemberInput] = Field(min_length=1, max_length=4)
    title_formula_candidate_codes: list[str] = Field(min_length=1)
    body_formula_candidate_codes: list[str] = Field(min_length=1)
    scenario_description: str = Field(min_length=1, max_length=4000)
    required_variable_codes: list[str] = Field(default_factory=list)
    required_evidence_types: list[str] = Field(default_factory=list)
    priority: int = Field(default=0, ge=0, le=10000)
    conditions: dict[str, Any] = Field(default_factory=dict)
    hard_conditions: dict[str, Any] = Field(default_factory=dict)
    score_weights: dict[str, float] = Field(default_factory=dict)
    fallback_rule_id: str | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    recommendation_reason: str = Field(default="", max_length=4000)


class TitleCandidate(BaseModel):
    id: str
    text: str
    formula_code: str
    pattern_code: str | None = None
    variable_mapping: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)


class GeneratedContent(BaseModel):
    body: str
    topics: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    paragraph_evidence: list[dict[str, Any]] = Field(default_factory=list)


class ReviewCheck(BaseModel):
    code: str
    level: Literal["info", "warning", "error"]
    location: str
    message: str
    evidence_ids: list[str] = Field(default_factory=list)
    suggestion: str | None = None


class ReviewReport(BaseModel):
    status: Literal["passed", "warning", "blocked"]
    checks: list[ReviewCheck] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# contentSwarm V3 可发布规则协议
# ---------------------------------------------------------------------------


class ContentTypeInput(RuleInputBase):
    code: str = Field(pattern=r"^CT0[1-7]$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    supported_goals: list[str] = Field(default_factory=list)
    required_variable_codes: list[str] = Field(default_factory=list)
    evidence_policy: dict[str, Any] = Field(default_factory=dict)
    default_narrative_axes: list[str] = Field(default_factory=list)
    default_body_formula_codes: list[str] = Field(default_factory=list)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0)


class FormulaSlotInput(RuleInputBase):
    slot_key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    value_type: str = Field(default="string", max_length=32)
    source_type: Literal["brief", "evidence", "evidence_or_goal", "persona", "lexicon", "system"]
    source_path: str | None = Field(default=None, max_length=255)
    alternative_sources: list[dict[str, Any]] = Field(default_factory=list)
    lexicon_pack_codes: list[str] = Field(default_factory=list)
    required: bool = True
    evidence_required: bool = False
    fallback_policy: Literal["block", "omit", "ask_user", "use_goal", "use_lexicon"] = "block"
    validation_schema: dict[str, Any] = Field(default_factory=dict)
    max_length: int | None = Field(default=None, ge=1, le=10000)
    sort_order: int = Field(default=0, ge=0)


class FormulaPatternInput(RuleInputBase):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    formula_kind: Literal["title", "body"]
    formula_code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=160)
    template_text: str = Field(min_length=1, max_length=20000)
    paragraph_schema: list[dict[str, Any]] = Field(default_factory=list)
    content_type_codes: list[str] = Field(default_factory=list)
    channel_scope: list[str] = Field(default_factory=list)
    risk_policy: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0)
    slots: list[FormulaSlotInput] = Field(default_factory=list)


class VariableDefinitionInput(RuleInputBase):
    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=120)
    value_type: str = Field(default="string", max_length=32)
    unit_schema: dict[str, Any] = Field(default_factory=dict)
    evidence_policy: dict[str, Any] = Field(default_factory=dict)
    sensitivity: Literal["normal", "sensitive", "high_risk"] = "normal"
    allowed_usages: list[str] = Field(default_factory=list)
    validation_schema: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0)


class RuleBundleUpdate(RuleInputBase):
    changelog: str = Field(default="", max_length=1000)
    methods: list[CreationMethodInput] = Field(default_factory=list, max_length=200)
    title_formulas: list[TitleFormulaInput] = Field(default_factory=list, max_length=500)
    content_formulas: list[ContentFormulaInput] = Field(default_factory=list, max_length=500)
    combination_rules: list[CombinationRuleInput] = Field(default_factory=list, max_length=1000)
    content_types: list[ContentTypeInput] = Field(default_factory=list, max_length=100)
    formula_patterns: list[FormulaPatternInput] = Field(default_factory=list, max_length=1000)
    variables: list[VariableDefinitionInput] = Field(default_factory=list, max_length=1000)


class StrategyRecommendV3Request(BaseModel):
    content_direction_code: str | None = Field(default=None, pattern=r"^CT0[1-7]$")
    limit: int = Field(default=28, ge=1, le=28)


class ChannelPreviewRequest(BaseModel):
    channel_profile_version_id: str
    title: str
    body: str
    topics: list[str] = Field(default_factory=list)


class MaterialCreate(BaseModel):
    attachment_id: str = Field(min_length=1, max_length=128)
    object_uri: str = Field(min_length=1)
    media_type: Literal["image", "video", "document", "text", "audio"]
    original_filename: str | None = Field(default=None, max_length=512)
    extracted_text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_hash: str = Field(min_length=8, max_length=128)
    allowed_usage: list[str] = Field(default_factory=list)


class MaterialConfirmation(BaseModel):
    verified_status: Literal["pending", "confirmed", "rejected"]
    privacy_status: Literal["unreviewed", "approved", "restricted", "blocked"]
    allowed_usage: list[str] = Field(default_factory=list)
    confirmed_facts: list[dict[str, Any]] = Field(default_factory=list)
