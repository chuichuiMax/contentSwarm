from copy import deepcopy
from types import SimpleNamespace

import pytest

from yuxi.content.catalog import INDUSTRY_CONFIG
from yuxi.content.model.workflows.definition import workflow_definition_hash
from yuxi.content.v3.joint_workflow import PLATFORM_WORKFLOW_PRICE_RECOVERY_ID, WORKFLOW_PRICE_RECOVERY
from yuxi.content.v3.seed import PLATFORM_RULE_V3_ID, _activate_v3_seed_data
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
                status="draft",
                published_at=None,
                definition_json=deepcopy(WORKFLOW_PRICE_RECOVERY),
                definition_hash=workflow_definition_hash(WORKFLOW_PRICE_RECOVERY),
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
        (None, "system", PLATFORM_WORKFLOW_PRICE_RECOVERY_ID),
        (PLATFORM_WORKFLOW_V3_ID, "system", PLATFORM_WORKFLOW_PRICE_RECOVERY_ID),
        (PLATFORM_WORKFLOW_PRICE_RECOVERY_ID, "system", PLATFORM_WORKFLOW_PRICE_RECOVERY_ID),
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
        workflow = await db.get(ContentWorkflowVersion, PLATFORM_WORKFLOW_PRICE_RECOVERY_ID)
        assert workflow.status == "published"
        assert workflow.definition_json == WORKFLOW_PRICE_RECOVERY


@pytest.mark.asyncio
async def test_seed_rejects_modified_blueprint_definition_before_switching_templates():
    db = SeedDatabase(PLATFORM_WORKFLOW_V3_ID, "system")
    workflow = await db.get(ContentWorkflowVersion, PLATFORM_WORKFLOW_PRICE_RECOVERY_ID)
    workflow.definition_json["selection_policy"] = "modified"
    with pytest.raises(RuntimeError, match="Blueprint First"):
        await _activate_v3_seed_data(db)
    assert workflow.status == "draft"
    for slug in INDUSTRY_CONFIG:
        template = await db.get(IndustryTemplateVersion, f"industry-{slug}-v3")
        assert template.default_workflow_version_id == PLATFORM_WORKFLOW_V3_ID
