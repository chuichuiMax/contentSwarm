from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


class CoverEditorRenderError(ValueError):
    pass


def _font(layer: dict[str, Any]) -> ImageFont.ImageFont:
    size = max(8, round(float(layer.get("font_size") or 64)))
    family = str(layer.get("font_family") or "").lower()
    bold = int(layer.get("font_weight") or 400) >= 600
    italic = layer.get("font_style") == "italic"
    serif = "serif" in family or "宋" in family or "simsun" in family or "georgia" in family
    linux_name = (
        "NotoSerifCJK-Bold.ttc"
        if serif and bold
        else "NotoSerifCJK-Regular.ttc"
        if serif
        else "NotoSansCJK-Bold.ttc"
        if bold
        else "NotoSansCJK-Regular.ttc"
    )
    windows_name = "simkai.ttf" if italic else "simhei.ttf" if bold else "simsun.ttc" if serif else "msyh.ttc"
    if "georgia" in family:
        candidates = (
            f"/usr/share/fonts/truetype/liberation/LiberationSerif-{'Bold' if bold else 'Regular'}.ttf",
            f"C:/Windows/Fonts/{'georgiab.ttf' if bold else 'georgia.ttf'}",
        )
    elif "arial" in family:
        candidates = (
            f"/usr/share/fonts/truetype/liberation/LiberationSans-{'Bold' if bold else 'Regular'}.ttf",
            f"C:/Windows/Fonts/{'arialbd.ttf' if bold else 'arial.ttf'}",
        )
    else:
        candidates = (
            f"/usr/share/fonts/opentype/noto/{linux_name}",
            f"C:/Windows/Fonts/{windows_name}",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        )
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _rgba(color: str | None, opacity: float = 1) -> tuple[int, int, int, int]:
    value = str(color or "#000000").lstrip("#")
    if len(value) != 6:
        value = "000000"
    try:
        rgb = tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        rgb = (0, 0, 0)
    return (*rgb, max(0, min(255, round(opacity * 255))))


def _character_width(draw: ImageDraw.ImageDraw, character: str, font: ImageFont.ImageFont) -> float:
    try:
        return float(draw.textlength(character, font=font))
    except AttributeError:
        box = draw.textbbox((0, 0), character, font=font)
        return float(box[2] - box[0])


def _spaced_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, spacing: float) -> float:
    characters = list(text)
    return sum(_character_width(draw, item, font) for item in characters) + max(0, len(characters) - 1) * spacing


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    width: float,
    spacing: float,
    max_lines: int,
) -> list[str]:
    lines: list[str] = []
    for paragraph in str(text or "").split("\n"):
        current = ""
        for character in paragraph:
            candidate = f"{current}{character}"
            if current and _spaced_width(draw, candidate, font, spacing) > width:
                lines.append(current)
                current = character
            else:
                current = candidate
            if len(lines) >= max_lines:
                break
        if len(lines) >= max_lines:
            break
        lines.append(current)
    lines = lines[:max_lines] or [""]
    consumed = "".join(lines)
    source = str(text or "").replace("\n", "")
    if len(consumed) < len(source) and lines:
        suffix = "…"
        candidate = lines[-1]
        while candidate and _spaced_width(draw, f"{candidate}{suffix}", font, spacing) > width:
            candidate = candidate[:-1]
        lines[-1] = f"{candidate}{suffix}"
    return lines


def _draw_spaced_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: float,
    y: float,
    *,
    font: ImageFont.ImageFont,
    spacing: float,
    fill: tuple[int, int, int, int],
    stroke_width: int,
    stroke_fill: tuple[int, int, int, int],
    fill_runs: list[dict[str, Any]] | None = None,
    source_offset: int = 0,
    source_length: int = 0,
) -> None:
    cursor = x
    for index, character in enumerate(text):
        character_fill = fill
        if fill_runs and source_length > 0:
            run_length = max(int(item.get("end") or 0) for item in fill_runs)
            source_index = min(run_length - 1, int((source_offset + index) * run_length / source_length))
            run = next(
                (item for item in fill_runs if int(item.get("start") or 0) <= source_index < int(item.get("end") or 0)),
                None,
            )
            if run:
                character_fill = _rgba(run.get("fill"), fill[3] / 255)
        draw.text(
            (cursor, y),
            character,
            font=font,
            fill=character_fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
            anchor="lt",
        )
        cursor += _character_width(draw, character, font)
        if index < len(text) - 1:
            cursor += spacing


def _render_text_layer(layer: dict[str, Any]) -> Image.Image:
    width = max(1, round(float(layer["width"])))
    height = max(1, round(float(layer["height"])))
    padding = max(
        8,
        round(float(layer.get("background_padding") or 0)),
        round(float(layer.get("shadow_blur") or 0) * 2 + abs(float(layer.get("shadow_offset_x") or 0))),
        round(float(layer.get("shadow_blur") or 0) * 2 + abs(float(layer.get("shadow_offset_y") or 0))),
    )
    surface = Image.new("RGBA", (width + padding * 2, height + padding * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(surface)
    opacity = float(layer.get("opacity", 1))
    background = layer.get("background_fill")
    background_padding = round(float(layer.get("background_padding") or 0))
    if background:
        radius = max(0, round(float(layer.get("background_radius") or 0)))
        draw.rounded_rectangle(
            (
                padding - background_padding,
                padding - background_padding,
                padding + width + background_padding,
                padding + height + background_padding,
            ),
            radius=radius,
            fill=_rgba(background, opacity * float(layer.get("background_opacity", 1))),
        )

    letter_spacing = float(layer.get("letter_spacing") or 0)
    effective_font_size = float(layer.get("font_size") or 64)
    font = _font(layer)
    text = str(layer.get("text") or "")
    if "\n" not in text:
        spacing_width = max(0, len(text) - 1) * letter_spacing
        glyph_width = _spaced_width(draw, text, font, 0)
        available_glyph_width = max(0, width - spacing_width)
        if glyph_width > available_glyph_width:
            effective_font_size = max(8, effective_font_size * available_glyph_width / glyph_width)
            font = _font({**layer, "font_size": effective_font_size})
        while _spaced_width(draw, text, font, letter_spacing) > width and effective_font_size > 8:
            effective_font_size = max(8, effective_font_size - 1)
            font = _font({**layer, "font_size": effective_font_size})
    line_height = max(1, round(effective_font_size * float(layer.get("line_height") or 1.2)))
    max_lines = max(1, height // line_height)
    lines = _wrap_text(draw, text, font, width, letter_spacing, max_lines)
    block_height = min(height, line_height * len(lines))
    y = padding + max(0, (height - block_height) / 2)
    stroke_width = round(float(layer.get("stroke_width") or 0)) if layer.get("stroke") else 0
    fill = _rgba(layer.get("fill"), opacity)
    stroke_fill = _rgba(layer.get("stroke_color"), opacity)

    source_length = len(text.replace("\n", ""))
    source_offset = 0
    layout: list[tuple[str, float, float, int]] = []
    for line in lines:
        line_width = _spaced_width(draw, line, font, letter_spacing)
        align = layer.get("align") or "center"
        x = (
            padding
            if align == "left"
            else padding + width - line_width
            if align == "right"
            else padding + (width - line_width) / 2
        )
        layout.append((line, x, y, source_offset))
        source_offset += len(line)
        y += line_height

    if layer.get("shadow"):
        shadow_surface = Image.new("RGBA", surface.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_surface)
        for line, x, y, _ in layout:
            _draw_spaced_line(
                shadow_draw,
                line,
                x + float(layer.get("shadow_offset_x") or 0),
                y + float(layer.get("shadow_offset_y") or 0),
                font=font,
                spacing=letter_spacing,
                fill=_rgba(layer.get("shadow_color"), opacity),
                stroke_width=stroke_width,
                stroke_fill=_rgba(layer.get("shadow_color"), opacity),
            )
        shadow_blur = max(0, float(layer.get("shadow_blur") or 0))
        if shadow_blur:
            shadow_surface = shadow_surface.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
        surface.alpha_composite(shadow_surface)
        draw = ImageDraw.Draw(surface)

    for line, x, y, source_offset in layout:
        _draw_spaced_line(
            draw,
            line,
            x,
            y,
            font=font,
            spacing=letter_spacing,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
            fill_runs=layer.get("fill_runs") or [],
            source_offset=source_offset,
            source_length=source_length,
        )
    return surface


def render_editor_scene(background: Image.Image, scene: dict[str, Any]) -> bytes:
    canvas = scene.get("canvas") or {}
    try:
        size = (int(canvas["width"]), int(canvas["height"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise CoverEditorRenderError("画板尺寸无效") from exc
    if not (320 <= size[0] <= 4096 and 320 <= size[1] <= 4096):
        raise CoverEditorRenderError("画板尺寸超出允许范围")

    output = ImageOps.fit(background.convert("RGBA"), size, Image.Resampling.LANCZOS)
    for layer in sorted(scene.get("layers") or [], key=lambda item: int(item.get("order") or 0)):
        if layer.get("layer_type") != "text" or not layer.get("visible", True) or not str(layer.get("text") or ""):
            continue
        surface = _render_text_layer(layer)
        rotation = float(layer.get("rotation") or 0)
        if rotation:
            surface = surface.rotate(-rotation, expand=True, resample=Image.Resampling.BICUBIC)
        center_x = float(layer["x"]) + float(layer["width"]) / 2
        center_y = float(layer["y"]) + float(layer["height"]) / 2
        output.alpha_composite(surface, (round(center_x - surface.width / 2), round(center_y - surface.height / 2)))

    encoded = io.BytesIO()
    output.convert("RGB").save(encoded, format="PNG", optimize=True)
    return encoded.getvalue()
