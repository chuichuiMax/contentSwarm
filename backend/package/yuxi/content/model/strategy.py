"""行业策略候选与通用契约；业务权重和行业路由来自正式 Skill 资源。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from itertools import product
from pathlib import Path
from typing import Any

from yuxi.content.industry_matrix import resolve_industry_formula


def load_selection_policy() -> dict[str, Any]:
    path = Path(__file__).parents[2] / "agents/skills/buildin/content-strategy-planner/references/selection-policy.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    for scale in policy["scoring"].values():
        weights = scale["weights"]
        if sum(weights.values()) != 100 or any(value <= 0 for value in weights.values()):
            raise ValueError("Skill 评分权重必须为正数且合计为 100")
        if set(scale["tie_break"]) != set(weights) or len(scale["tie_break"]) != len(weights):
            raise ValueError("Skill 同分规则必须完整且不重复")
    if sum(policy["formula_pair_weights"].values()) != 100:
        raise ValueError("Skill 公式配对权重合计必须为 100")
    canonical = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**policy, "policy_hash": hashlib.sha256(canonical.encode()).hexdigest()}


def build_strategy_candidates(
    bundle: dict[str, Any],
    *,
    industry_slug: str,
    direction_code: str | None,
    rule_version_id: str,
    policy: dict[str, Any] | None = None,
    auto_direction: bool = False,
) -> dict[str, Any]:
    """仅装配合法候选，不评价适配度、不选择公式或手法。"""
    if not industry_slug or not rule_version_id:
        raise ValueError("缺少行业或锁定规则版本")
    policy = deepcopy(policy if policy is not None else load_selection_policy())
    mode = policy["industry_modes"].get(industry_slug, policy["default_mode"])
    if mode not in policy["mode_skills"]:
        raise ValueError("行业策略模式未配置 Skill")
    direction = direction_code or None
    if mode == "direction_scoped" and not direction and not auto_direction:
        raise ValueError("请先选择内容方向")
    rules = sorted(
        (
            deepcopy(item)
            for item in bundle.get("combination_rules", [])
            if item.get("enabled", True)
            and item.get("compatibility") != "disabled"
            and (not item.get("industry_scope") or industry_slug in item["industry_scope"])
            and (mode != "direction_scoped" or (auto_direction and not direction)
                 or direction in item.get("content_type_codes", []))
        ),
        key=lambda item: str(item.get("id") or item.get("code")),
    )
    formula_lists = {}
    for section, reference_key in (
        ("title_formulas", "title_formula_candidate_codes"),
        ("content_formulas", "body_formula_candidate_codes"),
    ):
        codes = {code for rule in rules for code in rule.get(reference_key, [])}
        formula_lists[section] = sorted(
            (
                resolve_industry_formula(
                    item,
                    industry_slug=industry_slug,
                    scenario="；".join(
                        rule.get("scenario_description", "")
                        for rule in rules
                        if item["code"] in rule.get(reference_key, [])
                    ),
                )
                for item in bundle.get(section, [])
                if item["code"] in codes
                and item.get("enabled", True)
                and (not item.get("industry_scope") or industry_slug in item["industry_scope"])
            ),
            key=lambda item: item["code"],
        )
    methods = sorted(
        (
            deepcopy(item)
            for item in bundle.get("methods", [])
            if item.get("enabled", True) and (not item.get("industry_scope") or industry_slug in item["industry_scope"])
        ),
        key=lambda item: item["code"],
    )
    titles = {item["code"] for item in formula_lists["title_formulas"]}
    bodies = {item["code"] for item in formula_lists["content_formulas"]}
    if not titles or not bodies or not methods:
        raise ValueError("当前行业或内容方向没有可用的公式/手法候选")
    pairs = set(product(titles, bodies))
    explicit_pairs = []
    forbidden_pairs = set()
    for rule in rules:
        compatibility = rule.get("hard_conditions", {})
        if "allowed_formula_pairs" in compatibility:
            explicit_pairs.append({tuple(pair) for pair in compatibility["allowed_formula_pairs"]})
        forbidden_pairs.update(tuple(pair) for pair in compatibility.get("forbidden_formula_pairs", []))
    if explicit_pairs:
        pairs &= set.union(*explicit_pairs)
    pairs -= forbidden_pairs
    if not pairs:
        raise ValueError("当前候选没有兼容的标题与正文公式配对")
    scoring = deepcopy(policy["scoring"])
    if mode == "direction_scoped":
        del scoring["formula"]
    direction_options = []
    if auto_direction and mode == "direction_scoped":
        for item in bundle.get("content_types", []):
            if not item.get("enabled", True):
                continue
            scoped_rules = [rule for rule in rules if item["code"] in rule.get("content_type_codes", [])]
            scoped_titles = titles & {
                code for rule in scoped_rules for code in rule.get("title_formula_candidate_codes", [])
            }
            scoped_bodies = bodies & {
                code for rule in scoped_rules for code in rule.get("body_formula_candidate_codes", [])
            }
            if not scoped_titles or not scoped_bodies:
                continue
            scoped = (
                build_strategy_candidates(
                    bundle,
                    industry_slug=industry_slug,
                    direction_code=item["code"],
                    rule_version_id=rule_version_id,
                    policy=policy,
                )
                if any(item["code"] in rule.get("content_type_codes", []) for rule in rules)
                else None
            )
            if scoped:
                direction_options.append(
                    {
                        "code": item["code"],
                        "name": item["name"],
                        "description": item.get("description", ""),
                        "title_formula_codes": [entry["code"] for entry in scoped["title_formulas"]],
                        "body_formula_codes": [entry["code"] for entry in scoped["content_formulas"]],
                        "valid_formula_pairs": scoped["valid_formula_pairs"],
                        "direction_blueprint": scoped.get("direction_blueprint"),
                    }
                )
    blueprints = [
        deepcopy(rule.get("source_metadata", {}).get("composition_blueprint"))
        for rule in rules
        if rule.get("source_metadata", {}).get("composition_blueprint")
    ]
    return {
        **({"auto_direction": True, "direction_options": direction_options} if auto_direction else {}),
        "industry_slug": industry_slug,
        "strategy_mode": mode,
        "direction_code": direction,
        "direction_blueprint": blueprints[0] if len(blueprints) == 1 else None,
        "rule_version_id": rule_version_id,
        "policy_version": policy["version"],
        "policy_hash": policy["policy_hash"],
        "reference_candidate_limit": policy["reference_candidate_limit"],
        "selection_skill": policy["mode_skills"][mode],
        "methods": methods,
        **formula_lists,
        "valid_formula_pairs": [list(pair) for pair in sorted(pairs)],
        "source_rules": rules,
        "scoring": scoring,
        "score_anchors": policy["score_anchors"],
        "formula_pair_weights": policy["formula_pair_weights"] if mode == "scored" else None,
    }
