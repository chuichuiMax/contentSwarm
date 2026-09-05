from pathlib import Path

from yuxi.agents.skills.buildin import BUILTIN_SKILLS


def test_viral_reference_selector_requires_variable_match_and_fillable_structure():
    spec = next(item for item in BUILTIN_SKILLS if item.slug == "viral-reference-selector")
    skill_text = (Path(spec.source_dir) / "SKILL.md").read_text(encoding="utf-8")

    assert spec.version == "2.1.0"
    assert "全部非空输入" in skill_text
    assert "不得直接选择候选列表第一项" in skill_text
    assert "不得再次查询知识库" in skill_text
    assert "structure_fillability" in skill_text
    assert "unfilled_required_slots" in skill_text
    assert "content_block_sequence" in skill_text
    assert "list_pattern" in skill_text
    assert "relative_position=start|middle|end" in skill_text
    assert "无法执行的摘要" in skill_text
