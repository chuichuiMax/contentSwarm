"""策略及已准备参考的联合决策；模型输出不包含蓝图或原文。"""

from __future__ import annotations

import hashlib
import json
from math import isclose
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .strategy import (
    CandidateAssessment,
    SelectStrategyInputV2,
    StrategyContract,
    StrategyDecisionV2,
    resolve_input_path,
    validate_strategy_decision,
)


class JointStrategyInputV1(SelectStrategyInputV2):
    reference_candidates: list[dict[str, Any]]


class ReevaluateJointStrategyInputV1(JointStrategyInputV1):
    strategy_price_evidence_collection: dict[str, Any]

    @field_validator("strategy_price_evidence_collection")
    @classmethod
    def research_summary(cls, collection):
        # 价格事实已在证据包中，不重复发送整份报价表。
        return {key: collection[key] for key in ("citations", "unresolved_questions", "skipped") if key in collection}


class PreparedReferenceDecisionV1(StrategyContract):
    status: Literal["selected", "not_requested", "no_candidate", "needs_input"]
    selected_asset_id: str | None = None
    source_hash: str | None = None
    assessments: list[CandidateAssessment] = Field(default_factory=list)
    slot_mapping: dict[str, list[str]] = Field(default_factory=dict)
    reason: str = Field(min_length=1)
    unresolved_questions: list[str] = Field(default_factory=list)

    @field_validator("slot_mapping")
    @classmethod
    def canonical_slot_paths(cls, mapping):
        return {slot: [path.removeprefix("payload.") for path in paths] for slot, paths in mapping.items()}

    @model_validator(mode="after")
    def check_result_shape(self):
        if self.status == "selected":
            if not self.selected_asset_id or not self.source_hash or self.unresolved_questions:
                raise ValueError("选中参考必须锁定原文版本且无必要资料缺口")
        elif self.selected_asset_id or self.source_hash or self.slot_mapping:
            raise ValueError("未选中参考不得提交资产或事实映射")
        if self.status in {"no_candidate", "needs_input"} and not self.unresolved_questions:
            raise ValueError("参考未选中时必须说明具体缺口")
        return self


class JointStrategyDecisionV1(StrategyContract):
    strategy: StrategyDecisionV2
    reference: PreparedReferenceDecisionV1 = Field(
        description=(
            '所有模式必填。原创也必须提交 {"status":"not_requested","reason":"原创模式不选择参考"}，'
            "不能省略 reference 或放入 strategy 内。仿写按参考候选提交选择与评价。"
        )
    )


class JointStrategyDecisionV2(JointStrategyDecisionV1):
    price_research_questions: list[str] = Field(
        description=(
            "报价缺口中可由价格库检索解决的问题；没有报价缺口时填 []。"
            "不要把工程量、实际成交价或公开授权伪装成标准单价问题。"
        )
    )

    @model_validator(mode="after")
    def validate_price_gap(self):
        if self.price_research_questions and self.reference.status not in {"needs_input", "no_candidate"}:
            raise ValueError("只有参考存在资料缺口时才能申请报价补证")
        if any(not question.strip() for question in self.price_research_questions):
            raise ValueError("报价检索问题不能为空")
        return self


def validate_joint_strategy(payload, inputs: dict[str, Any]) -> JointStrategyDecisionV1:
    model = JointStrategyDecisionV2 if "price_research_questions" in payload else JointStrategyDecisionV1
    result = model.model_validate(payload)
    candidates = inputs["strategy_candidates"]
    result.strategy = validate_strategy_decision(
        result.strategy.model_dump(),
        candidates,
        content_brief=inputs["content_brief"],
        evidence_bundle=inputs["evidence_bundle"],
    )
    reference = result.reference
    rewrite = inputs["runtime_config_snapshot"].get("creation_mode") == "viral_rewrite"
    if not rewrite:
        if reference.status != "not_requested" or reference.assessments:
            raise ValueError("原创任务不进行爆款选择")
        return result
    if reference.status == "not_requested":
        raise ValueError("仿写任务不能跳过爆款选择")
    pool = {item["id"]: item for item in inputs["reference_candidates"]}
    ids = [item.candidate_id for item in reference.assessments]
    if len(ids) != len(set(ids)) or not set(ids).issubset(pool):
        raise ValueError("参考评价包含重复或候选池外资产")
    if reference.status == "selected" and set(ids) != set(pool):
        raise ValueError("必须比较本次提供的全部参考卡")
    scale = candidates["scoring"]["reference"]
    eligible = []
    for item in reference.assessments:
        for path in item.input_paths:
            validate_fact_path(inputs, path)
        if not item.eligible:
            if item.dimensions or item.total is not None:
                raise ValueError("硬性淘汰参考不能评分")
            continue
        if not item.input_paths or set(item.dimensions) != set(scale["weights"]):
            raise ValueError("参考评分需要完整维度及本次输入依据")
        if any(score < 0 or score > 4 for score in item.dimensions.values()):
            raise ValueError("参考得分必须为 0—4 整数")
        total = sum(item.dimensions[key] / 4 * weight for key, weight in scale["weights"].items())
        if item.total is not None and not isclose(total, item.total, abs_tol=0.001, rel_tol=0):
            raise ValueError("参考总分与锁定权重不一致")
        item.total = total
        eligible.append(item)
    if reference.status != "selected":
        return result
    if result.strategy.status != "selected" or not eligible:
        raise ValueError("策略及合格参考均确定后才能选择参考")
    winner = min(
        eligible, key=lambda item: (-item.total, *(-item.dimensions[k] for k in scale["tie_break"]), item.candidate_id)
    )
    if reference.selected_asset_id != winner.candidate_id:
        raise ValueError("应选择最高分参考并遵守同分规则")
    selected = pool[reference.selected_asset_id]
    if candidates["industry_slug"] == "decoration" and (
        selected["reference_card"].get("content_type_code") != result.strategy.direction_code
    ):
        raise ValueError("爆款参考创作类型必须与所选创作类型一致")
    if reference.source_hash != selected["source_hash"]:
        raise ValueError("参考原文版本不一致")
    slots = {slot["name"]: slot for slot in selected["reference_card"]["required_slots"]}
    required = {name for name, slot in slots.items() if slot["required"]}
    if not required.issubset(reference.slot_mapping) or not set(reference.slot_mapping).issubset(slots):
        raise ValueError("必要事实槽位未完整映射或提交了不存在的槽位")
    for paths in reference.slot_mapping.values():
        if not paths:
            raise ValueError("事实槽位映射不能为空")
        for path in paths:
            validate_fact_path(inputs, path)
    return result


def validate_fact_path(inputs, path):
    if not path.startswith(("content_brief.", "evidence_bundle.")):
        raise ValueError("事实只能引用本次简报或业务证据，不能引用爆款原文")
    value = resolve_input_path(inputs, path)
    parts = path.split(".")
    if len(parts) >= 3 and parts[:2] == ["evidence_bundle", "items"] and parts[2].isdigit():
        item = inputs["evidence_bundle"]["items"][int(parts[2])]
        if (
            item.get("evidence_type") == "style_reference"
            or item.get("type") == "style_reference"
            or (item.get("metadata") or {}).get("material_type") == "viral_example"
        ):
            raise ValueError("爆款结构参考不能充当事实依据")
    return value


class StrategySnapshotV2(StrategyContract):
    schema_version: Literal[2] = 2
    industry_slug: str
    strategy_mode: Literal["direction_scoped", "scored"]
    content_direction: str | None
    direction_blueprint: dict[str, Any] | None = None
    creation_methods: list[str] = Field(min_length=1)
    creation_method_definitions: list[dict[str, Any]] = Field(min_length=1)
    title_formula: dict[str, Any] = Field(min_length=1)
    body_formula: dict[str, Any] = Field(min_length=1)
    rule_version_id: str
    policy_hash: str
    decision: JointStrategyDecisionV1
    reference_snapshot: dict[str, Any] | None
    snapshot_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def verify_hash(self):
        payload = self.model_dump(mode="json", exclude={"snapshot_hash"})
        if payload.get("direction_blueprint") is None:
            payload.pop("direction_blueprint")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if self.snapshot_hash != hashlib.sha256(canonical.encode()).hexdigest():
            raise ValueError("新策略快照哈希不一致")
        return self
