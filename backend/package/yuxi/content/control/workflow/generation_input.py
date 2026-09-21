"""正文与审核模型视图：完整审计快照留在服务端。"""

from copy import deepcopy
import re

from yuxi.content.model.contracts.content_nodes import (
    GenerateContentPromptV1,
    PlanVisualsInputV1,
    SemanticReviewPromptV1,
    StandardizedGenerateContentPromptV1,
    VisualReviewInputV1,
)
from yuxi.content.model.locked_blocks import extract_locked_quote_block, quote_body_limits, render_semicolon_lines
from yuxi.content.model.materials import FrozenProductionPackV1, build_formula_lexicon_constraints
from yuxi.content.v3.modular_rules import (
    BASE_GENERATION_SKILLS,
    COVER_SKILL,
    GENERATION_SKILLS,
    REVIEW_SKILL,
    has_price_context,
    required_review_codes,
    select_modular_generation_skills,
)
from yuxi.content.v3.title_formula_slots import enrich_decoration_title_formula


def _project_frozen_writing_context(projected: dict) -> None:
    """仅去除已校验输入中的审计副本，保留每条可写事实和参考蓝图。"""

    strategy = projected["strategy_snapshot"]
    strategy.pop("decision", None)
    strategy.pop("snapshot_hash", None)
    strategy.pop("planner_version", None)
    strategy.pop("input_snapshot_hash", None)
    # 参考蓝图与选择依据已经冻结在 style_reference Evidence 中。
    strategy.pop("reference_snapshot", None)
    body_formula = strategy["body_formula"]
    # 锁定快照为了审计在三个位置保存同一方向蓝图；模型只需顶层一份。
    if strategy.get("direction_blueprint") is not None:
        if body_formula.get("composition_blueprint") == strategy["direction_blueprint"]:
            body_formula.pop("composition_blueprint", None)
        if (
            body_formula.get("body_calling")
            and body_formula["body_calling"].get("composition_blueprint") == strategy["direction_blueprint"]
        ):
            body_formula["body_calling"].pop("composition_blueprint", None)
    body_formula.pop("body_calling_source", None)
    brief = projected["content_brief"]
    brief.pop("visual_material", None)
    for key in list(brief):
        if key.endswith("_version_id"):
            del brief[key]
    for section in ("business_variables", "form_values"):
        values = brief.get(section) or {}
        for key in list(values):
            if key == "user_request":
                continue
            if key.endswith("_version_id") or key in {"attachments", "visual_material"}:
                del values[key]
            elif any(
                item.get("source_type") == "manual_input"
                and item.get("source_id", "").startswith("field_")
                and item.get("verified_status") == "user_confirmed"
                and key in item.get("variable_codes", [])
                and type(item.get("value")) is type(values[key])
                and item.get("value") == values[key]
                for item in projected["evidence_bundle"].get("items", [])
            ):
                del values[key]
    for item in projected["evidence_bundle"].get("items", []):
        for field in ("source_hash", "source_version", "created_at"):
            item.pop(field, None)
        metadata = item.get("metadata") or {}
        if metadata.get("material_type") == "viral_example" and metadata.get("selected_reference"):
            basis = metadata.get("selection_basis") or {}
            if basis:
                metadata["selection_basis"] = {
                    key: basis[key]
                    for key in ("input_variable_paths", "matched_dimensions", "structure_fillability")
                    if key in basis
                }


def _project_rule_bundle(bundle: dict, active_skills: tuple[str, ...]) -> dict:
    active = set(active_skills)
    return {
        "schema_version": bundle.get("schema_version"),
        "bundle_version": bundle.get("bundle_version"),
        "bundle_hash": bundle.get("bundle_hash"),
        "active_modules": list(active_skills),
        "active_rule_ids": [
            rule_id for rule_id in bundle.get("active_rule_ids") or [] if str(rule_id).split(".v", 1)[0] in active
        ],
        "runtime_rules": {slug: value for slug, value in (bundle.get("runtime_rules") or {}).items() if slug in active},
        **({"topic_candidates": bundle.get("topic_candidates") or []} if "viral-topic-author" in active else {}),
    }


def _redact_locked_quote_context(projected: dict) -> None:
    brief = projected.get("content_brief") or {}
    brief.pop("material_confirmations", None)
    runtime = projected.get("runtime_config_snapshot") or {}
    runtime.pop("trusted_external_material_snapshot", None)
    runtime.pop("dangjia_request_fingerprint", None)
    for item in (projected.get("evidence_bundle") or {}).get("items") or []:
        if "quote_block" not in (item.get("variable_codes") or []):
            continue
        item["value"] = {
            "locked": True,
            "block_type": "verbatim_quote",
            "instruction": "报价原文由程序插入，模型不得输出报价项目或金额",
        }
        item.pop("source_hash", None)
        item.pop("source_version", None)
        item["metadata"] = {"material_type": "locked_quote_block"}


def _redact_locked_quote_from_review(review_report: dict | None, production_pack: dict) -> dict | None:
    if review_report is None:
        return None
    locked_quote = extract_locked_quote_block(production_pack)
    if locked_quote is None:
        return deepcopy(review_report)
    replacements = (
        locked_quote["rendered_content"],
        locked_quote["original_content"],
    )
    marker = "[程序锁定报价块]"

    def redact(value):
        if isinstance(value, str):
            for original in replacements:
                value = value.replace(original, marker)
            return value
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        return value

    return redact(deepcopy(review_report))


def project_locked_quote_safe_input(payload: dict) -> dict | None:
    runtime = payload.get("runtime_config_snapshot") or {}
    has_snapshot = "trusted_external_material_snapshot" in runtime
    has_quote_evidence = any(
        "quote_block" in (item.get("variable_codes") or [])
        for item in (payload.get("evidence_bundle") or {}).get("items") or []
    )
    if not has_snapshot and not has_quote_evidence:
        return None
    projected = deepcopy(payload)
    _redact_locked_quote_context(projected)
    return projected


def _redact_composed_locked_quote(projected: dict) -> None:
    quote_items = [
        item
        for item in (projected.get("evidence_bundle") or {}).get("items") or []
        if "quote_block" in (item.get("variable_codes") or [])
    ]
    if not quote_items:
        return
    if len(quote_items) != 1:
        raise ValueError("模型输入必须且只能包含一个锁定报价块")
    value = quote_items[0].get("value") or {}
    original = value.get("original_content")
    if not isinstance(original, str) or not original:
        raise ValueError("锁定报价块缺少可脱敏的原文")
    rendered = render_semicolon_lines(original)
    draft = projected.get("content_draft") or {}
    body = str(draft.get("body") or "")
    marker = "[锁定报价块已由程序插入；原文不提供给模型]"
    if rendered in body:
        draft["body"] = body.replace(rendered, marker, 1)
    elif original in body:
        draft["body"] = body.replace(original, marker, 1)
    else:
        raise ValueError("合成后的锁定报价块无法在模型正文输入中定位")


def _project_standardized_production_pack(production_pack: dict) -> dict:
    projected = deepcopy(production_pack)
    strategy = projected.get("strategy_snapshot") or {}
    if strategy.get("industry_slug") == "decoration" or str(
        (strategy.get("title_formula") or {}).get("code", "")
    ).startswith("FRT"):
        strategy["title_formula"] = enrich_decoration_title_formula(strategy.get("title_formula") or {})
    locked_quote = extract_locked_quote_block(production_pack)
    locked_blocks = []
    quality_report = production_pack.get("material_quality_report") or {}
    bindings = quality_report.get("bindings")
    if isinstance(bindings, list):
        bound_material_ids = {
            str(material_id) for binding in bindings for material_id in binding.get("material_ids") or []
        }
        projected["materials"] = [
            material
            for material in projected.get("materials") or []
            if material.get("material_type")
            in {"viral_reference", "business_rule", "compliance_rule", "style_reference"}
            or str(material.get("id") or "") in bound_material_ids
        ]
    title_price_label = next(
        (
            str((material.get("payload") or {}).get("value") or "")
            for material in production_pack.get("materials") or []
            if "title_price_label" in (material.get("variable_codes") or [])
        ),
        "已确认价格",
    )
    for material in projected.get("materials") or []:
        source = material.get("source") or {}
        material["source"] = {"source_type": source.get("source_type")}
        governance = material.get("governance") or {}
        material["governance"] = {
            key: governance.get(key) for key in ("review_status", "verified_status", "risk_level", "allowed_usage")
        }
        if "quote_block" in (material.get("variable_codes") or []):
            if locked_quote is None:
                raise ValueError("quote_block 物料缺少可校验的锁定文本")
            limits = quote_body_limits(production_pack, locked_quote["rendered_content"])
            if limits["creative_body_max_chars"] < limits["creative_body_min_chars"]:
                raise ValueError("锁定报价块过长，渠道正文已没有足够的创作空间")
            material["payload"] = {
                "value": {
                    "locked": True,
                    "render_policy": locked_quote["render_policy"],
                    "insertion_policy": locked_quote["insertion_policy"],
                    "char_count": len(locked_quote["rendered_content"]),
                    **limits,
                }
            }
            locked_blocks.append(
                {
                    "block_id": "quote_block",
                    "block_type": "verbatim_quote",
                    "label": title_price_label,
                    "insertion_policy": locked_quote["insertion_policy"],
                    "render_policy": locked_quote["render_policy"],
                    "char_count": len(locked_quote["rendered_content"]),
                    **{key: limits[key] for key in ("creative_body_min_chars", "creative_body_max_chars")},
                    "instruction": "只创作报价块以外的正文；不要输出、猜测、概括或改写任何报价项目与金额。",
                }
            )
    for key in (
        "id",
        "task_id",
        "evidence_bundle_id",
        "evidence_bundle_version",
        "evidence_bundle_hash",
        "formula_lexicon_bundle_hash",
        "production_pack_hash",
        "frozen_at",
        "compliance_policy_version_ids",
    ):
        projected.pop(key, None)
    projected["locked_blocks"] = locked_blocks
    return projected


def project_generation_input(payload: dict, *, active_skills: tuple[str, ...] | None = None) -> dict:
    # 调用方必须先完成 GenerateContentInputV1 校验（包括冻结策略 hash）。
    if payload["runtime_config_snapshot"].get("creation_mode") != "viral_rewrite":
        raise ValueError("内容生成只支持爆款仿写")
    if payload.get("production_pack") is not None:
        production_pack = FrozenProductionPackV1.model_validate(payload["production_pack"]).model_dump(mode="json")
        lexicon_constraints = build_formula_lexicon_constraints(
            materials=production_pack.get("materials") or [],
            formula_lexicon_bundle=production_pack.get("formula_lexicon_bundle") or {},
            material_quality_report=production_pack.get("material_quality_report") or None,
        )
        repair_constraints = None
        validation_report = payload.get("validation_report") or {}
        projected_review_report = _redact_locked_quote_from_review(
            payload.get("review_report"),
            production_pack,
        )
        review_report = projected_review_report or {}
        blocked_review_codes = {
            str(item.get("code") or "") for item in review_report.get("checks") or [] if item.get("status") == "blocked"
        }
        repairable_codes = {
            "PERSONA_OPENING",
            "PERSONA_CLOSING",
            "EMOJI_COVERAGE",
            "EMOJI_APPROPRIATENESS",
            "EMOJI_RESTRICTIONS",
        }
        if (
            payload.get("content_draft")
            and payload.get("selected_title")
            and payload.get("content_outline")
            and validation_report.get("status") in {"passed", "warning"}
            and blocked_review_codes
            and blocked_review_codes <= repairable_codes
        ):
            body = str(payload["content_draft"].get("body") or "")
            paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
            persona_repair = bool(blocked_review_codes & {"PERSONA_OPENING", "PERSONA_CLOSING"})
            repair_constraints = {
                "mode": "persona_edges_only" if persona_repair else "emoji_only",
                "original_body": body,
                "immutable_title": payload["selected_title"],
                "immutable_outline": payload["content_outline"],
                "immutable_topics": payload["content_draft"].get("topics") or [],
                "immutable_middle_paragraphs": paragraphs[1:-1] if persona_repair else [],
                "instruction": (
                    "只重写首段和末段；中间段落必须按数组逐项原样复制、顺序和分段不变，标题、大纲、话题保持原样。"
                    if persona_repair
                    else "只调整 Emoji 及其相邻空格；标题、大纲、正文文字、数字、标点、顺序和话题保持原样。"
                ),
            }
        return StandardizedGenerateContentPromptV1.model_validate(
            {
                "production_pack": _project_standardized_production_pack(production_pack),
                "lexicon_constraints": lexicon_constraints,
                "validation_report": payload.get("validation_report"),
                "review_report": projected_review_report,
                "selected_title": payload.get("selected_title"),
                "content_outline": payload.get("content_outline"),
                "content_draft": payload.get("content_draft"),
                "repair_constraints": repair_constraints,
            }
        ).model_dump(mode="json", exclude_none=True)
    projected = deepcopy(payload)
    _project_frozen_writing_context(projected)
    _redact_locked_quote_context(projected)
    runtime = projected["runtime_config_snapshot"]
    rule_bundle = runtime.get("content_rule_bundle") or {}
    if rule_bundle:
        active_skills = active_skills or select_modular_generation_skills(GENERATION_SKILLS, projected)
    projected["runtime_config_snapshot"] = {
        "creation_mode": "viral_rewrite",
        **({"content_rule_bundle": _project_rule_bundle(rule_bundle, active_skills)} if rule_bundle else {}),
    }
    return GenerateContentPromptV1.model_validate(projected).model_dump(mode="json")


def project_review_input(payload: dict) -> dict:
    """语义审核仍读取全文、全部事实和选中参考，只去除重复审计字段。"""

    projected = deepcopy(payload)
    _project_frozen_writing_context(projected)
    _redact_locked_quote_context(projected)
    runtime = projected.get("runtime_config_snapshot") or {}
    rule_bundle = runtime.get("content_rule_bundle") or {}
    if rule_bundle:
        active_skills = list(BASE_GENERATION_SKILLS)
        if has_price_context(projected):
            active_skills.insert(-1, "viral-price-author")
        active_skills.append(REVIEW_SKILL)
        projected["runtime_config_snapshot"] = {
            "content_rule_bundle": _project_rule_bundle(rule_bundle, tuple(active_skills)),
            "required_review_codes": list(required_review_codes(projected)),
        }
    return SemanticReviewPromptV1.model_validate(projected).model_dump(mode="json", exclude_none=True)


def project_visual_input(
    payload: dict,
    *,
    required_visual_intent: str | None = None,
    required_source_asset_ids: tuple[str, ...] = (),
    allowed_visual_evidence_ids: frozenset[str] = frozenset(),
) -> dict:
    projected = deepcopy(payload)
    _redact_composed_locked_quote(projected)
    _redact_locked_quote_context(projected)
    runtime = projected.get("runtime_config_snapshot") or {}
    rule_bundle = runtime.get("content_rule_bundle") or {}
    if rule_bundle:
        runtime["content_rule_bundle"] = _project_rule_bundle(rule_bundle, (COVER_SKILL,))
    projected["runtime_config_snapshot"] = runtime
    projected["required_visual_intent"] = required_visual_intent or "general"
    projected["required_source_asset_ids"] = list(required_source_asset_ids)
    projected["allowed_visual_evidence_ids"] = sorted(allowed_visual_evidence_ids)
    return PlanVisualsInputV1.model_validate(projected).model_dump(mode="json")


def project_visual_review_input(payload: dict) -> dict:
    projected = deepcopy(payload)
    _redact_composed_locked_quote(projected)
    _redact_locked_quote_context(projected)
    return VisualReviewInputV1.model_validate(projected).model_dump(mode="json")


__all__ = [
    "project_generation_input",
    "project_locked_quote_safe_input",
    "project_review_input",
    "project_visual_input",
    "project_visual_review_input",
]
