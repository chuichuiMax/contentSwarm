from pathlib import Path

from yuxi.agents.skills.buildin import BUILTIN_SKILLS


def _skill_text(slug: str) -> tuple[str, str]:
    spec = next(item for item in BUILTIN_SKILLS if item.slug == slug)
    return spec.version, (Path(spec.source_dir) / "SKILL.md").read_text(encoding="utf-8")


def test_research_skill_freezes_forbidden_replacement_map_from_knowledge_base():
    version, skill_text = _skill_text("content-compliance-researcher")

    assert version == "1.1.0"
    assert "封禁词库" in skill_text
    assert "问题词—常用表达方式" in skill_text
    assert "rule_kind=forbidden_replacement_map" in skill_text
    assert "空列表" in skill_text
    assert "不得自行编造替代词" in skill_text


def test_review_skill_blocks_unreplaced_or_invalid_forbidden_terms():
    version, skill_text = _skill_text("content-reviewer")

    assert version == "1.7.0"
    assert "rule_kind=forbidden_replacement_map" in skill_text
    assert "逐项复查最终标题、正文和话题" in skill_text
    assert "FACT_CHECK_FAILED" in skill_text
    assert "候选列表为空" in skill_text
    assert "不得误算为爆款蓝图要求" in skill_text
