from copy import deepcopy
from types import SimpleNamespace

import pytest

from yuxi.content.catalog import INDUSTRY_CONFIG
from yuxi.content.model.workflows.definition import workflow_definition_hash
from yuxi.content.v3.joint_workflow import (
    PLATFORM_WORKFLOW_DETERMINISTIC_PLAN_ID,
    PLATFORM_WORKFLOW_EXPRESSION_GUIDANCE_ID,
    PLATFORM_WORKFLOW_MODULAR_AUTHOR_ID,
    PLATFORM_WORKFLOW_PRICE_RECOVERY_ID,
    PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID,
    PLATFORM_WORKFLOW_VIRAL_AUTHOR_ID,
    WORKFLOW_EXPRESSION_GUIDANCE,
    WORKFLOW_DETERMINISTIC_PLAN,
    WORKFLOW_MODULAR_AUTHOR,
    WORKFLOW_VIRAL_AUTHOR,
    WORKFLOW_STANDARDIZED_FACTORY,
)
from yuxi.content.v3.seed import PLATFORM_RULE_V3_ID, _activate_v3_seed_data
from yuxi.content.v3.modular_rules import STANDARDIZED_FACTORY_WORKFLOW_V1_ID
from yuxi.content.v3.workflow import PLATFORM_WORKFLOW_V3_ID
from yuxi.storage.postgres.models_content import (
    ContentRuleVersion,
    ContentWorkflowVersion,
    IndustryTemplateVersion,
)


class SeedDatabase:
    def __init__(self, previous_id, created_by):
        self.rows = {
            (ContentRuleVersion, PLATFORM_RULE_V3_ID): SimpleNamespace(status="published", published_at=None),
            (ContentWorkflowVersion, PLATFORM_WORKFLOW_V3_ID): SimpleNamespace(status="published", published_at=None),
            (ContentWorkflowVersion, PLATFORM_WORKFLOW_PRICE_RECOVERY_ID): SimpleNamespace(
                status="published", published_at=None
            ),
            (ContentWorkflowVersion, PLATFORM_WORKFLOW_VIRAL_AUTHOR_ID): SimpleNamespace(
                status="draft",
                published_at=None,
                definition_json=deepcopy(WORKFLOW_VIRAL_AUTHOR),
                definition_hash=workflow_definition_hash(WORKFLOW_VIRAL_AUTHOR),
            ),
            (ContentWorkflowVersion, PLATFORM_WORKFLOW_MODULAR_AUTHOR_ID): SimpleNamespace(
                status="draft",
                published_at=None,
                definition_json=deepcopy(WORKFLOW_MODULAR_AUTHOR),
                definition_hash=workflow_definition_hash(WORKFLOW_MODULAR_AUTHOR),
            ),
            (ContentWorkflowVersion, PLATFORM_WORKFLOW_EXPRESSION_GUIDANCE_ID): SimpleNamespace(
                status="draft",
                published_at=None,
                definition_json=deepcopy(WORKFLOW_EXPRESSION_GUIDANCE),
                definition_hash=workflow_definition_hash(WORKFLOW_EXPRESSION_GUIDANCE),
            ),
            (ContentWorkflowVersion, PLATFORM_WORKFLOW_DETERMINISTIC_PLAN_ID): SimpleNamespace(
                status="draft",
                published_at=None,
                definition_json=deepcopy(WORKFLOW_DETERMINISTIC_PLAN),
                definition_hash=workflow_definition_hash(WORKFLOW_DETERMINISTIC_PLAN),
            ),
            (ContentWorkflowVersion, PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID): SimpleNamespace(
                status="draft",
                published_at=None,
                definition_json=deepcopy(WORKFLOW_STANDARDIZED_FACTORY),
                definition_hash=workflow_definition_hash(WORKFLOW_STANDARDIZED_FACTORY),
            ),
        }
        if previous_id:
            for slug in INDUSTRY_CONFIG:
                self.rows[IndustryTemplateVersion, f"industry-{slug}-v3"] = SimpleNamespace(
                    default_workflow_version_id=previous_id,
                    created_by=created_by,
                )

    async def get(self, model, row_id):
        return self.rows.get((model, row_id))

    def add(self, row):
        self.rows[type(row), row.id] = row

    async def execute(self, query):
        rows = []
        if query.column_descriptions[0]["entity"].__name__ == "IndustryContentPackVersion":
            rows = [SimpleNamespace(slug=slug, status="published", published_at=None) for slug in INDUSTRY_CONFIG]
        return SimpleNamespace(scalars=lambda: rows)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("previous_id", "created_by", "expected_id"),
    [
        (None, "system", PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID),
        (PLATFORM_WORKFLOW_V3_ID, "system", PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID),
        (PLATFORM_WORKFLOW_PRICE_RECOVERY_ID, "system", PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID),
        (PLATFORM_WORKFLOW_VIRAL_AUTHOR_ID, "system", PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID),
        (PLATFORM_WORKFLOW_MODULAR_AUTHOR_ID, "system", PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID),
        (PLATFORM_WORKFLOW_EXPRESSION_GUIDANCE_ID, "system", PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID),
        (PLATFORM_WORKFLOW_DETERMINISTIC_PLAN_ID, "system", PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID),
        (STANDARDIZED_FACTORY_WORKFLOW_V1_ID, "system", PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID),
        (PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID, "system", PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID),
        ("content-workflow-blueprint-first-v1", "system", "content-workflow-blueprint-first-v1"),
        ("custom-workflow", "system", "custom-workflow"),
        (PLATFORM_WORKFLOW_V3_ID, "admin", PLATFORM_WORKFLOW_V3_ID),
    ],
)
async def test_seed_defaults_to_blueprint_first_without_overwriting_custom_templates(
    previous_id, created_by, expected_id
):
    db = SeedDatabase(previous_id, created_by)
    for _ in range(2):
        await _activate_v3_seed_data(db)
        template = await db.get(IndustryTemplateVersion, "industry-decoration-v3")
        assert template.default_workflow_version_id == expected_id
        for slug in INDUSTRY_CONFIG:
            if slug == "decoration":
                continue
            template = await db.get(IndustryTemplateVersion, f"industry-{slug}-v3")
            if previous_id:
                assert template.default_workflow_version_id == previous_id
            else:
                assert template is None
        workflow = await db.get(ContentWorkflowVersion, PLATFORM_WORKFLOW_VIRAL_AUTHOR_ID)
        assert workflow.status == "draft"
        assert workflow.definition_json == WORKFLOW_VIRAL_AUTHOR
        modular = await db.get(ContentWorkflowVersion, PLATFORM_WORKFLOW_MODULAR_AUTHOR_ID)
        assert modular.status == "published"
        assert modular.published_at is not None
        assert modular.definition_json == WORKFLOW_MODULAR_AUTHOR
        expression = await db.get(ContentWorkflowVersion, PLATFORM_WORKFLOW_EXPRESSION_GUIDANCE_ID)
        assert expression.status == "published"
        assert expression.published_at is not None
        assert expression.definition_json == WORKFLOW_EXPRESSION_GUIDANCE
        deterministic = await db.get(ContentWorkflowVersion, PLATFORM_WORKFLOW_DETERMINISTIC_PLAN_ID)
        assert deterministic.status == "published"
        assert deterministic.published_at is not None
        assert deterministic.definition_json == WORKFLOW_DETERMINISTIC_PLAN
        standardized = await db.get(ContentWorkflowVersion, PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID)
        assert standardized.status == "published"
        assert standardized.published_at is not None
        assert standardized.definition_json == WORKFLOW_STANDARDIZED_FACTORY


@pytest.mark.asyncio
async def test_seed_rejects_modified_blueprint_definition_before_switching_templates():
    db = SeedDatabase(PLATFORM_WORKFLOW_V3_ID, "system")
    workflow = await db.get(ContentWorkflowVersion, PLATFORM_WORKFLOW_VIRAL_AUTHOR_ID)
    workflow.definition_json["selection_policy"] = "modified"
    with pytest.raises(RuntimeError, match="爆款仿写工作流"):
        await _activate_v3_seed_data(db)
    assert workflow.status == "draft"
    for slug in INDUSTRY_CONFIG:
        template = await db.get(IndustryTemplateVersion, f"industry-{slug}-v3")
        assert template.default_workflow_version_id == PLATFORM_WORKFLOW_V3_ID
