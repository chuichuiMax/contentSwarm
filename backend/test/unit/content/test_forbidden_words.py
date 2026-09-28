from types import SimpleNamespace

import pytest

from yuxi.content.model.forbidden_words import parse_replacement_table, replace_forbidden_words
from yuxi.services import content_forbidden_words_service as service


def test_table_preserves_alternatives_and_empty_cells():
    assert parse_replacement_table("| 问题词 | 常用表达方式 |\n|---|---|\n| 报价 | 报J、报+、费用、 |\n|APP||") == {
        "报价": ["报J", "报+", "费用"],
        "APP": [],
    }


def test_longest_words_win_and_replacements_do_not_cascade():
    assert (
        replace_forbidden_words("报价、价格、单价", {"价": "jia", "报价": "费用", "价格": "费用"})
        == "费用、费用、单jia"
    )
    assert replace_forbidden_words("甲乙", {"甲": "乙", "乙": "丙"}) == "乙丙"
    with pytest.raises(ValueError, match="数字"):
        replace_forbidden_words("100元/㎡", {"100": "200"})


@pytest.mark.parametrize(
    ("text", "replacements", "expected"),
    [
        ("石膏板吊顶：报价280元", {"报价": "报J"}, "石膏板吊顶：报J280元"),
        ("长沙120平两卫翻新报价1.2万？", {"报价": "报J"}, "长沙120平两卫翻新报J1.2万？"),
        ("总价1.206万元", {"价": "jia"}, "总jia1.206万元"),
        ("报价100元/㎡×30㎡=3000元", {"报价": "报J"}, "报J100元/㎡×30㎡=3000元"),
        ("报J280元", {"报J": "费用"}, "费用280元"),
    ],
)
def test_replacement_preserves_numbers_adjacent_to_latin_letters(text, replacements, expected):
    assert replace_forbidden_words(text, replacements) == expected


@pytest.mark.parametrize(
    ("text", "replacements"),
    [
        ("报J280元", {"280": "380"}),
        ("报J280元", {"280": ""}),
        ("报J1.2万元", {"1.2": "1.3"}),
        ("报J280元", {"元": "万元"}),
        ("报J280元", {"元": "天"}),
        ("面积30㎡", {"㎡": "个"}),
    ],
)
def test_replacement_still_rejects_changed_amounts_and_units(text, replacements):
    with pytest.raises(ValueError, match="封禁词库替换会改变数字或计价单位"):
        replace_forbidden_words(text, replacements)


def test_empty_candidate_is_still_checked_in_topics():
    from yuxi.content.validators import validate_modular_content

    checks = validate_modular_content(
        title="装修",
        body="施工记录",
        topics=["APP"],
        draft={},
        brief={},
        evidence_bundle={},
        rule_bundle={
            "runtime_rules": {
                "viral-platform-expression": {"forbidden_lexicon": {"alternatives": {"APP": []}}},
            }
        },
    )
    assert any(item["code"] == "CONTENT_FORBIDDEN_TERM" and "APP" in item["message"] for item in checks)


def test_conflicting_table_rows_are_not_silently_overwritten():
    with pytest.raises(ValueError, match="冲突映射"):
        parse_replacement_table("|问题词|常用表达方式|\n|---|---|\n|报价|报J|\n|报价|费用|")


def test_visual_projection_recognizes_replaced_quote():
    from yuxi.content.control.workflow.generation_input import _redact_composed_locked_quote

    payload = {
        "evidence_bundle": {
            "items": [
                {
                    "variable_codes": ["quote_block"],
                    "value": {
                        "original_content": "总价：5.8w",
                        "render_policy": "checkmark-lines-v1",
                    },
                }
            ]
        },
        "content_draft": {"body": "项目说明\n\n✅ 总jia：5.8w\n\n结尾"},
        "runtime_config_snapshot": {
            "content_rule_bundle": {
                "runtime_rules": {
                    "viral-platform-expression": {
                        "forbidden_lexicon": {"snapshot_hash": "test"},
                        "forbidden_replacements": {"价": "jia"},
                    },
                }
            }
        },
    }
    _redact_composed_locked_quote(payload)
    assert "总jia" not in payload["content_draft"]["body"]
    assert "锁定报价块已由程序插入" in payload["content_draft"]["body"]


@pytest.mark.asyncio
async def test_loads_full_authorized_table_and_refreshes_between_runs(monkeypatch):
    from yuxi import knowledge_base
    from yuxi.repositories import knowledge_base_repository as kb_repo_mod
    from yuxi.repositories import user_repository as user_repo_mod

    user = SimpleNamespace(uid="user", role="superadmin", department_id=1)

    async def get_user(uid):
        assert uid == "user"
        return user

    async def list_by_name(self, name):
        assert name == "封禁词库"
        return [SimpleNamespace(kb_id="kb", created_by="user", share_config={"access_level": "global"})]

    chunks = [
        SimpleNamespace(
            file_id="file", chunk_id="chunk", chunk_index=0, content="|问题词|常用表达方式|\n|---|---|\n|报价|费用|"
        )
    ]

    async def list_chunks(self, kb_id):
        assert kb_id == "kb"
        return chunks

    monkeypatch.setattr(user_repo_mod.UserRepository, "get_by_uid", staticmethod(get_user))
    monkeypatch.setattr(kb_repo_mod.KnowledgeBaseRepository, "list_by_name", list_by_name)
    monkeypatch.setattr(service.KnowledgeChunkRepository, "list_by_kb_id", list_chunks)
    monkeypatch.setattr(knowledge_base, "_database_info_accessible", staticmethod(lambda _user, _db: True))
    first = await service.load_forbidden_words("user", "封禁词库")
    chunks[0].content = chunks[0].content.replace("费用", "报J")
    second = await service.load_forbidden_words("user", "封禁词库")
    assert first["alternatives"] == {"报价": ["费用"]}
    assert second["alternatives"] == {"报价": ["报J"]}
    assert first["snapshot_hash"] != second["snapshot_hash"]
    assert first["sources"][0]["chunk_ids"] == ["chunk"]


@pytest.mark.asyncio
async def test_inaccessible_knowledge_base_fails_before_reading_chunks(monkeypatch):
    from yuxi import knowledge_base
    from yuxi.repositories import knowledge_base_repository as kb_repo_mod
    from yuxi.repositories import user_repository as user_repo_mod

    user = SimpleNamespace(uid="user", role="user", department_id=1)

    async def get_user(uid):
        return user

    async def list_by_name(self, name):
        return [SimpleNamespace(kb_id="kb", created_by="other", share_config={"access_level": "user", "user_uids": []})]

    monkeypatch.setattr(user_repo_mod.UserRepository, "get_by_uid", staticmethod(get_user))
    monkeypatch.setattr(kb_repo_mod.KnowledgeBaseRepository, "list_by_name", list_by_name)
    monkeypatch.setattr(knowledge_base, "_database_info_accessible", staticmethod(lambda _user, _db: False))
    with pytest.raises(ValueError, match="能访问唯一"):
        await service.load_forbidden_words("user", "封禁词库")


@pytest.mark.asyncio
@pytest.mark.parametrize("mapped", [False, True])
async def test_locked_lexicon_accepts_frozen_replacements_during_emoji_repair(mapped):
    from yuxi.content.model.contracts import (
        ContentNodeResultCollector,
        ContractDomainContext,
        get_contract_model,
        validate_content_node_result,
    )
    from yuxi.content.model.contracts.content_nodes import ContractDomainValidationError

    replacements = {"报价": "报J", "价": "jia"}
    body = "低价套头，报价对比。"
    title = "报价透明"
    if mapped:
        body = replace_forbidden_words(body, replacements)
        title = replace_forbidden_words(title, replacements)
    context = ContractDomainContext(
        locked_title_formula_code="T1",
        locked_body_formula_code="B1",
        required_title_lexicon_codes=frozenset({"title.advantage"}),
        allowed_title_lexicon_terms={"title.advantage": frozenset({"报价透明"})},
        locked_title_lexicon_terms={"title.advantage": ("报价透明",)},
        allowed_body_lexicon_codes=frozenset({"body.pain"}),
        allowed_body_lexicon_terms={"body.pain": frozenset({"低价套头", "报价对比"})},
        locked_body_lexicon_terms={"body.pain": ("低价套头", "报价对比")},
        forbidden_replacements=replacements,
        emoji_repair_body=body,
        locked_title=title,
    )
    parsed = get_contract_model("GeneratedContentResultV1").model_validate(
        {
            "title": {"text": title, "formula_code": "T1", "evidence_ids": []},
            "outline": {
                "body_formula_code": "B1",
                "sections": [{"section_id": "s1", "goal": "说明", "evidence_ids": []}],
            },
            "draft": {"body": body + "🔨", "topics": [], "paragraph_evidence": [], "body_formula_code": "B1"},
        }
    )
    runtime = SimpleNamespace(_required_skill_closure=[], _activated_required_skills=[])
    collector = ContentNodeResultCollector("GeneratedContentResultV1", context, runtime)
    await collector.submit(title=parsed.title, outline=parsed.outline, draft=parsed.draft)
    result = collector.finalize()
    assert result["draft"]["body"] == body + "🔨"
    assert result["title"]["lexicon_usage"] == [{"code": "title.advantage", "selected_terms": ["报价透明"]}]
    assert result["draft"]["lexicon_usage"] == [{"code": "body.pain", "selected_terms": ["低价套头", "报价对比"]}]
    from dataclasses import replace

    context = replace(context, emoji_repair_body=None)
    result["title"]["text"] = "装修细节"
    with pytest.raises(ContractDomainValidationError, match="标题未逐字使用"):
        validate_content_node_result("GeneratedContentResultV1", result, context)
    result["title"]["text"] = title
    result["draft"]["body"] = "先看看每项包含哪些活儿，别只看一开始说的便宜。"
    validate_content_node_result("GeneratedContentResultV1", result, context)
    result["draft"]["lexicon_usage"][0]["selected_terms"] = ["自行编造的词库来源"]
    with pytest.raises(ContractDomainValidationError, match="候选外词条"):
        validate_content_node_result("GeneratedContentResultV1", result, context)
