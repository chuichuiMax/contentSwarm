"""生成标题的限制。新规则追加到 TITLE_RULES。"""

from __future__ import annotations

import re
from dataclasses import dataclass

_AREA = r"\d+(?:\.\d+)?\s*(?:㎡|m²|m2|平米|平)"
_SPACES = (
    "客餐厅",
    "客厅",
    "餐厅",
    "厨房",
    "卫生间",
    "洗手间",
    "主卫",
    "公卫",
    "浴室",
    "主卧",
    "次卧",
    "卧室",
    "儿童房",
    "书房",
    "阳台",
    "玄关",
    "衣帽间",
    "储物间",
    "茶室",
    "过道",
    "走廊",
)
_SPACE = "(?:" + "|".join(sorted(_SPACES, key=len, reverse=True)) + ")"
_GAP = r"\s*(?:的)?\s*"
_AREA_THEN_SPACE = re.compile(rf"({_AREA}){_GAP}({_SPACE})")
_SPACE_THEN_AREA = re.compile(rf"({_SPACE}){_GAP}({_AREA})")
_BODY_PARTS = ("脖子", "颈椎", "肩膀", "肩", "腰", "背", "腿", "手", "脚")
_BODY_REACTION = re.compile(r"(软|酸|麻|疼|痛|累)(下来|起来)")


@dataclass(frozen=True)
class TitleRule:
    code: str
    description: str

    def apply(self, title: str) -> str:
        raise NotImplementedError


class AreaWithSpaceRule(TitleRule):
    """项目面积是整户面积，不能紧挨着单个空间。"""

    def apply(self, title: str) -> str:
        text = _AREA_THEN_SPACE.sub(r"\1", title)
        text = _SPACE_THEN_AREA.sub(r"\1", text)
        return re.sub(r"\s{2,}", " ", text).strip(" ，,、")


TITLE_RULES: tuple[TitleRule, ...] = (
    AreaWithSpaceRule(
        code="area_with_space",
        description="项目面积是整户面积，标题和正文里都不要把面积和单个空间写在一起",
    ),
)


def title_is_incomplete(title: str) -> bool:
    """身体感受少了部位时，句子读不通，例如「一抬头就软下来」。"""

    text = str(title or "")
    for match in _BODY_REACTION.finditer(text):
        window = text[max(0, match.start() - 8) : match.start()]
        if not any(part in window for part in _BODY_PARTS):
            return True
    return False


def apply_title_rules(title: str) -> str:
    original = str(title or "").strip()
    text = original
    for rule in TITLE_RULES:
        text = rule.apply(text).strip()
    return text or original


def title_rule_instructions() -> str:
    descriptions = [rule.description for rule in TITLE_RULES if rule.description]
    if not descriptions:
        return ""
    descriptions.append("标题必须是读得通的完整句子，动作对象不能省略；写身体感受时要写清部位")
    return "文案限制：" + "；".join(descriptions) + "。"
