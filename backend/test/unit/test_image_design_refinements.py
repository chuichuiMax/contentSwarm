from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.image_design.prompt_compiler import build_prompt_plan
from yuxi.image_design.schemas import StyleTransferRefinementCreate, WORKFLOW_PROFILE_VERSION
from yuxi.image_design.service import refinement_fingerprint, validate_refinement_integrity


def _refinement(*, semantic: dict, materials: list[dict[str, str]], plan: dict):
    return SimpleNamespace(
        request_json={"semantic": semantic, "materials": materials},
        workflow_version=WORKFLOW_PROFILE_VERSION,
        input_fingerprint=refinement_fingerprint(semantic, materials),
        plan_json=plan,
    )


def test_refinement_integrity_accepts_matching_semantics_materials_and_roles() -> None:
    payload = StyleTransferRefinementCreate(
        workflow="style_transfer",
        source_material_id="mli_source",
        style_label="现代简约",
        user_prompt="暖色自然光",
    )
    plan = build_prompt_plan(
        payload,
        {"structure_source": {"room_type": "卧室", "preserve": ["窗户位置"]}},
    )
    materials = [
        {"role": "structure_source", "material_id": "mli_source", "asset_sha256": "a" * 64}
    ]
    semantic = payload.model_dump(mode="json", exclude={"parent_refinement_id", "edited_prompt"})
    semantic["effective_prompt"] = payload.user_prompt
    row = _refinement(semantic=semantic, materials=materials, plan=plan.model_dump(mode="json"))

    assert validate_refinement_integrity(row, materials) == plan


def test_refinement_integrity_rejects_changed_asset_hash() -> None:
    payload = StyleTransferRefinementCreate(
        workflow="style_transfer",
        source_material_id="mli_source",
        style_label="现代简约",
        user_prompt="暖色自然光",
    )
    plan = build_prompt_plan(payload, {"structure_source": {"room_type": "卧室"}})
    original = [{"role": "structure_source", "material_id": "mli_source", "asset_sha256": "a" * 64}]
    changed = [{"role": "structure_source", "material_id": "mli_source", "asset_sha256": "b" * 64}]
    semantic = payload.model_dump(mode="json", exclude={"parent_refinement_id", "edited_prompt"})
    semantic["effective_prompt"] = payload.user_prompt
    row = _refinement(semantic=semantic, materials=original, plan=plan.model_dump(mode="json"))

    with pytest.raises(HTTPException) as exc_info:
        validate_refinement_integrity(row, changed)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "IMAGE_DESIGN_REFINEMENT_STALE"


def test_refinement_integrity_rejects_plan_role_tampering() -> None:
    payload = StyleTransferRefinementCreate(
        workflow="style_transfer",
        source_material_id="mli_source",
        style_label="现代简约",
        user_prompt="暖色自然光",
    )
    plan = build_prompt_plan(payload, {"structure_source": {"room_type": "卧室"}})
    plan.image_roles[0]["material_id"] = "mli_other"
    materials = [
        {"role": "structure_source", "material_id": "mli_source", "asset_sha256": "a" * 64}
    ]
    semantic = payload.model_dump(mode="json", exclude={"parent_refinement_id", "edited_prompt"})
    semantic["effective_prompt"] = payload.user_prompt
    row = _refinement(semantic=semantic, materials=materials, plan=plan.model_dump(mode="json"))

    with pytest.raises(HTTPException) as exc_info:
        validate_refinement_integrity(row, materials)

    assert exc_info.value.detail["error"]["code"] == "IMAGE_DESIGN_REFINEMENT_STALE"
