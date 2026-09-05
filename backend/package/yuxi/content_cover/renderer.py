from __future__ import annotations

import io
from collections.abc import Sequence
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from yuxi.content_cover.templates import COVER_SIZES, COVER_TEMPLATES, COVER_THEMES


class CoverRenderError(ValueError):
    pass


_TEMPLATE_OCR: Any = None


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "C:/Windows/Fonts/msyhbd.ttc",
        )
        if bold
        else (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "C:/Windows/Fonts/msyh.ttc",
        )
    )
    candidates = (*candidates, "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    left, _, right, _ = draw.textbbox((0, 0), text, font=font)
    return right - left


def _wrap_title(
    draw: ImageDraw.ImageDraw,
    title: str,
    font: ImageFont.ImageFont,
    *,
    max_width: int,
    max_lines: int = 3,
) -> str:
    lines: list[str] = []
    truncated = False
    paragraphs = title.splitlines() or [title]
    for paragraph_index, paragraph in enumerate(paragraphs):
        current = ""
        for character in paragraph:
            candidate = f"{current}{character}"
            if current and _text_width(draw, candidate, font) > max_width:
                lines.append(current.rstrip())
                current = character.lstrip()
                if len(lines) >= max_lines:
                    truncated = True
                    break
            else:
                current = candidate
        if truncated:
            break
        if current or not paragraph:
            lines.append(current.rstrip())
        if len(lines) >= max_lines and paragraph_index < len(paragraphs) - 1:
            truncated = True
            break

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    if truncated and lines:
        final_line = lines[-1].rstrip()
        while final_line and _text_width(draw, f"{final_line}…", font) > max_width:
            final_line = final_line[:-1]
        lines[-1] = f"{final_line}…"
    return "\n".join(lines)


def _fit_title(
    draw: ImageDraw.ImageDraw,
    title: str,
    *,
    max_width: int,
    max_height: int,
    preferred_size: int,
    minimum_size: int = 34,
    bold: bool = True,
) -> tuple[ImageFont.ImageFont, str, int, int, int]:
    for size in range(preferred_size, minimum_size - 1, -2):
        font = _font(size, bold=bold)
        spacing = max(8, size // 5)
        text = _wrap_title(draw, title, font, max_width=max_width)
        bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing)
        text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if text_height <= max_height:
            return font, text, text_width, text_height, spacing
    font = _font(minimum_size, bold=bold)
    spacing = max(8, minimum_size // 5)
    text = _wrap_title(draw, title, font, max_width=max_width)
    bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing)
    return font, text, bbox[2] - bbox[0], bbox[3] - bbox[1], spacing


def apply_title_overlay(image: Image.Image, title: str) -> Image.Image:
    """Add a deterministic, readable title to an image2-generated cover."""
    title = title.strip()
    canvas = image.convert("RGBA")
    if not title:
        return canvas

    width, height = canvas.size
    margin = max(28, round(width * 0.055))
    padding_x = max(26, round(width * 0.035))
    padding_y = max(22, round(height * 0.018))
    accent_width = max(7, round(width * 0.008))
    panel_max_width = round(width * 0.87)
    text_max_width = panel_max_width - padding_x * 2 - accent_width
    text_max_height = round(height * 0.25) - padding_y * 2

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font, text, text_width, text_height, spacing = _fit_title(
        draw,
        title,
        max_width=text_max_width,
        max_height=text_max_height,
        preferred_size=max(58, min(92, width // 12)),
    )
    panel_width = min(panel_max_width, text_width + padding_x * 2 + accent_width)
    panel_height = text_height + padding_y * 2
    left, top = margin, margin
    right, bottom = left + panel_width, top + panel_height
    radius = max(18, round(width * 0.024))
    shadow_offset = max(7, round(width * 0.009))

    draw.rounded_rectangle(
        (left + shadow_offset, top + shadow_offset, right + shadow_offset, bottom + shadow_offset),
        radius=radius,
        fill=(0, 0, 0, 48),
    )
    draw.rounded_rectangle((left, top, right, bottom), radius=radius, fill=(250, 248, 243, 238))
    accent_left = left + padding_x // 2
    draw.rounded_rectangle(
        (accent_left, top + padding_y, accent_left + accent_width, bottom - padding_y),
        radius=accent_width // 2,
        fill=(211, 66, 54, 255),
    )
    draw.multiline_text(
        (left + padding_x + accent_width, top + padding_y),
        text,
        fill=(24, 28, 31, 255),
        font=font,
        spacing=spacing,
    )
    return Image.alpha_composite(canvas, overlay)


def _extract_template_text_blocks(image: Image.Image) -> list[dict[str, Any]]:
    """Read text boxes lazily so normal cover generation does not load OCR models."""
    global _TEMPLATE_OCR
    try:
        if _TEMPLATE_OCR is None:
            from yuxi.knowledge.parser.rapid_ocr import RapidOCRParser

            _TEMPLATE_OCR = RapidOCRParser()
        result = _TEMPLATE_OCR.process_image_result(image)
        return list(result.get("blocks") or [])
    except Exception as exc:
        raise CoverRenderError("模板文字识别失败，请确认 OCR 模型可用") from exc


def _template_text_blocks(image: Image.Image) -> list[dict[str, Any]]:
    width, height = image.size
    blocks: list[dict[str, Any]] = []
    for block in _extract_template_text_blocks(image):
        text = str(block.get("text") or "").strip()
        box = block.get("box") or []
        if not text or len(box) < 4:
            continue
        try:
            left = max(0, min(width - 1, round(min(point[0] for point in box))))
            top = max(0, min(height - 1, round(min(point[1] for point in box))))
            right = max(left + 1, min(width, round(max(point[0] for point in box))))
            bottom = max(top + 1, min(height, round(max(point[1] for point in box))))
        except (TypeError, ValueError, IndexError):
            continue
        blocks.append({"text": text, "box": (left, top, right, bottom)})
    return blocks


def _merge_box(blocks: Sequence[dict[str, Any]]) -> tuple[int, int, int, int]:
    boxes = [block["box"] for block in blocks]
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _main_title_group(
    image: Image.Image,
    blocks: Sequence[dict[str, Any]],
    *,
    content_top: int,
) -> list[dict[str, Any]]:
    width, height = image.size
    upper_limit = max(round(height * 0.55), content_top)
    candidates = [
        block
        for block in blocks
        if block["box"][1] < upper_limit
        and (block["box"][2] - block["box"][0]) * (block["box"][3] - block["box"][1])
        >= width * height * 0.002
    ]
    rows: list[list[dict[str, Any]]] = []
    for block in sorted(candidates, key=lambda item: (item["box"][1], item["box"][0])):
        left, top, right, bottom = block["box"]
        block_height = bottom - top
        for row in rows:
            row_left, row_top, row_right, row_bottom = _merge_box(row)
            overlap = min(bottom, row_bottom) - max(top, row_top)
            gap = max(0, left - row_right, row_left - right)
            if overlap >= min(block_height, row_bottom - row_top) * 0.45 and gap <= width * 0.12:
                row.append(block)
                break
        else:
            rows.append([block])
    if not rows:
        raise CoverRenderError("未识别到模板主标题区域，请更换文字清晰的模板")

    def score(row: list[dict[str, Any]]) -> float:
        left, top, right, bottom = _merge_box(row)
        box_height = bottom - top
        return (right - left) * box_height * (1 + box_height / height * 5) * (
            1.5 if (top + bottom) / 2 < height * 0.5 else 1
        )

    return max(rows, key=score)


def _template_foreground_color(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    width, height = image.size
    edge_pixels: list[tuple[int, int, int, int]] = []
    for x in range(max(0, left - 8), min(width, right + 8)):
        for y in (max(0, top - 8), min(height - 1, bottom + 8)):
            edge_pixels.append(image.getpixel((x, y)))
    if not edge_pixels:
        return (24, 48, 79, 255)
    background = tuple(sum(pixel[channel] for pixel in edge_pixels) // len(edge_pixels) for channel in range(4))
    candidates: list[tuple[int, tuple[int, int, int, int]]] = []
    for y in range(top, bottom, 2):
        for x in range(left, right, 2):
            pixel = image.getpixel((x, y))
            distance = sum(abs(pixel[channel] - background[channel]) for channel in range(3))
            if distance > 30:
                candidates.append((distance, pixel))
    if not candidates:
        return (24, 48, 79, 255)
    candidates.sort(key=lambda item: item[0], reverse=True)
    strongest = [pixel for _, pixel in candidates[: min(500, len(candidates))]]
    return tuple(sum(pixel[channel] for pixel in strongest) // len(strongest) for channel in range(4))


def _erase_template_text(
    image: Image.Image,
    box: tuple[int, int, int, int],
    *,
    reference: Image.Image | None = None,
    background_source: Image.Image | None = None,
) -> None:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise CoverRenderError("模板文字适配依赖 OpenCV，但当前环境未安装") from exc

    width, height = image.size
    left, top, right, bottom = box
    padding_x = max(3, round((bottom - top) * 0.12))
    padding_y = max(2, round((bottom - top) * 0.08))
    left, top = max(0, left - padding_x), max(0, top - padding_y)
    right, bottom = min(width, right + padding_x), min(height, bottom + padding_y)
    rgb = np.asarray(image.convert("RGB")).copy()
    mask = np.zeros((height, width), dtype=np.uint8)

    if reference is not None:
        reference_rgb = np.asarray(reference.convert("RGB"))
        foreground = np.asarray(_template_foreground_color(reference.convert("RGBA"), box)[:3])
        region = reference_rgb[top:bottom, left:right].astype(np.int16)
        color_distance = np.linalg.norm(region - foreground, axis=2)
        glyph_mask = (color_distance <= 72).astype(np.uint8) * 255
        kernel_size = max(3, round((box[3] - box[1]) * 0.045))
        if kernel_size % 2 == 0:
            kernel_size += 1
        glyph_mask = cv2.dilate(
            glyph_mask,
            np.ones((kernel_size, kernel_size), dtype=np.uint8),
            iterations=1,
        )
        mask[top:bottom, left:right] = glyph_mask

    if cv2.countNonZero(mask) < max(8, round((right - left) * (bottom - top) * 0.004)):
        if background_source is not None:
            image.paste(background_source.crop((left, top, right, bottom)), (left, top))
        return

    repaired = cv2.inpaint(
        rgb,
        mask,
        max(2, round((box[3] - box[1]) * 0.04)),
        cv2.INPAINT_TELEA,
    )
    image.paste(Image.fromarray(repaired).convert("RGBA"))


def _template_overlay_mask(
    size: tuple[int, int],
    blocks: Sequence[dict[str, Any]],
) -> Image.Image:
    """Build soft regions around template copy and its attached stickers/panels."""
    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for block in blocks:
        left, top, right, bottom = block["box"]
        block_width, block_height = right - left, bottom - top
        padding_x = max(round(block_height * 1.35), round(block_width * 0.055))
        padding_y = max(round(block_height * 0.48), round(height * 0.006))
        expanded = (
            max(0, left - padding_x),
            max(0, top - padding_y),
            min(width, right + padding_x),
            min(height, bottom + padding_y),
        )
        radius = max(4, round(block_height * 0.38))
        draw.rounded_rectangle(expanded, radius=radius, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(radius=max(2, round(min(size) * 0.004))))


def _expanded_box(
    box: tuple[int, int, int, int],
    *,
    size: tuple[int, int],
    padding_x: int,
    padding_y: int,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    return (
        max(0, left - padding_x),
        max(0, top - padding_y),
        min(size[0], right + padding_x),
        min(size[1], bottom + padding_y),
    )


def _paste_centered(
    canvas: Image.Image,
    layer: Image.Image,
    mask: Image.Image,
    *,
    top: int,
    offset_x: int = 0,
) -> None:
    left = max(0, min(canvas.width - layer.width, (canvas.width - layer.width) // 2 + offset_x))
    top = max(0, min(canvas.height - layer.height, top))
    canvas.paste(layer, (left, top), mask)


def _stacked_poster_source(source: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Leave a top safe area while retaining substantially more of the uploaded image."""
    width, height = size
    source = source.convert("RGBA")
    resized_height = round(height * 0.895)
    resized_width = max(width, round(source.width * resized_height / source.height))
    resized = source.resize((resized_width, resized_height), Image.Resampling.LANCZOS)
    sample_x = min(5, source.width - 1)
    sample_y = min(5, source.height - 1)
    background = Image.new("RGBA", size, source.getpixel((sample_x, sample_y)))
    background.paste(resized, ((width - resized_width) // 2, round(height * 0.064)))
    return background


def _draw_poster_text(
    canvas: Image.Image,
    text: str,
    box: tuple[int, int, int, int],
    *,
    fill: tuple[int, int, int, int],
    bold: bool = True,
    shadow: bool = False,
) -> None:
    left, top, right, bottom = box
    draw = ImageDraw.Draw(canvas)
    font, fitted, text_width, text_height, spacing = _fit_title(
        draw,
        text,
        max_width=max(20, right - left),
        max_height=max(20, bottom - top),
        preferred_size=max(18, round((bottom - top) * 0.82)),
        minimum_size=14,
        bold=bold,
    )
    text_bbox = draw.multiline_textbbox((0, 0), fitted, font=font, spacing=spacing)
    text_left = left + (right - left - text_width) // 2 - text_bbox[0]
    text_top = top + (bottom - top - text_height) // 2 - text_bbox[1]
    stroke_width = max(1, round(getattr(font, "size", 24) * 0.025)) if shadow else 0
    if shadow:
        offset = max(3, round(getattr(font, "size", 24) * 0.07))
        draw.multiline_text(
            (text_left + offset, text_top + offset),
            fitted,
            fill=(18, 18, 18, 220),
            font=font,
            spacing=spacing,
            stroke_width=stroke_width,
            stroke_fill=(18, 18, 18, 230),
        )
    draw.multiline_text(
        (text_left, text_top),
        fitted,
        fill=fill,
        font=font,
        spacing=spacing,
        stroke_width=stroke_width,
        stroke_fill=(25, 25, 25, 220) if stroke_width else None,
    )


def _saturated_template_mask(
    template: Image.Image,
    blocks: Sequence[dict[str, Any]],
) -> Image.Image:
    """Keep original colored pixels without inventing outlines or shadows."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise CoverRenderError("模板图层提取依赖 OpenCV，但当前环境未安装") from exc

    rgb = np.asarray(template.convert("RGB"))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    saturation = hsv[:, :, 1].astype(np.float32)
    brightness = hsv[:, :, 2].astype(np.float32)
    alpha = np.minimum(
        np.clip((saturation - 22) * 255 / 62, 0, 255),
        np.clip((brightness - 48) * 255 / 92, 0, 255),
    ).astype(np.uint8)
    region_mask = np.zeros(alpha.shape, dtype=np.uint8)
    for block in blocks:
        left, top, right, bottom = _expanded_box(
            block["box"],
            size=template.size,
            padding_x=max(8, round(template.width * 0.065)),
            padding_y=max(6, round(template.height * 0.018)),
        )
        region_mask[top:bottom, left:right] = 255
    alpha = cv2.bitwise_and(alpha, region_mask)
    alpha = cv2.GaussianBlur(alpha, (3, 3), 0)
    return Image.fromarray(alpha, mode="L")


def _bright_glyph_layer(
    template: Image.Image,
    box: tuple[int, int, int, int],
    *,
    minimum_height_ratio: float = 0.08,
) -> tuple[Image.Image, Image.Image]:
    """Extract exact bright title pixels and their anti-aliased edges."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise CoverRenderError("模板文字图层提取依赖 OpenCV，但当前环境未安装") from exc

    block_height = box[3] - box[1]
    crop_box = _expanded_box(
        box,
        size=template.size,
        padding_x=max(5, round(block_height * 0.055)),
        padding_y=max(5, round(block_height * 0.055)),
    )
    layer = template.crop(crop_box).convert("RGBA")
    rgb = np.asarray(layer.convert("RGB"))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    seed = ((hsv[:, :, 2] >= 235) & (hsv[:, :, 1] <= 58)).astype(np.uint8) * 255
    count, labels, stats, _ = cv2.connectedComponentsWithStats(seed, 8)
    selected = np.zeros_like(seed)
    crop_height, crop_width = seed.shape
    minimum_area = max(12, round(crop_width * crop_height * 0.00045))
    for index in range(1, count):
        left, top, width, height, area = stats[index]
        touches_edge = (
            left <= 1
            or top <= 1
            or left + width >= crop_width - 1
            or top + height >= crop_height - 1
        )
        if (
            not touches_edge
            and area >= minimum_area
            and height >= crop_height * minimum_height_ratio
            and width >= crop_width * 0.008
        ):
            selected[labels == index] = 255
    if cv2.countNonZero(selected) == 0:
        raise CoverRenderError("未能从模板中分离主标题字形，请更换文字更清晰的模板")
    neighborhood = cv2.dilate(selected, np.ones((5, 5), dtype=np.uint8), iterations=1)
    antialias = (
        (hsv[:, :, 2] >= 178)
        & (hsv[:, :, 1] <= 105)
        & (neighborhood > 0)
    ).astype(np.uint8) * 255
    antialias = cv2.GaussianBlur(antialias, (3, 3), 0)
    return layer, Image.fromarray(antialias, mode="L")


def _fit_layer(
    layer: Image.Image,
    mask: Image.Image,
    *,
    max_width: int,
    max_height: int,
) -> tuple[Image.Image, Image.Image]:
    scale = min(1.0, max_width / layer.width, max_height / layer.height)
    if scale >= 1:
        return layer, mask
    size = (max(1, round(layer.width * scale)), max(1, round(layer.height * scale)))
    return (
        layer.resize(size, Image.Resampling.LANCZOS),
        mask.resize(size, Image.Resampling.LANCZOS),
    )


def _template_panel_layer(
    template: Image.Image,
    block: dict[str, Any],
    *,
    padding_x: int,
    padding_y: int,
    replacement_text: str = "",
    glyph_minimum_height_ratio: float = 0.16,
) -> tuple[Image.Image, Image.Image]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise CoverRenderError("模板面板提取依赖 OpenCV，但当前环境未安装") from exc

    box = block["box"]
    crop_box = _expanded_box(
        box,
        size=template.size,
        padding_x=padding_x,
        padding_y=padding_y,
    )
    reference_crop = template.crop(crop_box).convert("RGB")
    rgb = np.asarray(reference_crop)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    candidates = rgb[
        (hsv[:, :, 1] <= 90)
        & (hsv[:, :, 2] >= 45)
        & (hsv[:, :, 2] <= 235)
    ]
    if candidates.size:
        quantized = (candidates // 16).astype(np.uint8)
        colors, counts = np.unique(quantized, axis=0, return_counts=True)
        dominant = colors[int(np.argmax(counts))]
        panel_pixels = candidates[np.all(quantized == dominant, axis=1)]
        panel_color = tuple(int(value) for value in np.median(panel_pixels, axis=0))
    else:
        panel_color = (128, 128, 128)
    layer = Image.new("RGBA", reference_crop.size, (0, 0, 0, 0))
    mask = Image.new("L", layer.size, 0)
    radius = max(4, round(layer.height * 0.2))
    ImageDraw.Draw(layer).rounded_rectangle(
        (1, 1, layer.width - 1, layer.height - 1),
        radius=radius,
        fill=(*panel_color, 242),
    )
    ImageDraw.Draw(mask).rounded_rectangle(
        (1, 1, layer.width - 1, layer.height - 1),
        radius=radius,
        fill=255,
    )
    replacement_text = replacement_text.strip()
    if not replacement_text:
        glyph, glyph_mask = _bright_glyph_layer(
            template,
            box,
            minimum_height_ratio=glyph_minimum_height_ratio,
        )
        glyph, glyph_mask = _fit_layer(
            glyph,
            glyph_mask,
            max_width=max(1, layer.width - padding_x),
            max_height=max(1, layer.height - padding_y),
        )
        _paste_centered(
            layer,
            glyph,
            glyph_mask,
            top=max(0, (layer.height - glyph.height) // 2),
        )
        return layer, mask

    _draw_poster_text(
        layer,
        replacement_text,
        (
            padding_x,
            padding_y,
            layer.width - padding_x,
            layer.height - padding_y,
        ),
        fill=_template_foreground_color(template, box),
        bold=True,
        shadow=False,
    )
    return layer, mask


def _stacked_poster_overlay(
    source: Image.Image,
    original_source: Image.Image,
    generated: Image.Image,
    template: Image.Image,
    template_blocks: Sequence[dict[str, Any]],
    generated_blocks: Sequence[dict[str, Any]],
    *,
    title: str,
    subtitle: str,
) -> Image.Image | None:
    """Transfer a stacked poster's real pixels while replacing only requested copy."""
    width, height = source.size
    top_template = [
        block
        for block in template_blocks
        if block["box"][1] < height * 0.18
        and block["box"][3] - block["box"][1] < height * 0.09
    ]
    bottom_template = [block for block in template_blocks if block["box"][1] > height * 0.82]
    subtitle_template = [block for block in template_blocks if str(block["text"]).count("/") >= 3]
    main_template = [
        block
        for block in template_blocks
        if block["box"][1] < height * 0.45
        and block["box"][3] - block["box"][1] > height * 0.1
        and block not in top_template
    ]
    if not (
        len(top_template) >= 2
        and bottom_template
        and subtitle_template
        and main_template
    ):
        return None

    canvas = _stacked_poster_source(original_source, source.size)

    color_mask = _saturated_template_mask(template, top_template)
    canvas = Image.composite(template.convert("RGBA"), canvas, color_mask)

    main_block = max(
        main_template,
        key=lambda block: (block["box"][2] - block["box"][0])
        * (block["box"][3] - block["box"][1]),
    )
    main_layer, main_mask = _bright_glyph_layer(template, main_block["box"])
    main_color = tuple(round(value) for value in ImageStat.Stat(main_layer.convert("RGB"), main_mask).median)
    main_top = round(height * 0.29)
    if title.strip():
        _draw_poster_text(
            canvas,
            title.strip(),
            (round(width * 0.06), main_top, round(width * 0.94), round(height * 0.465)),
            fill=(*main_color, 255),
            shadow=False,
        )
    else:
        main_layer, main_mask = _fit_layer(
            main_layer,
            main_mask,
            max_width=round(width * 0.9),
            max_height=round(height * 0.18),
        )
        _paste_centered(canvas, main_layer, main_mask, top=main_top)

    template_subtitle = max(subtitle_template, key=lambda block: len(str(block["text"])))
    subtitle_layer, subtitle_mask = _template_panel_layer(
        template,
        template_subtitle,
        padding_x=max(6, round(width * 0.008)),
        padding_y=max(3, round(height * 0.003)),
        replacement_text=subtitle,
    )
    subtitle_layer, subtitle_mask = _fit_layer(
        subtitle_layer,
        subtitle_mask,
        max_width=round(width * 0.9),
        max_height=round(height * 0.07),
    )
    _paste_centered(canvas, subtitle_layer, subtitle_mask, top=round(height * 0.475))

    template_slogan = max(
        bottom_template,
        key=lambda block: (block["box"][2] - block["box"][0])
        * (block["box"][3] - block["box"][1]),
    )
    slogan_layer, slogan_mask = _template_panel_layer(
        template,
        template_slogan,
        padding_x=max(8, round(width * 0.012)),
        padding_y=max(4, round(height * 0.003)),
        glyph_minimum_height_ratio=0.1,
    )
    slogan_layer, slogan_mask = _fit_layer(
        slogan_layer,
        slogan_mask,
        max_width=round(width * 0.86),
        max_height=round(height * 0.09),
    )
    _paste_centered(canvas, slogan_layer, slogan_mask, top=round(height * 0.9))
    return canvas


def _merge_generated_template_overlay(
    source: Image.Image,
    original_source: Image.Image,
    generated: Image.Image,
    template: Image.Image,
    generated_blocks: Sequence[dict[str, Any]],
    *,
    title: str,
    subtitle: str,
) -> tuple[Image.Image, bool]:
    """Keep the uploaded source exact and take only template-style overlay neighborhoods."""
    blocks = _template_text_blocks(template)
    if not blocks:
        return generated.convert("RGBA"), False
    stacked = _stacked_poster_overlay(
        source,
        original_source,
        generated,
        template,
        blocks,
        generated_blocks,
        title=title,
        subtitle=subtitle,
    )
    if stacked is not None:
        return stacked, True
    mask = _template_overlay_mask(source.size, blocks)
    return Image.composite(generated.convert("RGBA"), source.convert("RGBA"), mask), False


def _validate_source_preservation(source: Image.Image, generated: Image.Image) -> None:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise CoverRenderError("模板复刻质量检查依赖 OpenCV，但当前环境未安装") from exc

    def descriptors(image: Image.Image):
        grayscale = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
        height, width = grayscale.shape
        scale = min(1.0, 768 / max(width, height))
        if scale < 1:
            grayscale = cv2.resize(
                grayscale,
                (max(2, round(width * scale)), max(2, round(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
        return cv2.ORB_create(nfeatures=1200).detectAndCompute(grayscale, None)[1]

    source_descriptors = descriptors(source)
    generated_descriptors = descriptors(generated)
    if source_descriptors is None or len(source_descriptors) < 24:
        return
    if generated_descriptors is None:
        raise CoverRenderError("image2 未保留原图主体，请调整模板或生成要求后重试")
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(
        source_descriptors,
        generated_descriptors,
        k=2,
    )
    good_matches = [
        pair[0]
        for pair in matches
        if len(pair) == 2 and pair[0].distance < 0.78 * pair[1].distance
    ]
    minimum = max(10, round(len(source_descriptors) * 0.025))
    if len(good_matches) < minimum:
        raise CoverRenderError("image2 未保留原图主体，请调整模板或生成要求后重试")


def _draw_template_text(
    image: Image.Image,
    text: str,
    blocks: Sequence[dict[str, Any]],
    *,
    bold: bool,
    color_reference: Image.Image | None = None,
    background_source: Image.Image | None = None,
) -> None:
    box = _merge_box(blocks)
    reference = color_reference or image
    colors = [_template_foreground_color(reference, block["box"]) for block in blocks]
    _erase_template_text(
        image,
        box,
        reference=reference,
        background_source=background_source,
    )
    left, top, right, bottom = box
    box_width, box_height = right - left, bottom - top
    draw = ImageDraw.Draw(image)
    preferred_size = max(16, round(box_height * (0.86 if bold else 0.74)))
    minimum_size = max(12, round(preferred_size * 0.58))
    font, fitted, text_width, text_height, spacing = _fit_title(
        draw,
        text,
        max_width=max(20, box_width),
        max_height=max(16, round(box_height * 1.35)),
        preferred_size=preferred_size,
        minimum_size=minimum_size,
        bold=bold,
    )
    center_x = (left + right) / 2
    canvas_center = image.width / 2
    if abs(center_x - canvas_center) <= image.width * 0.1:
        text_left = round(center_x - text_width / 2)
    elif center_x < canvas_center:
        text_left = left
    else:
        text_left = right - text_width
    text_top = top + (box_height - text_height) // 2
    shadow_offset = max(2, round(preferred_size * 0.055)) if bold and preferred_size >= 48 else 0
    stroke_width = max(1, round(preferred_size * 0.025)) if shadow_offset else 0
    if "\n" in fitted or len(colors) == 1:
        if shadow_offset:
            draw.multiline_text(
                (text_left + shadow_offset, text_top + shadow_offset),
                fitted,
                fill=(18, 18, 18, 210),
                font=font,
                spacing=spacing,
                stroke_width=stroke_width,
                stroke_fill=(18, 18, 18, 220),
            )
        draw.multiline_text(
            (text_left, text_top),
            fitted,
            fill=colors[0],
            font=font,
            spacing=spacing,
            stroke_width=stroke_width,
            stroke_fill=(25, 25, 25, 220) if stroke_width else None,
        )
        return
    original_lengths = [max(1, len(str(block["text"]))) for block in blocks]
    total_length = sum(original_lengths)
    start = 0
    current_x = text_left
    for index, original_length in enumerate(original_lengths):
        end = len(fitted) if index == len(original_lengths) - 1 else round(
            len(fitted) * sum(original_lengths[: index + 1]) / total_length
        )
        segment = fitted[start:end]
        draw.text((current_x, text_top), segment, fill=colors[index], font=font)
        current_x += _text_width(draw, segment, font)
        start = end


def apply_template_title(
    image: Image.Image,
    title: str,
    *,
    reference: Image.Image | None = None,
    content_top: int | None = None,
) -> Image.Image:
    """Replace the template's main title while keeping its box, alignment and color segments."""
    title = title.strip()
    canvas = image.convert("RGBA")
    if not title:
        return canvas
    reference = (reference or canvas).convert("RGBA")
    blocks = _template_text_blocks(reference)
    title_blocks = _main_title_group(
        reference,
        blocks,
        content_top=content_top if content_top is not None else round(reference.height * 0.55),
    )
    _draw_template_text(canvas, title, title_blocks, bold=True, color_reference=reference)
    return canvas


def apply_template_content(
    image: Image.Image,
    *,
    reference: Image.Image,
    title: str,
    subtitle: str = "",
    tags: Sequence[str] = (),
    content_top: int,
    background_source: Image.Image | None = None,
) -> Image.Image:
    """Adapt content only into text regions that already exist in the template."""
    canvas = image.convert("RGBA")
    blocks = _template_text_blocks(reference)
    title_blocks = _main_title_group(reference, blocks, content_top=content_top)
    _draw_template_text(
        canvas,
        title,
        title_blocks,
        bold=True,
        color_reference=reference,
        background_source=background_source,
    )
    title_box = _merge_box(title_blocks)
    title_ids = {id(block) for block in title_blocks}
    remaining = [
        block
        for block in blocks
        if id(block) not in title_ids
        and block["box"][1] >= title_box[3]
        and block["box"][3] <= content_top
    ]
    subtitle_block = None
    if subtitle.strip() and remaining:
        title_height = title_box[3] - title_box[1]
        subtitle_candidates = [
            block
            for block in remaining
            if block["box"][3] - block["box"][1] <= title_height * 0.85
        ]
        if subtitle_candidates:
            subtitle_block = max(
                subtitle_candidates,
                key=lambda item: (item["box"][2] - item["box"][0])
                * (item["box"][3] - item["box"][1]),
            )
            _draw_template_text(
                canvas,
                subtitle.strip(),
                [subtitle_block],
                bold=False,
                color_reference=reference,
            )

    tag_candidates = [
        block
        for block in remaining
        if block is not subtitle_block
        and (subtitle_block is None or block["box"][1] >= subtitle_block["box"][3])
    ]
    if tags and tag_candidates:
        smallest_height = min(block["box"][3] - block["box"][1] for block in tag_candidates)
        tag_candidates = [
            block
            for block in tag_candidates
            if block["box"][3] - block["box"][1] <= smallest_height * 1.6
        ]
        for block, tag in zip(
            sorted(tag_candidates, key=lambda item: (item["box"][1], item["box"][0])),
            tags,
            strict=False,
        ):
            if tag.strip():
                _draw_template_text(
                    canvas,
                    tag.strip(),
                    [block],
                    bold=False,
                    color_reference=reference,
                )
    return canvas


def _encode_png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _open_full_image(data: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.load()
            return image
    except OSError as exc:
        raise CoverRenderError("模板复刻素材不是有效图片") from exc


def finalize_template_transfer(
    template_data: bytes,
    source_data: bytes,
    generated_data: bytes,
    *,
    target_size: tuple[int, int],
    title: str,
    subtitle: str = "",
    tags: Sequence[str] = (),
) -> bytes:
    """Normalize image2 output and adapt copy in the template's original text regions."""
    template = ImageOps.fit(_open_full_image(template_data), target_size, Image.Resampling.LANCZOS)
    original_source = _open_full_image(source_data)
    source = ImageOps.fit(original_source, target_size, Image.Resampling.LANCZOS)
    composite = ImageOps.fit(
        _open_full_image(generated_data),
        target_size,
        Image.Resampling.LANCZOS,
    )
    generated_blocks = _template_text_blocks(composite)
    generated_text = " ".join(block["text"] for block in generated_blocks).lower()
    if any(
        marker in generated_text
        for marker in ("缺少参考图", "未提供参考图", "missing reference", "reference image missing")
    ):
        raise CoverRenderError("image2 未接收到模板复刻参考图，请缩小图片后重试")
    composite, used_stacked_layout = _merge_generated_template_overlay(
        source,
        original_source,
        composite,
        template,
        generated_blocks,
        title=title,
        subtitle=subtitle,
    )
    if not used_stacked_layout:
        _validate_source_preservation(source, composite)
    if title.strip() and not used_stacked_layout:
        composite = apply_template_content(
            composite,
            reference=template,
            title=title,
            subtitle=subtitle,
            tags=tags,
            content_top=round(template.height * 0.52),
            background_source=source,
        )
    opaque = Image.new("RGB", composite.size, "white")
    opaque.paste(composite.convert("RGB"))
    return _encode_png(opaque)


def _open_image(data: bytes, *, max_size: tuple[int, int]) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.load()
            image.thumbnail(max_size, Image.Resampling.LANCZOS)
            return image
    except OSError as exc:
        raise CoverRenderError("素材不是有效图片") from exc


def _place(
    canvas: Image.Image,
    source: Image.Image,
    box: tuple[int, int, int, int],
    *,
    fit: str = "cover",
    radius: int = 0,
    background: str = "#FFFFFF",
) -> None:
    left, top, right, bottom = box
    width, height = max(1, right - left), max(1, bottom - top)
    if fit == "contain":
        rendered = ImageOps.contain(source, (width, height), Image.Resampling.LANCZOS)
        tile = Image.new("RGBA", (width, height), background)
        tile.alpha_composite(rendered, ((width - rendered.width) // 2, (height - rendered.height) // 2))
    else:
        tile = ImageOps.fit(source, (width, height), Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    if radius:
        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, width, height), radius=radius, fill=255)
        canvas.paste(tile, (left, top), mask)
    else:
        canvas.alpha_composite(tile, (left, top))


def _validate(template_id: str, theme_id: str, size: str, count: int) -> tuple[dict, dict, dict]:
    template = COVER_TEMPLATES.get(template_id)
    theme = COVER_THEMES.get(theme_id)
    dimensions = COVER_SIZES.get(size)
    if not template:
        raise CoverRenderError("封面版式不存在")
    if not theme:
        raise CoverRenderError("封面主题不存在")
    if not dimensions:
        raise CoverRenderError("封面尺寸不支持")
    if count < template["min_assets"] or count > template["max_assets"]:
        raise CoverRenderError(
            f"{template['name']} 需要 {template['min_assets']}–{template['max_assets']} 张图片"
        )
    return template, theme, dimensions


def render_cover(
    image_bytes: Sequence[bytes],
    *,
    template_id: str,
    theme_id: str,
    size: str,
    layout: dict | None = None,
) -> bytes:
    _, theme, dimensions = _validate(template_id, theme_id, size, len(image_bytes))
    width, height = dimensions["width"], dimensions["height"]
    images = [_open_image(data, max_size=(width * 2, height * 2)) for data in image_bytes]
    canvas = Image.new("RGBA", (width, height), theme["background"])
    draw = ImageDraw.Draw(canvas)
    options = layout or {}
    gap = max(0, min(int(options.get("gap", 18)), 80))
    margin = max(0, min(int(options.get("margin", 24)), 120))
    fit = options.get("fit", "cover") if options.get("fit") in {"cover", "contain"} else "cover"

    if template_id == "grid_3x3":
        cell_width = (width - margin * 2 - gap * 2) // 3
        cell_height = (height - margin * 2 - gap * 2) // 3
        for index, image in enumerate(images):
            row, column = divmod(index, 3)
            x = margin + column * (cell_width + gap)
            y = margin + row * (cell_height + gap)
            _place(canvas, image, (x, y, x + cell_width, y + cell_height), fit=fit, radius=18)

    elif template_id == "split_vertical":
        split = max(0.35, min(float(options.get("split", 0.5)), 0.65))
        first_width = int((width - margin * 2 - gap) * split)
        x0, y0, y1 = margin, margin, height - margin
        _place(canvas, images[0], (x0, y0, x0 + first_width, y1), fit=fit, radius=22)
        _place(canvas, images[1], (x0 + first_width + gap, y0, width - margin, y1), fit=fit, radius=22)

    elif template_id == "split_horizontal":
        split = max(0.35, min(float(options.get("split", 0.5)), 0.65))
        first_height = int((height - margin * 2 - gap) * split)
        x0, x1, y0 = margin, width - margin, margin
        _place(canvas, images[0], (x0, y0, x1, y0 + first_height), fit=fit, radius=22)
        _place(canvas, images[1], (x0, y0 + first_height + gap, x1, height - margin), fit=fit, radius=22)

    elif template_id == "before_after":
        half = width // 2
        _place(canvas, images[0], (0, 0, half, height), fit=fit)
        _place(canvas, images[1], (half, 0, width, height), fit=fit)
        draw.rectangle((half - 4, 0, half + 4, height), fill=theme["surface"])
        label_font = _font(42)
        for text, x in (("前", 46), ("后", half + 46)):
            draw.rounded_rectangle((x, 48, x + 96, 116), radius=34, fill=theme["accent"])
            draw.text((x + 27, 57), text, fill="#FFFFFF", font=label_font)

    elif template_id == "card_stack":
        _place(canvas, images[0], (0, 0, width, height), fit=fit)
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 50))
        canvas.alpha_composite(overlay)
        card_count = len(images) - 1
        card_width = int(width * 0.72)
        card_height = int(height * 0.24)
        start_y = height - margin - card_height - (card_count - 1) * 46
        for index, image in enumerate(images[1:]):
            x = width - margin - card_width - index * 18
            y = start_y + index * 46
            shadow_box = (x + 10, y + 12, x + card_width + 10, y + card_height + 12)
            draw.rounded_rectangle(shadow_box, radius=26, fill=(0, 0, 0, 70))
            _place(canvas, image, (x, y, x + card_width, y + card_height), fit=fit, radius=26)
            draw.rounded_rectangle(
                (x, y, x + card_width, y + card_height),
                radius=26,
                outline=theme["surface"],
                width=4,
            )

    elif template_id == "hero_thumbs":
        thumb_height = int(height * 0.23)
        hero_bottom = height - margin - thumb_height - gap
        _place(canvas, images[0], (margin, margin, width - margin, hero_bottom), fit=fit, radius=24)
        thumb_count = len(images) - 1
        thumb_width = (width - margin * 2 - gap * (thumb_count - 1)) // thumb_count
        for index, image in enumerate(images[1:]):
            x = margin + index * (thumb_width + gap)
            _place(
                canvas,
                image,
                (x, hero_bottom + gap, x + thumb_width, height - margin),
                fit=fit,
                radius=18,
            )

    title = str(options.get("title") or "").strip()
    if title:
        font_size = max(36, min(int(options.get("title_size", 62)), 96))
        title_font, text, text_width, text_height, spacing = _fit_title(
            draw,
            title,
            max_width=width - 172,
            max_height=round(height * 0.25),
            preferred_size=font_size,
            minimum_size=34,
        )
        box_width, box_height = text_width + 64, text_height + 48
        x = 42
        y = height - box_height - 42 if template_id in {"grid_3x3", "before_after"} else 42
        draw.rounded_rectangle((x, y, x + box_width, y + box_height), radius=20, fill=theme["surface"])
        draw.multiline_text(
            (x + 32, y + 21),
            text,
            fill=theme["foreground"],
            font=title_font,
            spacing=spacing,
        )

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()
