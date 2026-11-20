"""Cover-fit with bottom focal must keep watermark rows on taller sources."""

from __future__ import annotations


def cover_crop_box(
    src_w: int,
    src_h: int,
    box_w: int,
    box_h: int,
    *,
    focal_x: float = 0.5,
    focal_y: float = 0.5,
) -> tuple[int, int, int, int]:
    """Mirror HyCanvas engine fitRect cover crop (integer edges)."""
    scale = max(box_w / src_w, box_h / src_h)
    vis_w = box_w / scale
    vis_h = box_h / scale
    sx = (src_w - vis_w) * focal_x
    sy = (src_h - vis_h) * focal_y
    left = int(round(sx))
    top = int(round(sy))
    right = int(round(sx + vis_w))
    bottom = int(round(sy + vis_h))
    return left, top, right, bottom


def test_taller_source_center_focal_discards_bottom_watermark_band():
    left, top, right, bottom = cover_crop_box(1080, 1619, 1080, 1440, focal_x=0.5, focal_y=0.5)
    assert left == 0 and right == 1080
    assert top > 0
    assert bottom < 1619


def test_taller_source_bottom_focal_keeps_source_floor():
    left, top, right, bottom = cover_crop_box(1080, 1619, 1080, 1440, focal_x=0.5, focal_y=1.0)
    assert left == 0 and right == 1080
    assert bottom == 1619
    assert top == 1619 - 1440


def test_wider_source_bottom_focal_still_fills_height():
    left, top, right, bottom = cover_crop_box(1920, 1080, 1080, 1440, focal_x=0.5, focal_y=1.0)
    assert top == 0 and bottom == 1080
    assert abs((right - left) - 1080 * (1080 / 1440)) < 1
