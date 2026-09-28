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


@pytest.mark.asyncio
async def test_loads_full_authorized_table_and_refreshes_between_runs(monkeypatch):
    from yuxi import knowledge_base

    async def accessible(uid):
        assert uid == "user"
        return {"databases": [{"kb_id": "kb", "name": "封禁词库"}]}

    chunks = [
        SimpleNamespace(
            file_id="file", chunk_id="chunk", chunk_index=0, content="|问题词|常用表达方式|\n|---|---|\n|报价|费用|"
        )
    ]

    async def list_chunks(self, kb_id):
        assert kb_id == "kb"
        return chunks

    monkeypatch.setattr(knowledge_base, "get_databases_by_uid", accessible)
    monkeypatch.setattr(service.KnowledgeChunkRepository, "list_by_kb_id", list_chunks)
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

    async def inaccessible(uid):
        return {"databases": []}

    monkeypatch.setattr(knowledge_base, "get_databases_by_uid", inaccessible)
    with pytest.raises(ValueError, match="能访问唯一"):
        await service.load_forbidden_words("user", "封禁词库")
