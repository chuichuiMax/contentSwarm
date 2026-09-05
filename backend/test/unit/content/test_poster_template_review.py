from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import yuxi.services.content_cover_service as service
from yuxi.content_cover.schemas import PosterTemplateReviewUpdate


def _slot(text: str = "OCR 原文") -> dict:
    return {
        "id": "slot-1",
        "role": "title",
        "source_text": text,
        "editable": True,
        "box": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.12},
        "style": {
            "fill": "#FFFFFF",
            "stroke": "#111111",
            "stroke_width_ratio": 0.02,
            "font_size_ratio": 0.08,
            "bold": True,
            "align": "center",
            "panel_fill": None,
            "panel_radius_ratio": 0.2,
        },
        "max_chars": 20,
        "max_lines": 1,
        "confidence": 0.72,
        "candidate_count": 3,
        "consensus_count": 1,
        "source_variant": "original#recall",
        "alternatives": ["OCR 正文"],
        "review_state": "recognized",
    }


class Template:
    def __init__(self):
        self.id = "cpt-review"
        self.asset_id = "cca-template"
        self.name = "待校对模板"
        self.category = "product_promotion"
        self.template_type = "layout_template"
        self.canvas_width = 1080
        self.canvas_height = 1440
        self.product_box_json = {"x": 0, "y": 0, "width": 1, "height": 1}
        self.safe_area_json = {"x": 0.02, "y": 0.02, "width": 0.96, "height": 0.96}
        self.text_slots_json = [_slot()]
        self.fixed_regions_json = []
        self.editable_regions_json = [self.text_slots_json[0]["box"]]
        self.analysis_json = {
            "review_status": "pending",
            "ocr_raw_layers": [{"id": "raw-1", "text": "OCR 原文"}],
            "recognition_metrics": {"raw_layer_count": 1, "low_confidence_count": 1},
            "decoration_regions": [],
        }
        self.version = 1
        self.analysis_version = "poster-v3"
        self.status = "needs_review"
        self.error_message = None
        self.updated_at = None

    def to_dict(self):
        return {
            "id": self.id,
            "asset_id": self.asset_id,
            "name": self.name,
            "category": self.category,
            "template_type": self.template_type,
            "canvas_width": self.canvas_width,
            "canvas_height": self.canvas_height,
            "product_box": self.product_box_json,
            "safe_area": self.safe_area_json,
            "text_slots": self.text_slots_json,
            "fixed_regions": self.fixed_regions_json,
            "editable_regions": self.editable_regions_json,
            "analysis": self.analysis_json,
            "version": self.version,
            "analysis_version": self.analysis_version,
            "status": self.status,
            "error_message": self.error_message,
        }


class Database:
    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_review_confirmation_preserves_raw_ocr_and_enables_confirmed_layers(monkeypatch):
    template = Template()
    library_item = SimpleNamespace(
        display_name=template.name,
        category=template.category,
        status="disabled",
        updated_at=None,
    )

    class CoverRepository:
        def __init__(self, db):
            del db

        async def get_poster_template_for_user(self, template_id, owner_uid, for_update=False):
            assert (template_id, owner_uid, for_update) == (template.id, "owner", True)
            return template

        async def poster_template_is_selected_by_task(self, template_id, owner_uid, locked_only=False):
            assert (template_id, owner_uid, locked_only) == (template.id, "owner", True)
            return False

    class MaterialRepository:
        def __init__(self, db):
            del db

        async def get_item_by_asset(self, asset_id):
            assert asset_id == template.asset_id
            return library_item

    async def resolve_category(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(name="产品推广")

    monkeypatch.setattr(service, "ContentCoverRepository", CoverRepository)
    monkeypatch.setattr(service, "MaterialLibraryRepository", MaterialRepository)
    monkeypatch.setattr(service, "resolve_material_category", resolve_category)

    corrected = _slot("OCR 正文")
    result = await service.review_poster_template(
        Database(),
        SimpleNamespace(uid="owner", department_id=None),
        template.id,
        PosterTemplateReviewUpdate.model_validate(
            {
                "version": 1,
                "product_box": template.product_box_json,
                "text_slots": [corrected],
                "confirm": True,
            }
        ),
    )

    assert template.status == "ready"
    assert library_item.status == "enabled"
    assert template.version == 2
    assert template.text_slots_json[0]["source_text"] == "OCR 正文"
    assert template.text_slots_json[0]["review_state"] == "user_edited"
    assert template.analysis_json["ocr_raw_layers"] == [{"id": "raw-1", "text": "OCR 原文"}]
    assert template.analysis_json["review_status"] == "confirmed"
    assert template.analysis_json["confirmed_layers"] == template.text_slots_json
    assert result["template"]["requires_review"] is False


@pytest.mark.asyncio
async def test_review_rejects_stale_version(monkeypatch):
    template = Template()

    class CoverRepository:
        def __init__(self, db):
            del db

        async def get_poster_template_for_user(self, *args, **kwargs):
            del args, kwargs
            return template

    monkeypatch.setattr(service, "ContentCoverRepository", CoverRepository)

    with pytest.raises(HTTPException) as raised:
        await service.review_poster_template(
            Database(),
            SimpleNamespace(uid="owner", department_id=None),
            template.id,
            PosterTemplateReviewUpdate.model_validate(
                {
                    "version": 9,
                    "product_box": template.product_box_json,
                    "text_slots": template.text_slots_json,
                    "confirm": False,
                }
            ),
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["error"]["code"] == "POSTER_TEMPLATE_VERSION_CONFLICT"


@pytest.mark.asyncio
async def test_reanalysis_rejects_template_locked_by_content_task(monkeypatch):
    template = Template()

    class CoverRepository:
        def __init__(self, db):
            del db

        async def get_poster_template_for_user(self, template_id, owner_uid, for_update=False):
            assert (template_id, owner_uid, for_update) == (template.id, "owner", True)
            return template

        async def poster_template_is_selected_by_task(self, template_id, owner_uid, locked_only=False):
            assert (template_id, owner_uid, locked_only) == (template.id, "owner", True)
            return True

    monkeypatch.setattr(service, "ContentCoverRepository", CoverRepository)

    with pytest.raises(HTTPException) as raised:
        await service.reanalyze_poster_template(
            Database(),
            SimpleNamespace(uid="owner", department_id=None),
            template.id,
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["error"]["code"] == "POSTER_TEMPLATE_IN_USE"
