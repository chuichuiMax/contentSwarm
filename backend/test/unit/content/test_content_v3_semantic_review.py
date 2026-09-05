from __future__ import annotations

import pytest

from yuxi.content import generation


@pytest.mark.asyncio
async def test_semantic_review_normalizes_model_pass_levels(monkeypatch):
    async def fake_invoke_json(model_spec, *, skill_slug, prompt):
        return {
            "status": "success",
            "checks": [
                {
                    "code": "FACT_CHECK",
                    "level": "passed",
                    "message": "事实引用一致",
                }
            ],
        }

    monkeypatch.setattr(generation, "_invoke_json", fake_invoke_json)
    result = await generation.review_generated_content(
        model_spec=None,
        title="标题",
        body="正文",
        topics=[],
        brief={},
        workflow_snapshot={},
        evidence_bundle={"items": []},
    )

    assert result["status"] == "passed"
    assert result["checks"][0]["level"] == "info"
    assert result["checks"][0]["location"] == "content"
