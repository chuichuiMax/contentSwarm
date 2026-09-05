from __future__ import annotations

from yuxi.agents.skills.buildin import BUILTIN_SKILLS


def test_humanizer_skill_is_registered_with_required_resources():
    spec = next(item for item in BUILTIN_SKILLS if item.slug == "humanizer-zh")

    assert spec.version == "1.0.0"
    assert spec.tool_dependencies == ()
    assert spec.mcp_dependencies == ()
    assert (spec.source_dir / "SKILL.md").is_file()
    assert (spec.source_dir / "references" / "patterns.md").is_file()
    assert (spec.source_dir / "references" / "LICENSE-Humanizer-zh.txt").is_file()

    skill_content = (spec.source_dir / "SKILL.md").read_text(encoding="utf-8")
    assert skill_content.startswith("---\nname: humanizer-zh\n")
    assert "不得凭空增加案例、数据、用户反馈、引语、来源或个人经历" in skill_content
