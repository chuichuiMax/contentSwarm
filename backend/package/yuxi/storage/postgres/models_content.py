"""通用内容策略工作台业务模型。"""

from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from yuxi.storage.postgres.models_business import Base
from yuxi.utils.datetime_utils import format_utc_datetime, utc_now_naive


class ContentRuleVersion(Base):
    __tablename__ = "content_rule_versions"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    version = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="draft", index=True)
    changelog = Column(Text, nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    published_at = Column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "version", name="uq_content_rule_versions_tenant_version"),)


class CreationMethod(Base):
    __tablename__ = "content_creation_methods"

    id = Column(String(64), primary_key=True)
    version_id = Column(
        String(64), ForeignKey("content_rule_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code = Column(String(32), nullable=False)
    name = Column(String(80), nullable=False)
    method_type = Column(String(32), nullable=False, default="core")
    principle = Column(Text, nullable=False)
    suitable_scenes = Column(JSON, nullable=False, default=list)
    sentence_patterns = Column(JSON, nullable=False, default=list)
    tag_schema = Column(JSON, nullable=False, default=dict)
    variable_schema = Column(JSON, nullable=False, default=list)
    risk_rules = Column(JSON, nullable=False, default=list)
    enabled = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("version_id", "code", name="uq_content_creation_methods_version_code"),)


class TitleFormula(Base):
    __tablename__ = "content_title_formulas"

    id = Column(String(64), primary_key=True)
    version_id = Column(
        String(64), ForeignKey("content_rule_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code = Column(String(32), nullable=False)
    name = Column(String(120), nullable=False)
    suitable_scenes = Column(JSON, nullable=False, default=list)
    core_goal = Column(Text, nullable=False)
    reference_examples = Column(JSON, nullable=False, default=list)
    variable_schema = Column(JSON, nullable=False, default=list)
    compatible_methods = Column(JSON, nullable=False, default=list)
    risk_rules = Column(JSON, nullable=False, default=list)
    enabled = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("version_id", "code", name="uq_content_title_formulas_version_code"),)


class ContentFormula(Base):
    __tablename__ = "content_body_formulas"

    id = Column(String(64), primary_key=True)
    version_id = Column(
        String(64), ForeignKey("content_rule_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code = Column(String(32), nullable=False)
    name = Column(String(120), nullable=False)
    industry_aliases = Column(JSON, nullable=False, default=dict)
    compatible_methods = Column(JSON, nullable=False, default=list)
    suitable_scenes = Column(JSON, nullable=False, default=list)
    business_pains = Column(JSON, nullable=False, default=list)
    structure_schema = Column(JSON, nullable=False, default=list)
    reference_examples = Column(JSON, nullable=False, default=list)
    required_variables = Column(JSON, nullable=False, default=list)
    output_schema = Column(JSON, nullable=False, default=dict)
    risk_rules = Column(JSON, nullable=False, default=list)
    enabled = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("version_id", "code", name="uq_content_body_formulas_version_code"),)


class ContentCombinationRule(Base):
    __tablename__ = "content_combination_rules"

    id = Column(String(64), primary_key=True)
    version_id = Column(
        String(64), ForeignKey("content_rule_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    schema_version = Column(Integer, nullable=False, default=2, index=True)
    content_goal = Column(String(64), nullable=True, index=True)
    content_type_codes = Column(JSON, nullable=False, default=list)
    industry_scope = Column(JSON, nullable=False, default=list)
    channel_scope = Column(JSON, nullable=False, default=list)
    narrative_axis_codes = Column(JSON, nullable=False, default=list)
    methods = Column(JSON, nullable=False, default=list)
    title_formula_codes = Column(JSON, nullable=False, default=list)
    title_pattern_codes = Column(JSON, nullable=False, default=list)
    content_formula_code = Column(String(32), nullable=True)
    body_pattern_codes = Column(JSON, nullable=False, default=list)
    required_evidence_types = Column(JSON, nullable=False, default=list)
    compatibility = Column(String(32), nullable=False, default="compatible")
    priority = Column(Integer, nullable=False, default=0)
    conditions = Column(JSON, nullable=False, default=dict)
    hard_conditions = Column(JSON, nullable=False, default=dict)
    score_weights = Column(JSON, nullable=False, default=dict)
    fallback_rule_id = Column(String(64), nullable=True)
    recommendation_reason = Column(Text, nullable=False, default="")
    combination_type = Column(String(32), nullable=True)
    method_members = Column(JSON, nullable=False, default=list)
    content_goal_codes = Column(JSON, nullable=False, default=list)
    scenario_description = Column(Text, nullable=False, default="")
    required_variable_codes = Column(JSON, nullable=False, default=list)
    source_metadata = Column(JSON, nullable=False, default=dict)
    title_formula_candidate_codes = Column(JSON, nullable=False, default=list)
    body_formula_candidate_codes = Column(JSON, nullable=False, default=list)

    __table_args__ = (
        CheckConstraint("schema_version IN (2, 3)", name="ck_content_combination_rule_schema_version"),
        CheckConstraint(
            "schema_version <> 2 OR (content_goal IS NOT NULL AND content_formula_code IS NOT NULL)",
            name="ck_content_combination_rule_v2_required_fields",
        ),
    )


class ContentWorkflowVersion(Base):
    __tablename__ = "content_workflow_versions"

    id = Column(String(64), primary_key=True)
    slug = Column(String(80), nullable=False, index=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    version = Column(Integer, nullable=False)
    schema_version = Column(Integer, nullable=False, default=2)
    status = Column(String(32), nullable=False, default="draft", index=True)
    definition_json = Column(JSON, nullable=False, default=dict)
    input_schema = Column(JSON, nullable=False, default=dict)
    output_schema = Column(JSON, nullable=False, default=dict)
    definition_hash = Column(String(64), nullable=True, index=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    published_at = Column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "slug", "version", name="uq_content_workflow_version"),)


class IndustryTemplateVersion(Base):
    __tablename__ = "content_industry_template_versions"

    id = Column(String(64), primary_key=True)
    slug = Column(String(80), nullable=False, index=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    version = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="draft", index=True)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=False, default="")
    icon = Column(String(64), nullable=True)
    quick_form_schema = Column(JSON, nullable=False, default=list)
    pro_form_schema = Column(JSON, nullable=False, default=list)
    default_goal = Column(String(64), nullable=False, default="acquire")
    default_strategy = Column(JSON, nullable=False, default=dict)
    default_knowledge_scope = Column(JSON, nullable=False, default=list)
    default_workflow_version_id = Column(
        String(64), ForeignKey("content_workflow_versions.id", ondelete="RESTRICT"), nullable=False
    )
    review_policy = Column(JSON, nullable=False, default=dict)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    published_at = Column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "slug", "version", name="uq_content_industry_template_version"),)


class ContentTypeDefinition(Base):
    """平台稳定内容类型；行业名称通过 IndustryContentPackVersion 做别名覆盖。"""

    __tablename__ = "content_type_definitions"

    id = Column(String(64), primary_key=True)
    version_id = Column(
        String(64), ForeignKey("content_rule_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code = Column(String(32), nullable=False)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=False, default="")
    supported_goals = Column(JSON, nullable=False, default=list)
    required_variable_codes = Column(JSON, nullable=False, default=list)
    evidence_policy = Column(JSON, nullable=False, default=dict)
    default_narrative_axes = Column(JSON, nullable=False, default=list)
    default_body_formula_codes = Column(JSON, nullable=False, default=list)
    enabled = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("version_id", "code", name="uq_content_type_definition_version_code"),)


class FormulaPattern(Base):
    """可执行的标题句式或正文段落模板。"""

    __tablename__ = "content_formula_patterns"

    id = Column(String(64), primary_key=True)
    rule_version_id = Column(
        String(64), ForeignKey("content_rule_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    formula_kind = Column(String(32), nullable=False, index=True)
    formula_code = Column(String(32), nullable=False, index=True)
    code = Column(String(64), nullable=False)
    name = Column(String(160), nullable=False)
    template_text = Column(Text, nullable=False)
    paragraph_schema = Column(JSON, nullable=False, default=list)
    content_type_codes = Column(JSON, nullable=False, default=list)
    channel_scope = Column(JSON, nullable=False, default=list)
    risk_policy = Column(JSON, nullable=False, default=dict)
    enabled = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("rule_version_id", "code", name="uq_formula_pattern_rule_version_code"),)


class FormulaSlotBinding(Base):
    __tablename__ = "content_formula_slot_bindings"

    id = Column(String(64), primary_key=True)
    pattern_id = Column(
        String(64), ForeignKey("content_formula_patterns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slot_key = Column(String(80), nullable=False)
    value_type = Column(String(32), nullable=False, default="string")
    source_type = Column(String(32), nullable=False)
    source_path = Column(String(255), nullable=True)
    alternative_sources = Column(JSON, nullable=False, default=list)
    lexicon_pack_codes = Column(JSON, nullable=False, default=list)
    required = Column(Boolean, nullable=False, default=True)
    evidence_required = Column(Boolean, nullable=False, default=False)
    fallback_policy = Column(String(32), nullable=False, default="block")
    validation_schema = Column(JSON, nullable=False, default=dict)
    max_length = Column(Integer, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("pattern_id", "slot_key", name="uq_formula_slot_pattern_key"),)


class LexiconPack(Base):
    __tablename__ = "content_lexicon_packs"

    id = Column(String(64), primary_key=True)
    code = Column(String(80), nullable=False)
    scope_type = Column(String(32), nullable=False, index=True)
    scope_id = Column(String(64), nullable=True, index=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    name = Column(String(160), nullable=False)
    semantic_category = Column(String(80), nullable=False, index=True)
    description = Column(Text, nullable=False, default="")
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)

    __table_args__ = (
        UniqueConstraint("tenant_id", "scope_type", "scope_id", "code", name="uq_lexicon_pack_scope_code"),
    )


class LexiconVersion(Base):
    __tablename__ = "content_lexicon_versions"

    id = Column(String(64), primary_key=True)
    pack_id = Column(String(64), ForeignKey("content_lexicon_packs.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="draft", index=True)
    changelog = Column(Text, nullable=False, default="")
    source_metadata = Column(JSON, nullable=False, default=dict)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    published_at = Column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("pack_id", "version", name="uq_lexicon_version_pack_version"),)


class LexiconEntry(Base):
    __tablename__ = "content_lexicon_entries"

    id = Column(String(64), primary_key=True)
    version_id = Column(
        String(64), ForeignKey("content_lexicon_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text = Column(Text, nullable=False)
    normalized_text = Column(Text, nullable=False)
    tags = Column(JSON, nullable=False, default=list)
    risk_level = Column(String(32), nullable=False, default="safe")
    applicable_formula_codes = Column(JSON, nullable=False, default=list)
    applicable_slot_keys = Column(JSON, nullable=False, default=list)
    replacement_text = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("version_id", "normalized_text", name="uq_lexicon_entry_version_text"),)


class VariableDefinition(Base):
    __tablename__ = "content_variable_definitions"

    id = Column(String(64), primary_key=True)
    rule_version_id = Column(
        String(64), ForeignKey("content_rule_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code = Column(String(80), nullable=False)
    name = Column(String(120), nullable=False)
    value_type = Column(String(32), nullable=False, default="string")
    unit_schema = Column(JSON, nullable=False, default=dict)
    evidence_policy = Column(JSON, nullable=False, default=dict)
    sensitivity = Column(String(32), nullable=False, default="normal")
    allowed_usages = Column(JSON, nullable=False, default=list)
    validation_schema = Column(JSON, nullable=False, default=dict)
    enabled = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("rule_version_id", "code", name="uq_variable_definition_rule_code"),)


class IndustryContentPackVersion(Base):
    __tablename__ = "content_industry_pack_versions"

    id = Column(String(64), primary_key=True)
    slug = Column(String(80), nullable=False, index=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    version = Column(Integer, nullable=False)
    schema_version = Column(Integer, nullable=False, default=2, index=True)
    status = Column(String(32), nullable=False, default="draft", index=True)
    name = Column(String(160), nullable=False)
    description = Column(Text, nullable=False, default="")
    content_type_aliases = Column(JSON, nullable=False, default=dict)
    variable_schema = Column(JSON, nullable=False, default=list)
    lexicon_version_ids = Column(JSON, nullable=False, default=list)
    pattern_ids = Column(JSON, nullable=False, default=list)
    combination_overrides = Column(JSON, nullable=False, default=list)
    persona_templates = Column(JSON, nullable=False, default=list)
    knowledge_scope = Column(JSON, nullable=False, default=list)
    evidence_policy = Column(JSON, nullable=False, default=dict)
    review_policy = Column(JSON, nullable=False, default=dict)
    compliance_policy = Column(JSON, nullable=False, default=dict)
    visual_policy = Column(JSON, nullable=False, default=dict)
    golden_samples = Column(JSON, nullable=False, default=list)
    negative_examples = Column(JSON, nullable=False, default=list)
    minimum_coverage = Column(Float, nullable=False, default=1.0)
    source_metadata = Column(JSON, nullable=False, default=dict)
    changelog = Column(Text, nullable=False, default="")
    rollback_target_version_id = Column(String(64), nullable=True)
    evaluation_report = Column(JSON, nullable=False, default=dict)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    published_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", "version", name="uq_content_industry_pack_version"),
        Index(
            "uq_content_global_industry_pack_version",
            "slug",
            "version",
            unique=True,
            postgresql_where=text("tenant_id IS NULL"),
        ),
    )


class IndustryVariableMapping(Base):
    __tablename__ = "content_industry_variable_mappings"

    id = Column(String(64), primary_key=True)
    industry_pack_version_id = Column(
        String(64), ForeignKey("content_industry_pack_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_key = Column(String(120), nullable=False)
    variable_code = Column(String(80), nullable=False, index=True)
    transform_type = Column(String(32), nullable=False, default="identity")
    transform_config = Column(JSON, nullable=False, default=dict)
    required_by_content_types = Column(JSON, nullable=False, default=list)

    __table_args__ = (
        UniqueConstraint("industry_pack_version_id", "field_key", name="uq_industry_variable_mapping_field"),
    )


class PersonaProfile(Base):
    __tablename__ = "content_persona_profiles"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    name = Column(String(160), nullable=False)
    status = Column(String(32), nullable=False, default="active", index=True)
    created_by = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)
    deleted_at = Column(DateTime, nullable=True, index=True)


class PersonaProfileVersion(Base):
    __tablename__ = "content_persona_profile_versions"

    id = Column(String(64), primary_key=True)
    profile_id = Column(
        String(64), ForeignKey("content_persona_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="draft", index=True)
    identity = Column(JSON, nullable=False, default=dict)
    experience_facts = Column(JSON, nullable=False, default=list)
    professional_background = Column(JSON, nullable=False, default=dict)
    tone = Column(JSON, nullable=False, default=dict)
    values = Column(JSON, nullable=False, default=list)
    positions = Column(JSON, nullable=False, default=list)
    service_boundaries = Column(JSON, nullable=False, default=list)
    preferred_phrases = Column(JSON, nullable=False, default=list)
    forbidden_phrases = Column(JSON, nullable=False, default=list)
    evidence_ids = Column(JSON, nullable=False, default=list)
    created_by = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=utc_now_naive)
    published_at = Column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("profile_id", "version", name="uq_persona_profile_version"),)


class ChannelProfile(Base):
    __tablename__ = "content_channel_profiles"

    id = Column(String(64), primary_key=True)
    code = Column(String(64), nullable=False, unique=True)
    name = Column(String(120), nullable=False)
    connector_type = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)


class ChannelProfileVersion(Base):
    __tablename__ = "content_channel_profile_versions"

    id = Column(String(64), primary_key=True)
    profile_id = Column(
        String(64), ForeignKey("content_channel_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="draft", index=True)
    title_constraints = Column(JSON, nullable=False, default=dict)
    body_constraints = Column(JSON, nullable=False, default=dict)
    topic_constraints = Column(JSON, nullable=False, default=dict)
    media_constraints = Column(JSON, nullable=False, default=dict)
    cta_policy = Column(JSON, nullable=False, default=dict)
    link_policy = Column(JSON, nullable=False, default=dict)
    preview_schema = Column(JSON, nullable=False, default=dict)
    connector_config_ref = Column(String(255), nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    published_at = Column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("profile_id", "version", name="uq_channel_profile_version"),)


class CompliancePolicyVersion(Base):
    __tablename__ = "content_compliance_policy_versions"

    id = Column(String(64), primary_key=True)
    scope_type = Column(String(32), nullable=False, index=True)
    scope_id = Column(String(64), nullable=True, index=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    version = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="draft", index=True)
    name = Column(String(160), nullable=False)
    policy_config = Column(JSON, nullable=False, default=dict)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    published_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "scope_type", "scope_id", "version", name="uq_compliance_policy_scope_version"),
    )


class ReplacementRule(Base):
    __tablename__ = "content_replacement_rules"

    id = Column(String(64), primary_key=True)
    policy_version_id = Column(
        String(64), ForeignKey("content_compliance_policy_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_code = Column(String(80), nullable=False)
    pattern = Column(Text, nullable=False)
    match_type = Column(String(32), nullable=False, default="literal")
    risk_level = Column(String(32), nullable=False, default="warning")
    action = Column(String(32), nullable=False, default="warn")
    replacement = Column(Text, nullable=True)
    human_confirmation_required = Column(Boolean, nullable=False, default=False)
    explanation = Column(Text, nullable=False, default="")
    enabled = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("policy_version_id", "rule_code", name="uq_replacement_rule_policy_code"),)


class ContentTask(Base):
    __tablename__ = "content_tasks"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    project_id = Column(String(64), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    industry_template_version_id = Column(
        String(64), ForeignKey("content_industry_template_versions.id", ondelete="RESTRICT"), nullable=False
    )
    workflow_version_id = Column(
        String(64), ForeignKey("content_workflow_versions.id", ondelete="RESTRICT"), nullable=False
    )
    workflow_definition_hash = Column(String(64), nullable=True)
    rule_version_id = Column(String(64), ForeignKey("content_rule_versions.id", ondelete="RESTRICT"), nullable=False)
    mode = Column(String(32), nullable=False, default="quick")
    content_goal = Column(String(64), nullable=False, default="acquire")
    # 历史任务允许为空；ContentService 对所有 V3 新任务强制写入唯一内容类型。
    content_type_code = Column(String(32), nullable=True, index=True)
    industry_pack_version_id = Column(
        String(64), ForeignKey("content_industry_pack_versions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    persona_profile_version_id = Column(
        String(64), ForeignKey("content_persona_profile_versions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    channel_profile_version_id = Column(
        String(64), ForeignKey("content_channel_profile_versions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    primary_narrative_axis = Column(String(80), nullable=True)
    selected_angle_json = Column(JSON, nullable=False, default=dict)
    runtime_config_snapshot_json = Column(JSON, nullable=False, default=dict)
    # 跨素材表的引用由业务层校验和删除保护维护；避免 ContentTask/Asset 形成建表环依赖。
    selected_image_item_id = Column(String(64), nullable=True, index=True)
    selected_poster_template_id = Column(String(64), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="draft", index=True)
    current_stage = Column(String(32), nullable=False, default="brief", index=True)
    brief_json = Column(JSON, nullable=False, default=dict)
    strategy_json = Column(JSON, nullable=False, default=dict)
    evidence_json = Column(JSON, nullable=False, default=dict)
    active_evidence_bundle_id = Column(String(64), nullable=True, index=True)
    title_candidates_json = Column(JSON, nullable=False, default=list)
    selected_title_json = Column(JSON, nullable=True)
    review_json = Column(JSON, nullable=False, default=dict)
    latest_run_id = Column(String(64), nullable=True, index=True)
    error_json = Column(JSON, nullable=True)
    created_by = Column(String(64), nullable=False, index=True)
    updated_by = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=utc_now_naive, index=True)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)
    deleted_at = Column(DateTime, nullable=True, index=True)

    __table_args__ = (
        Index("idx_content_tasks_owner_updated", "created_by", "updated_at"),
        Index("idx_content_tasks_tenant_updated", "tenant_id", "updated_at"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "name": self.name,
            "industry_template_version_id": self.industry_template_version_id,
            "workflow_version_id": self.workflow_version_id,
            "workflow_definition_hash": self.workflow_definition_hash,
            "rule_version_id": self.rule_version_id,
            "mode": self.mode,
            "content_goal": self.content_goal,
            "content_type_code": self.content_type_code,
            "industry_pack_version_id": self.industry_pack_version_id,
            "persona_profile_version_id": self.persona_profile_version_id,
            "channel_profile_version_id": self.channel_profile_version_id,
            "primary_narrative_axis": self.primary_narrative_axis,
            "selected_angle": self.selected_angle_json or {},
            "runtime_config_snapshot": self.runtime_config_snapshot_json or {},
            "selected_image_item_id": self.selected_image_item_id,
            "selected_poster_template_id": self.selected_poster_template_id,
            "status": self.status,
            "current_stage": self.current_stage,
            "brief": self.brief_json or {},
            "strategy": self.strategy_json or {},
            "evidence_bundle": self.evidence_json or {},
            "active_evidence_bundle_id": self.active_evidence_bundle_id,
            "title_candidates": self.title_candidates_json or [],
            "selected_title": self.selected_title_json,
            "review": self.review_json or {},
            "latest_run_id": self.latest_run_id,
            "error": self.error_json,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class ContentOCRResult(Base):
    __tablename__ = "content_ocr_results"

    id = Column(String(64), primary_key=True)
    task_id = Column(String(64), ForeignKey("content_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    original_file_name = Column(String(255), nullable=False)
    content_type = Column(String(80), nullable=False)
    file_size = Column(Integer, nullable=False)
    image_width = Column(Integer, nullable=False)
    image_height = Column(Integer, nullable=False)
    bucket_name = Column(String(120), nullable=False)
    object_name = Column(Text, nullable=False)
    engine = Column(String(32), nullable=False, default="rapid_ocr")
    engine_version = Column(String(64), nullable=False, default="PP-OCRv5")
    status = Column(String(32), nullable=False, default="processing", index=True)
    raw_text = Column(Text, nullable=False, default="")
    corrected_text = Column(Text, nullable=True)
    blocks_json = Column(JSON, nullable=False, default=list)
    processing_ms = Column(Integer, nullable=True)
    error_code = Column(String(80), nullable=True)
    error_message = Column(Text, nullable=True)
    created_by = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now_naive, index=True)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    __table_args__ = (Index("idx_content_ocr_task_created", "task_id", "created_at"),)

    def to_dict(self) -> dict[str, Any]:
        corrected_text = self.corrected_text
        return {
            "id": self.id,
            "task_id": self.task_id,
            "source_image": {
                "file_name": self.original_file_name,
                "content_type": self.content_type,
                "file_size": self.file_size,
                "width": self.image_width,
                "height": self.image_height,
            },
            "engine": self.engine,
            "engine_version": self.engine_version,
            "status": self.status,
            "raw_text": self.raw_text or "",
            "corrected_text": corrected_text,
            "effective_text": corrected_text if corrected_text is not None else self.raw_text or "",
            "blocks": self.blocks_json or [],
            "processing_ms": self.processing_ms,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "created_by": self.created_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class MediaEvidenceItem(Base):
    """原始素材、解析结果和人工确认状态的可追溯记录。"""

    __tablename__ = "content_media_evidence_items"

    id = Column(String(64), primary_key=True)
    task_id = Column(String(64), ForeignKey("content_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    attachment_id = Column(String(128), nullable=False, index=True)
    object_uri = Column(Text, nullable=False)
    media_type = Column(String(32), nullable=False)
    original_filename = Column(String(512), nullable=True)
    extracted_text = Column(Text, nullable=False, default="")
    parser_version = Column(String(128), nullable=False, default="unknown")
    metadata_json = Column(JSON, nullable=False, default=dict)
    source_hash = Column(String(128), nullable=False, index=True)
    verified_status = Column(String(32), nullable=False, default="pending", index=True)
    privacy_status = Column(String(32), nullable=False, default="unreviewed", index=True)
    allowed_usage = Column(JSON, nullable=False, default=list)
    confirmed_facts = Column(JSON, nullable=False, default=list)
    confirmed_by = Column(String(64), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)

    __table_args__ = (UniqueConstraint("task_id", "attachment_id", name="uq_media_evidence_task_attachment"),)


class ContentEvidenceItem(Base):
    """V3 不可变事实证据；词库值不得进入本表。"""

    __tablename__ = "content_evidence_items"

    id = Column(String(64), primary_key=True)
    task_id = Column(String(64), ForeignKey("content_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    variable_codes = Column(JSON, nullable=False, default=list)
    value_json = Column(JSON, nullable=False)
    source_type = Column(String(32), nullable=False, index=True)
    source_id = Column(String(255), nullable=False, index=True)
    source_version = Column(String(128), nullable=False)
    verified_status = Column(String(32), nullable=False, index=True)
    allowed_usage = Column(JSON, nullable=False, default=list)
    risk_level = Column(String(32), nullable=False, default="normal", index=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    source_hash = Column(String(128), nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "source_type IN ('manual_input', 'business_record', 'media', 'knowledge_base', 'human_confirmation')",
            name="ck_content_evidence_source_type",
        ),
        CheckConstraint(
            "verified_status IN ('retrieved', 'confirmed', 'user_confirmed', 'rejected')",
            name="ck_content_evidence_verified_status",
        ),
    )


class ContentEvidenceBundleVersion(Base):
    """不可变 EvidenceBundle 版本。"""

    __tablename__ = "content_evidence_bundle_versions"

    id = Column(String(64), primary_key=True)
    task_id = Column(String(64), ForeignKey("content_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="frozen", index=True)
    evidence_ids = Column(JSON, nullable=False, default=list)
    source_counts = Column(JSON, nullable=False, default=dict)
    citations = Column(JSON, nullable=False, default=list)
    bundle_hash = Column(String(64), nullable=False, unique=True, index=True)
    supersedes_id = Column(
        String(64), ForeignKey("content_evidence_bundle_versions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    frozen_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("task_id", "version", name="uq_content_evidence_bundle_task_version"),
        CheckConstraint("status = 'frozen'", name="ck_content_evidence_bundle_frozen"),
    )


class ContentNodeRun(Base):
    __tablename__ = "content_node_runs"

    id = Column(String(64), primary_key=True)
    task_id = Column(String(64), ForeignKey("content_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_run_id = Column(String(64), nullable=False, index=True)
    node_id = Column(String(80), nullable=False)
    node_type = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="pending", index=True)
    attempt = Column(Integer, nullable=False, default=1)
    delegated_agent_run_id = Column(
        String(64), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, unique=True, index=True
    )
    input_snapshot = Column(JSON, nullable=False, default=dict)
    output_snapshot = Column(JSON, nullable=False, default=dict)
    error_type = Column(String(80), nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_content_node_runs_task_node", "task_id", "node_id", "attempt"),
        Index("idx_content_node_runs_parent_node_attempt", "agent_run_id", "node_id", "attempt"),
    )


class ContentMatchDecisionSnapshot(Base):
    """一次 V3 组合组匹配的不可变决策证据。"""

    __tablename__ = "content_match_decision_snapshots"

    id = Column(String(64), primary_key=True)
    task_id = Column(String(64), ForeignKey("content_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    content_run_id = Column(String(64), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    node_run_id = Column(String(64), ForeignKey("content_node_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    rule_version_id = Column(
        String(64), ForeignKey("content_rule_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    industry_pack_version_id = Column(
        String(64), ForeignKey("content_industry_pack_versions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    channel_profile_version_id = Column(
        String(64), ForeignKey("content_channel_profile_versions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    content_direction = Column(String(32), nullable=False, index=True)
    eligible_group_ids = Column(JSON, nullable=False, default=list)
    rejected_groups = Column(JSON, nullable=False, default=list)
    score_details = Column(JSON, nullable=False, default=dict)
    selected_group_id = Column(
        String(64), ForeignKey("content_combination_rules.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    selection_mode = Column(String(32), nullable=False, default="deterministic")
    selected_by = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="active", index=True)
    supersedes_id = Column(
        String(64), ForeignKey("content_match_decision_snapshots.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('active', 'superseded')", name="ck_content_match_snapshot_status"),
        Index(
            "uq_content_match_snapshot_active_run",
            "content_run_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )


class ContentFormulaSelectionSnapshot(Base):
    """一次 V3 标题/正文公式选择的不可变决策证据。"""

    __tablename__ = "content_formula_selection_snapshots"

    id = Column(String(64), primary_key=True)
    task_id = Column(String(64), ForeignKey("content_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    content_run_id = Column(String(64), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    node_run_id = Column(String(64), ForeignKey("content_node_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    match_snapshot_id = Column(
        String(64),
        ForeignKey("content_match_decision_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    combination_group_id = Column(
        String(64), ForeignKey("content_combination_rules.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    eligible_title_formula_codes = Column(JSON, nullable=False, default=list)
    eligible_body_formula_codes = Column(JSON, nullable=False, default=list)
    title_score_details = Column(JSON, nullable=False, default=dict)
    body_score_details = Column(JSON, nullable=False, default=dict)
    selected_title_formula_code = Column(String(32), nullable=True)
    selected_body_formula_code = Column(String(32), nullable=True)
    title_selection_reason = Column(Text, nullable=True)
    body_selection_reason = Column(Text, nullable=True)
    selection_mode = Column(String(32), nullable=False, default="deterministic")
    selected_by = Column(String(64), nullable=False)
    delegated_agent_run_id = Column(
        String(64), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rule_version_id = Column(
        String(64), ForeignKey("content_rule_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    evidence_bundle_hash = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="active", index=True)
    supersedes_id = Column(
        String(64),
        ForeignKey("content_formula_selection_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('active', 'superseded')", name="ck_content_formula_snapshot_status"),
        Index(
            "uq_content_formula_snapshot_active_run",
            "content_run_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )


class ContentArtifact(Base):
    __tablename__ = "content_artifacts"

    id = Column(String(64), primary_key=True)
    task_id = Column(String(64), ForeignKey("content_tasks.id", ondelete="CASCADE"), nullable=False, unique=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="draft", index=True)
    current_version = Column(Integer, nullable=False, default=1)
    title = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    topics = Column(JSON, nullable=False, default=list)
    strategy_snapshot = Column(JSON, nullable=False, default=dict)
    evidence_snapshot = Column(JSON, nullable=False, default=dict)
    evidence_usage_snapshot = Column(JSON, nullable=False, default=dict)
    review_snapshot = Column(JSON, nullable=False, default=dict)
    cover_asset_id = Column(String(64), nullable=True, index=True)
    cover_job_id = Column(String(64), nullable=True, index=True)
    hycanvas_design_snapshot = Column(JSON, nullable=False, default=dict)
    content_type_snapshot = Column(JSON, nullable=False, default=dict)
    angle_snapshot = Column(JSON, nullable=False, default=dict)
    pattern_slot_snapshot = Column(JSON, nullable=False, default=dict)
    persona_snapshot = Column(JSON, nullable=False, default=dict)
    channel_snapshot = Column(JSON, nullable=False, default=dict)
    compliance_snapshot = Column(JSON, nullable=False, default=dict)
    runtime_config_snapshot = Column(JSON, nullable=False, default=dict)
    edit_diff_snapshot = Column(JSON, nullable=False, default=list)
    created_by = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "status": self.status,
            "current_version": self.current_version,
            "title": self.title,
            "body": self.body,
            "topics": self.topics or [],
            "strategy_snapshot": self.strategy_snapshot or {},
            "evidence_snapshot": self.evidence_snapshot or {},
            "evidence_usage_snapshot": self.evidence_usage_snapshot or {},
            "review_snapshot": self.review_snapshot or {},
            "cover_asset_id": self.cover_asset_id,
            "cover_job_id": self.cover_job_id,
            "hycanvas_design_snapshot": self.hycanvas_design_snapshot or {},
            "content_type_snapshot": self.content_type_snapshot or {},
            "angle_snapshot": self.angle_snapshot or {},
            "pattern_slot_snapshot": self.pattern_slot_snapshot or {},
            "persona_snapshot": self.persona_snapshot or {},
            "channel_snapshot": self.channel_snapshot or {},
            "compliance_snapshot": self.compliance_snapshot or {},
            "runtime_config_snapshot": self.runtime_config_snapshot or {},
            "edit_diff_snapshot": self.edit_diff_snapshot or [],
            "created_by": self.created_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class ContentArtifactVersion(Base):
    __tablename__ = "content_artifact_versions"

    id = Column(String(64), primary_key=True)
    artifact_id = Column(String(64), ForeignKey("content_artifacts.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    title = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    topics = Column(JSON, nullable=False, default=list)
    source_type = Column(String(32), nullable=False, default="generated")
    model_spec = Column(String(255), nullable=True)
    skill_versions = Column(JSON, nullable=False, default=dict)
    rule_version_id = Column(String(64), nullable=False)
    knowledge_snapshot = Column(JSON, nullable=False, default=dict)
    evidence_usage_snapshot = Column(JSON, nullable=False, default=dict)
    review_snapshot = Column(JSON, nullable=False, default=dict)
    cover_asset_id = Column(String(64), nullable=True, index=True)
    cover_job_id = Column(String(64), nullable=True, index=True)
    hycanvas_design_snapshot = Column(JSON, nullable=False, default=dict)
    content_type_snapshot = Column(JSON, nullable=False, default=dict)
    angle_snapshot = Column(JSON, nullable=False, default=dict)
    pattern_slot_snapshot = Column(JSON, nullable=False, default=dict)
    persona_snapshot = Column(JSON, nullable=False, default=dict)
    channel_snapshot = Column(JSON, nullable=False, default=dict)
    compliance_snapshot = Column(JSON, nullable=False, default=dict)
    runtime_config_snapshot = Column(JSON, nullable=False, default=dict)
    edit_diff_snapshot = Column(JSON, nullable=False, default=list)
    created_by = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=utc_now_naive)

    __table_args__ = (UniqueConstraint("artifact_id", "version", name="uq_content_artifact_version"),)


class ContentCoverImage2Setting(Base):
    __tablename__ = "content_cover_image2_settings"

    owner_uid = Column(String(255), primary_key=True)
    base_url = Column(String(500), nullable=False)
    api_key = Column(String(500), nullable=False)
    model = Column(String(255), nullable=False, default="gpt-image-2")
    capabilities_json = Column(JSON, nullable=False, default=dict)
    verification_status = Column(String(32), nullable=False, default="unverified")
    verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)


class ContentCoverAsset(Base):
    __tablename__ = "content_cover_assets"

    id = Column(String(64), primary_key=True)
    owner_uid = Column(String(255), nullable=False, index=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    content_task_id = Column(String(64), ForeignKey("content_tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    role = Column(String(32), nullable=False, index=True)
    original_file_name = Column(String(255), nullable=False)
    content_type = Column(String(80), nullable=False)
    file_size = Column(Integer, nullable=False)
    image_width = Column(Integer, nullable=False)
    image_height = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    bucket_name = Column(String(120), nullable=False)
    object_name = Column(Text, nullable=False)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=utc_now_naive, index=True)
    deleted_at = Column(DateTime, nullable=True, index=True)

    __table_args__ = (Index("idx_content_cover_assets_owner_created", "owner_uid", "created_at"),)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content_task_id": self.content_task_id,
            "role": self.role,
            "file_name": self.original_file_name,
            "content_type": self.content_type,
            "file_size": self.file_size,
            "width": self.image_width,
            "height": self.image_height,
            "sha256": self.sha256,
            "metadata": self.metadata_json or {},
            "created_at": format_utc_datetime(self.created_at),
        }


class ContentMaterialLibraryItem(Base):
    """用户素材库目录项；文件事实由 ContentCoverAsset 承载。"""

    __tablename__ = "content_material_library_items"

    id = Column(String(64), primary_key=True)
    owner_uid = Column(String(255), nullable=False, index=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    asset_id = Column(String(64), ForeignKey("content_cover_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    material_type = Column(String(32), nullable=False, index=True)
    display_name = Column(String(255), nullable=False)
    category = Column(String(80), nullable=False, default="未分类", index=True)
    tags_json = Column(JSON, nullable=False, default=list)
    status = Column(String(32), nullable=False, default="enabled", index=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=utc_now_naive, index=True)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)
    deleted_at = Column(DateTime, nullable=True, index=True)

    __table_args__ = (
        CheckConstraint(
            "material_type IN ('image', 'cover_template')",
            name="ck_content_material_library_type",
        ),
        CheckConstraint(
            "status IN ('enabled', 'disabled')",
            name="ck_content_material_library_status",
        ),
        Index(
            "uq_content_material_library_asset_active",
            "asset_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index("idx_content_material_library_owner_type_created", "owner_uid", "material_type", "created_at"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "asset_id": self.asset_id,
            "material_type": self.material_type,
            "name": self.display_name,
            "category": self.category,
            "status": self.status,
            "metadata": self.metadata_json or {},
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class ContentMaterialCategory(Base):
    """用户维护的素材图片图库或封面模板分类。"""

    __tablename__ = "content_material_categories"

    owner_uid = Column(String(255), primary_key=True)
    material_type = Column(String(32), primary_key=True, index=True)
    id = Column(String(64), primary_key=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    parent_id = Column(String(64), nullable=True, index=True)
    industry_slug = Column(String(80), nullable=False, default="uncategorized", index=True)
    name = Column(String(80), nullable=False)
    description = Column(String(255), nullable=False, default="")
    sort_order = Column(Integer, nullable=False, default=0)
    is_system = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utc_now_naive, index=True)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)
    deleted_at = Column(DateTime, nullable=True, index=True)

    __table_args__ = (
        CheckConstraint(
            "material_type IN ('image', 'cover_template')",
            name="ck_content_material_category_type",
        ),
        Index(
            "uq_content_material_category_owner_type_name_active",
            "owner_uid",
            "material_type",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_content_material_category_owner_type_sort",
            "owner_uid",
            "material_type",
            "sort_order",
        ),
        Index(
            "idx_content_material_category_owner_type_parent",
            "owner_uid",
            "material_type",
            "parent_id",
        ),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "code": self.id,
            "material_type": self.material_type,
            "parent_id": self.parent_id,
            "level": 2 if self.parent_id else 1,
            "industry_slug": self.industry_slug,
            "name": self.name,
            "description": self.description or "",
            "sort_order": self.sort_order,
            "is_system": self.is_system,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class ContentCoverPosterTemplate(Base):
    __tablename__ = "content_cover_poster_templates"

    id = Column(String(64), primary_key=True)
    owner_uid = Column(String(255), nullable=False, index=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    asset_id = Column(
        String(64), ForeignKey("content_cover_assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name = Column(String(255), nullable=False)
    category = Column(String(80), nullable=False, default="未分类", index=True)
    tags_json = Column(JSON, nullable=False, default=list)
    template_type = Column(String(32), nullable=False, default="alpha_overlay", index=True)
    canvas_width = Column(Integer, nullable=False)
    canvas_height = Column(Integer, nullable=False)
    product_box_json = Column(JSON, nullable=True)
    safe_area_json = Column(JSON, nullable=False, default=dict)
    text_slots_json = Column(JSON, nullable=False, default=list)
    fixed_regions_json = Column(JSON, nullable=False, default=list)
    editable_regions_json = Column(JSON, nullable=False, default=list)
    analysis_json = Column(JSON, nullable=False, default=dict)
    checksum = Column(String(64), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    analysis_version = Column(String(64), nullable=False, default="poster-v1")
    status = Column(String(32), nullable=False, default="ready", index=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive, index=True)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)
    deleted_at = Column(DateTime, nullable=True, index=True)

    __table_args__ = (
        Index(
            "uq_content_cover_poster_owner_checksum_active",
            "owner_uid",
            "checksum",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index("idx_content_cover_poster_owner_created", "owner_uid", "created_at"),
        Index("idx_content_cover_poster_owner_status", "owner_uid", "status"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "asset_id": self.asset_id,
            "name": self.name,
            "category": self.category,
            "template_type": self.template_type,
            "canvas_width": self.canvas_width,
            "canvas_height": self.canvas_height,
            "product_box": self.product_box_json,
            "safe_area": self.safe_area_json or {},
            "text_slots": self.text_slots_json or [],
            "fixed_regions": self.fixed_regions_json or [],
            "editable_regions": self.editable_regions_json or [],
            "analysis": self.analysis_json or {},
            "version": self.version,
            "analysis_version": self.analysis_version,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class ContentCoverJob(Base):
    __tablename__ = "content_cover_jobs"

    id = Column(String(64), primary_key=True)
    owner_uid = Column(String(255), nullable=False, index=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    content_task_id = Column(String(64), ForeignKey("content_tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    artifact_id = Column(String(64), ForeignKey("content_artifacts.id", ondelete="SET NULL"), nullable=True, index=True)
    parent_job_id = Column(
        String(64), ForeignKey("content_cover_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    mode = Column(String(32), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="queued", index=True)
    model = Column(String(255), nullable=True)
    provider_task_id = Column(String(255), nullable=True, index=True)
    idempotency_key = Column(String(128), nullable=False)
    request_json = Column(JSON, nullable=False, default=dict)
    result_json = Column(JSON, nullable=False, default=dict)
    error_code = Column(String(80), nullable=True)
    error_message = Column(Text, nullable=True)
    progress = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utc_now_naive, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    __table_args__ = (
        UniqueConstraint("owner_uid", "idempotency_key", name="uq_content_cover_jobs_owner_idempotency"),
        Index("idx_content_cover_jobs_owner_created", "owner_uid", "created_at"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content_task_id": self.content_task_id,
            "artifact_id": self.artifact_id,
            "parent_job_id": self.parent_job_id,
            "mode": self.mode,
            "status": self.status,
            "model": self.model,
            "provider_task_id": self.provider_task_id,
            "request": self.request_json or {},
            "result": self.result_json or {},
            "error_code": self.error_code,
            "error_message": self.error_message,
            "progress": self.progress,
            "created_at": format_utc_datetime(self.created_at),
            "started_at": format_utc_datetime(self.started_at),
            "completed_at": format_utc_datetime(self.completed_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class ContentCoverEditProject(Base):
    """A recoverable, non-destructive editing draft for one source cover."""

    __tablename__ = "content_cover_edit_projects"

    id = Column(String(64), primary_key=True)
    owner_uid = Column(String(255), nullable=False, index=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    content_task_id = Column(String(64), ForeignKey("content_tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    artifact_id = Column(String(64), ForeignKey("content_artifacts.id", ondelete="SET NULL"), nullable=True, index=True)
    source_asset_id = Column(
        String(64), ForeignKey("content_cover_assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_job_id = Column(
        String(64), ForeignKey("content_cover_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    base_asset_id = Column(
        String(64), ForeignKey("content_cover_assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    scene_json = Column(JSON, nullable=False, default=dict)
    revision = Column(Integer, nullable=False, default=1)
    editability = Column(String(32), nullable=False, default="flattened")
    status = Column(String(32), nullable=False, default="active", index=True)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False)

    __table_args__ = (
        UniqueConstraint("owner_uid", "source_asset_id", name="uq_content_cover_edit_project_source"),
        CheckConstraint("revision >= 1", name="ck_content_cover_edit_project_revision"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_content_cover_edit_project_status"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content_task_id": self.content_task_id,
            "artifact_id": self.artifact_id,
            "source_asset_id": self.source_asset_id,
            "source_job_id": self.source_job_id,
            "base_asset_id": self.base_asset_id,
            "scene": self.scene_json or {},
            "revision": self.revision,
            "editability": self.editability,
            "status": self.status,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class ContentReviewRecord(Base):
    __tablename__ = "content_review_records"

    id = Column(String(64), primary_key=True)
    artifact_version_id = Column(
        String(64), ForeignKey("content_artifact_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    review_type = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False)
    checks = Column(JSON, nullable=False, default=list)
    reviewer_uid = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)


class XiaohongshuAccount(Base):
    __tablename__ = "xiaohongshu_accounts"

    id = Column(String(64), primary_key=True)
    owner_uid = Column(String(255), nullable=False, index=True)
    display_name = Column(String(120), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    login_status = Column(String(32), nullable=False, default="unbound", index=True)
    platform_nickname = Column(String(120), nullable=True)
    platform_account_id = Column(String(120), nullable=True)
    last_verified_at = Column(DateTime, nullable=True)
    last_error_code = Column(String(80), nullable=True)
    last_error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)
    deleted_at = Column(DateTime, nullable=True, index=True)

    __table_args__ = (
        UniqueConstraint("owner_uid", "display_name", name="uq_xiaohongshu_accounts_owner_name"),
        Index("idx_xiaohongshu_accounts_owner_updated", "owner_uid", "updated_at"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "enabled": bool(self.enabled),
            "login_status": self.login_status,
            "platform_nickname": self.platform_nickname,
            "platform_account_id": self.platform_account_id,
            "last_verified_at": format_utc_datetime(self.last_verified_at),
            "last_error_code": self.last_error_code,
            "last_error_message": self.last_error_message,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class XiaohongshuLoginSession(Base):
    __tablename__ = "xiaohongshu_login_sessions"

    id = Column(String(64), primary_key=True)
    account_id = Column(
        String(64), ForeignKey("xiaohongshu_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_uid = Column(String(255), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    expires_at = Column(DateTime, nullable=False)
    error_code = Column(String(80), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)
    completed_at = Column(DateTime, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "account_id": self.account_id,
            "status": self.status,
            "expires_at": format_utc_datetime(self.expires_at),
            "error_code": self.error_code,
            "error_message": self.error_message,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
            "completed_at": format_utc_datetime(self.completed_at),
        }


class XiaohongshuBrowserSession(Base):
    __tablename__ = "xiaohongshu_browser_sessions"

    id = Column(String(80), primary_key=True)
    owner_uid = Column(String(255), nullable=False, index=True)
    account_id = Column(
        String(64), ForeignKey("xiaohongshu_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status = Column(String(32), nullable=False, default="stopped", index=True)
    worker_id = Column(String(120), nullable=True)
    browser_version = Column(String(120), nullable=True)
    started_at = Column(DateTime, nullable=True)
    last_heartbeat_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    last_error_code = Column(String(80), nullable=True)
    last_error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    __table_args__ = (
        UniqueConstraint("owner_uid", "account_id", name="uq_xhs_browser_sessions_owner_account"),
        Index("idx_xhs_browser_sessions_owner_status", "owner_uid", "status"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "account_id": self.account_id,
            "status": self.status,
            "worker_id": self.worker_id,
            "browser_version": self.browser_version,
            "started_at": format_utc_datetime(self.started_at),
            "last_heartbeat_at": format_utc_datetime(self.last_heartbeat_at),
            "last_used_at": format_utc_datetime(self.last_used_at),
            "expires_at": format_utc_datetime(self.expires_at),
            "last_error_code": self.last_error_code,
            "last_error_message": self.last_error_message,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
            "remote_view_available": self.status in {"ready", "login_required"},
        }


class ContentDistributionJob(Base):
    __tablename__ = "content_distribution_jobs"

    id = Column(String(64), primary_key=True)
    owner_uid = Column(String(255), nullable=False, index=True)
    artifact_id = Column(String(64), ForeignKey("content_artifacts.id", ondelete="CASCADE"), nullable=False, index=True)
    artifact_version = Column(Integer, nullable=False)
    platform = Column(String(32), nullable=False, default="xiaohongshu")
    mode = Column(String(16), nullable=False)
    payload_snapshot = Column(JSON, nullable=False, default=dict)
    idempotency_key = Column(String(160), nullable=False, unique=True)
    dedupe_key = Column(String(160), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="queued", index=True)
    error_code = Column(String(80), nullable=True)
    error_message = Column(Text, nullable=True)
    confirmed_by = Column(String(255), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_content_distribution_jobs_owner_created", "owner_uid", "created_at"),
        Index("idx_content_distribution_jobs_artifact_created", "artifact_id", "created_at"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "artifact_id": self.artifact_id,
            "artifact_version": self.artifact_version,
            "platform": self.platform,
            "mode": self.mode,
            "payload": self.payload_snapshot or {},
            "status": self.status,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "confirmed": bool(self.confirmed_at),
            "confirmed_at": format_utc_datetime(self.confirmed_at),
            "created_at": format_utc_datetime(self.created_at),
            "started_at": format_utc_datetime(self.started_at),
            "completed_at": format_utc_datetime(self.completed_at),
        }


class ContentDistributionResult(Base):
    __tablename__ = "content_distribution_results"

    id = Column(String(64), primary_key=True)
    job_id = Column(
        String(64), ForeignKey("content_distribution_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id = Column(
        String(64), ForeignKey("xiaohongshu_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status = Column(String(32), nullable=False, default="queued", index=True)
    error_code = Column(String(80), nullable=True)
    error_message = Column(Text, nullable=True)
    note_url = Column(Text, nullable=True)
    screenshot_path = Column(Text, nullable=True)
    browser_session_id = Column(String(80), nullable=True, index=True)
    evidence_type = Column(String(32), nullable=True)
    uncertain = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utc_now_naive)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("job_id", "account_id", name="uq_distribution_results_job_account"),)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "account_id": self.account_id,
            "status": self.status,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "note_url": self.note_url,
            "has_screenshot": bool(self.screenshot_path),
            "browser_session_id": self.browser_session_id,
            "evidence_type": self.evidence_type,
            "uncertain": bool(self.uncertain),
            "created_at": format_utc_datetime(self.created_at),
            "started_at": format_utc_datetime(self.started_at),
            "completed_at": format_utc_datetime(self.completed_at),
        }


class ContentAnalyticsEvent(Base):
    __tablename__ = "content_analytics_events"

    id = Column(String(64), primary_key=True)
    event_name = Column(String(80), nullable=False, index=True)
    task_id = Column(String(64), nullable=True, index=True)
    run_id = Column(String(64), nullable=True, index=True)
    uid = Column(String(64), nullable=False, index=True)
    properties = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=utc_now_naive, index=True)


class ContentAccount(Base):
    """内容发布账号（企业号/个人号）。"""

    __tablename__ = "content_accounts"

    id = Column(String(64), primary_key=True)
    account_id = Column(String(64), nullable=False, unique=True, index=True)
    name = Column(String(80), nullable=False)
    account_type = Column(String(32), nullable=False)
    following_count = Column(Integer, nullable=False, default=0)
    follower_count = Column(Integer, nullable=False, default=0)
    likes_count = Column(Integer, nullable=False, default=0)
    works_count = Column(Integer, nullable=False, default=0)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    created_by = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "account_id": self.account_id,
            "name": self.name,
            "account_type": self.account_type,
            "following_count": self.following_count or 0,
            "follower_count": self.follower_count or 0,
            "likes_count": self.likes_count or 0,
            "works_count": self.works_count or 0,
            "enabled": bool(self.enabled),
            "created_by": self.created_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class ContentEmployee(Base):
    """内容发布员工。"""

    __tablename__ = "content_employees"

    id = Column(String(64), primary_key=True)
    employee_code = Column(String(64), nullable=False, unique=True, index=True)
    name = Column(String(80), nullable=False)
    login_account = Column(String(64), nullable=False, unique=True, index=True)
    gender = Column(String(16), nullable=False)
    login_port = Column(JSON, nullable=False, default=list)
    role = Column(String(64), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    avatar = Column(String(1024), nullable=True)
    bio = Column(Text, nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    created_by = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "employee_code": self.employee_code,
            "name": self.name,
            "login_account": self.login_account,
            "gender": self.gender,
            "login_port": list(self.login_port or []),
            "role": self.role,
            "enabled": bool(self.enabled),
            "avatar": self.avatar,
            "bio": self.bio or "",
            "last_login_at": format_utc_datetime(self.last_login_at),
            "created_by": self.created_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class ContentRole(Base):
    """内容发布角色，与员工角色名称联动。"""

    __tablename__ = "content_roles"

    id = Column(String(64), primary_key=True)
    role_code = Column(String(32), nullable=False, unique=True, index=True)
    name = Column(String(64), nullable=False, unique=True, index=True)
    role_type = Column(String(32), nullable=False, default="新增")
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    permissions = Column(JSON, nullable=False, default=list)
    created_by = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    def to_dict(self, *, member_count: int = 0) -> dict[str, Any]:
        return {
            "id": self.id,
            "role_code": self.role_code,
            "name": self.name,
            "role_type": self.role_type,
            "member_count": member_count,
            "enabled": bool(self.enabled),
            "created_by": self.created_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class ContentType(Base):
    """内容类型配置。"""

    __tablename__ = "content_types"

    id = Column(String(64), primary_key=True)
    type_code = Column(String(32), nullable=False, unique=True, index=True)
    name = Column(String(64), nullable=False, unique=True, index=True)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    created_by = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type_code": self.type_code,
            "name": self.name,
            "enabled": bool(self.enabled),
            "created_by": self.created_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class ContentVariable(Base):
    """变量配置，服务入口与内容类型名称联动。"""

    __tablename__ = "content_variables"

    id = Column(String(64), primary_key=True)
    variable_code = Column(String(32), nullable=False, unique=True, index=True)
    name = Column(String(64), nullable=False, unique=True, index=True)
    service_entry = Column(String(64), nullable=False, index=True)
    ports = Column(JSON, nullable=False, default=list)
    editions = Column(JSON, nullable=False, default=list)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    created_by = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "variable_code": self.variable_code,
            "name": self.name,
            "service_entry": self.service_entry,
            "ports": list(self.ports or []),
            "editions": list(self.editions or []),
            "enabled": bool(self.enabled),
            "created_by": self.created_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class ContentCover(Base):
    """内容封面。"""

    __tablename__ = "content_covers"

    id = Column(String(64), primary_key=True)
    category = Column(String(32), nullable=False, index=True)
    image_url = Column(String(1024), nullable=False)
    image_name = Column(String(255), nullable=False)
    title = Column(String(120), nullable=False, default="")
    generation_count = Column(Integer, nullable=False, default=0)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    created_by = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "image_url": self.image_url,
            "image_name": self.image_name,
            "title": self.title or "",
            "generation_count": self.generation_count or 0,
            "enabled": bool(self.enabled),
            "created_by": self.created_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class ContentMpFavorite(Base):
    """小程序内容收藏，按员工隔离。"""

    __tablename__ = "content_mp_favorites"

    id = Column(String(64), primary_key=True)
    employee_id = Column(
        String(64), ForeignKey("content_employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id = Column(String(64), ForeignKey("content_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now_naive)

    __table_args__ = (UniqueConstraint("employee_id", "task_id", name="uq_content_mp_favorites_employee_task"),)
