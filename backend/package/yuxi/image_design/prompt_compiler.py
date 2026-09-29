from __future__ import annotations

import re
from typing import Any

from .schemas import (
    CrossSpaceRefinementCreate,
    PromptPlan,
    RoomAdaptRefinementCreate,
    StyleTransferRefinementCreate,
)
from .workflow_profiles import (
    ADDONS,
    LAYOUTS,
    MP_SHARED_ADDONS,
    MP_SHARED_LAYOUTS,
    SPACE_LABELS,
    STYLE_PROFILES,
    WORKFLOW_PROFILES,
)

RefinementPayload = StyleTransferRefinementCreate | RoomAdaptRefinementCreate | CrossSpaceRefinementCreate

ASPECT_COMPOSITION = {
    "3:4": "采用竖向 3:4 构图，完整呈现空间纵深，主体不裁切",
    "4:3": "采用横向 4:3 构图，强调空间横向关系与开阔感，主体不裁切",
    "1:1": "采用方形 1:1 构图，画面重心稳定，主体不裁切",
}
STRUCTURE_CONFLICT_PATTERNS = ("拆除墙", "改变墙", "移动门", "移动窗", "新增窗", "封闭窗", "改变层高", "改变户型")
NEGATION_MARKERS = ("不", "不得", "不要", "禁止", "避免", "不可")
CROSS_SPACE_COPY_DIRECTIVES = ("沿用", "保留", "保持", "照搬", "复制")
CROSS_SPACE_STRUCTURE_TERMS = ("布局", "空间类型", "房间类型", "家具位置", "家具", "床", "餐桌", "沙发", "岛台")


def _analysis(analyses: dict[str, Any], role: str) -> dict[str, Any]:
    value = analyses.get(role) or {}
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if isinstance(value, str) and value.strip()))


def _contains_positive_phrase(text: str, phrase: str) -> bool:
    start = text.find(phrase)
    while start >= 0:
        prefix = text[max(0, start - 4) : start]
        if not any(prefix.endswith(marker) for marker in NEGATION_MARKERS):
            return True
        start = text.find(phrase, start + 1)
    return False


def _clause_conflict(clause: str, workflow: str) -> str | None:
    for pattern in STRUCTURE_CONFLICT_PATTERNS:
        if _contains_positive_phrase(clause, pattern):
            return pattern
    if workflow == "cross_space":
        copies_structure = any(
            _contains_positive_phrase(clause, directive) for directive in CROSS_SPACE_COPY_DIRECTIVES
        ) and any(term in clause for term in CROSS_SPACE_STRUCTURE_TERMS)
        if copies_structure:
            return "沿用参考图空间、家具或布局"
    return None


def _resolve_user_intent(text: str, workflow: str) -> tuple[str, list[str]]:
    clauses = [item.strip() for item in re.split(r"[。；;\n]+", text) if item.strip()]
    kept: list[str] = []
    conflicts: list[str] = []
    for clause in clauses:
        conflict = _clause_conflict(clause, workflow)
        if conflict:
            conflicts.append(f"已忽略与工作流硬约束冲突的描述：{clause}")
        else:
            kept.append(clause)
    return "；".join(kept) or "按当前工作流和已选条件生成", conflicts


def apply_edited_prompt(plan: PromptPlan, text: str) -> PromptPlan:
    tokens = re.split(r"([。；;\n]+)", text.strip())
    kept: list[str] = []
    conflicts: list[str] = []
    for index in range(0, len(tokens), 2):
        clause = tokens[index]
        delimiter = tokens[index + 1] if index + 1 < len(tokens) else ""
        if not clause.strip():
            kept.append(clause + delimiter)
            continue
        conflict = _clause_conflict(clause.strip(), plan.workflow)
        if conflict:
            conflicts.append(f"已忽略与工作流硬约束冲突的描述：{clause.strip()}")
            continue
        kept.append(clause + delimiter)
    cleaned = "".join(kept).strip()
    if not cleaned:
        raise ValueError("修改后的优化结果不能只包含与工作流冲突的描述")
    updated = plan.model_copy(deep=True)
    updated.edited_prompt = cleaned
    updated.conflicts = _unique([*plan.conflicts, *conflicts])
    return updated


def build_prompt_plan(payload: RefinementPayload, analyses: dict[str, Any]) -> PromptPlan:
    profile = WORKFLOW_PROFILES[payload.workflow]
    intent_source = payload.edited_prompt or payload.user_prompt
    user_intent, conflicts = _resolve_user_intent(intent_source, payload.workflow)
    warnings: list[str] = []

    image_roles: list[dict[str, str]] = []
    preserve: list[str] = []
    materials: list[str] = []
    palette: list[str] = []
    lighting: list[str] = []
    camera: list[str] = []
    composition = ["真实室内建筑摄影", "自然比例与透视", "空间主体完整清晰"]
    style_profile: dict[str, object]
    target_space = None
    layout = None
    addon_profiles: list[dict[str, str]] = []

    if isinstance(payload, StyleTransferRefinementCreate):
        structure = _analysis(analyses, "structure_source")
        image_roles.append(
            {"order": "1", "role": "structure_source", "material_id": payload.source_material_id, "label": "原房实拍图"}
        )
        preserve = _unique(list(structure.get("preserve") or []) + list(structure.get("structural_features") or []))
        camera = _unique(list(structure.get("camera") or []))
        if payload.use_prompt_as_style:
            style_profile = {"id": "user_prompt", "label": "使用补充描述定义风格", "traits": [user_intent]}
        else:
            selected = STYLE_PROFILES.get(payload.style_label or "")
            if selected is None:
                raise ValueError("未知的换装风格")
            style_profile = {"id": payload.style_label, "label": payload.style_label, **selected}
            materials = list(selected["materials"])
            palette = list(selected["palette"])
    elif isinstance(payload, RoomAdaptRefinementCreate):
        style = _analysis(analyses, "style_reference")
        structure = _analysis(analyses, "structure_source")
        image_roles.extend(
            [
                {
                    "order": "1",
                    "role": "style_reference",
                    "material_id": payload.style_reference_material_id,
                    "label": "设计风格参考图",
                },
                {
                    "order": "2",
                    "role": "structure_source",
                    "material_id": payload.raw_structure_material_id,
                    "label": "毛坯实拍图",
                },
            ]
        )
        preserve = _unique(list(structure.get("preserve") or []) + list(structure.get("structural_features") or []))
        style_profile = {
            "id": "visual_reference",
            "label": style.get("style") or "参考图设计语言",
            "traits": list(style.get("transferable_features") or []),
        }
        materials = _unique(list(style.get("materials") or []))
        palette = _unique(list(style.get("palette") or []))
        lighting = _unique(list(style.get("lighting") or []))
        camera = _unique(list(structure.get("camera") or []))
    else:
        style = _analysis(analyses, "cross_space_style")
        image_roles.append(
            {
                "order": "1",
                "role": "cross_space_style",
                "material_id": payload.style_reference_material_id,
                "label": "跨空间风格参考图",
            }
        )
        style_profile = {
            "id": "visual_reference",
            "label": style.get("style") or "参考图可迁移风格",
            "traits": list(style.get("transferable_features") or []),
        }
        materials = _unique(list(style.get("materials") or []))
        palette = _unique(list(style.get("palette") or []))
        lighting = _unique(list(style.get("lighting") or []))
        space_label = SPACE_LABELS.get(payload.target_space)
        layout_label = LAYOUTS.get(payload.target_space, {}).get(payload.layout) or MP_SHARED_LAYOUTS.get(
            payload.layout
        )
        if not space_label or not layout_label:
            raise ValueError("目标空间或布局不在当前工作流配置中")
        addon_choices = {**ADDONS[payload.target_space], **MP_SHARED_ADDONS}
        unknown_addons = [item for item in payload.addons if item not in addon_choices]
        if unknown_addons:
            raise ValueError(f"目标空间不支持附加元素：{', '.join(unknown_addons)}")
        target_space = {"id": payload.target_space, "label": space_label}
        layout = {"id": payload.layout, "label": layout_label}
        addon_profiles = [{"id": item, "label": addon_choices[item]} for item in payload.addons]

    if not preserve and payload.workflow != "cross_space":
        warnings.append("视觉分析未发现可枚举的固定结构，仍按工作流硬约束保留原图结构")
    return PromptPlan(
        workflow=payload.workflow,
        image_roles=image_roles,
        hard_constraints=list(profile["hard_constraints"]),
        preserve=preserve,
        style_profile=style_profile,
        target_space=target_space,
        layout=layout,
        addons=addon_profiles,
        user_intent=user_intent,
        materials=materials,
        palette=palette,
        lighting=lighting or ["符合原图采光方向的自然光与真实人工照明"],
        camera=camera or ["保持可信的室内摄影视角与透视"],
        composition=composition,
        negative_constraints=list(profile["negative_constraints"])
        + ["不要文字、Logo、水印或界面元素", "不要畸变、漂浮家具或不合理结构"],
        conflicts=conflicts,
        warnings=warnings,
    )


def validate_plan_coverage(plan: PromptPlan) -> dict[str, list[str]]:
    missing: list[str] = []
    unexpected: list[str] = []
    expected_roles = [item[0] for item in WORKFLOW_PROFILES[plan.workflow]["roles"]]
    actual_roles = [item.get("role") for item in plan.image_roles]
    if actual_roles != expected_roles:
        missing.append("image_roles")
    if not plan.hard_constraints:
        missing.append("hard_constraints")
    if not plan.style_profile:
        missing.append("style_profile")
    if not plan.user_intent:
        missing.append("user_intent")
    if not plan.negative_constraints:
        missing.append("negative_constraints")
    if plan.workflow == "cross_space":
        if not plan.target_space:
            missing.append("target_space")
        if not plan.layout:
            missing.append("layout")
    elif plan.target_space or plan.layout or plan.addons:
        unexpected.extend(
            [
                name
                for name, value in (
                    ("target_space", plan.target_space),
                    ("layout", plan.layout),
                    ("addons", plan.addons),
                )
                if value
            ]
        )
    return {"missing": missing, "unexpected": unexpected}


def _role_clause(workflow: str) -> str:
    if workflow == "room_adapt":
        return (
            "第 1 张图仅作为设计风格参考；"
            "第 2 张毛坯实拍图是结构、门窗、空间比例、镜头与透视的唯一基准；"
            "发生冲突时以第 2 张图为准。"
        )
    if workflow == "style_transfer":
        return "第 1 张图是原房结构、门窗、空间比例、镜头与透视的唯一基准。"
    return "第 1 张图只作为可迁移的风格、材质、配色与光照语言参考，不沿用其空间类型和家具布局。"


def _compile_edited_prompt(plan: PromptPlan, aspect_ratio: str | None) -> str:
    body = re.sub(r"(?m)^画幅：.*(?:\n|$)", "", plan.edited_prompt or "").strip()
    required = [_role_clause(plan.workflow), *plan.hard_constraints]
    if plan.preserve:
        required.extend(plan.preserve)
    style_label = str(plan.style_profile.get("label") or "").strip()
    if style_label:
        required.append(style_label)
    if plan.target_space:
        required.append(plan.target_space["label"])
    if plan.layout:
        required.append(plan.layout["label"])
    required.extend(item["label"] for item in plan.addons)
    required.extend(plan.negative_constraints)
    missing = [fragment for fragment in _unique(required) if fragment not in body]
    parts = [body]
    if missing:
        parts.append("服务端校验补充（必须遵守）：" + "；".join(missing) + "。")
    if aspect_ratio:
        parts.append("画幅：" + ASPECT_COMPOSITION[aspect_ratio] + "。")
    return "\n".join(parts)


def compile_prompt(plan: PromptPlan, aspect_ratio: str | None = None) -> str:
    if plan.edited_prompt:
        return _compile_edited_prompt(plan, aspect_ratio)
    role_clause = _role_clause(plan.workflow)

    style_label = str(plan.style_profile.get("label") or "参考图设计语言")
    style_traits = _unique(list(plan.style_profile.get("traits") or []))
    parts = [
        role_clause,
        f"生成目标：{WORKFLOW_PROFILES[plan.workflow]['label']}，输出真实、可落地的室内设计案例图。",
        "工作流硬约束：" + "；".join(plan.hard_constraints) + "。",
    ]
    if plan.preserve:
        parts.append("必须保留：" + "、".join(plan.preserve) + "。")
    parts.append("风格：" + "；".join([style_label, *style_traits]) + "。")
    if plan.materials:
        parts.append("材质：" + "、".join(plan.materials) + "。")
    if plan.palette:
        parts.append("配色：" + "、".join(plan.palette) + "。")
    if plan.target_space:
        parts.append(f"目标空间：{plan.target_space['label']}。")
    if plan.layout:
        parts.append(f"布局要求：{plan.layout['label']}。")
    if plan.addons:
        parts.append("附加元素：" + "、".join(item["label"] for item in plan.addons) + "。")
    parts.extend(
        [
            "光线：" + "、".join(plan.lighting) + "。",
            "镜头：" + "、".join(plan.camera) + "。",
            "构图：" + "、".join(plan.composition) + "。",
        ]
    )
    if aspect_ratio:
        parts.append("画幅：" + ASPECT_COMPOSITION[aspect_ratio] + "。")
    parts.extend(
        [
            "用户补充要求：" + plan.user_intent + "。",
            "禁止事项：" + "；".join(plan.negative_constraints) + "。",
        ]
    )
    return "\n".join(parts)
