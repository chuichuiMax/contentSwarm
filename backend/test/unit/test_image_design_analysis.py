from __future__ import annotations

import pytest
from pydantic import ValidationError

from yuxi.image_design.analysis import (
    VisualModelUnavailableError,
    analysis_cache_key,
    parse_analysis_response,
    run_visual_analysis,
)


def test_analysis_cache_key_includes_role_model_and_schema() -> None:
    base = analysis_cache_key("abc123", "style_reference", "vision:model-a", 1)

    assert base != analysis_cache_key("abc123", "structure_source", "vision:model-a", 1)
    assert base != analysis_cache_key("abc123", "style_reference", "vision:model-b", 1)
    assert base != analysis_cache_key("abc123", "style_reference", "vision:model-a", 2)


def test_analysis_requires_structured_visual_result() -> None:
    with pytest.raises((ValueError, ValidationError)):
        parse_analysis_response("这是一张客厅图片", "structure_source")


def test_analysis_parses_json_fence_without_accepting_free_text() -> None:
    result = parse_analysis_response(
        """```json
        {
          "room_type": "客厅",
          "structural_features": ["落地窗"],
          "preserve": ["窗户位置"],
          "style": "现代简约",
          "palette": ["米白"],
          "materials": ["木饰面"],
          "furniture": ["沙发"],
          "lighting": ["自然光"],
          "camera": ["平视广角"],
          "transferable_features": ["暖色材质语言"],
          "exclusions": [],
          "confidence_notes": ["窗侧区域略暗"]
        }
        ```""",
        "structure_source",
    )

    assert result.role == "structure_source"
    assert result.room_type == "客厅"


async def test_visual_model_configuration_failure_is_distinguishable(monkeypatch) -> None:
    def fail_to_load(**_kwargs):
        raise ValueError("missing provider")

    monkeypatch.setattr("yuxi.image_design.analysis.load_chat_model", fail_to_load)

    with pytest.raises(VisualModelUnavailableError, match="视觉模型配置不可用"):
        await run_visual_analysis(b"not-read", "structure_source", model_spec="missing:model")
