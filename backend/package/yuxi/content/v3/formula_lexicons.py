"""锁定公式到装修标题/正文知识库词库文件的确定性映射。"""

from __future__ import annotations

from typing import Any

from yuxi.content.v3.body_calling import get_decoration_body_calling
from yuxi.content.v3.fixtures import load_decoration_semantic_lexicons


TITLE_FORMULA_LEXICON_CODES = {
    "T01": ("title.audience", "title.positive_result"),
    "T02": ("title.oral_emotion", "title.contrast_result"),
    "T03": ("title.positioning", "title.question", "title.beneficial_result"),
    "T04": ("title.suspense", "title.pain"),
    "T05": ("title.advice", "title.audience", "title.solution"),
    "T06": ("title.house_type", "title.contrast"),
    "T07": ("title.instruction_value",),
}


def _semantic_lexicon_catalog() -> dict[str, dict[str, Any]]:
    return {item["code"]: item for item in load_decoration_semantic_lexicons()["categories"]}


def get_formula_lexicon_requirements(title_formula_code: str, body_formula_code: str) -> dict[str, Any]:
    """返回必须从标题资料库和正文资料库加载的精确文件清单。"""

    catalog = _semantic_lexicon_catalog()
    title_codes = TITLE_FORMULA_LEXICON_CODES[title_formula_code]
    body_codes = tuple(get_decoration_body_calling(body_formula_code)["lexicon_calls"])

    def build(scope: str, codes: tuple[str, ...]) -> list[dict[str, str]]:
        requirements = []
        for code in codes:
            item = catalog[code]
            requirements.append(
                {
                    "code": code,
                    "name": item["name"],
                    "knowledge_base_name": "标题资料库" if scope == "title" else "正文资料库",
                    "filename": item["source_heading"].split("、", 1)[-1],
                }
            )
        return requirements

    return {
        "title_formula_code": title_formula_code,
        "body_formula_code": body_formula_code,
        "title": build("title", title_codes),
        "body": build("body", body_codes),
    }
