"""共享公式逻辑下的行业组合编排，以及锁定时的通用公式应用。"""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from yuxi.content.catalog import CONTENT_TYPES, INDUSTRY_CONFIG


def import_industry_matrix(bundle: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(bundle)
    fixtures = Path(__file__).parent / "v3/fixtures"
    matrix = json.loads((fixtures / "industry_matrix.json").read_text())
    formulas = json.loads((fixtures / "cross_industry_formulas.json").read_text())
    for section, definitions in formulas.items():
        by_code = {item["code"]: item for item in definitions}
        for item in result[section]:
            definition = by_code[item["code"]]
            item.setdefault("source_content", {})["cross_industry"] = {
                key: value for key, value in definition.items() if key != "code"
            }
    industries = {group["industry"] for group in matrix["groups"]}
    # 仅替换原平台占位组合或本目录之前的导入；管理员自行新增的组合保留。
    result["combination_rules"] = [
        group
        for group in result["combination_rules"]
        if not (
            set(group.get("industry_scope") or []) & industries
            and group.get("source_metadata", {}).get("source")
            in {"platform-v3-seed", "published-v2-industry-migration", "industry-matrix-editorial"}
        )
    ]
    for index, group in enumerate(matrix["groups"]):
        slug = group["industry"]
        direction = group["content_type_code"]
        aliases = dict(zip((item["code"] for item in CONTENT_TYPES), INDUSTRY_CONFIG[slug]["aliases"], strict=True))
        result["combination_rules"].append(
            {
                "id": group["code"],
                "schema_version": 3,
                "enabled": True,
                "content_type_codes": [direction],
                "industry_scope": [slug],
                "combination_type": ["single", "double", "triple", "quadruple"][len(group["methods"]) - 1],
                "method_members": [
                    {"method_code": code, "order": order, "role": "primary" if order == 1 else "supporting"}
                    for order, code in enumerate(group["methods"], 1)
                ],
                "title_formula_candidate_codes": group["title_formula_candidate_codes"],
                "body_formula_candidate_codes": group["body_formula_candidate_codes"],
                "scenario_description": group["scenario_description"],
                "recommendation_reason": group["scenario_description"],
                "priority": 1000 - index,
                "source_metadata": {
                    **matrix["source"],
                    "industry": slug,
                    "source_row": index + 1,
                    "catalog_code": group["code"],
                    "content_direction_name": aliases[direction],
                },
                "hard_conditions": {"single_narrative_axis": True, "unsupported_numbers": "block"},
            }
        )
    return result


def resolve_industry_formula(formula: dict[str, Any], *, industry_slug: str, scenario: str) -> dict[str, Any]:
    """同一编码沿用启停与引用关系；原文和旧版本不受通用应用说明影响。"""
    application = (formula.get("source_content") or {}).get("cross_industry")
    if industry_slug == "decoration" or not application:
        return deepcopy(formula)
    result = deepcopy(formula)
    result.update(
        {
            key: deepcopy(application[key])
            for key in ("name", "core_goal", "structure_schema", "reference_examples")
            if key in application
        }
    )
    result["source_content"] = {
        "application": "cross_industry",
        "industry": industry_slug,
        "scenario_description": scenario,
        "core_goal": application["core_goal"],
    }
    if "suitable_scenes" in result:
        result["suitable_scenes"] = [scenario] if scenario else []
    if "structure_schema" in application:
        result["output_schema"] = {**result.get("output_schema", {}), "sections": len(application["structure_schema"])}
    return result
