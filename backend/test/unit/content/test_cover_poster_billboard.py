from __future__ import annotations

import io
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError

from yuxi.content_cover.poster_billboard import (
    POSTER_SIZE,
    PosterBillboardError,
    _draw_text_slot,
    analyze_poster_template,
    build_image2_protection_mask,
    build_poster_copy_plan,
    evaluate_poster_quality,
    normalize_poster_text_slots,
    render_poster_billboard,
)
from yuxi.content_cover.schemas import PosterGenerateCreate, PosterTextSlot, TemplateAnalysis
from yuxi.content_cover.schemas import Image2Output, Image2Submission
from yuxi.services import content_cover_worker
from yuxi.services.content_cover_service import serialize_poster_template


def _slot(*, editable: bool = True, source_text: str = "模板标题") -> dict:
    return {
        "id": "title-1",
        "role": "title",
        "source_text": source_text,
        "editable": editable,
        "box": {"x": 0.1, "y": 0.08, "width": 0.8, "height": 0.12},
        "style": {
            "fill": "#FFFFFF",
            "stroke": "#111111",
            "stroke_width_ratio": 0.02,
            "font_size_ratio": 0.07,
            "bold": True,
            "align": "center",
            "panel_fill": "#E94B20",
            "panel_radius_ratio": 0.15,
        },
        "max_chars": 14,
        "max_lines": 2,
    }


def _record(*, template_type: str = "alpha_overlay", text_slots: list[dict] | None = None) -> dict:
    return {
        "id": "cpt_test",
        "asset_id": "cca_template",
        "template_type": template_type,
        "product_box": {"x": 0.1, "y": 0.24, "width": 0.8, "height": 0.68},
        "safe_area": {"x": 0.02, "y": 0.02, "width": 0.96, "height": 0.96},
        "text_slots": text_slots or [],
        "fixed_regions": [],
        "editable_regions": [],
    }


def test_poster_renderer_uses_detected_fill_runs_and_opaque_panel_color():
    canvas = Image.new("RGBA", (720, 360), "#FFFFFF")
    slot = PosterTextSlot.model_validate(
        {
            **_slot(source_text="4大产品服务"),
            "box": {"x": 0.08, "y": 0.12, "width": 0.84, "height": 0.28},
            "style": {
                **_slot()["style"],
                "fill": "#E4E4E4",
                "fill_runs": [
                    {"start": 0, "end": 2, "fill": "#F4DC28"},
                    {"start": 2, "end": 6, "fill": "#E4E4E4"},
                ],
                "panel_fill": "#E7D6C9",
                "panel_opacity": 1,
                "stroke": None,
                "stroke_width_ratio": 0,
            },
        }
    )

    assert _draw_text_slot(canvas, slot, slot.source_text) is False

    colors = canvas.get_flattened_data()
    assert sum(pixel[:3] == (244, 220, 40) for pixel in colors) > 20
    assert sum(pixel[:3] == (228, 228, 228) for pixel in colors) > 20
    assert sum(pixel[:3] == (231, 214, 201) for pixel in colors) > 500


def test_analyze_transparent_overlay_is_ready_and_keeps_alpha_statistics():
    template = Image.new("RGBA", POSTER_SIZE, (0, 0, 0, 0))
    ImageDraw.Draw(template).rectangle((0, 0, 1079, 1439), outline=(255, 80, 20, 255), width=24)

    result = analyze_poster_template(template)

    assert result["template_type"] == "alpha_overlay"
    assert result["status"] == "needs_review"
    assert result["review_status"] == "pending"
    assert result["product_box"]["x"] == pytest.approx(24 / 1080)
    assert result["product_box"]["y"] == pytest.approx(24 / 1440)
    assert result["product_box"]["width"] == pytest.approx((1080 - 48) / 1080)
    assert result["product_box"]["height"] == pytest.approx((1440 - 48) / 1440)
    assert result["alpha_statistics"]["transparent_ratio"] > 0.9


def test_analyze_opaque_layout_requires_explicit_product_annotation():
    result = analyze_poster_template(Image.new("RGB", POSTER_SIZE, "white"))

    assert result["template_type"] == "layout_template"
    assert result["status"] == "needs_annotation"
    assert result["product_box"] is None


def test_opaque_analysis_retains_every_detected_text_slot():
    style = {
        "fill": "#111111",
        "font_size_ratio": 0.04,
        "bold": True,
        "align": "center",
    }

    def detected(slot_id: str, role: str, y: float, height: float, width: float = 0.5) -> dict:
        return {
            "id": slot_id,
            "role": role,
            "source_text": slot_id,
            "box": {"x": 0.1, "y": y, "width": width, "height": height},
            "style": style,
            "max_chars": 20,
            "max_lines": 2,
        }

    analysis = TemplateAnalysis.model_validate(
        {
            "processing_version": "test",
            "canvas_width": 1080,
            "canvas_height": 1440,
            "text_slots": [
                detected("eyebrow", "eyebrow", 0.04, 0.04),
                detected("hero", "title", 0.12, 0.12, 0.8),
                detected("background-form", "other", 0.45, 0.04),
                detected("bottom-slogan", "slogan", 0.86, 0.05),
            ],
            "layout_fingerprint": "fixture",
        }
    )

    result = analyze_poster_template(Image.new("RGB", POSTER_SIZE, "white"), text_analysis=analysis)

    assert [slot["id"] for slot in result["text_slots"]] == [
        "eyebrow",
        "hero",
        "background-form",
        "bottom-slogan",
    ]
    assert result["status"] == "needs_review"
    assert result["review_status"] == "pending"
    assert result["product_box"] == {"x": 0, "y": 0, "width": 1, "height": 1}
    assert result["background_mode"] == "full_canvas"


def test_hirun_tall_poster_is_calibrated_to_eight_exact_logical_layers():
    style = {
        "fill": "#FFFFFF",
        "font_size_ratio": 0.03,
        "bold": False,
        "align": "left",
    }
    raw = [
        ("152 m", 45, 101, 274, 93),
        ("HONG YANG", 642, 130, 179, 27),
        ("JIA ZHUANG", 637, 165, 181, 37),
        ("岳阳・杏林小区", 42, 232, 650, 119),
        ("YUEYANG XINGLIN COMMUNITY", 66, 360, 412, 20),
        ("Interior design:hirun", 56, 453, 326, 32),
        ("date of compltion:2026", 59, 483, 363, 29),
        ("HunOme", 228, 1338, 266, 19),
        ("Ren0Va0n", 564, 1338, 288, 19),
        ("t", 751, 1340, 12, 16),
        ("h", 377, 1341, 19, 16),
    ]
    analysis = TemplateAnalysis.model_validate(
        {
            "processing_version": "test",
            "canvas_width": 1080,
            "canvas_height": 1440,
            "text_slots": [
                {
                    "id": f"slot-{index}",
                    "role": "other",
                    "source_text": text,
                    "box": {
                        "x": x / 1080,
                        "y": y / 1440,
                        "width": width / 1080,
                        "height": height / 1440,
                    },
                    "style": style,
                    "max_chars": max(4, len(text)),
                    "max_lines": 1,
                }
                for index, (text, x, y, width, height) in enumerate(raw, 1)
            ],
            "layout_fingerprint": "fixture",
        }
    )

    result = analyze_poster_template(Image.new("RGB", POSTER_SIZE, "white"), text_analysis=analysis)

    assert [slot["source_text"] for slot in result["text_slots"]] == [
        "152 m²",
        "HONG YANG",
        "JIA ZHUANG",
        "岳阳 · 杏林小区",
        "YUEYANG XINGLIN COMMUNITY",
        "Interior design: hirun",
        "date of completion: 2026",
        "Hirunhome Renovation",
    ]
    assert len(result["text_slots"]) == 8
    assert result["text_slots"][0]["style"]["font_family"] == "Georgia"
    assert result["text_slots"][0]["style"]["font_size_px"] == 88
    assert result["text_slots"][3]["style"]["font_family"] == "SimSun"
    assert result["text_slots"][3]["style"]["editor_x"] == 58
    assert result["text_slots"][3]["style"]["editor_y"] == 272
    assert result["text_slots"][7]["style"]["letter_spacing"] == 14


def test_copy_plan_changes_only_editable_slots():
    editable = _slot(editable=True)
    fixed = {**_slot(editable=False, source_text="品牌固定文案"), "id": "fixed-1", "role": "eyebrow"}

    plan = build_poster_copy_plan(
        [editable, fixed],
        title="内容资产概括后的新标题",
        source="content_asset",
        overrides={"fixed-1": "不应覆盖"},
    )

    assert plan["slots"][0]["changed"] is True
    assert plan["slots"][0]["text"] == "内容资产概括后的新标题"[:14]
    assert plan["slots"][1]["changed"] is False
    assert plan["slots"][1]["text"] == "品牌固定文案"


def test_copy_plan_does_not_redraw_identical_text_or_remove_english_spaces():
    source = "鸿扬家装 JIA ZHUANG HONG YANG"
    slot = _slot(editable=True, source_text=source)
    slot["max_chars"] = 40

    plan = build_poster_copy_plan(
        [slot],
        title="不会使用的内容标题",
        source="content_asset",
        overrides={slot["id"]: source},
    )

    assert plan["slots"][0]["changed"] is False
    assert plan["slots"][0]["text"] == source


def test_copy_plan_compacts_content_title_without_cutting_common_phrase():
    slot = _slot(editable=True, source_text="4大产品服务")
    slot["max_chars"] = 8

    plan = build_poster_copy_plan(
        [slot],
        title="90%的企业都忽略的获客细节",
        source="content_asset",
    )

    assert plan["slots"][0]["text"] == "90%企业都忽略"


def test_bilingual_brand_lockup_is_fixed_and_real_headline_is_promoted():
    brand = _slot(editable=True, source_text="鸿扬家装JIA ZHUANGHONG YANG")
    brand["id"] = "brand"
    brand["box"] = {"x": 0.07, "y": 0.05, "width": 0.69, "height": 0.12}
    headline = _slot(editable=False, source_text="4大产品服务")
    headline["id"] = "headline"
    headline["role"] = "other"
    headline["box"] = {"x": 0.07, "y": 0.16, "width": 0.69, "height": 0.1}

    normalized = normalize_poster_text_slots([brand, headline])
    plan = build_poster_copy_plan(
        normalized,
        title="全案服务升级",
        source="content_asset",
    )

    assert normalized[0]["role"] == "eyebrow"
    assert normalized[0]["editable"] is False
    assert normalized[1]["role"] == "title"
    assert normalized[1]["editable"] is True
    assert plan["slots"][0]["changed"] is False
    assert plan["slots"][1]["text"] == "全案服务升级"
    assert plan["slots"][1]["changed"] is True


def test_deterministic_render_preserves_overlay_and_places_product():
    template = Image.new("RGBA", POSTER_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(template)
    draw.rectangle((0, 0, 1079, 1439), outline=(235, 70, 30, 255), width=30)
    draw.rectangle((0, 0, 1079, 210), fill=(30, 30, 30, 255))
    product = Image.new("RGB", (800, 800), (30, 110, 220))
    record = _record()
    plan = build_poster_copy_plan([], source="template")

    rendered, metadata = render_poster_billboard(template, product, record, plan)

    with Image.open(io.BytesIO(rendered)) as output:
        output.load()
        assert output.size == POSTER_SIZE
        assert output.mode == "RGB"
        assert output.getpixel((10, 10)) == (30, 30, 30)
        assert output.getpixel((540, 800)) == (30, 110, 220)
        report = evaluate_poster_quality(output, record, metadata)
    assert report["passed"] is True
    assert report["template_locked"] is True
    assert report["product_locked"] is True


def test_opaque_layout_replaces_only_annotated_product_box():
    template = Image.new("RGB", POSTER_SIZE, (245, 240, 230))
    product = Image.new("RGB", (600, 900), (20, 130, 90))
    record = _record(template_type="layout_template")

    rendered, _ = render_poster_billboard(
        template,
        product,
        record,
        build_poster_copy_plan([], source="template"),
    )

    with Image.open(io.BytesIO(rendered)) as output:
        assert output.getpixel((20, 20)) == (245, 240, 230)
        assert output.getpixel((540, 800)) == (20, 130, 90)


def test_opaque_layout_restores_template_text_over_product_area():
    template = Image.new("RGB", POSTER_SIZE, (245, 240, 230))
    draw = ImageDraw.Draw(template)
    draw.rectangle((200, 450, 880, 560), fill=(245, 240, 230))
    draw.rectangle((260, 480, 820, 530), fill=(255, 255, 255))
    draw.rectangle((230, 610, 310, 690), fill=(230, 65, 35))
    product = Image.new("RGB", (600, 900), (20, 130, 90))
    slot = _slot(editable=False, source_text="LOCKED")
    slot["box"] = {"x": 0.2, "y": 0.32, "width": 0.6, "height": 0.08}
    slot["style"] = {**slot["style"], "fill": "#FFFFFF", "stroke": None, "panel_fill": None}
    record = _record(template_type="layout_template", text_slots=[slot])
    record["fixed_regions"] = [{"x": 0.21, "y": 0.42, "width": 0.08, "height": 0.06}]

    rendered, _ = render_poster_billboard(
        template,
        product,
        record,
        build_poster_copy_plan([slot], source="template"),
    )

    with Image.open(io.BytesIO(rendered)) as output:
        # Both the locked white overlay and the red decoration must survive replacement.
        assert output.getpixel((540, 500)) == (255, 255, 255)
        red = output.getpixel((270, 650))
        assert red[0] > 180 and red[1] < 100 and red[2] < 100


def test_full_canvas_layout_places_product_below_template_text() -> None:
    template = Image.new("RGB", POSTER_SIZE, "white")
    draw = ImageDraw.Draw(template)
    draw.rectangle((180, 120, 900, 250), fill=(224, 70, 36))
    product = Image.new("RGB", (900, 1200), (20, 130, 90))
    slot = _slot(editable=False, source_text="LOCKED")
    slot["box"] = {"x": 0.16, "y": 0.08, "width": 0.68, "height": 0.1}
    slot["style"] = {**slot["style"], "fill": "#E04624", "stroke": None, "panel_fill": None}
    record = _record(template_type="layout_template", text_slots=[slot])
    record["product_box"] = {"x": 0, "y": 0, "width": 1, "height": 1}

    rendered, _ = render_poster_billboard(
        template,
        product,
        record,
        build_poster_copy_plan([slot], source="template"),
    )

    with Image.open(io.BytesIO(rendered)) as output:
        assert output.getpixel((40, 700)) == (20, 130, 90)
        restored = output.getpixel((540, 180))
        assert restored[0] > 180 and restored[1] < 100 and restored[2] < 100


def test_full_canvas_layout_restores_multicolor_unchanged_title() -> None:
    template = Image.new("RGB", POSTER_SIZE, "white")
    draw = ImageDraw.Draw(template)
    # A neighbouring element crosses the padded crop border. It must not make
    # the dominant white background look non-uniform and disable extraction.
    draw.rectangle((200, 130, 800, 138), fill=(175, 175, 175))
    draw.rectangle((180, 160, 330, 260), fill=(242, 188, 42))
    draw.rectangle((350, 160, 900, 260), fill=(45, 45, 45))
    product = Image.new("RGB", (900, 1200), (20, 130, 90))
    slot = _slot(editable=True, source_text="4大产品服务")
    slot["box"] = {"x": 0.15, "y": 0.1, "width": 0.7, "height": 0.1}
    # OCR may infer one representative fill even when the authored title uses
    # more than one color. Unchanged text must still preserve every source color.
    slot["style"] = {**slot["style"], "fill": "#F2EBEB", "stroke": "#171717", "panel_fill": None}
    record = _record(template_type="layout_template", text_slots=[slot])
    record["product_box"] = {"x": 0, "y": 0, "width": 1, "height": 1}
    record["background_mode"] = "full_canvas"

    rendered, _ = render_poster_billboard(
        template,
        product,
        record,
        build_poster_copy_plan([slot], source="template"),
    )

    with Image.open(io.BytesIO(rendered)) as output:
        yellow = output.getpixel((240, 200))
        dark = output.getpixel((500, 200))
        assert yellow[0] > 200 and yellow[1] > 140 and yellow[2] < 100
        assert max(dark) < 80
        assert output.getpixel((500, 134)) == (20, 130, 90)


def test_changed_text_inside_product_area_does_not_create_background_patch():
    template = Image.new("RGB", POSTER_SIZE, (238, 228, 210))
    product = Image.new("RGB", (800, 1000), (20, 130, 90))
    slot = _slot(editable=True, source_text="OLD")
    slot["box"] = {"x": 0.2, "y": 0.32, "width": 0.6, "height": 0.08}
    slot["style"] = {**slot["style"], "panel_fill": None, "stroke": None}
    record = _record(template_type="layout_template", text_slots=[slot])

    rendered, _ = render_poster_billboard(
        template,
        product,
        record,
        build_poster_copy_plan([slot], title="NEW", source="manual"),
    )

    with Image.open(io.BytesIO(rendered)) as output:
        # A corner of the text erase box with no glyph must still be product green.
        assert output.getpixel((205, 465)) == (20, 130, 90)


def test_changed_text_crossing_product_edge_does_not_paint_white_strip_over_product():
    template = Image.new("RGB", POSTER_SIZE, (245, 240, 230))
    product = Image.new("RGB", (800, 1000), (20, 130, 90))
    slot = _slot(editable=True, source_text="4大产品服务")
    slot["box"] = {"x": 0.2, "y": 0.2, "width": 0.6, "height": 0.08}
    slot["style"] = {**slot["style"], "panel_fill": None, "stroke": None}
    record = _record(template_type="layout_template", text_slots=[slot])

    rendered, _ = render_poster_billboard(
        template,
        product,
        record,
        build_poster_copy_plan([slot], title="全案服务", source="content_asset"),
    )

    with Image.open(io.BytesIO(rendered)) as output:
        assert output.getpixel((205, 370)) == (20, 130, 90)


def test_product_cannot_be_moved_completely_outside_box():
    with pytest.raises(PosterBillboardError, match="移出展示区域"):
        render_poster_billboard(
            Image.new("RGBA", POSTER_SIZE, (0, 0, 0, 0)),
            Image.new("RGB", (200, 200), "blue"),
            _record(),
            build_poster_copy_plan([], source="template"),
            transform={"fit": "contain", "scale": 0.5, "x_offset": 0.5, "focal_x": 0},
        )


def test_image2_mask_locks_product_and_text_but_leaves_background_editable():
    record = _record(text_slots=[_slot()])

    data, has_editable = build_image2_protection_mask(record)

    with Image.open(io.BytesIO(data)) as mask:
        assert mask.getpixel((540, 800))[3] == 255
        assert mask.getpixel((540, 150))[3] == 255
        assert mask.getpixel((30, 400))[3] == 0
    assert has_editable is True


def test_quality_gate_rejects_text_overflow():
    report = evaluate_poster_quality(
        Image.new("RGB", POSTER_SIZE, "white"),
        _record(),
        {"overflow_count": 1},
    )

    assert report["passed"] is False
    assert "文字超出模板槽位" in report["failures"]


def test_deterministic_generation_rejects_multiple_identical_outputs():
    with pytest.raises(ValidationError, match="确定性大字报仅生成 1 张"):
        PosterGenerateCreate(
            poster_template_id="cpt_test",
            product_asset_id="cca_product",
            n=2,
            enhance_with_image2=False,
            idempotency_key="poster-test-123",
        )


def test_poster_template_display_uses_material_library_name_and_category():
    poster = SimpleNamespace(
        asset_id="cca_template",
        category="legacy-category",
        to_dict=lambda: {
            "id": "cpt_template",
            "asset_id": "cca_template",
            "name": "legacy-hash-name",
            "category": "legacy-category",
            "text_slots": [],
        },
    )
    library_item = SimpleNamespace(display_name="素材库模板名称", category="product_promotion")

    result = serialize_poster_template(poster, "产品推广", library_item)

    assert result["name"] == "素材库模板名称"
    assert result["category"] == "product_promotion"
    assert result["category_name"] == "产品推广"


@pytest.mark.asyncio
@pytest.mark.parametrize("product_role", ["source", "library_image"])
async def test_worker_runs_deterministic_poster_without_image2(
    monkeypatch: pytest.MonkeyPatch,
    product_role: str,
):
    product_asset = SimpleNamespace(id="cca_product", role=product_role)
    template_asset = SimpleNamespace(id="cca_template", role="poster_template")

    class FakeRepository:
        def __init__(self, db):
            del db

        async def get_asset_for_user(self, asset_id, owner_uid):
            assert owner_uid == "alice"
            return product_asset if asset_id == "cca_product" else template_asset

    @asynccontextmanager
    async def fake_session():
        yield object()

    async def download(asset):
        image = Image.new("RGBA", POSTER_SIZE, (0, 0, 0, 0))
        if asset.role == "poster_template":
            ImageDraw.Draw(image).rectangle((0, 0, 1079, 1439), outline=(240, 60, 20, 255), width=24)
        else:
            image = Image.new("RGB", (600, 600), (20, 100, 210))
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    async def no_event(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(content_cover_worker, "ContentCoverRepository", FakeRepository)
    monkeypatch.setattr(content_cover_worker.pg_manager, "get_async_session_context", fake_session)
    monkeypatch.setattr(content_cover_worker, "_download_asset", download)
    monkeypatch.setattr(content_cover_worker, "_check_cancelled", no_event)
    monkeypatch.setattr(content_cover_worker, "_set_job", no_event)
    job = SimpleNamespace(
        id="ccj-poster",
        owner_uid="alice",
        mode="poster_billboard",
        result_json={},
        request_json={
            "product_asset_id": "cca_product",
            "poster_template_snapshot": _record(),
            "copy_plan": build_poster_copy_plan([], source="template"),
            "transform": {"fit": "cover", "scale": 1},
            "n": 1,
            "enhance_with_image2": False,
        },
    )

    [rendered] = await content_cover_worker._run_poster_billboard(job)

    with Image.open(io.BytesIO(rendered)) as output:
        assert output.size == POSTER_SIZE
        assert output.getpixel((540, 800)) == (20, 100, 210)
    assert job.result_json["quality_reports"][0]["passed"] is True


@pytest.mark.asyncio
async def test_worker_image2_enhancement_uses_protection_mask_and_relocks_product(
    monkeypatch: pytest.MonkeyPatch,
):
    captured = {}
    product_asset = SimpleNamespace(id="cca_product", role="source")
    template_asset = SimpleNamespace(id="cca_template", role="poster_template")

    class FakeRepository:
        def __init__(self, db):
            del db

        async def get_asset_for_user(self, asset_id, owner_uid):
            assert owner_uid == "alice"
            return product_asset if asset_id == "cca_product" else template_asset

    class FakeClient:
        def __init__(self, config=None):
            del config

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback

        async def submit(self, request, *, idempotency_key=None):
            captured["request"] = request
            captured["idempotency_key"] = idempotency_key
            return Image2Submission(status="completed", images=[Image2Output(b64_data="enhanced")])

        async def read_output(self, output):
            assert output.b64_data == "enhanced"
            image = Image.new("RGB", POSTER_SIZE, (230, 210, 190))
            data = io.BytesIO()
            image.save(data, format="PNG")
            return data.getvalue(), "image/png"

    @asynccontextmanager
    async def fake_session():
        yield object()

    async def download(asset):
        if asset.role == "source":
            image = Image.new("RGB", (500, 700), (25, 115, 215))
        else:
            image = Image.new("RGBA", POSTER_SIZE, (0, 0, 0, 0))
            ImageDraw.Draw(image).rectangle((0, 0, 1079, 1439), outline=(245, 70, 20, 255), width=20)
        data = io.BytesIO()
        image.save(data, format="PNG")
        return data.getvalue()

    async def no_event(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(content_cover_worker, "ContentCoverRepository", FakeRepository)
    monkeypatch.setattr(content_cover_worker.pg_manager, "get_async_session_context", fake_session)
    monkeypatch.setattr(content_cover_worker, "Image2Client", FakeClient)
    monkeypatch.setattr(content_cover_worker, "_load_image2_config", no_event)
    monkeypatch.setattr(content_cover_worker, "_download_asset", download)
    monkeypatch.setattr(content_cover_worker, "_check_cancelled", no_event)
    monkeypatch.setattr(content_cover_worker, "_set_job", no_event)
    monkeypatch.setattr(content_cover_worker, "_emit", no_event)
    job = SimpleNamespace(
        id="ccj-poster-image2",
        owner_uid="alice",
        mode="poster_billboard",
        provider_task_id=None,
        result_json={},
        request_json={
            "product_asset_id": "cca_product",
            "poster_template_snapshot": _record(),
            "copy_plan": build_poster_copy_plan([], source="template"),
            "transform": {"fit": "cover", "scale": 1},
            "n": 1,
            "enhance_with_image2": True,
        },
    )

    [rendered] = await content_cover_worker._run_poster_billboard(job)

    assert captured["request"].mode == "mask"
    with Image.open(io.BytesIO(captured["request"].mask_image.data)) as mask:
        assert mask.getpixel((540, 800))[3] == 255
        assert mask.getpixel((20, 400))[3] == 0
    with Image.open(io.BytesIO(rendered)) as output:
        assert output.getpixel((540, 800)) == (25, 115, 215)
        assert output.getpixel((20, 400)) == (230, 210, 190)
