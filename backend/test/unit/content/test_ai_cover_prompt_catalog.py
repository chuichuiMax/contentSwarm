import pytest

from yuxi.content_cover.ai_cover_prompt_catalog import (
    specs_for_content_type,
    variant_from_oss_basename,
)
from yuxi.services.ai_cover_prompt_catalog_service import list_ai_cover_prompt_catalog


def test_variant_from_oss_basename_accepts_cn_and_p_codes():
    assert variant_from_oss_basename("提示词四.png") == "p4"
    assert variant_from_oss_basename("p12.jpg") == "p12"
    assert variant_from_oss_basename("image/AI-cover/提示词一.webp") == "p1"
    assert variant_from_oss_basename("提示词六.png") == "p7"
    assert variant_from_oss_basename("提示词八.png") == "p14"
    assert variant_from_oss_basename("提示词九.png") == "p9"
    assert variant_from_oss_basename("提示词十一.png") == "p11"
    assert variant_from_oss_basename("提示词十四.png") == "p15"
    assert variant_from_oss_basename("提示词十五.png") == "p16"
    assert variant_from_oss_basename("unknown.png") is None


def test_specs_for_content_type_excludes_p12_unless_case_share():
    specs = specs_for_content_type(content_type_code="CT02")
    variants = [spec.variant for spec in specs]
    assert "p12" not in variants
    assert "p7" in variants
    assert specs[0].variant == "smart-random"
    assert specs[0].label == "智能随机适配"
    assert len(specs) == 15
    assert variants.count("p11") == 1
    assert "p14" in variants
    assert "p15" in variants
    assert "p16" in variants
    labels = [spec.label for spec in specs]
    assert labels.index("提示词八") < labels.index("提示词九") < labels.index("提示词十")

    case_specs = specs_for_content_type(content_type_name="装修案例分享")
    assert "p12" in [spec.variant for spec in case_specs]
    assert len(case_specs) == 16


@pytest.mark.asyncio
async def test_list_ai_cover_prompt_catalog_uses_api_preview_url():
    payload = await list_ai_cover_prompt_catalog(content_type_code="CT02")
    assert payload["prompts"][0]["variant"] == "smart-random"
    assert payload["prompts"][0]["label"] == "智能随机适配"
    assert len(payload["prompts"]) == 15
    assert payload["prompts"][0]["preview_url"].startswith("/api/content/covers/ai-cover-prompts/")
    assert all(item["variant"] != "p12" for item in payload["prompts"])
    assert payload["prompts"][0]["is_smart_random"] is True
    assert len(payload["prompts"][1]["prompt_body"]) > 50


@pytest.mark.asyncio
async def test_list_ai_cover_prompt_catalog_includes_p12_for_case_share():
    payload = await list_ai_cover_prompt_catalog(content_type_name="装修案例分享")
    p12 = next(item for item in payload["prompts"] if item["variant"] == "p12")
    assert p12["selectable"] is True
    assert len(payload["prompts"]) == 16
