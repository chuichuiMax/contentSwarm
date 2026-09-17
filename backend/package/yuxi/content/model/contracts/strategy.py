"""新行业策略契约；不改变历史 V1 的组合组及方向语义。"""

from __future__ import annotations

from math import isclose
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


class StrategyContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class SelectStrategyInputV2(StrategyContract):
    content_brief: dict[str, Any] = Field(min_length=1)
    evidence_bundle: dict[str, Any]
    strategy_candidates: dict[str, Any] = Field(min_length=1)
    runtime_config_snapshot: dict[str, Any]


class CandidateAssessment(StrategyContract):
    candidate_id: str = Field(
        min_length=1, description="公式/手法使用候选 code（例如 T03、C04、M03），参考文章使用资产 id"
    )
    eligible: bool
    dimensions: dict[str, StrictInt] = Field(default_factory=dict)
    total: float | None = None
    input_paths: list[str] = Field(
        default_factory=list,
        description=(
            "仅填写本次 payload 中真实存在且非空的证据路径，如 content_brief.form_values.pain。"
            "缺失字段只在 reason 中说明，不能作为路径；因资料缺失淘汰时可填 []。"
            "允许 payload. 包装前缀并统一移除。"
        ),
    )
    reason: str = Field(min_length=1)

    @field_validator("input_paths")
    @classmethod
    def canonical_input_paths(cls, paths):
        return [path.removeprefix("payload.") for path in paths]


class StrategyDecisionV2(StrategyContract):
    status: Literal["selected", "needs_input", "no_candidate"]
    industry_slug: str = Field(min_length=1)
    strategy_mode: Literal["direction_scoped", "scored"]
    direction_code: str | None = None
    rule_version_id: str = Field(min_length=1)
    policy_hash: str = Field(min_length=64, max_length=64)
    title_formula_code: str | None = None
    body_formula_code: str | None = None
    creation_method_codes: list[str] = Field(
        default_factory=list, description="首项为最高分兼容核心手法；所有项包括辅助项均须同时兼容标题和正文公式"
    )
    title_assessments: list[CandidateAssessment] = Field(default_factory=list)
    body_assessments: list[CandidateAssessment] = Field(default_factory=list)
    method_assessments: list[CandidateAssessment] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    unresolved_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_selection_shape(self):
        if self.strategy_mode == "direction_scoped":
            if not self.direction_code:
                raise ValueError("方向选式模式必须明确本次采用方向")
            if any(
                item.dimensions or item.total is not None for item in self.title_assessments + self.body_assessments
            ):
                raise ValueError("方向选式不允许公式数值评分")
        if len(self.creation_method_codes) != len(set(self.creation_method_codes)):
            raise ValueError("创作手法不能重复")
        if self.status == "selected":
            if not self.title_formula_code or not self.body_formula_code or not self.creation_method_codes:
                raise ValueError("成功决策必须选择标题、正文公式和手法")
            if self.unresolved_questions:
                raise ValueError("仍有必要资料缺口时不得标记选择成功")
        elif self.title_formula_code or self.body_formula_code or self.creation_method_codes:
            raise ValueError("未成功决策不得提交选中公式或手法")
        elif not self.unresolved_questions:
            raise ValueError("未成功决策必须说明具体缺口")
        return self


def resolve_input_path(inputs: dict[str, Any], path: str) -> Any:
    value: Any = inputs
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdecimal() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise ValueError(f"评分引用了不存在的输入字段: {path}")
    if value is None or value == "" or value == [] or value == {}:
        raise ValueError(f"评分引用了空输入字段: {path}")
    return value


def validate_strategy_decision(
    payload: dict[str, Any],
    candidates: dict[str, Any],
    *,
    content_brief: dict[str, Any],
    evidence_bundle: dict[str, Any],
) -> StrategyDecisionV2:
    result = StrategyDecisionV2.model_validate(payload)
    if candidates.get("auto_direction") and candidates["strategy_mode"] == "direction_scoped":
        direction = next(
            (item for item in candidates["direction_options"] if item["code"] == result.direction_code), None
        )
        if direction is None:
            raise ValueError("自动方向必须来自当前行业方向目录")
        candidates = {
            **candidates,
            "direction_code": direction["code"],
            "direction_blueprint": direction.get("direction_blueprint"),
            "valid_formula_pairs": direction["valid_formula_pairs"],
            "title_formulas": [
                item for item in candidates["title_formulas"] if item["code"] in direction["title_formula_codes"]
            ],
            "content_formulas": [
                item for item in candidates["content_formulas"] if item["code"] in direction["body_formula_codes"]
            ],
        }
    for key in ("industry_slug", "strategy_mode", "direction_code", "rule_version_id", "policy_hash"):
        if getattr(result, key) != candidates[key]:
            raise ValueError(f"Agent 不得修改锁定策略字段: {key}")
    inputs = {"content_brief": content_brief, "evidence_bundle": evidence_bundle}
    ranked: dict[str, dict[str, CandidateAssessment]] = {}
    for label, section, assessments, scale_key in (
        ("title", "title_formulas", result.title_assessments, "formula"),
        ("body", "content_formulas", result.body_assessments, "formula"),
        ("method", "methods", result.method_assessments, "method"),
    ):
        allowed = {item["code"] for item in candidates[section]}
        assessed = {item.candidate_id for item in assessments}
        if len(assessed) != len(assessments) or not assessed.issubset(allowed):
            raise ValueError(f"{label} 评分包含重复或候选池外 ID")
        if result.status == "selected" and assessed != allowed:
            raise ValueError(f"{label} 必须比较当前候选池全部候选")
        scale = candidates["scoring"].get(scale_key)
        ranked[label] = {}
        for item in assessments:
            for path in item.input_paths:
                if not path.startswith(("content_brief.", "evidence_bundle.")):
                    raise ValueError("评分证据必须引用本次输入或当前证据")
                resolve_input_path(inputs, path)
            if not item.eligible:
                if item.dimensions or item.total is not None:
                    raise ValueError("硬性淘汰的候选不得参与评分")
                continue
            if not item.input_paths:
                raise ValueError("合格候选必须提供输入证据路径")
            if scale:
                weights = scale["weights"]
                if set(item.dimensions) != set(weights):
                    raise ValueError(f"{label} 评分维度不完整或包含未定义维度")
                if any(value < 0 or value > 4 for value in item.dimensions.values()):
                    raise ValueError("评分必须为 0—4 整数")
                total = sum(item.dimensions[key] / 4 * weight for key, weight in weights.items())
                if item.total is not None and not isclose(item.total, total, abs_tol=0.001, rel_tol=0):
                    raise ValueError("评分总分与锁定权重不一致")
                item.total = total
            ranked[label][item.candidate_id] = item
    if result.status != "selected":
        return result
    for label, codes in (
        ("title", [result.title_formula_code]),
        ("body", [result.body_formula_code]),
        ("method", result.creation_method_codes),
    ):
        if not set(codes).issubset(ranked[label]):
            raise ValueError(f"选中的 {label} 必须来自合格候选")
    pair = [result.title_formula_code, result.body_formula_code]
    if pair not in candidates["valid_formula_pairs"]:
        raise ValueError("选中的标题与正文公式配对不兼容")
    body_definitions = {item["code"]: item for item in candidates["content_formulas"]}
    title_definitions = {item["code"]: item for item in candidates["title_formulas"]}
    method_definitions = {item["code"]: item for item in candidates["methods"]}
    core_codes = {code for code in ranked["method"] if method_definitions[code].get("method_type", "core") == "core"}

    def compatible_codes(title_code, body_code):
        allowed = set(ranked["method"])
        for definition in (title_definitions[title_code], body_definitions[body_code]):
            if definition.get("compatible_methods"):
                allowed &= set(definition["compatible_methods"])
        return allowed

    compatible = compatible_codes(result.title_formula_code, result.body_formula_code)
    if not set(result.creation_method_codes).issubset(compatible):
        raise ValueError("选中的创作手法与标题或正文公式不兼容")
    method_scale = candidates["scoring"]["method"]
    eligible_methods = [ranked["method"][code] for code in compatible & core_codes]
    if not eligible_methods:
        raise ValueError("缺少与公式兼容的合格核心手法")
    primary = min(
        eligible_methods,
        key=lambda item: (
            -item.total,
            *(-item.dimensions[key] for key in method_scale["tie_break"]),
            item.candidate_id,
        ),
    )
    if result.creation_method_codes[0] != primary.candidate_id:
        raise ValueError(
            "主手法必须为兼容候选中评分最高项，并遵守同分规则；"
            f"按本次提交评分计算，creation_method_codes[0] 应为 {primary.candidate_id}（{primary.total:g} 分）。"
            f"总分降序后依次比较 {', '.join(method_scale['tie_break'])} 维度降序，最后按 candidate_id 升序。"
            "保留基于事实的评分，修正选择顺序，不得为了保留原选择而改分。"
        )
    if result.strategy_mode == "scored":
        weights = candidates["formula_pair_weights"]
        tie_break = candidates["scoring"]["formula"]["tie_break"]
        possible = [
            (title, body)
            for title, body in candidates["valid_formula_pairs"]
            if title in ranked["title"] and body in ranked["body"] and compatible_codes(title, body) & core_codes
        ]

        def pair_key(pair):
            title, body = ranked["title"][pair[0]], ranked["body"][pair[1]]
            return (
                -(title.total * weights["title"] + body.total * weights["body"]),
                *(
                    -(title.dimensions[key] * weights["title"] + body.dimensions[key] * weights["body"])
                    for key in tie_break
                ),
                *pair,
            )

        expected_pair = min(possible, key=pair_key)
        if tuple(pair) != expected_pair:
            raise ValueError(
                "应选择评分最高的兼容公式配对，并遵守同分规则；"
                f"根据本次提交的评分，正确配对为 {expected_pair[0]} + {expected_pair[1]}"
            )
    return result
