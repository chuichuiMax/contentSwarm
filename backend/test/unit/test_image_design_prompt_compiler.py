from __future__ import annotations

from yuxi.image_design.prompt_compiler import (
    apply_edited_prompt,
    build_prompt_plan,
    compile_prompt,
    validate_plan_coverage,
)
from yuxi.image_design.schemas import CrossSpaceRefinementCreate, RoomAdaptRefinementCreate


def test_room_adapt_declares_image_order_and_structure_priority() -> None:
    payload = RoomAdaptRefinementCreate(
        workflow="room_adapt",
        style_reference_material_id="mli_style",
        raw_structure_material_id="mli_raw",
        user_prompt="奶油色调，保持采光",
    )
    plan = build_prompt_plan(
        payload,
        {
            "style_reference": {"style": "极简奶油风", "materials": ["浅色木饰面"]},
            "structure_source": {"room_type": "客厅", "preserve": ["落地窗", "承重柱"]},
        },
    )

    prompt = compile_prompt(plan, "3:4")

    assert "第 1 张图仅作为设计风格参考" in prompt
    assert "第 2 张毛坯实拍图" in prompt
    assert "发生冲突时以第 2 张图为准" in prompt
    assert validate_plan_coverage(plan) == {"missing": [], "unexpected": []}


def test_cross_space_compilation_is_deterministic_and_does_not_leak_style_transfer_fields() -> None:
    payload = CrossSpaceRefinementCreate(
        workflow="cross_space",
        style_reference_material_id="mli_style",
        target_space="study",
        layout="desk_window",
        addons=["bookcase"],
        user_prompt="安静、克制，保留自然光",
    )
    plan = build_prompt_plan(
        payload,
        {
            "cross_space_style": {
                "style": "侘寂风",
                "palette": ["米灰", "原木色"],
                "materials": ["微水泥", "原木"],
            }
        },
    )

    first = compile_prompt(plan, "4:3")
    second = compile_prompt(plan, "4:3")

    assert first == second
    assert "目标空间：书房" in first
    assert "书桌靠窗" in first
    assert "整墙书柜" in first
    assert "原房换装" not in first
    assert validate_plan_coverage(plan) == {"missing": [], "unexpected": []}


def test_aspect_ratio_only_changes_composition_clause() -> None:
    payload = CrossSpaceRefinementCreate(
        workflow="cross_space",
        style_reference_material_id="mli_style",
        target_space="study",
        layout="desk_window",
        addons=[],
        user_prompt="安静自然",
    )
    plan = build_prompt_plan(payload, {"cross_space_style": {"style": "现代简约"}})

    portrait = compile_prompt(plan, "3:4")
    landscape = compile_prompt(plan, "4:3")

    assert portrait != landscape
    assert "竖向 3:4" in portrait
    assert "横向 4:3" in landscape


def test_edited_compiled_prompt_is_not_reinserted_as_user_intent() -> None:
    payload = RoomAdaptRefinementCreate(
        workflow="room_adapt",
        style_reference_material_id="mli_style",
        raw_structure_material_id="mli_raw",
        user_prompt="奶油色调，保持采光",
    )
    plan = build_prompt_plan(
        payload,
        {
            "style_reference": {"style": "极简奶油风"},
            "structure_source": {"preserve": ["落地窗"]},
        },
    )
    original = compile_prompt(plan)
    edited = apply_edited_prompt(plan, original + "\n补充：家具尺度合理。")

    result = compile_prompt(edited, "4:3")

    assert result.count("第 1 张图仅作为设计风格参考") == 1
    assert "用户补充要求：第 1 张图仅作为设计风格参考" not in result
    assert "补充：家具尺度合理" in result
    assert "横向 4:3" in result


def test_edited_prompt_reinjects_removed_constraints_and_keeps_negated_phrases() -> None:
    payload = CrossSpaceRefinementCreate(
        workflow="cross_space",
        style_reference_material_id="mli_style",
        target_space="study",
        layout="desk_window",
        addons=[],
        user_prompt="安静自然",
    )
    plan = build_prompt_plan(payload, {"cross_space_style": {"style": "现代简约"}})
    edited = apply_edited_prompt(
        plan,
        "现代简约书房。不得改变户型结构。沿用参考图布局并保留餐桌。保持画面整洁。",
    )

    result = compile_prompt(edited)

    assert "不得改变户型结构" in result
    assert "沿用参考图布局" not in result
    assert "保留餐桌" not in result
    assert "第 1 张图只作为可迁移" in result
    assert "书桌靠窗" in result
    assert edited.conflicts == ["已忽略与工作流硬约束冲突的描述：沿用参考图布局并保留餐桌"]
