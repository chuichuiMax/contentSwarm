from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test.unit.content.test_joint_strategy import joint_example
from yuxi.content.control.workflow.strategy_input import load_strategy_profiles, project_strategy_input
from yuxi.content.model.contracts.joint_strategy import validate_joint_strategy
from yuxi.content.model.contracts.strategy import resolve_input_path


@pytest.mark.parametrize("mode", ["original", "viral_rewrite"])
def test_projection_preserves_facts_candidates_and_original_contract(mode):
    payload, decision = joint_example(mode)
    payload["runtime_config_snapshot"].update(visual_material={"secret_storage": "not-for-model"})
    payload["content_brief"]["form_values"].update(channel_profile_version_id="stale-channel", price="实际价另填")
    payload["content_brief"]["business_variables"] = {"pain": "与用户确认事实相同", "conflict": "不同值"}
    payload["content_brief"]["form_values"]["pain"] = "与用户确认事实相同"
    payload["evidence_bundle"]["items"] = [
        {
            "id": "fact-1",
            "source_type": "manual_input",
            "source_id": "field_pain",
            "variable_codes": ["pain"],
            "value": "与用户确认事实相同",
            "verified_status": "user_confirmed",
            "allowed_usage": ["body"],
            "risk_level": "low",
            "source_hash": "audit-hash",
            "created_at": "audit-time",
            "metadata": {"price_basis": "standard_unit_price", "region": "杭州", "scope": "不含主材"},
        },
        {"id": "fact-2", "value": None},
    ]
    for reference in payload["reference_candidates"]:
        reference.update(
            full_text="不要传入",
            reference_blueprint={"full": True},
            structure_preview={"blocks": ["事实"]},
        )
    before = deepcopy(payload)
    view = project_strategy_input(payload, channel_profile={"version_id": "locked"}, persona_profile={})
    assert payload == before
    assert view["channel_profile"] == {"version_id": "locked"}
    assert view["runtime_config_snapshot"] == {"creation_mode": mode}
    assert "pain" not in view["content_brief"]["form_values"]
    assert view["content_brief"]["form_values"]["price"] == "实际价另填"
    assert view["content_brief"]["business_variables"] == {"conflict": "不同值"}
    assert "channel_profile_version_id" not in view["content_brief"]["form_values"]
    for original, projected in zip(payload["evidence_bundle"]["items"], view["evidence_bundle"]["items"], strict=True):
        assert projected["id"] == original["id"] and projected["value"] == original["value"]
        for key in ("metadata", "allowed_usage", "risk_level", "verified_status", "source_id"):
            if key in original:
                assert projected[key] == original[key]
        assert "source_hash" not in projected
    for path in view["strategy_candidates"]["available_input_paths"]:
        assert resolve_input_path(view, path) == resolve_input_path(payload, path)
    for section in ("title_formulas", "content_formulas", "methods", "valid_formula_pairs"):
        assert view["strategy_candidates"][section] == payload["strategy_candidates"][section]
    if mode == "original":
        assert "reference_candidates" not in view
        assert "reference" not in view["strategy_candidates"]["scoring"]
        decision["reference"] = {"status": "not_requested", "reason": "原创"}
    else:
        assert len(view["reference_candidates"]) == 2
        assert view["reference_candidates"][0]["structure_preview"] == {"blocks": ["事实"]}
        assert "full_text" not in view["reference_candidates"][0]
        for assessment in decision["reference"]["assessments"]:
            assessment["input_paths"] = ["evidence_bundle.items.0.value"]
        decision["reference"]["slot_mapping"]["pain"] = ["evidence_bundle.items.0.value"]
    for section in ("title_assessments", "body_assessments", "method_assessments"):
        for assessment in decision["strategy"][section]:
            assessment["input_paths"] = ["evidence_bundle.items.0.value"]
    assert validate_joint_strategy(decision, payload).strategy.status == "selected"


@pytest.mark.parametrize("industry", ["decoration", "retail"])
def test_only_decoration_loses_cross_industry_appendix(industry):
    payload, _ = joint_example()
    payload["strategy_candidates"]["industry_slug"] = industry
    formula = payload["strategy_candidates"]["content_formulas"][0]
    formula["source_content"] = {"variables": ["必要事实"], "cross_industry": {"name": "行业通用说明"}}
    payload["strategy_price_evidence_collection"] = {"unresolved_questions": ["还缺来源"], "citations": ["price-1"]}
    view = project_strategy_input(payload, channel_profile={}, persona_profile={})
    source = view["strategy_candidates"]["content_formulas"][0]["source_content"]
    assert source["variables"] == ["必要事实"]
    assert ("cross_industry" in source) == (industry != "decoration")
    assert view["strategy_price_evidence_collection"] == payload["strategy_price_evidence_collection"]


@pytest.mark.asyncio
async def test_profiles_use_only_task_locked_versions_and_missing_versions_fail():
    version = SimpleNamespace(
        id="persona-old",
        **{
            k: {}
            for k in (
                "identity",
                "experience_facts",
                "professional_background",
                "tone",
                "values",
                "positions",
                "service_boundaries",
                "forbidden_phrases",
                "evidence_ids",
            )
        },
    )
    repo = SimpleNamespace(
        get_channel_strategy_profile=AsyncMock(return_value={"version_id": "channel-old", "code": "xiaohongshu"}),
        get_persona_version=AsyncMock(return_value=(version, object())),
    )
    channel, persona = await load_strategy_profiles(
        repo,
        {
            "channel_profile_version_id": "channel-old",
            "persona_profile_version_id": "persona-old",
        },
    )
    repo.get_channel_strategy_profile.assert_awaited_once_with("channel-old")
    repo.get_persona_version.assert_awaited_once_with("persona-old")
    assert channel["version_id"] == "channel-old" and persona["version_id"] == "persona-old"
    repo.get_channel_strategy_profile.return_value = None
    with pytest.raises(ValueError, match="渠道版本不存在"):
        await load_strategy_profiles(repo, {"channel_profile_version_id": "channel-old"})
    repo.get_persona_version.return_value = None
    with pytest.raises(ValueError, match="人设版本不存在"):
        await load_strategy_profiles(repo, {"persona_profile_version_id": "persona-old"})


@pytest.mark.parametrize("mode", ["original", "viral_rewrite"])
def test_joint_skill_only_injects_current_mode(mode):
    from yuxi.agents.middlewares.skills import SkillsMiddleware
    from yuxi.agents.skills.buildin import BUILTIN_SKILLS

    spec = next(s for s in BUILTIN_SKILLS if s.slug == "content-joint-strategy-selector")
    instructions = (Path(spec.source_dir) / "SKILL.md").read_text()
    context = SimpleNamespace(
        _content_max_model_calls=2,
        _content_node_input=SimpleNamespace(payload={"runtime_config_snapshot": {"creation_mode": mode}}),
        _runtime_skill_metadata={
            spec.slug: {"instructions": instructions, "version": spec.version, "content_hash": "full"},
        },
    )
    text = SkillsMiddleware()._build_required_skills_section([spec.slug], context)
    assert "共同决策规则" in text
    assert ("## 报价补证" in text) == (mode == "viral_rewrite")
    assert ("## 原创模式" in text) == (mode == "original")
    assert context._content_applied_skill_instructions[spec.slug]["instruction_chars"] < len(instructions)


def test_projection_keeps_type_conflicts_and_uses_locked_persona():
    payload, _ = joint_example("original")
    payload["content_brief"]["persona"] = {"tone": "旧简报人设"}
    payload["content_brief"]["form_values"]["count"] = True
    payload["evidence_bundle"]["items"] = [
        {
            "source_type": "manual_input",
            "source_id": "field_count",
            "variable_codes": ["count"],
            "verified_status": "user_confirmed",
            "value": 1,
        }
    ]
    view = project_strategy_input(payload, channel_profile={}, persona_profile={"version_id": "locked"})
    assert view["content_brief"]["form_values"]["count"] is True
    assert "persona" not in view["content_brief"]
    assert payload["content_brief"]["persona"] == {"tone": "旧简报人设"}
