"""正文换行和固定引流收尾。"""

CLOSING_CTA = "📩在下方留下【小区＋面积】\n我们将为你提供相关案例及费用参考，\n💕让装修预算更清楚，让装修更透明！"
_CTA_MARKERS = ("在下方留下", "相关案例及费用参考", "让装修预算更清楚", "让装修更透明")


def normalize_escaped_newlines(value: str) -> str:
    text = str(value or "")
    return text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")


def place_closing_cta(body: str) -> str:
    """去掉正文中已有的引流句，再把固定引流放在最后。"""

    kept = [line for line in str(body or "").split("\n") if not any(marker in line for marker in _CTA_MARKERS)]
    article = "\n".join(kept).strip()
    if not article:
        return CLOSING_CTA
    return f"{article}\n\n{CLOSING_CTA}"
