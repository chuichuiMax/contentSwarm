"""正文换行。"""


def normalize_escaped_newlines(value: str) -> str:
    text = str(value or "")
    return text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
