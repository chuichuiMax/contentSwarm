"""Normalize model-produced line breaks and keep a closing call to action last."""

from __future__ import annotations


def normalize_escaped_newlines(value: str) -> str:
    return value.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")


_CLOSING_CTA = (
    "📩在下方留下【小区＋面积】",
    "我们将为你提供相关案例及费用参考，",
    "💕让装修预算更清楚，让装修更透明！",
)


def place_closing_cta(body: str) -> str:
    content = "\n".join(line for line in body.splitlines() if line.strip() not in _CLOSING_CTA).strip()
    closing = "\n".join(_CLOSING_CTA)
    return f"{content}\n\n{closing}" if content else closing
