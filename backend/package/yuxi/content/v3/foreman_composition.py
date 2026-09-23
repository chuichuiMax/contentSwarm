"""按已抽取事实选定 M/N 标题与正文组合，订单冻结后不得重新选式。"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any


def lock_evidence_composition(catalog: dict[str, Any], fact_index: dict[str, Any], *, seed: str) -> dict[str, Any]:
    from yuxi.content.control.workflow.creation_plan import _missing_title_formula_variables

    result = deepcopy(catalog)
    direction = result.get("direction_code")
    rules = [r for r in result.get("source_rules", []) if direction in r.get("content_type_codes", [])]
    if len(rules) != 1:
        return result  # 由现有计划配置校验报告错误。
    rule = rules[0]
    metadata = rule.get("source_metadata") or {}
    if metadata.get("formula_selection_policy") != "evidence_composition_v1" or metadata.get("locked_composition"):
        return result
    available = set(fact_index["available_variable_codes"])
    method_codes = {item["method_code"] for item in rule["method_members"]}

    def rank(code: str) -> str:
        return hashlib.sha256(f"{seed}:{code}".encode()).hexdigest()

    titles = [
        item
        for code in rule["title_formula_candidate_codes"]
        for item in result["title_formulas"]
        if item["code"] == code and method_codes <= set(item.get("compatible_methods") or [])
    ]
    bodies = [
        item
        for code in rule["body_formula_candidate_codes"]
        for item in result["content_formulas"]
        if item["code"] == code and method_codes <= set(item.get("compatible_methods") or [])
    ]
    if not titles or not bodies:
        raise ValueError("事实组合规则没有可用的标题或正文公式")
    for body in bodies:
        source = body.get("source_content") or {}
        pool = source.get("component_pool")
        if not pool:
            continue
        # 四模块先按事实覆盖率筛选，再按任务种子变化；缺资料时锁定缺口最少的四项并明确补料。
        components = sorted(
            pool,
            key=lambda c: (len(set(c["required_variables"]) - available), rank(c["code"])),
        )[: source["component_count"]]
        # 保持文档叙事顺序，避免身份、背景、结果被无序拼接。
        selected = {c["code"] for c in components}
        components = [c for c in pool if c["code"] in selected]
        source["selected_components"] = [c["code"] for c in components]
        body["source_content"] = source
        body["required_variables"] = list(dict.fromkeys(v for c in components for v in c["required_variables"]))
        body["structure_schema"] = [c["instruction"] for c in components]
    title = min(titles, key=lambda item: len(_missing_title_formula_variables(item, available)))
    body = min(bodies, key=lambda item: (len(set(item["required_variables"]) - available), rank(item["code"])))
    rule["title_formula_candidate_codes"] = [title["code"]]
    rule["body_formula_candidate_codes"] = [body["code"]]
    rule.setdefault("hard_conditions", {})["allowed_formula_pairs"] = [[title["code"], body["code"]]]
    metadata["locked_composition"] = {
        "title_formula_code": title["code"],
        "body_formula_code": body["code"],
        "body_components": body["source_content"]["selected_components"],
        "available_variable_codes": sorted(available),
        "selection_seed": seed,
    }
    rule["source_metadata"] = metadata
    return result
