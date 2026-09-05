from pathlib import Path

from yuxi.agents.skills.buildin import BUILTIN_SKILLS


def test_content_human_expression_skill_preserves_facts_and_persona_boundaries():
    spec = next(item for item in BUILTIN_SKILLS if item.slug == "content-human-expression")
    skill_text = (Path(spec.source_dir) / "SKILL.md").read_text(encoding="utf-8")

    assert spec.version == "1.8.0"
    assert "自然语气" in skill_text
    assert "情绪推进" in skill_text
    assert "稳定人设" in skill_text
    assert "不得虚构" in skill_text
    assert "不得新增、修改或删除事实、价格、参数、数字" in skill_text
    assert "标题可以使用一个与主题或情绪直接相关的 emoji" in skill_text
    assert "叙事内容不要机械地每段固定放在开头" in skill_text
    assert "不用数字 emoji" in skill_text
    assert "不破坏标题公式槽位" in skill_text
    assert "旧况很典型" in skill_text
    assert "报幕式开头" in skill_text
    assert "成品不合格" in skill_text
    assert "读者视角改写" in skill_text
    assert "GeneratedContentResultV1" in skill_text
    assert "viral-layout-formatter" in skill_text
    assert "至少保留四处" in skill_text
    assert "叙事分散型、清单连续型和混合型" in skill_text
    assert "允许按参考连续多行使用 emoji 作为行首导航" in skill_text
    assert "每段最后一个" in skill_text
    assert "表情—相邻语义锨点—相对位置" in skill_text
    assert "rule_kind=forbidden_replacement_map" in skill_text
    assert "不得在 Skill 中固化具体问题词和替换词" in skill_text
    assert "按问题词长度从长到短扫描" in skill_text
    assert "不能机械地永远取第一个" in skill_text
    assert "候选列表为空时" in skill_text
