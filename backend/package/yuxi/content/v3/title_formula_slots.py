"""装修标题公式的可执行槽位语义。"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from typing import Any

from yuxi.content.v3.foreman_rules import load_foreman_rule_catalog


@lru_cache(maxsize=1)
def _canonical_title_formulas() -> dict[str, dict[str, Any]]:
    return {item["code"]: item for item in load_foreman_rule_catalog()["title_formulas"]}


def enrich_decoration_title_formula(formula: dict[str, Any]) -> dict[str, Any]:
    """为旧规则版本补齐不改变事实的槽位语义和写法示例。

    已冻结的公式代码、名称和业务事实保持不变；显式配置始终优先。
    """

    enriched = deepcopy(formula)
    canonical = _canonical_title_formulas().get(str(enriched.get("code") or ""))
    if canonical is None:
        return enriched
    source_content = deepcopy(enriched.get("source_content") or {})
    canonical_source = canonical.get("source_content") or {}
    if not source_content.get("slot_schema"):
        source_content["slot_schema"] = deepcopy(canonical_source.get("slot_schema") or [])
    enriched["source_content"] = source_content
    if not enriched.get("reference_examples"):
        enriched["reference_examples"] = deepcopy(canonical.get("reference_examples") or [])
    return enriched


def title_formula_slot_schema(formula: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """返回标题的有序必填槽位；同一槽位内的来源按 one-of 解释。"""

    enriched = enrich_decoration_title_formula(formula)
    slots = (enriched.get("source_content") or {}).get("slot_schema") or []
    return tuple(deepcopy(slots))


def required_title_lexicon_codes(formula: dict[str, Any]) -> frozenset[str]:
    """返回必须逐字使用的词库；槽位公式中的词库默认只是 one-of 来源。"""

    enriched = enrich_decoration_title_formula(formula)
    explicit = enriched.get("required_lexicon_codes")
    if explicit is not None:
        return frozenset(str(code) for code in explicit if str(code).strip())
    if title_formula_slot_schema(enriched):
        return frozenset()
    return frozenset(str(code) for code in enriched.get("lexicon_codes") or [] if str(code).strip())


__all__ = [
    "enrich_decoration_title_formula",
    "required_title_lexicon_codes",
    "title_formula_slot_schema",
]
