from pathlib import Path

from yuxi.agents.skills.buildin import BUILTIN_SKILLS


def test_viral_structure_rewriter_matches_reference_emoji_distribution():
    spec = next(item for item in BUILTIN_SKILLS if item.slug == "viral-structure-rewriter")
    skill_text = (Path(spec.source_dir) / "SKILL.md").read_text(encoding="utf-8")

    assert spec.version == "1.5.0"
    assert "叙事分散型" in skill_text
    assert "清单连续型" in skill_text
    assert "混合型" in skill_text
    assert "报价或项目清单" in skill_text
    assert "可以连续使用" in skill_text
    assert "参考在句中或句末" in skill_text
    assert "统一放在自然段开头" in skill_text
    assert "普通项目符号和编号不计入" in skill_text
    assert "相邻语义锨点" in skill_text
    assert "至少包含一个非段末落点" in skill_text


def test_viral_structure_rewriter_uses_dynamic_reference_and_targeted_repair():
    spec = next(item for item in BUILTIN_SKILLS if item.slug == "viral-structure-rewriter")
    skill_text = (Path(spec.source_dir) / "SKILL.md").read_text(encoding="utf-8")

    assert "title_slot_sequence" in skill_text
    assert "content_block_sequence" in skill_text
    assert "list_pattern" in skill_text
    assert "当前输入变量" in skill_text
    assert "审核建议与冻结蓝图冲突时，以冻结蓝图为准" in skill_text
    assert "禁止原样再次提交" in skill_text
