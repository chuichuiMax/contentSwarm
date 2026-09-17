"""装修工长新版创作规则目录及版本导入。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).with_name("fixtures") / "foreman_rule_catalog_v1.json"
DIRECTION_MATRIX_PATH = Path(__file__).with_name("fixtures") / "foreman_direction_matrix_v2.json"
METHOD_CODES = {f"FRM{index:02d}" for index in range(1, 10)}
TITLE_CODES = {f"FRT{index:02d}" for index in range(1, 13)}
BODY_CODES = {f"FRB{index:02d}" for index in range(1, 10)}
GROUP_CODES = {f"FRG{index:02d}" for index in range(1, 8)}
DIRECTION_BINDINGS = {
    "CT01": {"method": "FRM05", "body": "FRB05", "topic_type": "自我介绍", "content_group": "自我介绍"},
    "CT02": {"method": "FRM06", "body": "FRB06", "topic_type": "价格营销", "content_group": "人工单价"},
    "CT03": {"method": "FRM07", "body": "FRB07", "topic_type": "价格营销", "content_group": "施工报价"},
    "CT04": {"method": "FRM08", "body": "FRB08", "topic_type": "价格营销", "content_group": "施工报价"},
    "CT05": {"method": "FRM09", "body": "FRB09", "topic_type": "价格营销", "content_group": "施工报价"},
    "CT06": {"method": "FRM04", "body": "FRB04", "topic_type": "工艺展示", "content_group": "工艺展示"},
    "CT07": {"method": "FRM02", "body": "FRB02", "topic_type": "日常工作", "content_group": "日常工作"},
}


class ForemanRuleValidationError(ValueError):
    """装修工长规则目录不完整或引用关系错误。"""


def load_foreman_rule_catalog(
    path: Path = CATALOG_PATH,
    direction_matrix_path: Path = DIRECTION_MATRIX_PATH,
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        direction_matrix = json.loads(direction_matrix_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ForemanRuleValidationError("无法读取装修工长规则目录") from exc
    if payload.get("schema_version") != 1 or payload.get("industry_slug") != "decoration":
        raise ForemanRuleValidationError("装修工长规则目录版本或行业错误")
    if direction_matrix.get("schema_version") != 2 or direction_matrix.get("industry_slug") != "decoration":
        raise ForemanRuleValidationError("装修工长一级内容方向矩阵版本或行业错误")
    payload["source"] = {**payload["source"], **direction_matrix["source"]}
    payload["combination_rules"] = direction_matrix.get("groups") or []
    if {item.get("code") for item in payload.get("methods") or []} != METHOD_CODES:
        raise ForemanRuleValidationError("装修工长正文模式必须完整覆盖 FRM01～FRM09")
    if {item.get("code") for item in payload.get("title_formulas") or []} != TITLE_CODES:
        raise ForemanRuleValidationError("装修工长标题公式必须完整覆盖 FRT01～FRT12")
    if {item.get("code") for item in payload.get("content_formulas") or []} != BODY_CODES:
        raise ForemanRuleValidationError("装修工长正文公式必须完整覆盖 FRB01～FRB09")
    groups = payload.get("combination_rules") or []
    if {item.get("id") for item in groups} != GROUP_CODES:
        raise ForemanRuleValidationError("装修工长组合规则必须完整覆盖 FRG01～FRG07")
    directions = [code for item in groups for code in item.get("content_type_codes") or []]
    if len(directions) != 7 or set(directions) != set(DIRECTION_BINDINGS):
        raise ForemanRuleValidationError("装修工长 CT01～CT07 必须各自且仅命中一个组合")
    for item in [*payload["methods"], *payload["title_formulas"], *payload["content_formulas"]]:
        if item.get("industry_scope") != ["decoration"]:
            raise ForemanRuleValidationError(f"装修工长规则 {item.get('code')} 缺少行业范围")
    for group in groups:
        methods = [member.get("method_code") for member in group.get("method_members") or []]
        if len(methods) != 1 or methods[0] not in METHOD_CODES or group.get("combination_type") != "single":
            raise ForemanRuleValidationError(f"组合 {group.get('id')} 的正文模式引用无效")
        if not set(group.get("title_formula_candidate_codes") or []).issubset(TITLE_CODES):
            raise ForemanRuleValidationError(f"组合 {group.get('id')} 的标题公式引用无效")
        if set(group.get("body_formula_candidate_codes") or []) != {"FRB" + methods[0][-2:]}:
            raise ForemanRuleValidationError(f"组合 {group.get('id')} 的正文公式引用无效")
        if not group.get("content_type_codes") or group.get("industry_scope") != ["decoration"]:
            raise ForemanRuleValidationError(f"组合 {group.get('id')} 的业务方向或行业范围无效")
        direction = group["content_type_codes"][0]
        expected = DIRECTION_BINDINGS[direction]
        metadata = group.get("source_metadata") or {}
        blueprint = metadata.get("composition_blueprint") or {}
        if (
            methods != [expected["method"]]
            or group["body_formula_candidate_codes"] != [expected["body"]]
            or metadata.get("topic_type") != expected["topic_type"]
            or blueprint.get("content_type") != metadata.get("content_direction_name")
        ):
            raise ForemanRuleValidationError(f"组合 {group.get('id')} 未按一级内容方向一一绑定")
        layers = blueprint.get("layer_sequence") or []
        phrase_rules = blueprint.get("phrase_composition") or []
        layer_codes = [item.get("code") for item in layers]
        if [item.get("order") for item in layers] != list(range(1, len(layers) + 1)):
            raise ForemanRuleValidationError(f"组合 {group.get('id')} 的层级顺序无效")
        if [item.get("layer_code") for item in phrase_rules] != layer_codes:
            raise ForemanRuleValidationError(f"组合 {group.get('id')} 的词组组合未逐层对应")
        by_layer = {item["layer_code"]: item for item in phrase_rules}
        if by_layer["content_purpose"].get("allowed_groups") != [expected["content_group"]]:
            raise ForemanRuleValidationError(f"组合 {group.get('id')} 的内容词组不符合表格")
        if direction == "CT02" and "evidence" in layer_codes:
            raise ForemanRuleValidationError("项目单价层级组合不得加入证据层")
        if direction != "CT02" and layer_codes != [
            "persona",
            "business",
            "content_purpose",
            "user_value",
            "content_structure",
            "evidence",
            "conversion",
        ]:
            raise ForemanRuleValidationError(f"组合 {group.get('id')} 必须完整执行七层结构")
    return payload


def import_foreman_rules(bundle: dict[str, Any]) -> dict[str, Any]:
    """生成装修专属规则层，保留其他行业规则与历史版本。"""

    result = deepcopy(bundle)
    catalog = load_foreman_rule_catalog()
    other_industries = ["food", "education", "beauty", "retail", "professional-services"]
    for section in ("methods", "title_formulas", "content_formulas"):
        incoming = deepcopy(catalog[section])
        if section in {"title_formulas", "content_formulas"}:
            for item in incoming:
                item["source_content"] = {
                    **(item.get("source_content") or {}),
                    "source": deepcopy(catalog["source"]),
                    "business_formula": "装修工长 AI 小红书内容生成逻辑",
                }
        result[section] = [
            {**item, "industry_scope": other_industries}
            for item in result.get(section) or []
            if item.get("industry_scope") != ["decoration"]
            and item.get("code") not in {entry["code"] for entry in catalog[section]}
        ] + incoming
    foreman_groups = deepcopy(catalog["combination_rules"])
    for group in foreman_groups:
        group["source_metadata"] = {**deepcopy(catalog["source"]), **group.get("source_metadata", {})}
        body_code = group["body_formula_candidate_codes"][0]
        group.setdefault("hard_conditions", {})["allowed_formula_pairs"] = [
            [title_code, body_code] for title_code in group["title_formula_candidate_codes"]
        ]
    result["combination_rules"] = [
        item for item in result.get("combination_rules") or [] if item.get("industry_scope") != ["decoration"]
    ] + foreman_groups
    if not any(item.get("code") == "quote_type" for item in result.get("variables") or []):
        result.setdefault("variables", []).append(
            {
                "code": "quote_type",
                "name": "报价口径",
                "value_type": "string",
                "unit_schema": {},
                "evidence_policy": {"required": True},
                "sensitivity": "high_risk",
                "allowed_usages": ["title", "body"],
                "validation_schema": {
                    "allowed_values": ["standard_unit_price", "project_quote", "budget", "settlement"]
                },
                "enabled": True,
                "sort_order": len(result["variables"]),
            }
        )
    return result


__all__ = [
    "CATALOG_PATH",
    "DIRECTION_BINDINGS",
    "DIRECTION_MATRIX_PATH",
    "ForemanRuleValidationError",
    "import_foreman_rules",
    "load_foreman_rule_catalog",
]
