"""V4 爆款仿写的模块规则快照、条件装配和视觉意图。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from yuxi.content.rules import brief_variable_map

MODULAR_WORKFLOW_ID = "content-workflow-blueprint-first-v4"
EXPRESSION_GUIDANCE_WORKFLOW_ID = "content-workflow-blueprint-first-v5"
DETERMINISTIC_PLAN_WORKFLOW_ID = "content-workflow-deterministic-plan-v1"
STANDARDIZED_FACTORY_WORKFLOW_V1_ID = "content-workflow-standardized-factory-v1"
STANDARDIZED_FACTORY_WORKFLOW_V2_ID = "content-workflow-standardized-factory-v2"
STANDARDIZED_FACTORY_WORKFLOW_V3_ID = "content-workflow-standardized-factory-v3"
STANDARDIZED_FACTORY_WORKFLOW_ID = "content-workflow-standardized-factory-v4"
SINGLE_BLUEPRINT_WORKFLOW_ID = "content-workflow-single-blueprint-v4"
SINGLE_BLUEPRINT_WORKFLOW_IDS = frozenset(
    {
        "content-workflow-single-blueprint-v1",
        "content-workflow-single-blueprint-v2",
        "content-workflow-single-blueprint-v3",
        SINGLE_BLUEPRINT_WORKFLOW_ID,
    }
)
MODULAR_WORKFLOW_IDS = frozenset(
    {
        *SINGLE_BLUEPRINT_WORKFLOW_IDS,
        MODULAR_WORKFLOW_ID,
        EXPRESSION_GUIDANCE_WORKFLOW_ID,
        DETERMINISTIC_PLAN_WORKFLOW_ID,
        STANDARDIZED_FACTORY_WORKFLOW_V1_ID,
        STANDARDIZED_FACTORY_WORKFLOW_V2_ID,
        STANDARDIZED_FACTORY_WORKFLOW_V3_ID,
        STANDARDIZED_FACTORY_WORKFLOW_ID,
    }
)
MODULAR_RULE_BUNDLE_VERSION = "viral-modular-v1"

GENERATION_SKILLS = (
    "viral-author-core",
    "viral-title-author",
    "viral-body-author",
    "viral-persona-author",
    "viral-natural-expression",
    "viral-layout-expression",
    "viral-platform-expression",
    "viral-price-author",
    "viral-topic-author",
)
BASE_GENERATION_SKILLS = tuple(slug for slug in GENERATION_SKILLS if slug != "viral-price-author")
REVIEW_SKILL = "viral-modular-reviewer"
COVER_SKILL = "viral-cover-matcher"

_ALWAYS_REQUIRED_REVIEW_CODES = (
    "EMOJI_COVERAGE",
    "EMOJI_APPROPRIATENESS",
    "EMOJI_RESTRICTIONS",
)
_PERSONA_REVIEW_CODES = (
    "PERSONA_OPENING",
    "PERSONA_CLOSING",
    "PERSONA_GROUNDING",
)
_COMPOSITION_REVIEW_CODES = ("CREATION_TYPE_ALIGNMENT", "COMPOSITION_ALIGNMENT")
_MODULAR_REVIEW_CODES = (
    "TITLE_ALIGNMENT",
    "BODY_VALUE",
    "NATURAL_EXPRESSION",
    "LAYOUT_READABILITY",
    "PLATFORM_CTA",
    "TOPIC_ALIGNMENT",
)

_SKILL_ROOT = Path(__file__).resolve().parents[2] / "agents" / "skills" / "buildin"
_RULE_SKILLS = (*GENERATION_SKILLS, REVIEW_SKILL, COVER_SKILL)
_BASE_TOPIC_CANDIDATES = (
    "装修",
    "装修避坑",
    "装修经验",
    "装修知识",
    "装修报价",
    "装修预算",
    "装修日记",
    "装修案例",
    "新房装修",
    "旧房改造",
    "毛坯房装修",
    "局部改造",
    "工长",
    "施工现场",
    "装修施工",
    "装修工艺",
    "水电改造",
    "泥瓦施工",
    "木工施工",
    "油漆施工",
    "装修设计",
    "装修效果",
    "装修清单",
    "装修干货",
)


def _load_rule_document(slug: str) -> dict[str, Any]:
    path = _SKILL_ROOT / slug / "references" / "rules.yaml"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"模块规则不可用: {slug}") from exc
    if value.get("module_id") != slug or not value.get("version"):
        raise RuntimeError(f"模块规则标识无效: {slug}")
    return value


def _skill_content_hash(slug: str) -> str:
    directory = _SKILL_ROOT / slug
    hasher = hashlib.sha256()
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        hasher.update(path.relative_to(directory).as_posix().encode())
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def _topic_candidates(content_brief: dict[str, Any]) -> list[str]:
    candidates = list(_BASE_TOPIC_CANDIDATES)
    variables = brief_variable_map(content_brief)
    city = ""
    for key in ("city", "location", "region", "serviceCity"):
        value = str(variables.get(key) or "").strip()
        if value and len(value) <= 12:
            city = value
            break
    if not city:
        haystack = " ".join(str(value) for value in variables.values() if isinstance(value, str))
        matched = re.search(r'["\']serviceCity["\']\s*:\s*["\']([^"\']{2,12})["\']', haystack)
        city = matched.group(1).strip() if matched else ""
    if city:
        short_city = city.removesuffix("市")
        candidates.extend((f"{short_city}装修", f"{short_city}工长", f"{short_city}装修报价"))
    for key in ("project", "business", "house_type", "style", "audience"):
        value = str(variables.get(key) or "").strip()
        if value and len(value) <= 16:
            candidates.append(value)
    return list(dict.fromkeys(candidates))


def build_modular_rule_bundle(content_brief: dict[str, Any], *, single_blueprint: bool = False) -> dict[str, Any]:
    """从各 Skill 的规则文件编译紧凑快照，供生成、审核和硬校验共同读取。"""

    modules: list[dict[str, Any]] = []
    runtime_rules: dict[str, Any] = {}
    active_rule_ids: list[str] = []
    for slug in _RULE_SKILLS:
        document = _load_rule_document(slug)
        canonical = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        modules.append(
            {
                "slug": slug,
                "version": document["version"],
                "rules_hash": hashlib.sha256(canonical.encode()).hexdigest(),
                "content_hash": _skill_content_hash(slug),
            }
        )
        runtime_rules[slug] = document.get("runtime_rules") or {}
        active_rule_ids.extend(str(item) for item in document.get("rule_ids") or [])
    snapshot = {
        "schema_version": 1,
        "bundle_version": MODULAR_RULE_BUNDLE_VERSION,
        "modules": modules,
        "active_rule_ids": active_rule_ids,
        "runtime_rules": runtime_rules,
        "topic_candidates": _topic_candidates(content_brief),
    }
    if single_blueprint:
        snapshot["single_blueprint"] = json.loads(
            (_SKILL_ROOT / "single-blueprint-author/references/policy.json").read_text()
        )
        snapshot["bundle_version"] = "single-blueprint-v1"
        snapshot["modules"] = [
            {
                "slug": slug,
                "version": snapshot["single_blueprint"]["version"],
                "content_hash": _skill_content_hash(slug),
                "instructions": (_SKILL_ROOT / slug / "SKILL.md").read_text().split("---", 2)[2].strip(),
            }
            for slug in ("single-blueprint-author", "single-blueprint-reviewer")
        ]
        snapshot["runtime_rules"] = {
            key: runtime_rules[key] for key in ("viral-author-core", "viral-platform-expression", "viral-topic-author")
        }
        snapshot["runtime_rules"]["viral-layout-expression"] = snapshot["single_blueprint"].get("layout", {})
        snapshot["active_rule_ids"] = []
        if snapshot["single_blueprint"].get("writing_mode") in {"direct_reference", "raw_reference_text"}:
            snapshot["runtime_rules"]["viral-topic-author"] = {"must_use_candidate_pool": False}
            snapshot["topic_candidates"] = []
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**snapshot, "bundle_hash": hashlib.sha256(canonical.encode()).hexdigest()}


def runtime_policy(runtime_snapshot: dict[str, Any], module_id: str, policy_name: str) -> dict[str, Any]:
    """读取规则包中的版本化策略；业务代码不按工作流版本分支。"""

    bundle = runtime_snapshot.get("content_rule_bundle") or {}
    module_rules = (bundle.get("runtime_rules") or {}).get(module_id) or {}
    policy = module_rules.get(policy_name) or {}
    return dict(policy) if isinstance(policy, dict) else {}


def has_price_context(payload: dict[str, Any]) -> bool:
    evidence = (payload.get("evidence_bundle") or {}).get("items") or []
    formula_code = ((payload.get("strategy_snapshot") or {}).get("body_formula") or {}).get("code") or ""
    form_values = (payload.get("content_brief") or {}).get("form_values") or {}
    return (
        formula_code in {f"FRB{index:02d}" for index in range(6, 10)}
        or bool(form_values.get("quote_type"))
        or any(
            (item.get("metadata") or {}).get("price_basis")
            or set(item.get("variable_codes") or []) & {"price", "budget", "cost", "discount", "fee"}
            for item in evidence
        )
    )


def modular_review_codes(payload: dict[str, Any]) -> tuple[str, ...]:
    return (
        *_MODULAR_REVIEW_CODES,
        *(("PRICE_SCOPE_ALIGNMENT",) if has_price_context(payload) else ()),
    )


def requires_persona_review(payload: dict[str, Any]) -> bool:
    production_pack = payload.get("production_pack") or {}
    if not production_pack:
        return True
    variable_codes = {
        str(requirement.get("variable_code") or "")
        for requirement in (production_pack.get("material_manifest") or {}).get("requirements") or []
    }
    return bool(variable_codes & {"persona_fact", "advantage", "advantages"})


def required_review_codes(payload: dict[str, Any]) -> tuple[str, ...]:
    """返回当前审核契约要求模型逐项提交的完整 code 清单。"""

    bundle = (
        (payload.get("production_pack") or {}).get("content_rule_bundle")
        or (payload.get("runtime_config_snapshot") or {}).get("content_rule_bundle")
        or {}
    )
    if codes := (bundle.get("single_blueprint") or {}).get("review_codes"):
        return tuple(codes)
    strategy = payload.get("strategy_snapshot") or {}
    expression_policy = payload.get("expression_policy") or (
        (payload.get("production_pack") or {}).get("expression_policy") or {}
    )
    emoji_review_codes = _ALWAYS_REQUIRED_REVIEW_CODES if expression_policy.get("emoji_allowed", True) else ()
    return (
        *emoji_review_codes,
        *(_PERSONA_REVIEW_CODES if requires_persona_review(payload) else ()),
        *(_COMPOSITION_REVIEW_CODES if strategy.get("direction_blueprint") else ()),
        *modular_review_codes(payload),
    )


def _blocked_codes(payload: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for key in ("validation_report", "review_report"):
        report = payload.get(key) or {}
        if report.get("status") != "blocked":
            continue
        for item in report.get("checks") or []:
            if item.get("level") == "error" or item.get("status") == "blocked":
                result.add(str(item.get("code") or "").upper())
    return result


def select_modular_generation_skills(
    declared_skills: tuple[str, ...],
    payload: dict[str, Any],
) -> tuple[str, ...]:
    """首次生成加载基础模块；回修只加载阻断问题对应模块。"""

    if "viral-author-core" not in declared_skills:
        return declared_skills
    blocked = _blocked_codes(payload)
    if not blocked:
        active = list(BASE_GENERATION_SKILLS)
        if has_price_context(payload):
            active.insert(-1, "viral-price-author")
        return tuple(active)

    active = ["viral-author-core"]
    routing = {
        "viral-title-author": ("TITLE_",),
        "viral-body-author": (
            "BODY_",
            "CONTENT_STRUCTURE",
            "CREATION_TYPE",
            "COMPOSITION_",
            "KNOWLEDGE_EVIDENCE",
            "FACT_",
            "NUMERIC_",
        ),
        "viral-persona-author": ("PERSONA_",),
        "viral-natural-expression": ("MECHANICAL_", "NATURAL_", "PERSONA_TONE", "PERSONA_STYLE"),
        "viral-layout-expression": ("LAYOUT_", "EMOJI_", "BODY_LENGTH"),
        "viral-platform-expression": (
            "CTA_",
            "PLATFORM_",
            "CONTENT_FORBIDDEN",
            "CONTENT_HIGH_RISK",
            "COMPLIANCE_",
            "UNSAFE_AUTO_REPLACEMENT",
        ),
        "viral-price-author": ("PRICE_", "KNOWLEDGE_PRICE", "FACT_INCONSISTENT"),
        "viral-topic-author": ("TOPIC_", "CHANNEL_TOPIC"),
    }
    for slug, prefixes in routing.items():
        if any(code.startswith(prefix) for code in blocked for prefix in prefixes):
            active.append(slug)
    production_pack = payload.get("production_pack")
    if production_pack:
        expression_policy = production_pack.get("expression_policy") or {}
        if blocked & {"PERSONA_OPENING", "PERSONA_CLOSING", "PERSONA_GROUNDING"}:
            active.extend(("viral-natural-expression", "viral-layout-expression"))
        elif "NATURAL_EXPRESSION" in blocked and expression_policy.get("emoji_allowed", True):
            active.append("viral-layout-expression")
    if len(active) == 1:
        active.extend(BASE_GENERATION_SKILLS[1:])
        if has_price_context(payload):
            active.insert(-1, "viral-price-author")
    return tuple(dict.fromkeys(active))


def derive_visual_intent(payload: dict[str, Any]) -> str:
    if has_price_context(payload):
        return "whole_house_quote"
    variables = brief_variable_map(payload.get("content_brief") or {})
    text = " ".join(str(value) for value in variables.values() if value not in (None, "", [], {}))
    if any(term in text for term in ("局改", "局部", "厨房", "卫生间", "阳台")):
        return "partial_renovation"
    if any(term in text for term in ("工艺", "施工", "水电", "泥瓦", "木工", "油漆")):
        return "craft_detail"
    return "case_result"


__all__ = [
    "BASE_GENERATION_SKILLS",
    "COVER_SKILL",
    "EXPRESSION_GUIDANCE_WORKFLOW_ID",
    "GENERATION_SKILLS",
    "MODULAR_RULE_BUNDLE_VERSION",
    "MODULAR_WORKFLOW_ID",
    "MODULAR_WORKFLOW_IDS",
    "REVIEW_SKILL",
    "STANDARDIZED_FACTORY_WORKFLOW_ID",
    "STANDARDIZED_FACTORY_WORKFLOW_V1_ID",
    "STANDARDIZED_FACTORY_WORKFLOW_V2_ID",
    "STANDARDIZED_FACTORY_WORKFLOW_V3_ID",
    "build_modular_rule_bundle",
    "derive_visual_intent",
    "has_price_context",
    "runtime_policy",
    "select_modular_generation_skills",
]
