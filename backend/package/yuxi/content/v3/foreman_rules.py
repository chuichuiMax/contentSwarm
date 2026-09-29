"""装修工长新版创作规则目录及版本导入。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).with_name("fixtures") / "foreman_rule_catalog_v1.json"
DIRECTION_MATRIX_PATH = Path(__file__).with_name("fixtures") / "foreman_direction_matrix_v2.json"
METHOD_CODES = {f"FRM{index:02d}" for index in range(1, 13)}
TITLE_CODES = {f"FRT{index:02d}" for index in range(1, 23)} - {
    "FRT13",
    "FRT14",
    "FRT15",
    "FRT17",
    "FRT18",
    "FRT19",
}
BODY_CODES = {f"FRB{index:02d}" for index in range(1, 17)} - {"FRB12"}
GROUP_CODES = {f"FRG{index:02d}" for index in range(1, 8)}
DIRECTION_BINDINGS = {
    "CT01": {"method": "FRM02", "body": "FRB02", "topic_type": "案例分享", "content_group": "案例"},
    "CT02": {"method": "FRM01", "body": "FRB01", "topic_type": "价格营销", "content_group": "报价清单"},
    "CT03": {"method": "FRM07", "body": "FRB07", "topic_type": "价格营销", "content_group": "施工报价"},
    "CT04": {"method": "FRM08", "body": "FRB08", "topic_type": "价格营销", "content_group": "施工报价"},
    "CT05": {"method": "FRM09", "body": "FRB09", "topic_type": "价格营销", "content_group": "施工报价"},
    "CT06": {"method": "FRM11", "body": "FRB11", "topic_type": "工艺展示", "content_group": "工艺展示"},
    "CT07": {"method": "FRM05", "body": "FRB05", "topic_type": "人设自荐", "content_group": "自我介绍"},
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
        raise ForemanRuleValidationError("装修工长正文模式必须完整覆盖 FRM01～FRM12")
    if {item.get("code") for item in payload.get("title_formulas") or []} != TITLE_CODES:
        raise ForemanRuleValidationError(
            "装修工长标题公式必须完整覆盖 FRT01～FRT12、FRT16 及 FRT20～FRT22（不含已删除的日常巡检标题）"
        )
    if {item.get("code") for item in payload.get("content_formulas") or []} != BODY_CODES:
        raise ForemanRuleValidationError("装修工长正文公式必须完整覆盖 FRB01～FRB16（不含已删除的 FRB12）")
    groups = payload.get("combination_rules") or []
    if {item.get("id") for item in groups} != GROUP_CODES:
        raise ForemanRuleValidationError("装修工长组合规则必须完整覆盖 FRG01～FRG07")
    directions = [code for item in groups for code in item.get("content_type_codes") or []]
    if len(directions) != 7 or set(directions) != set(DIRECTION_BINDINGS):
        raise ForemanRuleValidationError("装修工长 CT01～CT07 必须各自且仅命中一个组合")
    for item in [*payload["methods"], *payload["title_formulas"], *payload["content_formulas"]]:
        if item.get("industry_scope") != ["decoration"]:
            raise ForemanRuleValidationError(f"装修工长规则 {item.get('code')} 缺少行业范围")
    for formula in payload["title_formulas"]:
        examples = formula.get("reference_examples") or []
        slots = (formula.get("source_content") or {}).get("slot_schema") or []
        if not examples or any(not isinstance(example, str) or not example.strip() for example in examples):
            raise ForemanRuleValidationError(f"标题公式 {formula['code']} 缺少有效参考示例")
        if not slots or len({slot.get("code") for slot in slots}) != len(slots):
            raise ForemanRuleValidationError(f"标题公式 {formula['code']} 的槽位定义缺失或重复")
        for slot in slots:
            variable_codes = slot.get("variable_codes") or []
            lexicon_codes = slot.get("lexicon_codes") or []
            if (
                not slot.get("code")
                or not slot.get("label")
                or (not variable_codes and not lexicon_codes)
                or len(variable_codes) != len(set(variable_codes))
                or len(lexicon_codes) != len(set(lexicon_codes))
            ):
                raise ForemanRuleValidationError(f"标题公式 {formula['code']} 的槽位 {slot.get('code')} 无效")
        declared_variables = {code for slot in slots for code in slot.get("variable_codes") or []}
        if not set(formula.get("variable_schema") or []).issubset(declared_variables):
            raise ForemanRuleValidationError(f"标题公式 {formula['code']} 的变量没有完整映射到标题槽位")
    for group in groups:
        methods = [member.get("method_code") for member in group.get("method_members") or []]
        if len(methods) != 1 or methods[0] not in METHOD_CODES or group.get("combination_type") != "single":
            raise ForemanRuleValidationError(f"组合 {group.get('id')} 的正文模式引用无效")
        if not set(group.get("title_formula_candidate_codes") or []).issubset(TITLE_CODES):
            raise ForemanRuleValidationError(f"组合 {group.get('id')} 的标题公式引用无效")
        expected_bodies = (
            ["FRB11", "FRB13", "FRB14", "FRB15", "FRB16"]
            if group["content_type_codes"][0] == "CT06"
            else ["FRB" + methods[0][-2:]]
        )
        if group.get("body_formula_candidate_codes") != expected_bodies:
            raise ForemanRuleValidationError(f"组合 {group.get('id')} 的正文公式引用无效")
        if not group.get("content_type_codes") or group.get("industry_scope") != ["decoration"]:
            raise ForemanRuleValidationError(f"组合 {group.get('id')} 的业务方向或行业范围无效")
        direction = group["content_type_codes"][0]
        expected = DIRECTION_BINDINGS[direction]
        metadata = group.get("source_metadata") or {}
        blueprint = metadata.get("composition_blueprint") or {}
        if (
            methods != [expected["method"]]
            or group["body_formula_candidate_codes"] != expected_bodies
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
            raise ForemanRuleValidationError("报价清单层级组合不强制独立证据层，费用以正文公式与已确认资料为准")
        if direction == "CT01" and "FRT20" not in group["title_formula_candidate_codes"]:
            raise ForemanRuleValidationError("案例分享必须包含 FRT20 风格情绪钩子+案例卖点标题公式")
        if direction == "CT07" and "FRT12" not in group["title_formula_candidate_codes"]:
            raise ForemanRuleValidationError("人设自荐必须包含 FRT12 地域+身份+业务标题公式")
        if direction == "CT07" and "FRT22" not in group["title_formula_candidate_codes"]:
            raise ForemanRuleValidationError("人设自荐必须包含 FRT22 痛点问题+专业主张标题公式")
        if direction not in {"CT03", "CT04", "CT05"} and "FRT06" in group["title_formula_candidate_codes"]:
            raise ForemanRuleValidationError("FRT06 含价格槽位，只能用于报价类型")
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
        group.setdefault("hard_conditions", {})["allowed_formula_pairs"] = [
            [title_code, body_code]
            for title_code in group["title_formula_candidate_codes"]
            for body_code in group["body_formula_candidate_codes"]
        ]
    result["combination_rules"] = [
        item for item in result.get("combination_rules") or [] if item.get("industry_scope") != ["decoration"]
    ] + foreman_groups
    quote_variables = {
        "quote_type": {
            "name": "报价口径",
            "value_type": "string",
            "allowed_usages": ["body"],
            "validation_schema": {"enum": ["standard_unit_price", "project_quote", "budget", "settlement"]},
            # 当家锁定走 business_record；内容工作室预算口径由用户确认表单注入，允许 manual_input。
            "allowed_sources": ["business_record", "manual_input", "human_confirmation"],
        },
        "title_price": {
            "name": "标题价格",
            "value_type": "string",
            "allowed_usages": ["title"],
            "validation_schema": {"minLength": 1, "maxLength": 100},
            "allowed_sources": ["business_record"],
        },
        "title_price_label": {
            "name": "标题价格口径",
            "value_type": "string",
            "allowed_usages": ["title", "body"],
            "validation_schema": {"minLength": 1, "maxLength": 100},
            "allowed_sources": ["business_record"],
        },
        "quote_block": {
            "name": "锁定报价原文",
            "value_type": "object",
            "allowed_usages": ["body"],
            "validation_schema": {},
            "allowed_sources": ["business_record"],
        },
    }
    variables = result.setdefault("variables", [])
    existing_by_code = {item.get("code"): item for item in variables}
    for code, definition in quote_variables.items():
        allowed_sources = tuple(definition.pop("allowed_sources"))
        payload = {
            "code": code,
            **definition,
            "unit_schema": {},
            "evidence_policy": {
                "required": True,
                "review_policy": "user_confirmed",
                "allowed_sources": list(allowed_sources),
            },
            "sensitivity": "high_risk",
            "enabled": True,
            "sort_order": existing_by_code.get(code, {}).get("sort_order", len(variables)),
        }
        if code in existing_by_code:
            variables[variables.index(existing_by_code[code])] = payload
        else:
            variables.append(payload)
    _add_craft_daily_variables(result)
    return result


def upgrade_intro_daily_rules(bundle: dict[str, Any]) -> dict[str, Any]:
    """历史兼容：修正 FRT12 作者身份槽位；人设自荐绑定 FRM05/FRB05，不再绑日常工作。"""
    result = deepcopy(bundle)
    catalog = load_foreman_rule_catalog()
    title = next(item for item in result["title_formulas"] if item["code"] == "FRT12")
    canonical_title = next(item for item in catalog["title_formulas"] if item["code"] == "FRT12")
    slots = title.setdefault("source_content", {}).setdefault(
        "slot_schema", deepcopy(canonical_title["source_content"]["slot_schema"])
    )
    identity = next(slot for slot in slots if slot["code"] == "identity")
    identity["lexicon_codes"] = [code for code in identity.get("lexicon_codes", []) if code != "title.audience"]
    title["compatible_methods"] = list(dict.fromkeys([*title.get("compatible_methods", []), "FRM05"]))
    for section, code in (("methods", "FRM05"), ("content_formulas", "FRB05")):
        if not any(item["code"] == code for item in result[section]):
            result[section].append(deepcopy(next(item for item in catalog[section] if item["code"] == code)))
    persona = next(item for item in result["combination_rules"] if item.get("content_type_codes") == ["CT07"])
    persona["method_members"] = [{"method_code": "FRM05", "role": "primary", "order": 1}]
    persona["title_formula_candidate_codes"] = ["FRT22", "FRT12"]
    persona["body_formula_candidate_codes"] = ["FRB05"]
    persona.setdefault("hard_conditions", {})["allowed_formula_pairs"] = [
        [title_code, "FRB05"] for title_code in persona["title_formula_candidate_codes"]
    ]
    return result


__all__ = [
    "CATALOG_PATH",
    "DIRECTION_BINDINGS",
    "DIRECTION_MATRIX_PATH",
    "ForemanRuleValidationError",
    "import_foreman_rules",
    "load_foreman_rule_catalog",
    "upgrade_intro_daily_rules",
    "upgrade_craft_daily_rules",
]


def _add_craft_daily_variables(bundle: dict[str, Any]) -> None:
    definitions = {
        "craft_role": ("本次施工工种或目标人群", "list"),
        "craft_count": ("本次工序数量（含单位）", "string"),
        "craft_duration": ("本次施工耗时（含单位）", "string"),
        "project": ("施工项目", "string"),
        "inspection": ("已确认巡检任务", "string"),
        "kickoff": ("已确认开工事项", "string"),
        "case_background": ("本次案例背景", "string"),
        "owner_need": ("本次业主需求", "string"),
        "solution": ("本次解决方案", "string"),
        "cost_explanation": ("本次费用解释", "string"),
    }
    variables = bundle.setdefault("variables", [])
    existing = {item["code"] for item in variables}
    for code, (name, value_type) in definitions.items():
        if code in existing:
            continue
        variables.append(
            {
                "code": code,
                "name": name,
                "value_type": value_type,
                "allowed_usages": ["title", "body"],
                "unit_schema": {},
                "validation_schema": (
                    {"pattern": r".*(?:[1-9]\d*|[一二两三四五六七八九十百]+)(?:道|步|项)(?:工序|流程|完整工序)?.*"}
                    if code == "craft_count"
                    else {"pattern": r".*(?:[1-9]\d*(?:\.\d+)?|[一二两三四五六七八九十百]+)(?:小时|天|周|个月|月).*"}
                    if code == "craft_duration"
                    else {}
                ),
                "evidence_policy": {
                    "required": True,
                    "review_policy": "user_confirmed",
                    "allowed_sources": ["manual_input", "business_record", "human_confirmation"],
                },
                "sensitivity": "high_risk" if code in {"craft_count", "craft_duration"} else "normal",
                "enabled": True,
                "sort_order": len(variables),
            }
        )


def upgrade_craft_daily_rules(bundle: dict[str, Any]) -> dict[str, Any]:
    """同步 CT06/CT07 最新公式及删除项，保留其他运营规则，不修改输入历史版本。"""
    result = deepcopy(bundle)
    catalog = load_foreman_rule_catalog()
    result["title_formulas"] = [
        item
        for item in result["title_formulas"]
        if item["code"] not in {"FRT13", "FRT14", "FRT15", "FRT17", "FRT18", "FRT19"}
    ]
    result["content_formulas"] = [item for item in result["content_formulas"] if item["code"] != "FRB12"]
    for section, codes in (
        ("methods", {"FRM01", "FRM02", "FRM05", "FRM11", "FRM12"}),
        ("title_formulas", {f"FRT{i:02d}" for i in range(13, 23)} | {"FRT01", "FRT05", "FRT07", "FRT08", "FRT09", "FRT12"}),
        ("content_formulas", {f"FRB{i:02d}" for i in range(11, 17)} | {"FRB01", "FRB02", "FRB05"}),
    ):
        existing = {item["code"]: item for item in result[section]}
        for item in catalog[section]:
            if item["code"] not in codes:
                continue
            updated = {**existing.get(item["code"], {}), **deepcopy(item)}
            if item["code"] in existing:
                result[section][result[section].index(existing[item["code"]])] = updated
            else:
                result[section].append(updated)
    for group in result["combination_rules"]:
        if group.get("content_type_codes") not in (["CT06"], ["CT07"], ["CT01"], ["CT02"]):
            continue
        canonical = next(
            x for x in catalog["combination_rules"] if x["content_type_codes"] == group["content_type_codes"]
        )
        for key in (
            "method_members",
            "title_formula_candidate_codes",
            "body_formula_candidate_codes",
            "required_variable_codes",
        ):
            group[key] = deepcopy(canonical[key])
        group.setdefault("source_metadata", {}).update(
            {
                "formula_selection_policy": "evidence_composition_v1",
                "craft_daily_source": deepcopy(catalog["source"]),
            }
        )
        group.setdefault("hard_conditions", {})["allowed_formula_pairs"] = [
            [title, body]
            for title in group["title_formula_candidate_codes"]
            for body in group["body_formula_candidate_codes"]
        ]
    _add_craft_daily_variables(result)
    return result
