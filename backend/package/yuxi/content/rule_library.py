"""规则库的原文导入与启用候选视图。"""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from yuxi.content.v3.fixtures import load_decoration_matrix


def active_combination_rules(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    methods = {item["code"] for item in bundle["methods"] if item.get("enabled", True)}
    titles = {item["code"] for item in bundle["title_formulas"] if item.get("enabled", True)}
    bodies = {item["code"] for item in bundle["content_formulas"] if item.get("enabled", True)}
    result = []
    for item in bundle["combination_rules"]:
        if not item.get("enabled", True):
            continue
        if any(member["method_code"] not in methods for member in item["method_members"]):
            continue
        title_codes = [code for code in item["title_formula_candidate_codes"] if code in titles]
        body_codes = [code for code in item["body_formula_candidate_codes"] if code in bodies]
        if title_codes and body_codes:
            result.append(
                {**item, "title_formula_candidate_codes": title_codes, "body_formula_candidate_codes": body_codes}
            )
    return result


def import_feishu_catalog(bundle: dict[str, Any]) -> dict[str, Any]:
    """只替换文档覆盖的公式与装修矩阵，保留其他行业配置及运行字段。"""
    result = deepcopy(bundle)
    catalog = json.loads((Path(__file__).parent / "v3/fixtures/feishu_formula_catalog.json").read_text())
    matrix = load_decoration_matrix()
    for section in ("title_formulas", "content_formulas"):
        existing = {item["code"]: item for item in result[section]}
        result[section] = [{**existing[item["code"]], **item} for item in catalog[section]]
    existing_groups = {
        item["source_metadata"].get("source_row"): item
        for item in result["combination_rules"]
        if item.get("industry_scope") == ["decoration"]
    }
    groups = []
    for group in matrix["groups"]:
        row = group["source_metadata"]["source_row"]
        groups.append(
            {
                **existing_groups[row],
                "content_type_codes": [group["content_direction"]["code"]],
                "method_members": group["method_members"],
                "combination_type": group["combination_type"],
                "title_formula_candidate_codes": group["title_formula_candidate_codes"],
                "body_formula_candidate_codes": group["body_formula_candidate_codes"],
                "scenario_description": group["scenario_description"],
                "recommendation_reason": group["scenario_description"],
                "source_metadata": {
                    **matrix["source"],
                    **group["source_metadata"],
                    "content_direction_name": group["content_direction"]["name"],
                },
            }
        )
    result["combination_rules"] = groups + [
        item for item in result["combination_rules"] if item.get("industry_scope") != ["decoration"]
    ]
    return result
