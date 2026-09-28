from pathlib import Path


SKILL_PATH = Path(__file__).resolve().parents[4] / "package/yuxi/agents/skills/buildin/content-visual-planner/SKILL.md"


def test_visual_planner_requires_distinct_template_field_copy():
    content = SKILL_PATH.read_text(encoding="utf-8")

    assert "version: 1.8.0" in content
    assert "每个可替换文字框必须承载不同的信息点" in content
    assert "多个 `title` 字段不得都复制 `text[0]`" in content
    assert "字段唯一 `key`" in content
    assert "visual_text_duplicate" in content
    assert "不改变模板节点、层级、样式、位置、字号、颜色或装饰" in content
    assert "visual_material.cover_mode=ai" in content
    assert "`text[0]` 是标题，`text[1]` 是副标题，`text[2]` 起是标签" in content
    assert "非 AI 封面继续按模板字段约束生成" in content
