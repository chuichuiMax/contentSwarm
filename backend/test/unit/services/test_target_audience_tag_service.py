import pytest

from yuxi.content.mp_studio_direct import _build_facts
from yuxi.services.target_audience_tag_service import (
    parse_audience_tag_lines,
    target_audience_tag_knowledge_base_name,
)


def test_target_audience_tag_knowledge_base_name():
    assert target_audience_tag_knowledge_base_name("旧房改造") == "目标人群-旧房改造-标签"
    assert target_audience_tag_knowledge_base_name("毛坯") == "目标人群-通用-标签"


def test_parse_audience_tag_lines():
    text = """
# 注释
旧房翻新
局部改造，全屋翻新
- 预算敏感
1. 工期紧张
"""
    assert parse_audience_tag_lines(text) == [
        "旧房翻新",
        "局部改造",
        "全屋翻新",
        "预算敏感",
        "工期紧张",
    ]


def test_parse_audience_tag_lines_reads_xiaohongshu_hashtags():
    text = """
##### 装修（通用标签）
#装修 #家装  #装修记录 #装修灵感
"""
    parsed = parse_audience_tag_lines(text)
    assert parsed[:4] == ["装修", "家装", "装修记录", "装修灵感"]


def test_build_facts_uses_audience_tags():
    facts = _build_facts(
        "装修报价清单",
        {},
        {"目标人群": "旧房改造", "居住人口": "三口之家"},
        audience_tags=["旧房翻新", "局部改造"],
    )
    assert facts["audience"] == ["三口之家", "旧房翻新", "局部改造"]


@pytest.mark.asyncio
async def test_list_target_audience_tags_reads_kb(monkeypatch):
    from yuxi.services import target_audience_tag_service as service

    async def fake_load(_uid: str, name: str, *, limit: int = 12000) -> str:
        assert name == "目标人群-旧房改造-标签"
        return "标签A\n标签B"

    monkeypatch.setattr(service, "load_knowledge_base_text", fake_load)
    tags, kb_name = await service.list_target_audience_tags("u1", "旧房改造")
    assert tags == ["标签A", "标签B"]
    assert kb_name == "目标人群-旧房改造-标签"


@pytest.mark.asyncio
async def test_list_target_audience_tags_falls_back_to_generic(monkeypatch):
    from yuxi.services import target_audience_tag_service as service

    async def fake_load(_uid: str, name: str, *, limit: int = 12000) -> str:
        if name == "目标人群-乡墅-标签":
            raise ValueError("必须能访问唯一的“目标人群-乡墅-标签”，当前找到 0 个")
        if name == service.GENERIC_TARGET_AUDIENCE_TAG_KB:
            return "通用标签1\n通用标签2"
        raise AssertionError(name)

    monkeypatch.setattr(service, "load_knowledge_base_text", fake_load)
    tags, kb_name = await service.list_target_audience_tags("u1", "乡墅")
    assert tags == ["通用标签1", "通用标签2"]
    assert kb_name == service.GENERIC_TARGET_AUDIENCE_TAG_KB


@pytest.mark.asyncio
async def test_list_target_audience_tags_returns_empty_when_kbs_unavailable(monkeypatch):
    from yuxi.services import target_audience_tag_service as service

    async def fake_load(_uid: str, _name: str, *, limit: int = 12000) -> str:
        raise ValueError("必须能访问唯一的“目标人群-通用-标签”，当前找到 0 个")

    monkeypatch.setattr(service, "load_knowledge_base_text", fake_load)
    tags, kb_name = await service.list_target_audience_tags("u1", "毛坯")
    assert tags == []
    assert kb_name == service.GENERIC_TARGET_AUDIENCE_TAG_KB
