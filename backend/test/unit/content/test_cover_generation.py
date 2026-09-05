from __future__ import annotations

import base64
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image, ImageDraw
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.content_cover.image2_client import Image2Client, Image2Config, Image2Error, image2_is_configured
import yuxi.content_cover.renderer as content_cover_renderer
import yuxi.content_cover.image2_settings as image2_settings
from yuxi.content_cover.renderer import (
    apply_template_title,
    finalize_template_transfer,
    render_cover,
)
from yuxi.content_cover.schemas import (
    CoverGenerateCreate,
    Image2CapabilityProfile,
    Image2GlobalConfigUpdate,
    Image2Input,
    Image2Output,
    Image2Request,
    Image2Submission,
    TemplateReplicatePlanCreate,
)
from yuxi.content_cover.templates import COVER_TEMPLATES
from yuxi.content_cover.template_replication import (
    apply_layout_overrides,
    analyze_template,
    build_copy_plan,
    build_edit_mask,
    ensure_clean_source,
    evaluate_quality,
    render_template_replication,
    _wrap,
)
from yuxi.content.schemas import XiaohongshuDistributionCreate
from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.services.content_cover_service import (
    _linked_content_title,
    _normalize_upload,
    _template_texts,
)
import yuxi.services.content_cover_worker as content_cover_worker
import yuxi.services.xiaohongshu_service as xiaohongshu_service
from yuxi.storage.postgres.models_content import ContentCoverJob


def _image(color: str, size: tuple[int, int] = (320, 240)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def test_hycanvas_output_does_not_add_generic_title_overlay():
    hycanvas_job = SimpleNamespace(mode="hycanvas", request_json={"title": "89㎡收纳逆袭"})
    generated_job = SimpleNamespace(mode="generate", request_json={"title": "89㎡收纳逆袭"})

    assert content_cover_worker._output_title_overlay(hycanvas_job) == ""
    assert content_cover_worker._output_title_overlay(generated_job) == "89㎡收纳逆袭"


def _template_analysis_fixture():
    template = Image.new("RGB", (1080, 1440), "#E8E2DC")
    draw = ImageDraw.Draw(template)
    draw.text((90, 120), "装修避坑", fill="white", font=content_cover_renderer._font(112, bold=True))
    draw.rounded_rectangle((90, 280, 880, 350), radius=24, fill="#706B68")
    draw.text((120, 292), "设计定制/环保定制", fill="#FFF1C7", font=content_cover_renderer._font(42))
    blocks = [
        {"text": "装修避坑", "box": (90, 120, 690, 235)},
        {"text": "设计定制/环保定制", "box": (120, 292, 820, 338)},
    ]
    return template, analyze_template(template, target_size=(1080, 1440), ocr_blocks=blocks)


def test_template_replication_analysis_and_copy_plan_keep_slot_budgets():
    _, analysis = _template_analysis_fixture()
    plan = build_copy_plan(
        analysis,
        title="这是一段明显超过模板标题槽位预算的内容资产标题需要被压缩",
        subtitle="五大装修服务一次讲清",
        source="content_asset",
    )

    assert analysis.processing_version == "template-replication-v2"
    assert [slot.role for slot in analysis.text_slots] == ["title", "subtitle"]
    assert all(len(item.text) <= item.max_chars for item in plan.slots)
    assert plan.slots[0].changed is True


def test_template_replication_plan_rejects_non_portrait_output():
    with pytest.raises(ValidationError):
        TemplateReplicatePlanCreate(
            template_asset_id="template-asset",
            source_asset_id="source-asset",
            size="1080x1080",
        )


def test_template_replication_title_wrap_avoids_orphan_character_line():
    canvas = Image.new("RGB", (1080, 1440), "white")
    draw = ImageDraw.Draw(canvas)
    font = content_cover_renderer._font(100, bold=True)
    text = "专业服务·干货教育"
    width = draw.textbbox((0, 0), text[:7], font=font)[2]

    lines = _wrap(draw, text, font, width, 2).splitlines()

    assert len(lines) == 2
    assert min(map(len, lines)) >= 2
    assert abs(len(lines[0]) - len(lines[1])) <= 1
    assert lines[1][0] not in "·，。！？；：、,.!?;:"


def test_template_replication_layout_micro_adjustment_is_bounded():
    _, analysis = _template_analysis_fixture()
    original = analysis.text_slots[0]
    adjusted = apply_layout_overrides(
        analysis,
        {original.id: {"x_offset": 0.02, "y_offset": -0.02, "font_scale": 1.15}},
    )

    slot = adjusted.text_slots[0]
    assert slot.box.x == pytest.approx(original.box.x + 0.02)
    assert slot.box.y == pytest.approx(original.box.y - 0.02)
    assert slot.style.font_size_ratio == pytest.approx(original.style.font_size_ratio * 1.15)
    assert adjusted.layout_fingerprint != analysis.layout_fingerprint
    with pytest.raises(ValidationError):
        apply_layout_overrides(analysis, {original.id: {"x_offset": 0.021}})


def test_template_replication_mask_only_opens_declared_overlay_regions():
    _, analysis = _template_analysis_fixture()
    with Image.open(io.BytesIO(build_edit_mask(analysis))) as mask:
        assert mask.mode == "RGBA"
        assert mask.getpixel((5, 5))[3] == 255
        title = analysis.editable_regions[0]
        center = (
            round((title.x + title.width / 2) * mask.width),
            round((title.y + title.height / 2) * mask.height),
        )
        assert mask.getpixel(center)[3] == 0


def test_template_replication_rejects_a_generated_cover_as_source():
    _, analysis = _template_analysis_fixture()

    with pytest.raises(ValueError, match="干净原片"):
        ensure_clean_source(
            Image.new("RGB", (1080, 1440), "#FFFFFF"),
            analysis,
            recognized_text="".join(slot.source_text for slot in analysis.text_slots),
        )


def test_template_replication_renders_on_locked_source_and_passes_quality_gate():
    template, analysis = _template_analysis_fixture()
    source = Image.new("RGB", (1080, 1440), "#36788D")
    plan = build_copy_plan(
        analysis,
        title="装修获客增长",
        subtitle="五大服务一次讲清",
        source="content_asset",
    )

    rendered, overflow_count = render_template_replication(source, template, analysis, plan)
    with Image.open(io.BytesIO(rendered)) as output:
        output.load()
        report = evaluate_quality(
            source,
            output,
            analysis,
            plan,
            overflow_count=overflow_count,
            recognized_text="".join(item.text for item in plan.slots),
        )
        assert output.size == (1080, 1440)
        assert output.format == "PNG"
        assert output.getpixel((10, 10)) == source.getpixel((10, 10))
        assert report.locked_ssim >= 0.98
        assert report.ocr_accuracy == 1
        assert report.passed is True


def test_quality_gate_only_counts_checkerboard_introduced_by_generation():
    template, analysis = _template_analysis_fixture()
    source = Image.new("RGB", (1080, 1440), "#36788D")
    source_draw = ImageDraw.Draw(source)
    for y in range(1100, 1180, 10):
        for x in range(800, 880, 10):
            source_draw.rectangle(
                (x, y, x + 9, y + 9),
                fill="#CCCCCC" if (x + y) // 10 % 2 else "#EEEEEE",
            )
    plan = build_copy_plan(analysis, source="template")
    rendered, overflow_count = render_template_replication(source, template, analysis, plan)

    with Image.open(io.BytesIO(rendered)) as output:
        report = evaluate_quality(
            source,
            output,
            analysis,
            plan,
            overflow_count=overflow_count,
            recognized_text={item.slot_id: item.text for item in plan.slots},
        )

    assert report.mosaic_count == 0
    assert report.passed is True


def test_quality_gate_detects_old_copy_residue_in_a_changed_slot():
    template, analysis = _template_analysis_fixture()
    source = Image.new("RGB", (1080, 1440), "#36788D")
    plan = build_copy_plan(analysis, title="NEW TITLE", source="content_asset")
    rendered, overflow_count = render_template_replication(source, template, analysis, plan)
    recognized = {item.slot_id: item.text for item in plan.slots}
    changed = next(item for item in plan.slots if item.changed)
    recognized[changed.slot_id] += changed.source_text

    with Image.open(io.BytesIO(rendered)) as output:
        report = evaluate_quality(
            source,
            output,
            analysis,
            plan,
            overflow_count=overflow_count,
            recognized_text=recognized,
        )

    assert report.residual_text_count == 1
    assert report.passed is False


def test_template_reference_is_normalized_to_the_edit_mask_size():
    reference = content_cover_worker._compact_template_reference(
        _image("#336699", (768, 1024)),
        "source.png",
        target_size=(1080, 1440),
    )

    with Image.open(io.BytesIO(reference.data)) as normalized:
        assert normalized.size == (1080, 1440)
        assert normalized.format == "PNG"


def test_image2_output_is_consumed_only_inside_detected_decoration_regions():
    template = Image.new("RGB", (1080, 1440), "#E8E2DC")
    ImageDraw.Draw(template).ellipse((910, 60, 990, 140), fill="#FF5A20")
    analysis = analyze_template(
        template,
        target_size=(1080, 1440),
        ocr_blocks=[{"text": "模板标题", "box": (90, 170, 720, 280)}],
    )
    assert analysis.decoration_regions
    source = Image.new("RGB", (1080, 1440), "#36788D")
    generated = Image.new("RGB", (1080, 1440), "#35A853")
    plan = build_copy_plan(analysis, source="template")

    rendered, _ = render_template_replication(
        source,
        template,
        analysis,
        plan,
        generated=generated,
    )

    with Image.open(io.BytesIO(rendered)) as output:
        assert output.getpixel((950, 100)) != source.getpixel((950, 100))
        assert output.getpixel((20, 1000)) == source.getpixel((20, 1000))


@pytest.fixture(autouse=True)
def stub_worker_image2_global_config(monkeypatch: pytest.MonkeyPatch):
    async def load_config(owner_uid):
        del owner_uid
        return Image2Config.from_values(
            base_url="https://relay.example.com/v1",
            api_key="test-key",
            model="image2-test",
        )

    monkeypatch.setattr(content_cover_worker, "_load_image2_config", load_config)


def test_image2_configuration_rejects_invalid_status_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("IMAGE2_BASE_URL", "https://relay.example.com/v1")
    monkeypatch.setenv("IMAGE2_API_KEY", "test-key")
    monkeypatch.setenv("IMAGE2_MODEL", "image2-test")
    monkeypatch.setenv("IMAGE2_STATUS_PATH", "/images/generations/status")

    assert image2_is_configured() is False
    with pytest.raises(Image2Error) as exc_info:
        Image2Config.from_env()
    assert exc_info.value.code == "IMAGE2_CONFIG_INVALID"


def test_global_image2_config_normalizes_values():
    payload = Image2GlobalConfigUpdate(
        base_url=" https://relay.example.com/v1 ",
        api_key=" request-secret ",
        model=" gpt-image-2 ",
    )

    assert payload.base_url == "https://relay.example.com/v1"
    assert payload.api_key == "request-secret"
    assert payload.model == "gpt-image-2"


@pytest.mark.asyncio
async def test_global_image2_config_preserves_saved_key_and_never_returns_it(
    monkeypatch: pytest.MonkeyPatch,
):
    captured = {}
    setting = SimpleNamespace(
        base_url="https://old-relay.example.com/v1",
        api_key="saved-secret",
        model="gpt-image-2",
    )

    class FakeDb:
        async def commit(self):
            captured["committed"] = True

    class FakeRepo:
        def __init__(self, db):
            del db

        async def get_image2_setting(self, owner_uid, *, for_update=False):
            assert owner_uid == "alice"
            del for_update
            return setting

        async def upsert_image2_setting(self, **values):
            captured["values"] = values
            setting.base_url = values["base_url"]
            setting.api_key = values["api_key"]
            setting.model = values["model"]
            return setting

    monkeypatch.setattr(image2_settings, "ContentCoverRepository", FakeRepo)

    db = FakeDb()
    await image2_settings.save_image2_config(
        db,
        base_url="https://new-relay.example.com/v1",
        api_key=None,
        owner_uid="alice",
    )
    state = await image2_settings.get_image2_config_state(db, owner_uid="alice")

    assert captured["values"]["api_key"] == "saved-secret"
    assert captured["committed"] is True
    assert state["base_url"] == "https://new-relay.example.com/v1"
    assert state["api_key_configured"] is True
    assert "api_key" not in state


@pytest.mark.asyncio
async def test_global_image2_config_reuses_environment_key_when_database_setting_is_absent(
    monkeypatch: pytest.MonkeyPatch,
):
    captured = {}

    class FakeDb:
        async def commit(self):
            captured["committed"] = True

    class FakeRepo:
        def __init__(self, db):
            del db

        async def get_image2_setting(self, owner_uid, *, for_update=False):
            assert owner_uid == "alice"
            del for_update
            return None

        async def upsert_image2_setting(self, **values):
            captured["values"] = values
            return SimpleNamespace(**values)

    monkeypatch.setenv("IMAGE2_API_KEY", "environment-secret")
    monkeypatch.setattr(image2_settings, "ContentCoverRepository", FakeRepo)

    await image2_settings.save_image2_config(
        FakeDb(),
        base_url="https://relay.example.com/v1",
        api_key=None,
        owner_uid="alice",
    )

    assert captured["values"]["api_key"] == "environment-secret"
    assert captured["committed"] is True


@pytest.mark.parametrize(
    ("template_id", "count"),
    [
        ("grid_3x3", 9),
        ("split_vertical", 2),
        ("split_horizontal", 2),
        ("before_after", 2),
        ("card_stack", 4),
        ("hero_thumbs", 5),
    ],
)
def test_all_declared_cover_templates_render_expected_dimensions(template_id: str, count: int):
    colors = ["#D64343", "#3F65C6", "#49A86B", "#E1A737", "#8A55B5", "#37A7A7", "#B96D3C", "#67707A", "#D8689A"]

    result = render_cover(
        [_image(colors[index]) for index in range(count)],
        template_id=template_id,
        theme_id="editorial_ink",
        size="1080x1440",
        layout={"title": "测试封面", "gap": 20, "margin": 24},
    )

    with Image.open(io.BytesIO(result)) as rendered:
        assert rendered.format == "PNG"
        assert rendered.size == (1080, 1440)
    assert set(COVER_TEMPLATES) >= {template_id}


def test_multi_reference_accepts_two_sources_without_template():
    payload = CoverGenerateCreate(
        mode="multi_reference",
        source_asset_ids=["source-1", "source-2"],
        prompt="参考两张图片生成封面",
        idempotency_key="request-1234",
    )

    assert payload.template_asset_id is None


def test_mask_generation_requires_one_source_and_mask():
    with pytest.raises(ValidationError):
        CoverGenerateCreate(
            mode="mask",
            source_asset_ids=["source-1"],
            prompt="局部优化",
            idempotency_key="request-1234",
        )


def test_generation_title_is_limited_to_sixty_characters():
    with pytest.raises(ValidationError):
        CoverGenerateCreate(
            mode="text_to_image",
            title="封" * 61,
            prompt="生成封面",
            idempotency_key="request-1234",
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "mode": "text_to_image",
            "source_asset_ids": ["source-1"],
            "prompt": "生成封面",
        },
        {
            "mode": "image_to_image",
            "source_asset_ids": ["source-1"],
            "template_asset_id": "template-1",
            "prompt": "优化封面",
        },
        {
            "mode": "multi_reference",
            "source_asset_ids": ["source-1", "source-2"],
            "mask_asset_id": "mask-1",
            "prompt": "参考生成",
        },
        {
            "mode": "mask",
            "source_asset_ids": ["source-1"],
            "template_asset_id": "template-1",
            "mask_asset_id": "mask-1",
            "prompt": "局部优化",
        },
    ],
)
def test_generation_modes_reject_conflicting_inputs(payload):
    with pytest.raises(ValidationError):
        CoverGenerateCreate(**payload, idempotency_key="request-1234")


def test_mask_upload_preserves_alpha_channel():
    source = io.BytesIO()
    image = Image.new("RGBA", (16, 16), (255, 255, 255, 255))
    image.putpixel((0, 0), (0, 0, 0, 0))
    image.save(source, format="PNG")

    normalized, width, height, content_type = _normalize_upload(source.getvalue(), "mask")

    with Image.open(io.BytesIO(normalized)) as result:
        assert result.mode == "RGBA"
        assert result.getpixel((0, 0))[3] == 0
    assert (width, height, content_type) == (16, 16, "image/png")


def test_cover_upload_rejects_unsupported_decodable_format():
    source = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(source, format="GIF")

    with pytest.raises(HTTPException) as exc_info:
        _normalize_upload(source.getvalue(), "source")

    assert exc_info.value.detail["error"]["code"] == "COVER_IMAGE_FORMAT_UNSUPPORTED"


def test_template_replica_requires_exactly_one_source():
    with pytest.raises(ValidationError):
        CoverGenerateCreate(
            mode="multi_reference",
            source_asset_ids=["source-1", "source-2"],
            template_asset_id="template-1",
            prompt="复刻模板",
            idempotency_key="request-1234",
        )


def test_template_texts_are_derived_from_linked_content_asset():
    artifact = SimpleNamespace(
        body="# 开头\n本文通过封面模板帮助装修企业提升客户转化。后续内容不应进入副标题",
        topics=["#家居", "装修获客", "签单转化", "多余话题"],
    )

    texts = _template_texts(artifact, "装修获客转化的完整方法与执行步骤")

    assert texts == {
        "title": "装修获客转化的完整方法与执行",
        "subtitle": "本文通过封面模板帮助装修企业提升客户转化",
        "tags": ["家居", "装修获客", "签单转化"],
        "preserve_fixed_copy": True,
        "source": "content_asset",
    }


def test_template_texts_mark_linked_task_title_as_content_asset_without_artifact():
    texts = _template_texts(None, "专业服务 · 干货教育", source="content_asset")

    assert texts["title"] == "专业服务·干货教育"
    assert texts["source"] == "content_asset"


def test_linked_content_title_falls_back_to_selected_task_title_or_name():
    task = SimpleNamespace(selected_title_json={"title": "内容资产标题"}, name="专业服务")
    assert _linked_content_title(SimpleNamespace(title=""), task) == "内容资产标题"
    task.selected_title_json = {}
    assert _linked_content_title(None, task) == "专业服务"


def test_template_transfer_keeps_generated_full_canvas_instead_of_template_background(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(content_cover_renderer, "_extract_template_text_blocks", lambda image: [])
    final = finalize_template_transfer(
        _image("#225533", (100, 100)),
        _image("#2244AA", (100, 100)),
        _image("#EE4422", (100, 100)),
        target_size=(100, 100),
        title="",
    )

    with Image.open(io.BytesIO(final)) as result:
        assert result.mode == "RGB"
        assert result.getpixel((5, 5)) == (238, 68, 34)
        assert result.getpixel((50, 50)) == (238, 68, 34)


def test_template_transfer_rejects_missing_reference_placeholder(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        content_cover_renderer,
        "_extract_template_text_blocks",
        lambda image: [{"text": "缺少参考图1和参考图2", "box": [[10, 10], [90, 10], [90, 30], [10, 30]]}],
    )

    with pytest.raises(content_cover_renderer.CoverRenderError, match="未接收到模板复刻参考图"):
        finalize_template_transfer(
            _image("#225533", (100, 100)),
            _image("#2244AA", (100, 100)),
            _image("#EE4422", (100, 100)),
            target_size=(100, 100),
            title="",
        )


def test_template_transfer_rejects_output_that_drops_source_subject(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(content_cover_renderer, "_extract_template_text_blocks", lambda image: [])
    source = Image.new("RGB", (400, 400), "white")
    generated = Image.new("RGB", (400, 400), "#ECE2D7")
    source_draw = ImageDraw.Draw(source)
    generated_draw = ImageDraw.Draw(generated)
    for offset in range(20, 380, 40):
        source_draw.rectangle((offset, 20, offset + 14, 380), fill="#17324D")
        source_draw.ellipse((20, offset, 380, offset + 18), outline="#D9473F", width=5)
        generated_draw.line((0, offset, 400, 400 - offset), fill="#2E6B55", width=9)
    source_output = io.BytesIO()
    generated_output = io.BytesIO()
    source.save(source_output, format="PNG")
    generated.save(generated_output, format="PNG")

    with pytest.raises(content_cover_renderer.CoverRenderError, match="未保留原图主体"):
        finalize_template_transfer(
            _image("#225533", (400, 400)),
            source_output.getvalue(),
            generated_output.getvalue(),
            target_size=(400, 400),
            title="",
        )


def test_worker_normalizes_model_result_to_requested_cover_size():
    normalized, width, height = content_cover_worker._normalize_output(
        _image("#52677A", (640, 480)),
        target_size=(1080, 1440),
    )

    with Image.open(io.BytesIO(normalized)) as result:
        assert result.size == (1080, 1440)
        assert result.format == "PNG"
    assert (width, height) == (1080, 1440)


def test_worker_adds_exact_title_overlay_after_model_output():
    source = _image("#52677A", (640, 480))
    without_title, _, _ = content_cover_worker._normalize_output(
        source,
        target_size=(1080, 1440),
    )
    with_title, width, height = content_cover_worker._normalize_output(
        source,
        target_size=(1080, 1440),
        title="内容生产新方式",
    )

    assert with_title != without_title
    with Image.open(io.BytesIO(with_title)) as result:
        assert result.size == (1080, 1440)
        assert result.format == "PNG"
    assert (width, height) == (1080, 1440)


def test_template_title_replaces_detected_text_in_place(monkeypatch: pytest.MonkeyPatch):
    source = _image("#52677A", (640, 480))
    monkeypatch.setattr(
        content_cover_renderer,
        "_extract_template_text_blocks",
        lambda image: [{"text": "OLD TITLE", "box": [[100, 80], [540, 80], [540, 220], [100, 220]]}],
    )

    normalized = apply_template_title(Image.open(io.BytesIO(source)), "内容生产新方式")

    assert normalized.size == (640, 480)
    output = io.BytesIO()
    normalized.save(output, format="PNG")
    assert output.getvalue() != source


def test_template_transfer_replaces_title_without_generic_panel(monkeypatch: pytest.MonkeyPatch):
    source = _image("#52677A", (640, 480))
    monkeypatch.setattr(
        content_cover_renderer,
        "_extract_template_text_blocks",
        lambda image: [{"text": "OLD TITLE", "box": [[100, 80], [540, 80], [540, 220], [100, 220]]}],
    )
    normalized = finalize_template_transfer(
        source,
        source,
        source,
        target_size=(640, 480),
        title="内容生产新方式",
    )

    assert normalized != source


def test_stacked_template_transfer_locks_source_and_rebuilds_overlay_layers():
    size = (300, 400)
    source = Image.new("RGBA", size, "#315A8C")
    source_draw = ImageDraw.Draw(source)
    source_draw.rectangle((20, 90, 280, 360), fill="#E8E1D5", outline="#17212B", width=3)
    source_draw.line((20, 210, 280, 210), fill="#17212B", width=3)
    template = Image.new("RGBA", size, "#C7C2BA")
    template_draw = ImageDraw.Draw(template)
    template_draw.rectangle((22, 20, 95, 42), fill="#FFD21C")
    template_draw.rectangle((205, 20, 278, 42), fill="#FFD21C")
    for left in (42, 92, 142, 192):
        template_draw.rectangle((left, 88, left + 34, 144), fill="white")
    template_draw.rounded_rectangle((18, 184, 282, 226), radius=10, fill="#B9B5AE")
    for left in range(34, 254, 36):
        template_draw.rectangle((left, 195, left + 22, 215), fill="#FFF2C8")
    template_draw.rounded_rectangle((26, 342, 274, 390), radius=12, fill="#62605D")
    for left in range(42, 244, 40):
        template_draw.rectangle((left, 353, left + 25, 378), fill="white")
    template_draw.rectangle((72, 380, 96, 384), fill="white")
    generated = source.copy()
    template_blocks = [
        {"text": "TOP LEFT", "box": (22, 20, 95, 42)},
        {"text": "TOP RIGHT", "box": (205, 20, 278, 42)},
        {"text": "MAIN TITLE", "box": (35, 80, 265, 150)},
        {"text": "A/B/C/D/E", "box": (25, 190, 275, 220)},
        {"text": "BOTTOM SLOGAN", "box": (35, 350, 265, 382)},
    ]

    result = content_cover_renderer._stacked_poster_overlay(
        source,
        source,
        generated,
        template,
        template_blocks,
        [],
        title="",
        subtitle="",
    )

    assert result is not None
    assert result.size == size
    assert result.getpixel((150, 245))[:3] == source.getpixel((150, 245))[:3]
    assert result.getpixel((45, 30))[:3] == template.getpixel((45, 30))[:3]
    assert result.getpixel((150, 60))[:3] == source.getpixel((150, 60))[:3]
    assert result.getpixel((112, 136))[:3] == (255, 255, 255)
    assert max(result.getpixel((x, y))[0] for x in range(70, 99) for y in range(378, 384)) > 240
    assert result.getpixel((150, 368))[0] < 130


def _client(handler, *, resolver=None) -> Image2Client:
    async def public_resolver(host: str, port: int) -> list[str]:
        assert host
        assert port in {80, 443}
        return ["93.184.216.34"]

    return Image2Client(
        Image2Config(
            base_url="https://relay.example.com/v1",
            api_key="test-key",
            model="image2-test",
        ),
        transport=httpx.MockTransport(handler),
        resolver=resolver or public_resolver,
    )


@pytest.mark.asyncio
async def test_gpt_image_2_generation_payload_uses_supported_contract_only():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        encoded = base64.b64encode(_image("#123456")).decode()
        return httpx.Response(200, json={"data": [{"b64_json": encoded}]})

    request = Image2Request(
        mode="text_to_image",
        prompt="生成小红书封面",
        size="1080x1440",
        extra={
            "strength": 0.7,
            "quality": "low",
            "input_fidelity": "low",
            "output_format": "jpeg",
            "model": "must-not-override",
            "size": "1x1",
        },
    )
    client = Image2Client(
        Image2Config(base_url="https://relay.example.com/v1", api_key="test-key", model="gpt-image-2"),
        transport=httpx.MockTransport(handler),
    )
    async with client:
        result = await client.submit(request)
        raw, content_type = await client.read_output(result.images[0])

    assert result.status == "completed"
    assert captured["model"] == "gpt-image-2"
    assert captured["size"] == "1024x1536"
    assert "images" not in captured
    assert "image" not in captured
    assert "mask" not in captured
    assert "strength" not in captured
    assert captured["quality"] == "high"
    assert "input_fidelity" not in captured
    assert captured["output_format"] == "png"
    assert content_type == "image/png"
    assert raw.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_template_replicate_places_primary_source_before_style_reference():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["content_type"] = request.headers["content-type"]
        captured["body"] = request.content
        encoded = base64.b64encode(_image("#123456")).decode()
        return httpx.Response(200, json={"data": [{"b64_json": encoded}]})

    source = Image2Input(data=_image("#FFFFFF"), content_type="image/png", file_name="source.png")
    template = Image2Input(data=_image("#000000"), content_type="image/png", file_name="template.png")
    request = Image2Request(
        mode="multi_reference",
        prompt="严格保留模板版式，仅替换底图",
        size="1080x1440",
        source_images=[source],
        template_image=template,
        extra={"template_replicate": True},
    )

    async with _client(handler) as client:
        result = await client.submit(request)

    assert result.status == "completed"
    assert captured["path"].endswith("/images/edits")
    assert captured["content_type"].startswith("multipart/form-data; boundary=")
    assert captured["body"].index(b'filename="source.png"') < captured["body"].index(b'filename="template.png"')
    assert b'name="image[]"' in captured["body"]
    assert b'name="quality"' in captured["body"]
    assert b'name="input_fidelity"' not in captured["body"]
    assert b'name="output_format"' in captured["body"]
    assert b"high" in captured["body"]
    assert b"png" in captured["body"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["image_to_image", "multi_reference", "mask"])
async def test_all_image_edit_modes_use_multipart_edit_endpoint(mode: str):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content
        encoded = base64.b64encode(_image("#123456")).decode()
        return httpx.Response(200, json={"data": [{"b64_json": encoded}]})

    source = Image2Input(data=_image("#FFFFFF"), content_type="image/png", file_name="source.png")
    template = Image2Input(data=_image("#000000"), content_type="image/png", file_name="template.png")
    request = Image2Request(
        mode=mode,
        prompt="编辑封面",
        size="1080x1440",
        source_images=[source],
        template_image=template if mode == "multi_reference" else None,
        mask_image=template if mode == "mask" else None,
    )

    async with _client(handler) as client:
        await client.submit(request)

    assert captured["path"].endswith("/images/edits")
    assert b'filename="source.png"' in captured["body"]
    assert (b'filename="template.png"' in captured["body"]) is (mode in {"multi_reference", "mask"})
    assert b'name="input_fidelity"' not in captured["body"]


@pytest.mark.asyncio
async def test_siliconflow_image_edit_uses_generation_json_contract():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["content_type"] = request.headers["content-type"]
        captured["payload"] = json.loads(request.content)
        encoded = base64.b64encode(_image("#123456")).decode()
        return httpx.Response(200, json={"images": [{"b64_json": encoded}]})

    source = Image2Input(data=_image("#FFFFFF"), content_type="image/png", file_name="source.png")
    config = Image2Config(
        base_url="https://api.siliconflow.cn/v1",
        api_key="test-key",
        model="Qwen/Qwen-Image",
        edit_path="/images/generations",
        edit_model="Qwen/Qwen-Image-Edit-2509",
        edit_request_format="siliconflow_json",
    )
    client = Image2Client(config, transport=httpx.MockTransport(handler))

    async with client:
        result = await client.submit(
            Image2Request(
                mode="image_to_image",
                prompt="保留主体并优化构图",
                negative_prompt="文字和水印",
                size="1080x1440",
                source_images=[source],
            )
        )

    assert result.status == "completed"
    assert captured["path"] == "/v1/images/generations"
    assert captured["content_type"] == "application/json"
    assert captured["payload"]["model"] == "Qwen/Qwen-Image-Edit-2509"
    assert captured["payload"]["image"].startswith("data:image/png;base64,")
    assert captured["payload"]["negative_prompt"] == "文字和水印"
    assert "size" not in captured["payload"]


@pytest.mark.asyncio
async def test_image2_capability_probe_reports_model_contract_without_paid_generation():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"data": [{"id": "gpt-image-2"}]})

    client = Image2Client(
        Image2Config(base_url="https://relay.example.com/v1", api_key="test-key", model="gpt-image-2"),
        transport=httpx.MockTransport(handler),
    )
    async with client:
        profile = await client.probe_capabilities()

    assert isinstance(profile, Image2CapabilityProfile)
    assert calls == [("GET", "/v1/models")]
    assert profile.model_discovered is True
    assert profile.supports_edit is True
    assert "input_fidelity" in profile.unsupported_parameters


@pytest.mark.asyncio
async def test_image2_async_submission_can_be_polled_to_completion():
    calls = []
    encoded = base64.b64encode(_image("#ABCDEF")).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(202, json={"task_id": "upstream-1", "status": "queued"})
        return httpx.Response(
            200,
            json={"task_id": "upstream-1", "status": "succeeded", "data": [{"b64_json": encoded}]},
        )

    async with _client(handler) as client:
        submitted = await client.submit(Image2Request(mode="text_to_image", prompt="封面", size="1080x1440"))
        completed = await client.poll(submitted.provider_task_id)

    assert submitted.status == "pending"
    assert completed.status == "completed"
    assert len(completed.images) == 1
    assert calls == [
        ("POST", "/v1/images/generations"),
        ("GET", "/v1/images/generations/upstream-1"),
    ]


@pytest.mark.asyncio
async def test_image2_understands_nested_async_relay_response():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"data": {"taskId": "nested-task", "state": "processing"}})

    async with _client(handler) as client:
        submitted = await client.submit(Image2Request(mode="text_to_image", prompt="封面", size="1080x1440"))

    assert submitted.provider_task_id == "nested-task"
    assert submitted.status == "pending"


@pytest.mark.asyncio
async def test_image2_understands_plain_base64_response():
    encoded = base64.b64encode(_image("#52677A")).decode()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": encoded})

    async with _client(handler) as client:
        submitted = await client.submit(Image2Request(mode="text_to_image", prompt="封面", size="1080x1440"))
        raw, content_type = await client.read_output(submitted.images[0])

    assert submitted.status == "completed"
    assert raw.startswith(b"\x89PNG")
    assert content_type == "image/png"


@pytest.mark.asyncio
async def test_image2_rejects_private_result_url():
    async with _client(lambda _: httpx.Response(200, json={})) as client:
        with pytest.raises(Image2Error) as exc_info:
            await client.read_output(Image2Output(url="http://127.0.0.1/private.png"))

    assert exc_info.value.code == "IMAGE2_OUTPUT_URL_INVALID"


@pytest.mark.asyncio
async def test_image2_allows_admin_configured_relay_origin_for_result_download():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_image("#223344"), headers={"content-type": "image/png"})

    client = Image2Client(
        Image2Config(
            base_url="http://127.0.0.1:8080/v1",
            api_key="test-key",
            model="image2-test",
        ),
        transport=httpx.MockTransport(handler),
    )
    async with client:
        data, _ = await client.read_output(Image2Output(url="http://127.0.0.1:8080/files/result.png"))

    assert data.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_image2_allows_admin_configured_output_origin_with_proxy_dns():
    seen_headers = {}

    async def proxy_resolver(host: str, port: int) -> list[str]:
        assert (host, port) == ("s3.siliconflow.cn", 443)
        return ["198.18.0.39"]

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(
            200,
            content=_image("#223344"),
            headers={"content-type": "application/octet-stream"},
        )

    client = Image2Client(
        Image2Config(
            base_url="https://api.siliconflow.cn/v1",
            api_key="test-key",
            model="Qwen/Qwen-Image",
            trusted_output_origins=("https://s3.siliconflow.cn",),
        ),
        transport=httpx.MockTransport(handler),
        resolver=proxy_resolver,
    )
    async with client:
        data, content_type = await client.read_output(Image2Output(url="https://s3.siliconflow.cn/outputs/result.png"))

    assert data.startswith(b"\x89PNG")
    assert content_type == "application/octet-stream"
    assert seen_headers["authorization"] == "Bearer test-key"


@pytest.mark.asyncio
async def test_image2_rejects_hostname_that_resolves_to_private_network():
    async def private_resolver(host: str, port: int) -> list[str]:
        assert (host, port) == ("cdn.example.com", 443)
        return ["10.0.0.8"]

    async with _client(
        lambda _: pytest.fail("private output URL must not be requested"),
        resolver=private_resolver,
    ) as client:
        with pytest.raises(Image2Error) as exc_info:
            await client.read_output(Image2Output(url="https://cdn.example.com/result.png"))

    assert exc_info.value.code == "IMAGE2_OUTPUT_URL_INVALID"


@pytest.mark.asyncio
async def test_image2_does_not_send_api_key_to_result_host():
    seen_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, content=_image("#223344"), headers={"content-type": "image/png"})

    async with _client(handler) as client:
        data, content_type = await client.read_output(Image2Output(url="https://cdn.example.com/result.png"))

    assert "authorization" not in seen_headers
    assert seen_headers["accept"] == "image/*"
    assert content_type == "image/png"
    assert data.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_image2_revalidates_redirect_target_before_following():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private.png"})

    async with _client(handler) as client:
        with pytest.raises(Image2Error) as exc_info:
            await client.read_output(Image2Output(url="https://cdn.example.com/result.png"))

    assert exc_info.value.code == "IMAGE2_OUTPUT_URL_INVALID"
    assert calls == ["https://cdn.example.com/result.png"]


@pytest.mark.asyncio
async def test_image2_retries_rate_limit_with_same_idempotency_key():
    calls = []
    encoded = base64.b64encode(_image("#887766")).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("idempotency-key"))
        if len(calls) == 1:
            return httpx.Response(429, json={"message": "slow down"}, headers={"retry-after": "0"})
        return httpx.Response(200, json={"data": [{"b64_json": encoded}]})

    async with _client(handler) as client:
        result = await client.submit(
            Image2Request(mode="text_to_image", prompt="封面", size="1080x1440"),
            idempotency_key="cover-job-1",
        )

    assert result.status == "completed"
    assert calls == ["cover-job-1", "cover-job-1"]


@pytest.mark.asyncio
async def test_image2_post_500_is_retryable_but_not_automatically_resubmitted():
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"message": "upstream failed"})

    async with _client(handler) as client:
        with pytest.raises(Image2Error) as exc_info:
            await client.submit(Image2Request(mode="text_to_image", prompt="封面", size="1080x1440"))

    assert calls == 1
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_image2_redacts_configuration_secrets_from_upstream_errors():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "test-key failed at https://relay.example.com/v1"}},
        )

    async with _client(handler) as client:
        with pytest.raises(Image2Error) as exc_info:
            await client.submit(Image2Request(mode="text_to_image", prompt="封面", size="1080x1440"))

    assert "test-key" not in str(exc_info.value)
    assert "relay.example.com" not in str(exc_info.value)
    assert "***" in str(exc_info.value)


@pytest.mark.asyncio
async def test_worker_resumes_existing_provider_task_without_resubmit(monkeypatch: pytest.MonkeyPatch):
    calls = {"submit": 0, "poll": 0}

    class FakeClient:
        def __init__(self, config=None):
            del config

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback

        async def submit(self, request, *, idempotency_key=None):
            del request, idempotency_key
            calls["submit"] += 1
            raise AssertionError("existing provider task must not be resubmitted")

        async def poll(self, task_id):
            calls["poll"] += 1
            assert task_id == "provider-existing"
            return Image2Submission(
                provider_task_id=task_id,
                status="completed",
                images=[Image2Output(b64_data="done")],
            )

        async def read_output(self, output):
            assert output.b64_data == "done"
            return _image("#456789"), "image/png"

    async def no_event(*args, **kwargs):
        del args, kwargs

    async def no_assets(job):
        del job
        return [], None, None

    monkeypatch.setattr(content_cover_worker, "Image2Client", FakeClient)
    monkeypatch.setattr(content_cover_worker, "_load_job_assets", no_assets)
    monkeypatch.setattr(content_cover_worker, "_check_cancelled", no_event)
    monkeypatch.setattr(content_cover_worker, "_set_job", no_event)
    monkeypatch.setattr(content_cover_worker, "_emit", no_event)
    monkeypatch.setattr(content_cover_worker, "POLL_INTERVAL_SECONDS", 0)
    job = SimpleNamespace(
        id="ccj-resume",
        owner_uid="alice",
        mode="text_to_image",
        provider_task_id="provider-existing",
        result_json={},
        request_json={"prompt": "封面", "size": "1080x1440", "n": 1},
    )

    outputs = await content_cover_worker._run_image2(job)

    assert len(outputs) == 1
    assert calls == {"submit": 0, "poll": 1}


@pytest.mark.asyncio
async def test_worker_generates_multiple_candidates_sequentially(monkeypatch: pytest.MonkeyPatch):
    submitted = []

    class FakeClient:
        def __init__(self, config=None):
            del config

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback

        async def submit(self, request, *, idempotency_key=None):
            submitted.append((request.n, idempotency_key))
            return Image2Submission(
                status="completed",
                images=[Image2Output(b64_data=f"result-{len(submitted)}")],
            )

        async def read_output(self, output):
            assert output.b64_data
            return _image("#456789"), "image/png"

    async def no_event(*args, **kwargs):
        del args, kwargs

    async def no_assets(job):
        del job
        return [], None, None

    monkeypatch.setattr(content_cover_worker, "Image2Client", FakeClient)
    monkeypatch.setattr(content_cover_worker, "_load_job_assets", no_assets)
    monkeypatch.setattr(content_cover_worker, "_check_cancelled", no_event)
    monkeypatch.setattr(content_cover_worker, "_set_job", no_event)
    monkeypatch.setattr(content_cover_worker, "_emit", no_event)
    job = SimpleNamespace(
        id="ccj-serial",
        owner_uid="alice",
        mode="text_to_image",
        provider_task_id=None,
        result_json={},
        request_json={"prompt": "封面", "size": "1080x1440", "n": 3},
    )

    outputs = await content_cover_worker._run_image2(job)

    assert len(outputs) == 3
    assert submitted == [
        (1, "ccj-serial:0"),
        (1, "ccj-serial:1"),
        (1, "ccj-serial:2"),
    ]


@pytest.mark.asyncio
async def test_worker_template_replica_uses_masked_edit_and_locked_source_canvas(
    monkeypatch: pytest.MonkeyPatch,
):
    captured = {}
    _, analysis = _template_analysis_fixture()
    source_asset = SimpleNamespace(role="source", content_type="image/png", original_file_name="source.png")
    template_asset = SimpleNamespace(role="template", content_type="image/png", original_file_name="template.png")

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
            return Image2Submission(
                status="completed",
                images=[Image2Output(b64_data="generated")],
            )

        async def read_output(self, output):
            assert output.b64_data == "generated"
            return _image("#EE4422", (1080, 1440)), "image/png"

    async def load_assets(job):
        del job
        return [source_asset], template_asset, None

    async def download(asset):
        return _image("#2244AA" if asset.role == "source" else "#225533", (1080, 1440))

    async def no_event(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(content_cover_worker, "Image2Client", FakeClient)
    monkeypatch.setattr(content_cover_worker, "_load_job_assets", load_assets)
    monkeypatch.setattr(content_cover_worker, "_download_asset", download)
    monkeypatch.setattr(content_cover_worker, "_check_cancelled", no_event)
    monkeypatch.setattr(content_cover_worker, "_set_job", no_event)
    monkeypatch.setattr(content_cover_worker, "_emit", no_event)
    monkeypatch.setattr(content_cover_worker, "analyze_template", lambda image, target_size: analysis)
    monkeypatch.setattr(
        content_cover_worker,
        "recognize_slot_texts",
        lambda image, current_analysis: "装修避坑设计定制/环保定制",
    )
    job = SimpleNamespace(
        id="ccj-template-transfer",
        owner_uid="alice",
        mode="multi_reference",
        provider_task_id=None,
        result_json={},
        request_json={
            "prompt": "使用原图作为完整底图并迁移模板上层样式",
            "size": "1080x1440",
            "n": 1,
            "title": "",
            "template_replicate": True,
            "parameters": {"template_replicate": True},
        },
    )

    [output] = await content_cover_worker._run_image2(job)

    request = captured["request"]
    assert request.mode == "multi_reference"
    assert request.source_images[0].file_name == "source-reference.png"
    assert request.source_images[0].content_type == "image/png"
    assert request.template_image.file_name == "template-reference.png"
    assert request.template_image.content_type == "image/png"
    assert request.mask_image.file_name == "template-edit-mask.png"
    assert request.mask_image.content_type == "image/png"
    with Image.open(io.BytesIO(request.source_images[0].data)) as source_reference:
        assert source_reference.size == (1080, 1440)
        assert source_reference.getpixel((200, 500))[2] > source_reference.getpixel((200, 500))[1]
    with Image.open(io.BytesIO(request.template_image.data)) as template_reference:
        assert template_reference.size == (1080, 1440)
        assert template_reference.getpixel((200, 500))[1] > template_reference.getpixel((200, 500))[2]
    with Image.open(io.BytesIO(output)) as result:
        assert result.getpixel((10, 10)) == (34, 68, 170)
        assert result.getpixel((540, 720)) == (34, 68, 170)


@pytest.mark.asyncio
async def test_asset_reference_is_active_until_job_reaches_terminal_status():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(ContentCoverJob.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as db:
        job = await ContentCoverRepository(db).create_job(
            id="ccj-active-reference",
            owner_uid="alice",
            mode="image_to_image",
            status="running",
            idempotency_key="reference-test",
            request_json={"source_asset_ids": ["source-1"]},
            result_json={},
        )
        await db.commit()
        assert await ContentCoverRepository(db).asset_is_in_active_job("source-1", "alice") is True

        job.status = "succeeded"
        await db.commit()
        assert await ContentCoverRepository(db).asset_is_in_active_job("source-1", "alice") is False

    await engine.dispose()


@pytest.mark.asyncio
async def test_xiaohongshu_distribution_snapshot_locks_selected_cover(monkeypatch: pytest.MonkeyPatch):
    artifact = SimpleNamespace(
        id="artifact-1",
        created_by="alice",
        review_snapshot={"status": "passed"},
        title="封面测试",
        body="正文",
        topics=["内容创作"],
        current_version=2,
        cover_asset_id="cover-2",
    )
    account = SimpleNamespace(id="account-1", enabled=True, login_status="logged_in")
    cover = SimpleNamespace(
        id="cover-2",
        role="output",
        bucket_name="content-covers",
        object_name="content-covers/alice/cover-2.png",
        sha256="a" * 64,
    )
    captured = {}
    job = SimpleNamespace(
        id="distribution-1",
        status="queued",
        to_dict=lambda: {"id": "distribution-1", "status": "queued"},
    )

    class FakeContentRepo:
        async def get_artifact(self, artifact_id):
            assert artifact_id == artifact.id
            return artifact

    class FakeXhsRepo:
        async def get_accounts(self, account_ids, owner_uid):
            assert account_ids == [account.id]
            assert owner_uid == "alice"
            return [account]

        async def get_artifact_version(self, artifact_id, version):
            return SimpleNamespace(artifact_id=artifact_id, version=version)

        async def get_job_by_idempotency_key(self, request_key, owner_uid):
            del request_key, owner_uid
            return None

        async def create_distribution_job(self, **values):
            captured.update(values)
            return job

        async def list_distribution_results(self, job_id):
            assert job_id == job.id
            return []

    class FakeCoverRepo:
        async def get_asset_for_user(self, asset_id, owner_uid):
            assert (asset_id, owner_uid) == (cover.id, "alice")
            return cover

    class FakeQueue:
        async def enqueue_job(self, *args, **kwargs):
            del args, kwargs
            return object()

    class FakeDb:
        async def commit(self):
            return None

    async def fake_pool():
        return FakeQueue()

    monkeypatch.setattr(xiaohongshu_service, "ContentRepository", lambda db: FakeContentRepo())
    monkeypatch.setattr(xiaohongshu_service, "XiaohongshuRepository", lambda db: FakeXhsRepo())
    monkeypatch.setattr(xiaohongshu_service, "ContentCoverRepository", lambda db: FakeCoverRepo())
    monkeypatch.setattr(xiaohongshu_service, "get_arq_pool", fake_pool)

    response = await xiaohongshu_service.create_distribution(
        FakeDb(),
        SimpleNamespace(uid="alice"),
        artifact.id,
        XiaohongshuDistributionCreate(
            request_id="request-cover-snapshot",
            account_ids=[account.id],
            mode="draft",
        ),
    )

    assert response["deduplicated"] is False
    assert captured["payload_snapshot"]["cover"] == {
        "type": "asset",
        "asset_id": cover.id,
        "bucket_name": cover.bucket_name,
        "object_name": cover.object_name,
        "sha256": cover.sha256,
    }
