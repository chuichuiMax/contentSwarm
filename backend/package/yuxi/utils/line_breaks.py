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


_LONE_EMOJI_PARAGRAPH = re.compile(
    r"^[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\s]+$"
)


def ensure_blank_line_paragraphs(body: str) -> str:
    """小红书正文：每句/每段之间空一行。单换行补成空行分段。"""
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return text
    if "\n\n" in text:
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    if "\n" in text:
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        return "\n\n".join(lines)
    return segment_unbroken_body(text)


def merge_lone_emoji_paragraphs(body: str) -> str:
    """表情不能单独成段：并入上一段末尾；若在文首则并入下一段开头。"""
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return text
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if not paragraphs:
        return text
    merged: list[str] = []
    pending = ""
    for paragraph in paragraphs:
        if _LONE_EMOJI_PARAGRAPH.fullmatch(paragraph):
            emoji = re.sub(r"\s+", "", paragraph)
            if not emoji:
                continue
            if merged:
                merged[-1] = f"{merged[-1].rstrip()}{emoji}"
            else:
                pending += emoji
            continue
        if pending:
            paragraph = f"{pending}{paragraph.lstrip()}"
            pending = ""
        merged.append(paragraph)
    if pending:
        if merged:
            merged[-1] = f"{merged[-1].rstrip()}{pending}"
        else:
            merged.append(pending)
    return "\n\n".join(merged)


def normalize_published_body(body: str) -> str:
    """发布正文落库/展示前：还原转义换行、纠正已知误写、统一空行分段。"""
    text = normalize_escaped_newlines(body or "").strip()
    text = fix_content_body_typos(text)
    text = ensure_blank_line_paragraphs(text)
    return merge_lone_emoji_paragraphs(text)
