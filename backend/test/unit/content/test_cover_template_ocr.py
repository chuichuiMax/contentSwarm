from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from yuxi.content_cover.template_replication import (
    _OcrCandidate,
    _fuse_ocr_candidates,
    _merge_rows,
    _prefer_document_consistent_alternatives,
    _foreground_and_panel,
    _infer_fill_runs,
    _suppress_ocr_artifacts,
)


def _candidate(text: str, confidence: float, variant: str) -> _OcrCandidate:
    return _OcrCandidate(
        text=text,
        confidence=confidence,
        box=(402, 1334, 690, 1384),
        variant=variant,
    )


def test_ocr_fusion_prefers_supported_complete_text_over_dropped_character() -> None:
    candidates = [
        _candidate("Hrun鸿扬家装", 0.97, "original"),
        _candidate("Hrun鸿扬家装", 0.96, "local-contrast"),
        _candidate("Hrun鸿扬家装", 0.95, "original#recall"),
        _candidate("Hirun鸿扬家装", 0.95, "original@detail-1.66x"),
        _candidate("Hirun鸿扬家装", 0.94, "local-contrast@detail-1.66x"),
    ]

    result = _fuse_ocr_candidates(candidates)

    assert len(result) == 1
    assert result[0]["text"] == "Hirun鸿扬家装"
    assert result[0]["consensus_count"] == 2
    assert result[0]["alternatives"] == ["Hrun鸿扬家装"]


def test_merge_rows_does_not_absorb_small_brand_lines_into_large_logo() -> None:
    rows = _merge_rows(
        [
            {"text": "鸿扬家装", "box": (88, 87, 627, 242)},
            {"text": "HONG YANG", "box": (643, 129, 821, 159)},
            {"text": "JIA ZHUANG", "box": (636, 166, 816, 203)},
            {"text": "4大产品服务", "box": (84, 234, 813, 381)},
            {"text": "以客户为核心全程无忧装修", "box": (145, 410, 696, 456)},
            {"text": "Hirun鸿扬家装", "box": (403, 1335, 690, 1383)},
        ],
        1080,
    )

    assert [row["text"] for row in rows] == [
        "鸿扬家装",
        "HONG YANG",
        "JIA ZHUANG",
        "4大产品服务",
        "以客户为核心全程无忧装修",
        "Hirun鸿扬家装",
    ]


def test_ocr_artifacts_remove_nested_partial_line_and_weak_recall_glyphs() -> None:
    lines = [
        {
            "text": "4大产品服务",
            "box": (75, 220, 826, 394),
            "confidence": 0.999,
            "source_variant": "original",
            "candidate_count": 9,
            "consensus_count": 7,
            "alternatives": [],
        },
        {
            "text": "4大",
            "box": (84, 231, 330, 379),
            "confidence": 0.997,
            "source_variant": "original@detail-1.66x",
            "candidate_count": 2,
            "consensus_count": 2,
            "alternatives": [],
        },
        {
            "text": "元",
            "box": (281, 259, 418, 312),
            "confidence": 0.35,
            "source_variant": "original@detail-1.66x#recall",
            "candidate_count": 1,
            "consensus_count": 1,
            "alternatives": [],
        },
    ]

    assert [item["text"] for item in _suppress_ocr_artifacts(lines)] == ["4大产品服务"]


def test_document_consistency_prefers_brand_spelling_confirmed_elsewhere() -> None:
    lines = [
        {
            "text": "鸿扬家装",
            "box": (80, 80, 620, 240),
            "confidence": 0.99,
            "source_variant": "original",
            "candidate_count": 9,
            "consensus_count": 8,
            "alternatives": [],
        },
        {
            "text": "Hirun鸿扬家裝",
            "box": (400, 1320, 700, 1390),
            "confidence": 0.94,
            "source_variant": "local-contrast@detail-1.66x",
            "candidate_count": 8,
            "consensus_count": 3,
            "alternatives": ["Hirun 鸿扬家装", "Hrun鸿扬家装"],
        },
    ]

    normalized = _prefer_document_consistent_alternatives(lines)

    assert normalized[1]["text"] == "Hirun 鸿扬家装"
    assert normalized[1]["alternatives"][0] == "Hirun鸿扬家裝"


def _style_font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def test_style_detection_preserves_multicolor_text_and_warm_panel_fill() -> None:
    image = Image.new("RGB", (720, 360), "#FFFFFF")
    draw = ImageDraw.Draw(image)
    title_font = _style_font(82)
    title_left, title_top = 42, 38
    first = "4大"
    second = "产品服务"
    draw.text((title_left, title_top), first, font=title_font, fill="#F4DC28")
    split = title_left + round(draw.textlength(first, font=title_font))
    draw.text((split, title_top), second, font=title_font, fill="#E4E4E4")
    title_box = draw.textbbox((title_left, title_top), first + second, font=title_font)

    panel_box = (70, 220, 650, 306)
    draw.rounded_rectangle(panel_box, radius=8, fill="#E7D6C9")
    subtitle_font = _style_font(38)
    subtitle = "以客户为核心全程无忧装修"
    subtitle_box = draw.textbbox((92, 235), subtitle, font=subtitle_font)
    draw.text((92, 235), subtitle, font=subtitle_font, fill="#482810")

    title_fill, _, title_panel = _foreground_and_panel(image, title_box)
    title_fill, title_runs = _infer_fill_runs(image, title_box, first + second, title_panel)
    subtitle_fill, _, subtitle_panel = _foreground_and_panel(image, subtitle_box)

    assert title_fill != "#F4DC28"
    assert title_panel is None
    assert [(item["start"], item["end"]) for item in title_runs] == [(0, 2), (2, 6)]
    assert title_runs[0]["fill"] != title_runs[1]["fill"]
    assert subtitle_fill == "#482810"
    assert subtitle_panel is not None
    panel_rgb = tuple(int(subtitle_panel[index : index + 2], 16) for index in (1, 3, 5))
    assert sum(abs(actual - expected) for actual, expected in zip(panel_rgb, (231, 214, 201), strict=True)) < 30
