"""正文换行与发布正文规范化。"""

from __future__ import annotations

import re

# 常见同音/形近误写：模型常把「在建工地」写成「在手工地」。
_CONTENT_BODY_TYPO_FIXES = (
    ("在手工地", "在建工地"),
)


def normalize_escaped_newlines(value: str) -> str:
    text = str(value or "")
    return text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")


def fix_content_body_typos(body: str) -> str:
    text = body or ""
    for wrong, right in _CONTENT_BODY_TYPO_FIXES:
        if wrong in text:
            text = text.replace(wrong, right)
    return text


def segment_unbroken_body(body: str) -> str:
    """正文没有换行时，按句末标点分成多段。"""
    if "\n" in body:
        return body
    return re.sub(r"([。！？!?])[ \t]*(?=\S)", r"\1\n\n", body).strip()


def normalize_published_body(body: str) -> str:
    """发布正文落库/展示前：还原转义换行、纠正已知误写、无换行时按句分段。"""
    text = normalize_escaped_newlines(body or "").strip()
    text = fix_content_body_typos(text)
    return segment_unbroken_body(text)
