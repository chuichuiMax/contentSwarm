from __future__ import annotations

import hashlib
import io
import json
import re
from collections.abc import Sequence
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from yuxi.content_cover import COVER_PROCESSING_VERSION
from yuxi.content_cover.schemas import NormalizedBox, PosterTextSlot, TemplateAnalysis

POSTER_PROCESSING_VERSION = f"{COVER_PROCESSING_VERSION}-poster-v3"
POSTER_SIZE = (1080, 1440)


class PosterBillboardError(ValueError):
    pass


class PosterBillboardQualityError(PosterBillboardError):
    def __init__(self, report: dict[str, Any]):
        self.report = report
        super().__init__("；".join(report.get("failures") or []) or "大字报质量检查未通过")


def _font(size: int, *, bold: bool) -> ImageFont.ImageFont:
    candidates = (
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "C:/Windows/Fonts/msyhbd.ttc")
        if bold
        else ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "C:/Windows/Fonts/msyh.ttc")
    )
    for path in (*candidates, "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"):
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _rgb(value: str | None, default: tuple[int, int, int] = (255, 255, 255)) -> tuple[int, int, int]:
    value = (value or "").lstrip("#")
    if len(value) != 6:
        return default
    try:
        return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return default


def _box_dict(box: NormalizedBox) -> dict[str, float]:
    return box.model_dump(mode="json")


def _looks_like_bilingual_brand(slot: PosterTextSlot) -> bool:
    text = slot.source_text.strip()
    latin_words = re.findall(r"[A-Za-z]{2,}", text)
    has_cjk = bool(re.search(r"[\u3400-\u9fff]", text))
    return slot.box.y < 0.22 and has_cjk and len(latin_words) >= 2


def normalize_poster_text_slots(text_slots: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repair a common OCR error where a bilingual brand lockup is selected as the headline."""
    slots = [PosterTextSlot.model_validate(item) for item in text_slots]
    brand_slots = [item for item in slots if _looks_like_bilingual_brand(item)]
    if not brand_slots:
        return [item.model_dump(mode="json") for item in slots]

    candidates = [
        item
        for item in slots
        if item not in brand_slots
        and item.role in {"title", "other"}
        and item.box.y + item.box.height <= 0.32
        and len(item.source_text.strip()) >= 2
    ]
    headline = max(candidates, key=lambda item: item.box.width * item.box.height, default=None)
    normalized: list[dict[str, Any]] = []
    for item in slots:
        adjusted = item.model_copy(deep=True)
        if item in brand_slots:
            adjusted.role = "eyebrow"
            adjusted.editable = False
        elif headline is not None and item.id == headline.id:
            adjusted.role = "title"
            adjusted.editable = True
        normalized.append(adjusted.model_dump(mode="json"))
    return normalized


def _calibrate_hirun_tall_poster(text_slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the owner's verified eight-layer profile to the matching 1080×1440 template."""
    compact_texts = {re.sub(r"[^A-Za-z0-9]+", "", str(item.get("source_text") or "")).upper() for item in text_slots}
    if not {"HONGYANG", "JIAZHUANG", "YUEYANGXINGLINCOMMUNITY"}.issubset(compact_texts):
        return text_slots

    layers = [
        ("152 m²", "other", False, (58, 108, 300, 106), "Georgia", 88, 700, "#FFFFFF", 0, True, 8, 5),
        ("HONG YANG", "other", False, (644, 131, 210, 38), "Georgia", 28, 400, "#C69778", 0, False, 0, 0),
        ("JIA ZHUANG", "other", False, (644, 172, 210, 38), "Georgia", 28, 400, "#C69778", 0, False, 0, 0),
        ("岳阳 · 杏林小区", "title", True, (58, 272, 630, 90), "SimSun", 72, 700, "#FFFFFF", 0, True, 8, 5),
        (
            "YUEYANG XINGLIN COMMUNITY",
            "other",
            False,
            (70, 360, 440, 32),
            "Georgia",
            24,
            700,
            "#D07A48",
            0,
            False,
            0,
            0,
        ),
        (
            "Interior design: hirun",
            "other",
            False,
            (60, 456, 380, 38),
            "Arial",
            30,
            600,
            "#FFFFFF",
            0,
            True,
            8,
            5,
        ),
        (
            "date of completion: 2026",
            "other",
            False,
            (60, 490, 420, 38),
            "Arial",
            30,
            600,
            "#FFFFFF",
            0,
            True,
            9,
            5,
        ),
        (
            "Hirunhome Renovation",
            "other",
            False,
            (228, 1345, 630, 34),
            "Arial",
            18,
            400,
            "#D07A48",
            14,
            False,
            0,
            0,
        ),
    ]
    source_boxes = [
        (45, 101, 274, 93),
        (642, 130, 179, 27),
        (637, 165, 181, 37),
        (42, 232, 650, 119),
        (66, 358, 415, 25),
        (56, 453, 326, 32),
        (59, 483, 363, 30),
        (228, 1336, 625, 25),
    ]
    calibrated: list[dict[str, Any]] = []
    for index, (
        text,
        role,
        editable,
        (x, y, width, height),
        family,
        font_size,
        weight,
        fill,
        spacing,
        shadow,
        shadow_blur,
        shadow_y,
    ) in enumerate(layers, 1):
        source_x, source_y, source_width, source_height = source_boxes[index - 1]
        calibrated.append(
            PosterTextSlot(
                id=f"slot-{index}",
                role=role,
                source_text=text,
                editable=editable,
                box={
                    "x": source_x / POSTER_SIZE[0],
                    "y": source_y / POSTER_SIZE[1],
                    "width": source_width / POSTER_SIZE[0],
                    "height": source_height / POSTER_SIZE[1],
                },
                style={
                    "fill": fill,
                    "stroke": None,
                    "stroke_width_ratio": 0,
                    "font_size_ratio": font_size / POSTER_SIZE[1],
                    "bold": weight >= 600,
                    "align": "left",
                    "panel_fill": None,
                    "panel_radius_ratio": 0,
                    "font_family": family,
                    "font_size_px": font_size,
                    "font_weight": weight,
                    "letter_spacing": spacing,
                    "shadow": shadow,
                    "shadow_color": "#000000",
                    "shadow_blur": shadow_blur,
                    "shadow_offset_x": 0,
                    "shadow_offset_y": shadow_y,
                    "editor_x": x,
                    "editor_y": y,
                    "editor_width": width,
                    "editor_height": height,
                },
                max_chars=max(12, len(text) + 8),
                max_lines=1,
            ).model_dump(mode="json")
        )
    return calibrated


def _pixel_box(box: NormalizedBox | dict[str, Any], size: tuple[int, int]) -> tuple[int, int, int, int]:
    item = box if isinstance(box, NormalizedBox) else NormalizedBox.model_validate(box)
    left = round(item.x * size[0])
    top = round(item.y * size[1])
    right = round((item.x + item.width) * size[0])
    bottom = round((item.y + item.height) * size[1])
    return left, top, max(left + 1, right), max(top + 1, bottom)


def _normalize_template(image: Image.Image) -> Image.Image:
    return ImageOps.fit(image.convert("RGBA"), POSTER_SIZE, Image.Resampling.LANCZOS)


def analyze_poster_template(
    image: Image.Image,
    *,
    text_analysis: TemplateAnalysis | None = None,
) -> dict[str, Any]:
    """Analyze a reusable poster overlay without confusing it with an image2 edit mask."""
    normalized = _normalize_template(image)
    alpha = normalized.getchannel("A")
    histogram = alpha.histogram()
    pixel_count = normalized.width * normalized.height
    transparent_count = sum(histogram[:32])
    translucent_count = sum(histogram[32:240])
    transparent_ratio = transparent_count / pixel_count
    template_type = "alpha_overlay" if transparent_ratio >= 0.03 else "layout_template"
    product_box = None
    if template_type == "alpha_overlay":
        editable_mask = alpha.point(lambda value: 255 if value < 32 else 0)
        bbox = editable_mask.getbbox()
        if bbox:
            left, top, right, bottom = bbox
            product_box = {
                "x": left / normalized.width,
                "y": top / normalized.height,
                "width": (right - left) / normalized.width,
                "height": (bottom - top) / normalized.height,
            }

    text_slots: list[dict[str, Any]] = []
    decoration_regions: list[dict[str, Any]] = []
    if text_analysis is not None:
        detected_slots = list(text_analysis.text_slots)
        for item in detected_slots:
            editable = item.role in {"title", "subtitle", "tag"}
            text_slots.append(
                PosterTextSlot(
                    id=item.id,
                    role=item.role,
                    source_text=item.source_text,
                    editable=editable,
                    box=item.box,
                    style=item.style,
                    max_chars=item.max_chars,
                    max_lines=item.max_lines,
                    confidence=item.confidence,
                    candidate_count=item.candidate_count,
                    consensus_count=item.consensus_count,
                    source_variant=item.source_variant,
                    alternatives=item.alternatives,
                ).model_dump(mode="json")
            )
        decoration_regions = [_box_dict(item) for item in text_analysis.decoration_regions]

    text_slots = _calibrate_hirun_tall_poster(normalize_poster_text_slots(text_slots))
    background_mode = "replace_region"
    if template_type == "layout_template" and text_slots:
        product_box = {"x": 0, "y": 0, "width": 1, "height": 1}
        background_mode = "full_canvas"
    editable_regions = [item["box"] for item in text_slots if item["editable"]]
    fixed_regions = [item["box"] for item in text_slots if not item["editable"]] + decoration_regions
    fingerprint_payload = {
        "alpha": round(transparent_ratio, 6),
        "product_box": product_box,
        "background_mode": background_mode,
        "text_slots": [[item["role"], item["box"]] for item in text_slots],
    }
    return {
        "processing_version": POSTER_PROCESSING_VERSION,
        "template_type": template_type,
        "canvas_width": POSTER_SIZE[0],
        "canvas_height": POSTER_SIZE[1],
        "product_box": product_box,
        "background_mode": background_mode,
        "safe_area": {"x": 0.02, "y": 0.02, "width": 0.96, "height": 0.96},
        "text_slots": text_slots,
        "fixed_regions": fixed_regions,
        "editable_regions": editable_regions,
        "decoration_regions": decoration_regions,
        "ocr_raw_layers": text_analysis.ocr_raw_layers if text_analysis is not None else [],
        "recognition_metrics": text_analysis.recognition_metrics if text_analysis is not None else {},
        "review_status": "pending",
        "alpha_statistics": {
            "transparent_ratio": round(transparent_ratio, 6),
            "translucent_ratio": round(translucent_count / pixel_count, 6),
            "minimum": alpha.getextrema()[0],
            "maximum": alpha.getextrema()[1],
        },
        "layout_fingerprint": hashlib.sha256(
            json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "status": "needs_review" if product_box is not None else "needs_annotation",
    }


def _compact_copy(value: str, limit: int) -> str:
    normalized = re.sub(r"https?://\S+", "", value or "")
    normalized = re.sub(r"[`#>*_\[\](){}]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip("，。！？；：,.!?;:—- ")
    if len(normalized) <= limit:
        return normalized
    clauses = [item for item in re.split(r"[，。！？；：,.!?;:—|｜]+", normalized) if item]
    suitable = next((item for item in clauses if 3 <= len(item) <= limit), None)
    if suitable:
        return suitable
    # Cover headlines benefit more from a complete phrase than a mechanically cut
    # particle. Remove a dispensable attributive particle before taking the prefix.
    # Example: “90%的企业都忽略” becomes “90%企业都忽略”, instead of “90%的企业都忽”.
    primary = clauses[0] if clauses else normalized
    condensed = re.sub(r"(?<=[\u3400-\u9fff0-9%])的(?=[\u3400-\u9fff])", "", primary)
    return condensed[:limit]


def build_poster_copy_plan(
    text_slots: Sequence[dict[str, Any]],
    *,
    title: str = "",
    subtitle: str = "",
    tags: Sequence[str] = (),
    source: str = "template",
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    overrides = overrides or {}
    tag_values = iter(tags)
    title_used = False
    subtitle_used = False
    planned: list[dict[str, Any]] = []
    for raw in normalize_poster_text_slots(text_slots):
        slot = PosterTextSlot.model_validate(raw)
        candidate = slot.source_text
        if not slot.editable:
            fitted = slot.source_text
        elif slot.id in overrides:
            candidate = overrides[slot.id]
            fitted = slot.source_text if candidate == slot.source_text else _compact_copy(candidate, slot.max_chars)
        elif source != "template" and slot.role == "title" and title and not title_used:
            candidate, title_used = title, True
            fitted = _compact_copy(candidate, slot.max_chars)
        elif source != "template" and slot.role == "subtitle" and subtitle and not subtitle_used:
            candidate, subtitle_used = subtitle, True
            fitted = _compact_copy(candidate, slot.max_chars)
        elif source != "template" and slot.role == "tag":
            candidate = next(tag_values, slot.source_text)
            fitted = _compact_copy(candidate, slot.max_chars)
        else:
            fitted = slot.source_text
        fitted = fitted or slot.source_text
        planned.append(
            {
                "slot_id": slot.id,
                "role": slot.role,
                "source_text": slot.source_text,
                "text": fitted,
                "editable": slot.editable,
                "changed": slot.editable and fitted != slot.source_text,
                "max_chars": slot.max_chars,
                "max_lines": slot.max_lines,
            }
        )
    return {"processing_version": POSTER_PROCESSING_VERSION, "source": source, "slots": planned}


def _place_product(
    canvas: Image.Image,
    product: Image.Image,
    box: tuple[int, int, int, int],
    transform: dict[str, Any],
) -> None:
    left, top, right, bottom = box
    box_width, box_height = right - left, bottom - top
    product = product.convert("RGBA")
    fit = transform.get("fit") if transform.get("fit") in {"cover", "contain"} else "cover"
    base_scale = (
        max(box_width / product.width, box_height / product.height)
        if fit == "cover"
        else min(box_width / product.width, box_height / product.height)
    )
    scale = max(0.5, min(2.0, float(transform.get("scale", 1))))
    width = max(1, round(product.width * base_scale * scale))
    height = max(1, round(product.height * base_scale * scale))
    rendered = product.resize((width, height), Image.Resampling.LANCZOS)
    focal_x = max(0, min(1, float(transform.get("focal_x", 0.5))))
    focal_y = max(0, min(1, float(transform.get("focal_y", 0.5))))
    x_offset = max(-0.5, min(0.5, float(transform.get("x_offset", 0))))
    y_offset = max(-0.5, min(0.5, float(transform.get("y_offset", 0))))
    x = round(left + box_width * (0.5 + x_offset) - width * focal_x)
    y = round(top + box_height * (0.5 + y_offset) - height * focal_y)
    clip_left, clip_top = max(left, x), max(top, y)
    clip_right, clip_bottom = min(right, x + width), min(bottom, y + height)
    if clip_right <= clip_left or clip_bottom <= clip_top:
        raise PosterBillboardError("产品图已移出展示区域，请恢复位置后重试")
    crop = rendered.crop((clip_left - x, clip_top - y, clip_right - x, clip_bottom - y))
    canvas.alpha_composite(crop, (clip_left, clip_top))


def _sample_background(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    band = max(2, round((bottom - top) * 0.08))
    samples = []
    for sample_box in (
        (left, max(0, top - band), right, top),
        (left, bottom, right, min(image.height, bottom + band)),
        (max(0, left - band), top, left, bottom),
        (right, top, min(image.width, right + band), bottom),
    ):
        if sample_box[2] > sample_box[0] and sample_box[3] > sample_box[1]:
            samples.append(ImageStat.Stat(image.crop(sample_box).convert("RGB")).median)
    if not samples:
        return 255, 255, 255, 255
    return tuple(round(sum(item[channel] for item in samples) / len(samples)) for channel in range(3)) + (255,)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int, lines: int) -> str:
    if lines <= 1:
        return text
    result: list[str] = []
    current = ""
    for character in text:
        candidate = current + character
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > width:
            result.append(current)
            current = character
        else:
            current = candidate
        if len(result) >= lines:
            break
    if len(result) < lines and current:
        result.append(current)
    return "\n".join(result[:lines])


def _fill_for_character(slot: PosterTextSlot, index: int, text_length: int) -> tuple[int, int, int, int]:
    if not slot.style.fill_runs or text_length <= 0:
        return (*_rgb(slot.style.fill), 255)
    source_length = max(item.end for item in slot.style.fill_runs)
    source_index = min(source_length - 1, int(index * source_length / text_length))
    fill = next(
        (item.fill for item in slot.style.fill_runs if item.start <= source_index < item.end),
        slot.style.fill,
    )
    return (*_rgb(fill), 255)


def _draw_text_slot(canvas: Image.Image, slot: PosterTextSlot, text: str) -> bool:
    draw = ImageDraw.Draw(canvas)
    left, top, right, bottom = _pixel_box(slot.box, canvas.size)
    padding_x = max(2, round((bottom - top) * 0.24))
    padding_y = max(2, round((bottom - top) * 0.15))
    if slot.style.panel_fill:
        draw.rounded_rectangle(
            (left - padding_x, top - padding_y, right + padding_x, bottom + padding_y),
            radius=max(2, round((bottom - top) * slot.style.panel_radius_ratio)),
            fill=(*_rgb(slot.style.panel_fill), round(slot.style.panel_opacity * 255)),
        )
    preferred = max(12, round(slot.style.font_size_ratio * canvas.height))
    selected = None
    for size in range(preferred, max(9, round(preferred * 0.52)) - 1, -1):
        font = _font(size, bold=slot.style.bold)
        wrapped = _wrap_text(draw, text, font, right - left, slot.max_lines)
        spacing = max(2, round(size * 0.12))
        bounds = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=spacing)
        if bounds[2] - bounds[0] <= right - left and bounds[3] - bounds[1] <= bottom - top:
            selected = font, wrapped, spacing, bounds
            break
    if selected is None:
        return True
    font, wrapped, spacing, bounds = selected
    text_width, text_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    if slot.style.align == "left":
        x = left - bounds[0]
    elif slot.style.align == "right":
        x = right - text_width - bounds[0]
    else:
        x = left + (right - left - text_width) / 2 - bounds[0]
    y = top + (bottom - top - text_height) / 2 - bounds[1]
    stroke = max(0, round(font.size * slot.style.stroke_width_ratio)) if hasattr(font, "size") else 0
    if not slot.style.fill_runs or "\n" in wrapped:
        draw.multiline_text(
            (x, y),
            wrapped,
            font=font,
            spacing=spacing,
            fill=(*_rgb(slot.style.fill), 255),
            stroke_width=stroke,
            stroke_fill=(*_rgb(slot.style.stroke or slot.style.fill), 255),
        )
    else:
        cursor = x
        for index, character in enumerate(wrapped):
            draw.text(
                (cursor, y),
                character,
                font=font,
                fill=_fill_for_character(slot, index, len(wrapped)),
                stroke_width=stroke,
                stroke_fill=(*_rgb(slot.style.stroke or slot.style.fill), 255),
            )
            cursor += float(draw.textlength(character, font=font))
    return False


def _paste_original_text_slot(canvas: Image.Image, template: Image.Image, slot: PosterTextSlot) -> None:
    import numpy as np

    left, top, right, bottom = _pixel_box(slot.box, canvas.size)
    height = max(1, bottom - top)
    pad_x = max(3, round(height * (0.3 if slot.style.panel_fill else 0.12)))
    pad_y = max(3, round(height * (0.22 if slot.style.panel_fill else 0.1)))
    box = (
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(canvas.width, right + pad_x),
        min(canvas.height, bottom + pad_y),
    )
    crop = template.crop(box).convert("RGBA")
    rgb = np.asarray(crop.convert("RGB"), dtype=np.int16)
    fills = [slot.style.fill, *(item.fill for item in slot.style.fill_runs)]
    fill_mask = np.zeros(rgb.shape[:2], dtype=bool)
    for fill_value in dict.fromkeys(fills):
        fill = np.asarray(_rgb(fill_value), dtype=np.int16)
        fill_mask |= np.linalg.norm(rgb - fill, axis=2) <= 64
    border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]), axis=0)
    background = np.median(border, axis=0)
    border_distance = np.linalg.norm(border - background, axis=1)
    # A neighbouring headline can cross one edge of the padded crop. Treat the
    # median border colour as background while it still owns most border pixels;
    # using the mean distance made one contaminated edge disable restoration for
    # the whole (otherwise flat) title area.
    uniform_background = float(np.mean(border_distance <= 18)) >= 0.6
    contrast_mask = (
        np.linalg.norm(rgb - background, axis=2) >= 12 if uniform_background else np.zeros(fill_mask.shape, bool)
    )
    if slot.style.stroke:
        stroke = np.asarray(_rgb(slot.style.stroke), dtype=np.int16)
        stroke_mask = np.linalg.norm(rgb - stroke, axis=2) <= 64
        foreground_seed = stroke_mask | contrast_mask
        nearby_foreground = (
            np.asarray(Image.fromarray(foreground_seed.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(11))) > 0
        )
        # White glyph interiors are indistinguishable from a white source
        # background. Their shadow/outline still gives us a reliable local seed,
        # so restore only near that seed instead of copying an opaque rectangle.
        alpha_mask = foreground_seed | (fill_mask & nearby_foreground)
    else:
        contrast = np.linalg.norm(rgb - background, axis=2)
        alpha_mask = (fill_mask & (contrast >= 10)) | contrast_mask
    slot_mask = np.zeros(alpha_mask.shape, dtype=bool)
    slot_mask[top - box[1] : bottom - box[1], left - box[0] : right - box[0]] = True
    alpha_mask &= slot_mask
    if slot.style.panel_fill:
        ImageDraw.Draw(canvas).rounded_rectangle(
            box,
            radius=max(2, round(height * slot.style.panel_radius_ratio)),
            fill=(*_rgb(slot.style.panel_fill), round(slot.style.panel_opacity * 255)),
        )
    alpha = Image.fromarray(alpha_mask.astype(np.uint8) * 255, mode="L").filter(ImageFilter.GaussianBlur(0.45))
    canvas.paste(crop, box[:2], alpha)


def _paste_fixed_region(canvas: Image.Image, template: Image.Image, raw_region: dict[str, Any]) -> None:
    import numpy as np

    box = _pixel_box(raw_region, canvas.size)
    crop = template.crop(box).convert("RGBA")
    rgb = np.asarray(crop.convert("RGB"), dtype=np.int16)
    if rgb.size == 0:
        return
    border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]), axis=0)
    background = np.median(border, axis=0)
    distance = np.linalg.norm(rgb - background, axis=2)
    alpha = np.clip((distance - 18) * 6.5, 0, 255).astype(np.uint8)
    alpha_image = Image.fromarray(alpha, mode="L").filter(ImageFilter.GaussianBlur(0.55))
    if alpha_image.getbbox():
        canvas.paste(crop, box[:2], alpha_image)


def _overlaps(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> bool:
    return min(first[2], second[2]) > max(first[0], second[0]) and min(first[3], second[3]) > max(first[1], second[1])


def _fill_outside_product(
    canvas: Image.Image,
    template: Image.Image,
    erase_box: tuple[int, int, int, int],
    product_box: tuple[int, int, int, int],
) -> None:
    """Erase old glyphs without painting an opaque rectangle over the inserted product."""
    left, top, right, bottom = erase_box
    product_left, product_top, product_right, product_bottom = product_box
    fill = _sample_background(template, erase_box)
    draw = ImageDraw.Draw(canvas)
    regions = (
        (left, top, right, min(bottom, product_top)),
        (left, max(top, product_bottom), right, bottom),
        (left, max(top, product_top), min(right, product_left), min(bottom, product_bottom)),
        (max(left, product_right), max(top, product_top), right, min(bottom, product_bottom)),
    )
    for region in regions:
        if region[2] > region[0] and region[3] > region[1]:
            draw.rectangle(region, fill=fill)


def render_poster_billboard(
    template: Image.Image,
    product: Image.Image,
    template_record: dict[str, Any],
    copy_plan: dict[str, Any],
    *,
    transform: dict[str, Any] | None = None,
    enhanced_background: Image.Image | None = None,
) -> tuple[bytes, dict[str, Any]]:
    product_box = template_record.get("product_box")
    if not product_box:
        raise PosterBillboardError("该画板尚未标注产品替换区域")
    normalized_template = _normalize_template(template)
    transform = transform or {}
    box = _pixel_box(product_box, POSTER_SIZE)
    template_type = template_record.get("template_type") or "layout_template"

    if template_type == "alpha_overlay":
        base = (
            ImageOps.fit(enhanced_background.convert("RGBA"), POSTER_SIZE, Image.Resampling.LANCZOS)
            if enhanced_background is not None
            else Image.new("RGBA", POSTER_SIZE, "white")
        )
        _place_product(base, product, box, transform)
        canvas = Image.alpha_composite(base, normalized_template)
    else:
        canvas = normalized_template.copy()
        _place_product(canvas, product, box, transform)
        base = canvas.copy()
        for region in template_record.get("fixed_regions") or []:
            if _overlaps(_pixel_box(region, POSTER_SIZE), box):
                _paste_fixed_region(canvas, normalized_template, region)

    by_id = {item["slot_id"]: item for item in copy_plan.get("slots") or []}
    overflow_count = 0
    for raw_slot in template_record.get("text_slots") or []:
        slot = PosterTextSlot.model_validate(raw_slot)
        planned = by_id.get(slot.id)
        if not planned:
            continue
        if not planned.get("changed"):
            if template_type == "layout_template" and _overlaps(_pixel_box(slot.box, POSTER_SIZE), box):
                _paste_original_text_slot(canvas, normalized_template, slot)
            continue
        slot_box = _pixel_box(slot.box, POSTER_SIZE)
        padding = max(2, round((slot_box[3] - slot_box[1]) * 0.16))
        erase_box = (
            max(0, slot_box[0] - padding),
            max(0, slot_box[1] - padding),
            min(canvas.width, slot_box[2] + padding),
            min(canvas.height, slot_box[3] + padding),
        )
        if template_type == "alpha_overlay":
            canvas.paste(base.crop(erase_box), erase_box[:2])
        else:
            _fill_outside_product(canvas, normalized_template, erase_box, box)
        overflow_count += int(_draw_text_slot(canvas, slot, str(planned.get("text") or "")))

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue(), {
        "processing_version": POSTER_PROCESSING_VERSION,
        "overflow_count": overflow_count,
        "image2_enhanced": enhanced_background is not None,
        "template_type": template_type,
        "template_lock_strategy": ("alpha_composite" if template_type == "alpha_overlay" else "foreground_restore"),
        "product_lock_strategy": "deterministic_recompose",
        "output_sha256": hashlib.sha256(output.getvalue()).hexdigest(),
    }


def build_image2_protection_mask(template_record: dict[str, Any]) -> tuple[bytes, bool]:
    """Return an OpenAI edit mask: transparent is editable, opaque is locked."""
    product_box = template_record.get("product_box")
    mask = Image.new("RGBA", POSTER_SIZE, (255, 255, 255, 0))
    draw = ImageDraw.Draw(mask)
    if product_box:
        draw.rectangle(_pixel_box(product_box, POSTER_SIZE), fill=(255, 255, 255, 255))
    for region in template_record.get("fixed_regions") or []:
        draw.rectangle(_pixel_box(region, POSTER_SIZE), fill=(255, 255, 255, 255))
    for slot in template_record.get("text_slots") or []:
        draw.rectangle(_pixel_box(slot["box"], POSTER_SIZE), fill=(255, 255, 255, 255))
    alpha = mask.getchannel("A")
    has_editable_area = alpha.getextrema()[0] < 255
    output = io.BytesIO()
    mask.save(output, format="PNG", optimize=True)
    return output.getvalue(), has_editable_area


def evaluate_poster_quality(
    output: Image.Image,
    template_record: dict[str, Any],
    render_metadata: dict[str, Any],
) -> dict[str, Any]:
    failures: list[str] = []
    if output.size != POSTER_SIZE:
        failures.append("输出尺寸不是 1080×1440")
    if output.mode not in {"RGB", "RGBA"}:
        failures.append("输出色彩模式无效")
    if output.mode == "RGBA" and output.getchannel("A").getextrema()[0] < 255:
        failures.append("输出仍包含透明像素")
    overflow_count = int(render_metadata.get("overflow_count") or 0)
    if overflow_count:
        failures.append("文字超出模板槽位")
    product_box = template_record.get("product_box")
    if not product_box:
        failures.append("模板没有产品替换区域")
    template_locked = render_metadata.get("template_lock_strategy") in {
        "alpha_composite",
        "foreground_restore",
    }
    product_locked = render_metadata.get("product_lock_strategy") == "deterministic_recompose"
    if not template_locked:
        failures.append("模板上层未按锁定策略合成")
    if not product_locked:
        failures.append("产品图未按确定性策略重组合")
    report = {
        "processing_version": POSTER_PROCESSING_VERSION,
        "passed": not failures,
        "output_width": output.width,
        "output_height": output.height,
        "output_format": "PNG",
        "template_locked": template_locked,
        "product_locked": product_locked,
        "template_lock_strategy": render_metadata.get("template_lock_strategy"),
        "product_lock_strategy": render_metadata.get("product_lock_strategy"),
        "output_sha256": render_metadata.get("output_sha256"),
        "overflow_count": overflow_count,
        "failures": failures,
    }
    return report
