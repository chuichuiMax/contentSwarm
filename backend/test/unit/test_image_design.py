from __future__ import annotations

import io

import pytest
from PIL import Image
from pydantic import ValidationError

from yuxi.content_cover.image2_client import Image2Client, Image2Error
from yuxi.content_cover.schemas import Image2Input
from yuxi.image_design.schemas import ImageDesignGenerateCreate
from yuxi.image_design.worker import _build_image2_request, _build_prompt, _normalize_output


def test_generate_schema_accepts_supported_render_options() -> None:
    payload = ImageDesignGenerateCreate(
        refinement_id="idr_verified",
        aspect_ratio="4:3",
        clarity="2K",
        gen_count=4,
    )

    assert payload.refinement_id == "idr_verified"
    assert payload.gen_count == 4


def test_generate_schema_rejects_unknown_size_options() -> None:
    with pytest.raises(ValidationError):
        ImageDesignGenerateCreate(
            refinement_id="idr_verified",
            aspect_ratio="16:9",
        )


def test_generate_schema_rejects_unsupported_generation_count() -> None:
    with pytest.raises(ValidationError):
        ImageDesignGenerateCreate(
            refinement_id="idr_verified",
            gen_count=3,
        )


def test_generate_schema_rejects_browser_refined_boolean() -> None:
    with pytest.raises(ValidationError):
        ImageDesignGenerateCreate.model_validate(
            {
                "refinement_id": "idr_verified",
                "has_refined": True,
            }
        )


def test_build_prompt_returns_server_compiled_prompt_unchanged() -> None:
    prompt = _build_prompt(
        {
            "workflow": "cross_space",
            "prompt_contract_version": 2,
            "compiled_prompt": "服务端已验证的唯一提示词",
        }
    )

    assert prompt == "服务端已验证的唯一提示词"


def test_build_prompt_rejects_missing_compiled_prompt_for_verified_contract() -> None:
    with pytest.raises(Image2Error, match="缺少编译后提示词"):
        _build_prompt({"workflow": "cross_space", "prompt_contract_version": 2})


def test_build_image2_request_omits_png_compression() -> None:
    source = Image2Input(data=b"image", content_type="image/png", file_name="room.png")

    request = _build_image2_request({"prompt": "现代客厅", "size": "1152x1536"}, [source])

    assert request.mode == "image_to_image"
    assert request.size == "1152x1536"
    assert "output_compression" not in request.extra


def test_image2_keeps_supported_design_size() -> None:
    assert Image2Client._provider_size("1152x1536") == "1152x1536"
    assert Image2Client._provider_size("1536x1152") == "1536x1152"


def test_image2_rejects_unknown_design_size() -> None:
    with pytest.raises(Image2Error, match="不支持输出尺寸"):
        Image2Client._provider_size("1600x900")


def test_normalize_output_returns_png_at_requested_size() -> None:
    source = io.BytesIO()
    Image.new("RGB", (1024, 1024), (20, 30, 40)).save(source, format="JPEG")

    normalized, width, height = _normalize_output(source.getvalue(), "1024x1024")

    assert (width, height) == (1024, 1024)
    with Image.open(io.BytesIO(normalized)) as result:
        assert result.format == "PNG"
        assert result.size == (1024, 1024)


def test_normalize_output_resizes_same_aspect_ratio() -> None:
    source = io.BytesIO()
    Image.new("RGB", (1086, 1448), (20, 30, 40)).save(source, format="PNG")

    normalized, width, height = _normalize_output(source.getvalue(), "1152x1536")

    assert (width, height) == (1152, 1536)
    with Image.open(io.BytesIO(normalized)) as result:
        assert result.size == (1152, 1536)


def test_normalize_output_rejects_wrong_aspect_ratio() -> None:
    source = io.BytesIO()
    Image.new("RGB", (1024, 768), (20, 30, 40)).save(source, format="PNG")

    with pytest.raises(Image2Error, match="与请求的 1024x1024 不一致"):
        _normalize_output(source.getvalue(), "1024x1024")
