from __future__ import annotations

import io

from PIL import Image

from yuxi.config.app import Config
from yuxi.services.material_watermark import watermark_upload_bytes


def _dark_png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (1024, 768), (20, 30, 40)).save(output, format="PNG")
    return output.getvalue()


def test_persisted_watermark_gallery_is_read_from_config_file(tmp_path):
    saved = Config(save_dir=str(tmp_path))
    saved.update(
        {
            "material_watermark_gallery_id": "cmc_case",
            "material_watermark_gallery_owner_uid": "owner-1",
        }
    )
    saved.save()

    loaded = Config(save_dir=str(tmp_path))

    assert loaded.resolve_material_watermark_gallery() == ("cmc_case", "owner-1")


def test_watermark_upload_bytes_skips_other_galleries(monkeypatch):
    monkeypatch.setattr(
        "yuxi.services.material_watermark.config.resolve_material_watermark_gallery",
        lambda: ("gallery-1", "owner-1"),
    )
    raw = _dark_png()

    assert watermark_upload_bytes(raw, category_id="other", owner_uid="owner-1") == raw
    assert watermark_upload_bytes(raw, category_id="child-1", owner_uid="owner-1", parent_id="gallery-1") != raw
    assert watermark_upload_bytes(raw, category_id="child-1", owner_uid="owner-1", parent_id="other") == raw


def test_apply_gallery_watermark_places_logo_at_bottom_center(monkeypatch):
    monkeypatch.setattr(
        "yuxi.services.material_watermark.config.resolve_material_watermark_gallery",
        lambda: ("gallery-1", "owner-1"),
    )
    marked = watermark_upload_bytes(_dark_png(), category_id="gallery-1", owner_uid="owner-1")

    with Image.open(io.BytesIO(marked)) as result:
        width, height = result.size
        assert result.getpixel((width // 2, 12))[:3] == (20, 30, 40)
        bright_x = [
            x
            for y in range(height - 120, height)
            for x in range(width)
            if all(channel > 200 for channel in result.getpixel((x, y))[:3])
        ]
        assert bright_x
        assert abs((min(bright_x) + max(bright_x)) / 2 - width / 2) < width * 0.08
