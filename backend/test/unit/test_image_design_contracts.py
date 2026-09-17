from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from yuxi.image_design.schemas import ImageDesignGenerateCreate, ImageDesignRefinementCreate


refinement_adapter = TypeAdapter(ImageDesignRefinementCreate)


def test_style_transfer_rejects_cross_space_fields() -> None:
    with pytest.raises(ValidationError):
        refinement_adapter.validate_python(
            {
                "workflow": "style_transfer",
                "source_material_id": "mli_source",
                "style_label": "现代简约",
                "user_prompt": "保留窗户并改成暖色空间",
                "target_space": "living_room",
            }
        )


def test_refinement_rejects_browser_selected_model() -> None:
    with pytest.raises(ValidationError):
        refinement_adapter.validate_python(
            {
                "workflow": "style_transfer",
                "source_material_id": "mli_source",
                "style_label": "现代简约",
                "user_prompt": "暖色自然光",
                "model_spec": "browser:selected-model",
            }
        )


def test_room_adapt_requires_two_distinct_role_fields() -> None:
    payload = refinement_adapter.validate_python(
        {
            "workflow": "room_adapt",
            "style_reference_material_id": "mli_style",
            "raw_structure_material_id": "mli_raw",
            "user_prompt": "适配为明亮自然的住宅空间",
        }
    )

    assert payload.style_reference_material_id == "mli_style"
    assert payload.raw_structure_material_id == "mli_raw"


def test_cross_space_rejects_style_transfer_selection() -> None:
    with pytest.raises(ValidationError):
        refinement_adapter.validate_python(
            {
                "workflow": "cross_space",
                "style_reference_material_id": "mli_style",
                "target_space": "study",
                "layout": "desk_window",
                "addons": ["bookcase"],
                "user_prompt": "自然光和木质感",
                "style_label": "现代轻奢",
            }
        )


def test_generate_contract_only_accepts_server_refinement() -> None:
    payload = ImageDesignGenerateCreate(
        refinement_id="idr_verified",
        aspect_ratio="4:3",
        clarity="2K",
        gen_count=4,
    )

    assert payload.refinement_id == "idr_verified"
    with pytest.raises(ValidationError):
        ImageDesignGenerateCreate.model_validate(
            {
                "workflow": "style_transfer",
                "reference_material_id": "mli_source",
                "has_refined": True,
                "user_prompt": "绕过服务端记录",
            }
        )


def test_edited_prompt_requires_parent_refinement() -> None:
    with pytest.raises(ValidationError):
        refinement_adapter.validate_python(
            {
                "workflow": "style_transfer",
                "source_material_id": "mli_source",
                "style_label": "现代简约",
                "user_prompt": "暖色自然光",
                "edited_prompt": "修改后的优化结果",
            }
        )

    payload = refinement_adapter.validate_python(
        {
            "workflow": "style_transfer",
            "source_material_id": "mli_source",
            "style_label": "现代简约",
            "user_prompt": "暖色自然光",
            "parent_refinement_id": "idr_parent",
            "edited_prompt": "修改后的优化结果",
        }
    )

    assert payload.parent_refinement_id == "idr_parent"
