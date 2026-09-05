from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from yuxi.content_cover.renderer import _font
from yuxi.content_cover.template_replication import (
    analyze_template,
    build_copy_plan,
    evaluate_quality,
    render_template_replication,
)


MANIFEST = json.loads(
    (Path(__file__).parents[2] / "fixtures/content_cover/golden/manifest.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda case: case["id"])
def test_template_replication_golden_quality_gates(case: dict[str, str]):
    template = Image.new("RGB", (1080, 1440), "#E7DDD3")
    draw = ImageDraw.Draw(template)
    draw.rounded_rectangle((55, 90, 1025, 360), radius=34, fill="#8B8178")
    draw.text(
        (92, 112), "模板主标题", fill="#FFFFFF", font=_font(116, bold=True), stroke_width=5, stroke_fill="#171717"
    )
    draw.rounded_rectangle((100, 285, 980, 355), radius=24, fill="#66615E")
    draw.text((130, 298), "模板副标题/重点信息", fill="#FFF0C3", font=_font(40, bold=False))
    draw.ellipse((920, 55, 1000, 135), fill="#FF6B35")
    blocks = [
        {"text": "模板主标题", "box": (92, 112, 770, 235)},
        {"text": "模板副标题/重点信息", "box": (130, 298, 800, 344)},
    ]
    source = Image.new("RGB", (1080, 1440), "#C9DCE8")
    source_draw = ImageDraw.Draw(source)
    for y in range(0, 1440, 80):
        source_draw.rectangle((0, y, 1080, y + 40), fill=(65 + y % 110, 120, 150))

    analysis = analyze_template(template, target_size=(1080, 1440), ocr_blocks=blocks)
    plan = build_copy_plan(
        analysis,
        title=case["title"],
        subtitle="三步讲清核心方法",
        source="template" if case["id"] == "no-content-asset" else "content_asset",
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
            recognized_text={item.slot_id: item.text for item in plan.slots},
        )

    gates = MANIFEST["quality_gates"]
    assert report.passed is True
    assert report.locked_ssim >= gates["locked_ssim_min"]
    assert report.layout_deviation <= gates["layout_deviation_max"]
    assert report.ocr_accuracy >= gates["ocr_accuracy_min"]
    assert report.mosaic_count <= gates["mosaic_count_max"]
    assert report.residual_text_count <= gates["residual_text_count_max"]
    assert report.overflow_count <= gates["overflow_count_max"]
    assert (report.output_width, report.output_height, report.output_format) == (1080, 1440, "PNG")


def test_deterministic_final_render_stays_within_local_performance_budget():
    template = Image.new("RGB", (1080, 1440), "#E7DDD3")
    source = Image.new("RGB", (1080, 1440), "#4D7F95")
    analysis = analyze_template(
        template,
        target_size=(1080, 1440),
        ocr_blocks=[
            {"text": "模板主标题", "box": (90, 100, 920, 250)},
            {"text": "模板副标题", "box": (140, 290, 760, 350)},
        ],
    )
    plan = build_copy_plan(
        analysis,
        title="内容资产封面标题",
        subtitle="三步讲清核心方法",
        source="content_asset",
    )

    started = time.perf_counter()
    outputs = [render_template_replication(source, template, analysis, plan)[0] for _ in range(4)]
    elapsed = time.perf_counter() - started

    assert elapsed < 4
    assert all(len(output) < 8 * 1024 * 1024 for output in outputs)
