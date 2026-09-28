"""知识库替换表的解析与确定性替换；不内置业务词表。"""

import re

from yuxi.content.validation import ComplianceEngine


def parse_replacement_table(content: str) -> dict[str, list[str]]:
    rows = {}
    in_table = False
    for line in content.splitlines():
        if not line.strip().startswith("|"):
            in_table = False
            continue
        cells = [cell.strip() for cell in line.strip().removeprefix("|").removesuffix("|").split("|")]
        if cells == ["问题词", "常用表达方式"]:
            in_table = True
            continue
        if not in_table or all(re.fullmatch(r"[-: ]+", cell) for cell in cells):
            continue
        if len(cells) != 2 or not cells[0]:
            raise ValueError("封禁词库替换表必须使用问题词、常用表达方式两列")
        term, expressions = cells
        alternatives = list(dict.fromkeys(part.strip() for part in re.split(r"[、，,]", expressions) if part.strip()))
        if term in rows and rows[term] != alternatives:
            raise ValueError(f"封禁词库存在冲突映射：{term}")
        rows[term] = alternatives
    return rows


def replace_forbidden_words(text: str, replacements: dict[str, str]) -> str:
    """长词优先，一次替换，不把新生成的词再次作为输入。"""
    if not replacements:
        return text
    pattern = "|".join(re.escape(term) for term in sorted(replacements, key=len, reverse=True))
    output = re.sub(pattern, lambda match: replacements[match.group()], text)
    if ComplianceEngine._numeric_meaning_changed(text, output):
        raise ValueError("封禁词库替换会改变数字或计价单位，请修正映射")
    return output


def contains_frozen_term(text: str, term: str, replacements: dict[str, str]) -> bool:
    """冻结事实/词条允许原文或词库映射后的展示形式，不接受任意同义改写。"""
    display_term = replace_forbidden_words(term, replacements)
    return term in text or bool(display_term and display_term in text)
