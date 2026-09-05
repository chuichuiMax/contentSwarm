from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from yuxi.content_cover.poster_billboard import (
    POSTER_SIZE,
    build_poster_copy_plan,
    evaluate_poster_quality,
    render_poster_billboard,
)

MANIFEST = json.loads(
    (Path(__file__).parents[2] / "fixtures/content_cover/poster-golden/manifest.json").read_text(encoding="utf-8")
)
CASES = [(template, product) for template in MANIFEST["templates"] for product in MANIFEST["products"]]


@pytest.mark.parametrize(
    ("template_case", "product_case"),
    CASES,
    ids=lambda case: case["id"],
)
def test_poster_billboard_sixty_case_golden_matrix(template_case: dict, product_case: dict):
    template = Image.new("RGBA", POSTER_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(template)
    draw.rectangle((0, 0, 1079, 1439), outline=template_case["accent"], width=26)
    draw.rectangle((0, 0, 1079, 190), fill=template_case["accent"])
    draw.rounded_rectangle((70, 1240, 1010, 1370), radius=30, fill=template_case["accent"])
    product = Image.new("RGB", (720, 960), product_case["color"])
    ImageDraw.Draw(product).ellipse((180, 230, 540, 590), fill="#FFFFFF")
    record = {
        "id": template_case["id"],
        "template_type": "alpha_overlay",
        "product_box": {"x": 0.04, "y": 0.14, "width": 0.92, "height": 0.72},
        "safe_area": {"x": 0.02, "y": 0.02, "width": 0.96, "height": 0.96},
        "text_slots": [],
        "fixed_regions": [],
        "editable_regions": [],
    }
    copy_plan = build_poster_copy_plan([], source="template")

    first, metadata = render_poster_billboard(template, product, record, copy_plan)
    second, _ = render_poster_billboard(template, product, record, copy_plan)

    assert hashlib.sha256(first).digest() == hashlib.sha256(second).digest()
    with Image.open(io.BytesIO(first)) as output:
        output.load()
        report = evaluate_poster_quality(output, record, metadata)
        assert output.size == (MANIFEST["output"]["width"], MANIFEST["output"]["height"])
        assert output.format == MANIFEST["output"]["format"]
        assert output.getpixel((10, 10)) == Image.new("RGB", (1, 1), template_case["accent"]).getpixel((0, 0))
    gates = MANIFEST["quality_gates"]
    assert report["passed"] is True
    assert report["overflow_count"] <= gates["overflow_count_max"]
    assert report["template_locked"] is gates["template_locked"]
    assert report["product_locked"] is gates["product_locked"]
