"""给指定图库的上传图片叠加鸿扬家装水印。"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from yuxi.config.app import config

_LOGO_PATH = Path(__file__).resolve().parents[1] / "image_design" / "assets" / "hirun-logo.png"
_LOGO_WIDTH_RATIO = 0.34
_LOGO_BOTTOM_MARGIN_RATIO = 0.035


def watermark_upload_bytes(
    data: bytes,
    *,
    category_id: str,
    owner_uid: str,
    parent_id: str | None = None,
) -> bytes:
    gallery_id, gallery_owner = config.resolve_material_watermark_gallery()
    covered = category_id == gallery_id or (parent_id or "") == gallery_id
    if not gallery_id or owner_uid != gallery_owner or not covered:
        return data
    return apply_gallery_watermark(data)


def apply_gallery_watermark(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as source:
        image = source.convert("RGBA")
        keep_alpha = source.mode in {"RGBA", "LA"} or (source.mode == "P" and "transparency" in source.info)
    _composite_logo(image)
    output = io.BytesIO()
    if keep_alpha:
        image.save(output, format="WEBP", quality=80, method=4)
    else:
        flattened = Image.new("RGB", image.size)
        flattened.paste(image, mask=image.getchannel("A"))
        flattened.save(output, format="WEBP", quality=80, method=4)
    return output.getvalue()


def _composite_logo(image: Image.Image) -> None:
    with Image.open(_LOGO_PATH) as source:
        logo = source.convert("RGBA")
    logo.putalpha(logo.convert("L"))
    target_width = max(1, int(image.width * _LOGO_WIDTH_RATIO))
    target_height = max(1, round(logo.height * target_width / logo.width))
    resized = logo.resize((target_width, target_height), Image.Resampling.LANCZOS)
    margin = max(8, int(image.height * _LOGO_BOTTOM_MARGIN_RATIO))
    x = (image.width - target_width) // 2
    y = max(0, image.height - target_height - margin)
    image.alpha_composite(resized, (x, y))
