from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

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
    assert asset.error_message is None
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


@pytest.mark.parametrize("action", ["approve", "enable"])
def test_only_approved_or_enabled_prepared_assets_are_ready(action):
    from yuxi.services.content_viral_assets import asset_has_approved_review

    asset = SimpleNamespace(prepared_json={"status": "prepared", "review": {"action": action}})

    assert asset_has_approved_review(asset) is True


@pytest.mark.parametrize("action", [None, "reject", "correct", "disable"])
def test_unapproved_prepared_assets_are_not_ready(action):
    from yuxi.services.content_viral_assets import asset_has_approved_review

    review = {} if action is None else {"action": action}
    asset = SimpleNamespace(prepared_json={"status": "prepared", "review": review})

    assert asset_has_approved_review(asset) is False


@pytest.mark.parametrize(
    ("prepared_json", "expected_status", "expected_error", "expected_attempt"),
    [
        ({"status": "prepared"}, "needs_review", "等待运营审核", 1),
        (
            {"status": "prepared", "review": {"action": "approve"}},
            "ready",
            None,
            1,
        ),
        (
            {"status": "prepared", "review": {"action": "reject"}},
            "needs_review",
            "运营驳回",
            1,
        ),
        ({}, "pending", None, 2),
    ],
)
def test_reidentified_asset_restores_only_reviewed_state(
    prepared_json, expected_status, expected_error, expected_attempt
):
    from yuxi.services.viral_document_worker import restore_reidentified_asset

    asset = SimpleNamespace(
        status="invalidated",
        error_message="运营驳回" if (prepared_json.get("review") or {}).get("action") == "reject" else "旧提示",
        prepared_json=prepared_json,
        attempt=1,
    )

    restore_reidentified_asset(asset)

    assert asset.status == expected_status
    assert asset.error_message == expected_error
    assert asset.attempt == expected_attempt


def test_reidentified_disabled_asset_remains_invalidated():
    from yuxi.services.viral_document_worker import restore_reidentified_asset

    asset = SimpleNamespace(
        status="invalidated",
        error_message="运营停用",
        prepared_json={"status": "prepared", "review": {"action": "disable"}},
        attempt=1,
    )

    restore_reidentified_asset(asset)

    assert asset.status == "invalidated"
    assert asset.error_message == "运营停用"


@pytest.mark.asyncio
async def test_delete_viral_asset_removes_file_job_references(monkeypatch):
    from yuxi.services import content_viral_assets

    asset = SimpleNamespace(id="asset-delete", file_id="file-1", status="needs_review")
    jobs = [
        SimpleNamespace(result_json={"asset_ids": ["asset-delete", "asset-keep"]}),
        SimpleNamespace(result_json={"asset_ids": ["asset-delete"]}),
    ]
    monkeypatch.setattr(content_viral_assets, "require_asset", AsyncMock(return_value=asset))
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: jobs)),
        delete=AsyncMock(),
        commit=AsyncMock(),
    )

    result = await content_viral_assets.delete_viral_asset(db, SimpleNamespace(uid="admin-1"), asset.id)

    assert result == {"success": True, "id": asset.id}
    assert jobs[0].result_json["asset_ids"] == ["asset-keep"]
    assert jobs[1].result_json["asset_ids"] == []
    db.delete.assert_awaited_once_with(asset)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_viral_asset_rejects_ready_asset(monkeypatch):
    from yuxi.services import content_viral_assets

    asset = SimpleNamespace(id="asset-ready", file_id="file-1", status="ready")
    monkeypatch.setattr(content_viral_assets, "require_asset", AsyncMock(return_value=asset))

    with pytest.raises(HTTPException, match="已发布资产不能删除") as exc_info:
        await content_viral_assets.delete_viral_asset(SimpleNamespace(), SimpleNamespace(uid="admin-1"), asset.id)

    assert exc_info.value.status_code == 409
