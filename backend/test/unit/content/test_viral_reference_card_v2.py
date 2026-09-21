from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test.unit.content.test_viral_asset_preparation import prepared, source
from yuxi.content.model.viral_assets import (
    ViralAssetCorrectionInput,
    ViralAssetReviewInput,
    validate_prepared_asset_v2,
)


def prepared_v2(article=None):
    article = article or source()
    payload = deepcopy(prepared(article))
    payload["schema_version"] = 2
    payload["reference_card"]["schema_version"] = 2
    payload["reference_card"]["required_slots"] = [
        {
            "slot_key": "scene",
            "name": "施工场景",
            "description": "本次真实施工场景",
            "variable_codes": ["scene"],
            "match_mode": "all",
            "evidence_required": True,
            "required": True,
            "anchor": payload["reference_card"]["anchors"][0],
        }
    ]
    return article, payload


def test_v2_reference_card_requires_machine_readable_slots():
    article, payload = prepared_v2()
    result = validate_prepared_asset_v2(payload, article)
    assert result.reference_card.required_slots[0].slot_key == "scene"
    assert result.reference_card.schema_version == 2


def test_v2_reference_card_rejects_unpublished_variable_code():
    article, payload = prepared_v2()
    with pytest.raises(ValueError, match="未发布变量"):
        validate_prepared_asset_v2(payload, article, allowed_variable_codes={"price", "product"})


@pytest.mark.parametrize("field", ["slot_key", "variable_codes", "match_mode", "evidence_required"])
def test_v2_reference_card_rejects_incomplete_slot_contract(field):
    article, payload = prepared_v2()
    del payload["reference_card"]["required_slots"][0][field]
    if field in {"match_mode", "evidence_required"}:
        # These fields have explicit safe defaults; invalid values remain forbidden.
        payload["reference_card"]["required_slots"][0][field] = "invalid"
    with pytest.raises(ValueError):
        validate_prepared_asset_v2(payload, article)


@pytest.mark.asyncio
async def test_review_ignores_runtime_metadata_but_validates_v2_contract(monkeypatch):
    from yuxi.services import content_viral_assets

    article, payload = prepared_v2()
    payload["runtime_config_snapshot"] = {"model": "preparer"}
    asset = SimpleNamespace(
        id="asset-v2",
        status="needs_review",
        error_message="等待运营审核",
        preparation_skill_hash="skill-v2",
        prepared_json=payload,
        source_json=article.model_dump(mode="json"),
    )
    monkeypatch.setattr(content_viral_assets, "require_asset", AsyncMock(return_value=asset))
    monkeypatch.setattr(content_viral_assets, "check_asset_source", AsyncMock(return_value=True))
    monkeypatch.setattr(content_viral_assets, "preparation_skill_hash", lambda: "skill-v2")
    monkeypatch.setattr(content_viral_assets, "asset_dict", lambda item, **_: {"status": item.status})
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: ["scene"])),
        commit=AsyncMock(),
    )

    result = await content_viral_assets.review_viral_asset(
        db,
        SimpleNamespace(uid="admin-1"),
        asset.id,
        ViralAssetReviewInput(action="approve"),
    )

    assert result["asset"]["status"] == "ready"
    assert asset.prepared_json["runtime_config_snapshot"] == {"model": "preparer"}
    assert asset.prepared_json["review"]["action"] == "approve"


@pytest.mark.asyncio
async def test_operator_can_correct_type_and_slot_metadata_before_approval(monkeypatch):
    from yuxi.services import content_viral_assets

    article, payload = prepared_v2()
    asset = SimpleNamespace(
        id="asset-v2",
        status="needs_review",
        error_message="等待运营审核",
        preparation_skill_hash="skill-v2",
        prepared_json=payload,
        source_json=article.model_dump(mode="json"),
    )
    monkeypatch.setattr(content_viral_assets, "require_asset", AsyncMock(return_value=asset))
    monkeypatch.setattr(content_viral_assets, "check_asset_source", AsyncMock(return_value=True))
    monkeypatch.setattr(content_viral_assets, "preparation_skill_hash", lambda: "skill-v2")
    monkeypatch.setattr(content_viral_assets, "asset_dict", lambda item, **_: {"status": item.status})
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: ["scene"])),
        commit=AsyncMock(),
    )

    await content_viral_assets.correct_viral_asset(
        db,
        SimpleNamespace(uid="admin-1"),
        asset.id,
        ViralAssetCorrectionInput(
            content_type_code="CT07",
            content_type_reason="主体为工地日常记录",
            required_slots=[
                {
                    "slot_key": "work_scene",
                    "name": "工作场景",
                    "description": "本次真实工地场景",
                    "variable_codes": ["scene"],
                    "match_mode": "all",
                    "evidence_required": True,
                    "required": True,
                }
            ],
            reason="纠正自动分类和槽位名称",
        ),
    )

    assert asset.prepared_json["reference_card"]["content_type_code"] == "CT07"
    assert asset.prepared_json["reference_card"]["required_slots"][0]["slot_key"] == "work_scene"
    assert asset.prepared_json["review_history"][-1]["action"] == "correct"
