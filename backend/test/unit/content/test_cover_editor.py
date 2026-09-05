import io
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from yuxi.content_cover import editor_renderer
from yuxi.content_cover.editor_renderer import render_editor_scene
from yuxi.content_cover.schemas import CoverEditorScene
from yuxi.services.content_cover_service import (
    _build_editor_background_copy_plan,
    _editor_font_family,
    _merge_editor_scenes,
    _poster_editor_scene,
    resolve_cover_editor_font,
)
from yuxi.services import content_cover_service, content_cover_worker


def _scene(*, layers: list[dict] | None = None) -> dict:
    return {
        "version": 1,
        "canvas": {
            "width": 1080,
            "height": 1440,
            "background_asset_id": "cca_background",
            "safe_area": {"x": 0.05, "y": 0.05, "width": 0.9, "height": 0.9},
        },
        "layers": layers or [],
    }


def _text_layer(layer_id: str = "title") -> dict:
    return {
        "id": layer_id,
        "layer_type": "text",
        "name": "主标题",
        "text": "可编辑封面",
        "x": 120,
        "y": 180,
        "width": 840,
        "height": 180,
        "rotation": 0,
        "opacity": 1,
        "visible": True,
        "locked": False,
        "order": 0,
        "font_family": "Noto Sans CJK SC",
        "font_size": 88,
        "font_weight": 700,
        "font_style": "normal",
        "fill": "#FFFFFF",
        "fill_runs": [],
        "align": "center",
        "line_height": 1.2,
        "letter_spacing": 2,
        "stroke": True,
        "stroke_color": "#111111",
        "stroke_width": 3,
        "shadow": True,
        "shadow_color": "#000000",
        "shadow_blur": 6,
        "shadow_offset_x": 0,
        "shadow_offset_y": 6,
        "background_fill": None,
        "background_opacity": 1,
        "background_radius": 12,
        "background_padding": 0,
    }


def test_editor_scene_rejects_duplicate_layer_ids():
    with pytest.raises(ValidationError, match="文字图层 ID 不能重复"):
        CoverEditorScene.model_validate(_scene(layers=[_text_layer(), _text_layer()]))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Microsoft YaHei", "Noto Sans CJK SC"),
        ("Noto Sans CJK SC", "Noto Sans CJK SC"),
        ("SimSun", "Noto Serif CJK SC"),
        ("Georgia", "Noto Serif CJK SC"),
    ],
)
def test_editor_normalizes_template_fonts_to_exportable_families(source: str, expected: str):
    assert _editor_font_family(source) == expected


def test_editor_font_endpoint_uses_same_noto_file_as_server_renderer():
    path = resolve_cover_editor_font("noto-sans-cjk-regular")

    assert path.name == "NotoSansCJK-Regular.ttc"
    assert path.is_file()


def test_editor_renderer_outputs_declared_png_and_draws_text():
    scene = CoverEditorScene.model_validate(_scene(layers=[_text_layer()])).model_dump(mode="json")
    background = Image.new("RGB", (1080, 1440), "#375a7f")

    rendered = render_editor_scene(background, scene)

    with Image.open(io.BytesIO(rendered)) as output:
        assert output.format == "PNG"
        assert output.size == (1080, 1440)
        assert output.getpixel((540, 250)) != background.getpixel((540, 250))


def test_editor_renderer_preserves_multicolor_text_and_detected_background():
    layer = _text_layer()
    layer.update(
        {
            "text": "4大产品服务",
            "fill": "#E4E4E4",
            "fill_runs": [
                {"start": 0, "end": 2, "fill": "#F4DC28"},
                {"start": 2, "end": 6, "fill": "#E4E4E4"},
            ],
            "stroke": False,
            "shadow": False,
            "background_fill": "#E7D6C9",
            "background_opacity": 1,
            "background_padding": 12,
        }
    )
    scene = CoverEditorScene.model_validate(_scene(layers=[layer])).model_dump(mode="json")

    rendered = render_editor_scene(Image.new("RGB", (1080, 1440), "#FFFFFF"), scene)

    with Image.open(io.BytesIO(rendered)) as output:
        colors = output.convert("RGB").get_flattened_data()
        assert sum(pixel == (244, 220, 40) for pixel in colors) > 20
        assert sum(pixel == (228, 228, 228) for pixel in colors) > 20
        assert sum(pixel == (231, 214, 201) for pixel in colors) > 500


def test_editor_renderer_shrinks_single_line_instead_of_truncating(monkeypatch: pytest.MonkeyPatch):
    layer = _text_layer()
    layer.update({"text": "真实体验89㎡三室", "width": 260, "height": 100, "font_size": 88, "shadow": False})
    rendered_lines: list[str] = []
    original_draw = editor_renderer._draw_spaced_line

    def capture_line(draw, text, x, y, **kwargs):
        rendered_lines.append(text)
        return original_draw(draw, text, x, y, **kwargs)

    monkeypatch.setattr(editor_renderer, "_draw_spaced_line", capture_line)

    editor_renderer._render_text_layer(layer)

    assert rendered_lines == ["真实体验89㎡三室"]


def test_poster_scene_recovers_all_mask_text_slots_and_current_copy():
    job = SimpleNamespace(
        request_json={
            "poster_template_snapshot": {
                "safe_area": {"x": 0.05, "y": 0.05, "width": 0.9, "height": 0.9},
                "text_slots": [
                    {
                        "id": "brand",
                        "role": "eyebrow",
                        "source_text": "HONG YANG",
                        "editable": False,
                        "box": {"x": 0.62, "y": 0.08, "width": 0.2, "height": 0.04},
                        "style": {
                            "fill": "#C39679",
                            "stroke": None,
                            "stroke_width_ratio": 0,
                            "font_size_ratio": 0.025,
                            "bold": False,
                            "align": "right",
                            "panel_fill": None,
                            "panel_radius_ratio": 0,
                        },
                        "max_chars": 20,
                        "max_lines": 1,
                    },
                    {
                        "id": "hero-title",
                        "role": "title",
                        "source_text": "原始标题",
                        "editable": True,
                        "box": {"x": 0.1, "y": 0.12, "width": 0.8, "height": 0.12},
                        "style": {
                            "fill": "#F8F0E6",
                            "stroke": "#222222",
                            "stroke_width_ratio": 0.02,
                            "font_size_ratio": 0.065,
                            "bold": True,
                            "align": "center",
                            "panel_fill": None,
                            "panel_radius_ratio": 0,
                        },
                        "max_chars": 24,
                        "max_lines": 2,
                    },
                ],
            },
            "copy_plan": {
                "slots": [
                    {"slot_id": "brand", "text": "HONG YANG", "changed": False},
                    {"slot_id": "hero-title", "text": "当前生成标题", "changed": True},
                ]
            },
        }
    )

    scene = _poster_editor_scene(job, "cca_clean")

    assert scene["canvas"]["background_asset_id"] == "cca_clean"
    assert len(scene["layers"]) == 2
    assert scene["layers"][0]["name"] == "HONG YANG"
    assert scene["layers"][0]["text"] == "HONG YANG"
    assert scene["layers"][0]["locked"] is False
    assert scene["layers"][1]["name"] == "当前生成标题"
    assert scene["layers"][1]["text"] == "当前生成标题"
    assert scene["layers"][1]["x"] == 108
    assert scene["layers"][1]["font_weight"] == 700


def test_editor_background_plan_uses_saved_layers_without_mutating_their_styles():
    snapshot = {
        "text_slots": [
            {
                "id": "subtitle",
                "role": "subtitle",
                "source_text": "以客户为核心 全程无忧装修",
                "style": {"fill": "#482810", "panel_fill": "#E7D6C9", "panel_opacity": 1},
                "max_chars": 40,
                "max_lines": 1,
            }
        ]
    }

    background_snapshot, clean_plan = _build_editor_background_copy_plan(snapshot)

    assert snapshot["text_slots"][0]["style"]["panel_fill"] == "#E7D6C9"
    assert background_snapshot["text_slots"][0]["style"]["panel_fill"] is None
    assert clean_plan["slots"] == [
        {
            "slot_id": "subtitle",
            "role": "subtitle",
            "source_text": "以客户为核心 全程无忧装修",
            "text": "",
            "max_chars": 40,
            "max_lines": 1,
            "changed": True,
        }
    ]


@pytest.mark.asyncio
async def test_editor_background_uses_saved_snapshot_without_running_ocr(monkeypatch: pytest.MonkeyPatch):
    template_asset = SimpleNamespace(id="template", bucket_name="covers", object_name="template.png")
    product_asset = SimpleNamespace(id="product", bucket_name="covers", object_name="product.png")
    source_asset = SimpleNamespace(id="output", content_task_id="task-1")
    snapshot = {
        "asset_id": "template",
        "product_box": {"x": 0, "y": 0, "width": 1, "height": 1},
        "text_slots": [
            {
                "id": "subtitle",
                "role": "subtitle",
                "source_text": "原始文字",
                "box": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.1},
                "style": {"fill": "#482810", "panel_fill": "#E7D6C9"},
            }
        ],
    }
    source_job = SimpleNamespace(
        id="job-1",
        request_json={
            "poster_template_snapshot": snapshot,
            "product_asset_id": "product",
            "transform": {},
        },
    )
    rendered_png = io.BytesIO()
    Image.new("RGB", (1080, 1440), "#FFFFFF").save(rendered_png, format="PNG")
    captured: dict = {}

    class FakeRepository:
        def __init__(self, db):
            del db

        async def get_asset_for_user(self, asset_id, owner_uid):
            del owner_uid
            return {"template": template_asset, "product": product_asset}.get(asset_id)

        async def create_asset(self, **kwargs):
            return SimpleNamespace(**kwargs)

    class FakeStorage:
        async def adownload_file(self, bucket_name, object_name):
            del bucket_name, object_name
            image = io.BytesIO()
            Image.new("RGB", (1080, 1440), "#FFFFFF").save(image, format="PNG")
            return image.getvalue()

        async def aupload_file(self, **kwargs):
            return SimpleNamespace(bucket_name=kwargs["bucket_name"], object_name=kwargs["object_name"])

    def render_without_text(template, product, template_record, copy_plan, **kwargs):
        del template, product, kwargs
        captured["template"] = template_record
        captured["copy_plan"] = copy_plan
        return rendered_png.getvalue(), {}

    def fail_if_ocr_runs(*args, **kwargs):
        del args, kwargs
        raise AssertionError("编辑器不应执行 OCR")

    monkeypatch.setattr(content_cover_service, "ContentCoverRepository", FakeRepository)
    monkeypatch.setattr(content_cover_service, "get_minio_client", lambda: FakeStorage())
    monkeypatch.setattr(content_cover_service, "render_poster_billboard", render_without_text)
    monkeypatch.setattr(content_cover_service, "analyze_template", fail_if_ocr_runs)

    background, returned_snapshot = await content_cover_service._create_poster_editor_background(
        object(),
        SimpleNamespace(uid="alice", department_id=None),
        source_asset,
        source_job,
    )

    assert background.role == "editor_background"
    assert returned_snapshot["text_slots"][0]["style"]["panel_fill"] == "#E7D6C9"
    assert captured["template"]["text_slots"][0]["style"]["panel_fill"] is None
    assert captured["copy_plan"]["slots"][0]["text"] == ""


def test_merge_editor_scenes_preserves_legacy_layer_without_duplicate():
    recovered = _text_layer("text_slot-4_3")
    recovered["text"] = "模板标题"
    edited = _text_layer("text_slot-4")
    edited["text"] = "用户编辑标题"

    merged = _merge_editor_scenes(_scene(layers=[recovered]), _scene(layers=[recovered, edited]))

    assert len(merged["layers"]) == 1
    assert merged["layers"][0]["id"] == "text_slot-4"
    assert merged["layers"][0]["text"] == "用户编辑标题"


def test_precise_recovery_replaces_generated_ocr_fragments_but_keeps_custom_layers():
    exact = _text_layer("text_slot-8_7")
    exact.update({"name": "Hirunhome Renovation", "text": "Hirunhome Renovation"})
    fragment = _text_layer("text_slot-8_5")
    fragment.update({"name": "口号", "text": "HunOme"})
    obsolete = _text_layer("text_slot-9_6")
    obsolete.update({"name": "口号", "text": "Ren0Va0n"})
    custom = _text_layer("text_custom_ab123")
    custom.update({"name": "用户文字", "text": "保留我"})

    merged = _merge_editor_scenes(
        _scene(layers=[exact]),
        _scene(layers=[fragment, obsolete, custom]),
        replace_generated=True,
    )

    assert [item["text"] for item in merged["layers"]] == ["Hirunhome Renovation", "保留我"]


@pytest.mark.asyncio
async def test_worker_editor_render_loads_owned_background_and_renders_scene(monkeypatch: pytest.MonkeyPatch):
    asset = SimpleNamespace(id="cca_background", owner_uid="alice")

    class FakeRepository:
        def __init__(self, db):
            del db

        async def get_asset_for_user(self, asset_id, owner_uid):
            assert asset_id == "cca_background"
            assert owner_uid == "alice"
            return asset

    @asynccontextmanager
    async def fake_session():
        yield object()

    async def download_background(item):
        assert item is asset
        output = io.BytesIO()
        Image.new("RGB", (1080, 1440), "#375a7f").save(output, format="PNG")
        return output.getvalue()

    monkeypatch.setattr(content_cover_worker, "ContentCoverRepository", FakeRepository)
    monkeypatch.setattr(content_cover_worker.pg_manager, "get_async_session_context", fake_session)
    monkeypatch.setattr(content_cover_worker, "_download_asset", download_background)
    job = SimpleNamespace(
        owner_uid="alice",
        request_json={
            "base_asset_id": "cca_background",
            "scene": _scene(layers=[_text_layer()]),
        },
    )

    [rendered] = await content_cover_worker._run_editor_render(job)

    with Image.open(io.BytesIO(rendered)) as output:
        assert output.size == (1080, 1440)
        assert output.getpixel((540, 250)) != (55, 90, 127)
