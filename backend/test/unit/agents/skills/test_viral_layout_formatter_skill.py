from pathlib import Path

from yuxi.agents.skills.buildin import BUILTIN_SKILLS


def test_viral_layout_formatter_preserves_facts_and_requires_visible_layout():
    spec = next(item for item in BUILTIN_SKILLS if item.slug == "viral-layout-formatter")
    skill_text = (Path(spec.source_dir) / "SKILL.md").read_text(encoding="utf-8")

    assert spec.version == "1.4.0"
    assert "creation_mode=viral_rewrite" in skill_text
    assert "排版映射表" in skill_text
    assert "使用真实换行符" in skill_text
    assert "相邻信息块之间保留一个空行" in skill_text
    assert "每个条目独立成行" in skill_text
    assert "互动收尾必须独立成段" in skill_text
    assert "不得新增、删除、替换或重排任何事实" in skill_text
    assert "连续大段正文" in skill_text
    assert "GeneratedContentResultV1" in skill_text
    assert "不得使用固定双换行数" in skill_text
    assert "叙事分散型、清单连续型还是混合型" in skill_text
    assert "报价、材料、步骤和改造清单可以按参考连续多行使用 Emoji 开头" in skill_text
    assert "也不能全部追加到句末或段末" in skill_text


def test_content_reviewer_blocks_flattened_viral_layout():
    spec = next(item for item in BUILTIN_SKILLS if item.slug == "content-reviewer")
    skill_text = (Path(spec.source_dir) / "SKILL.md").read_text(encoding="utf-8")

    assert spec.version == "1.8.0"
    assert "selected_reference=true" in skill_text
    assert "多个独立信息块被压成一行" in skill_text
    assert "CONTENT_STRUCTURE_MISMATCH" in skill_text
    assert "不得设置固定段落数、双换行数或条目数" in skill_text
    assert "报价、材料、步骤或改造清单参考连续使用行首 Emoji" in skill_text
    assert "句号前或自然段末尾" in skill_text


def test_viral_layout_formatter_follows_dynamic_list_type():
    spec = next(item for item in BUILTIN_SKILLS if item.slug == "viral-layout-formatter")
    skill_text = (Path(spec.source_dir) / "SKILL.md").read_text(encoding="utf-8")

    assert "content_block_sequence" in skill_text
    assert "list_pattern.type=numbered" in skill_text
    assert "参考没有列表时" in skill_text
    assert "不得被改成固定的 `1–4` 清单" in skill_text
