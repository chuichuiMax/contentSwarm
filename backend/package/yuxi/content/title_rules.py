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
        # 只合并同行空白，保留正文段间换行（apply_title_rules 也会作用到 body）。
        return re.sub(r"[^\S\n]{2,}", " ", text).strip(" ，,、")


# 标题里两段中文之间用空格断开时，改成逗号，例如「坐在客厅里 整个人都松了」。
_CJK_GAP = re.compile(r"([\u4e00-\u9fff])[^\S\n]+([\u4e00-\u9fff])")


class CjkTitleSpaceRule(TitleRule):
    """标题分句用逗号，不用空格。"""

    def apply(self, title: str) -> str:
        text = str(title or "")
        while True:
            nxt = _CJK_GAP.sub(r"\1，\2", text)
            if nxt == text:
                return text
            text = nxt


TITLE_RULES: tuple[TitleRule, ...] = (
    AreaWithSpaceRule(
        code="area_with_space",
        description="项目面积是整户面积，标题和正文里都不要把面积和单个空间写在一起",
    ),
    CjkTitleSpaceRule(
        code="cjk_title_space",
        description="标题里两段中文之间不要留空格，改用逗号分开",
    ),
)


_AWKWARD_TITLE = re.compile(r"做完整面|完整面木饰面")


def title_is_incomplete(title: str) -> bool:
    """身体感受少了部位时，句子读不通，例如「一抬头就软下来」。"""

    text = str(title or "")
    for match in _BODY_REACTION.finditer(text):
        window = text[max(0, match.start() - 8) : match.start()]
        if not any(part in window for part in _BODY_PARTS):
            return True
    return False


def title_needs_rewrite(title: str) -> bool:
    """标题残缺或明显拗口时，触发生成侧重写。"""

    text = str(title or "")
    return title_is_incomplete(text) or bool(_AWKWARD_TITLE.search(text))


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
    descriptions.append(
        "标题必须是读得通、顺口的完整句子，动作对象不能省略；写身体感受时要写清部位；"
        "不要写「做完整面木饰面」这类拗口堆砌"
    )
    return "文案限制：" + "；".join(descriptions) + "。"
