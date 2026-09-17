from copy import deepcopy

import pytest

from yuxi.content.model.viral_document import document_units, extract_detected_articles, validate_document_result
from yuxi.content.model.contracts import ContractDomainContext, validate_content_node_result


def example():
    document = {
        "units": document_units("# 厨房布局\n\n先看做饭习惯。\n\n再安排操作顺序。", "厨房.md"),
        "industries": ["decoration", "education"],
        "industry_hint": None,
    }
    result = {
        "articles": [{"title_unit": 0, "body_units": [1, 2], "industry_slug": "decoration", "reason": "完整方法文章"}],
        "issues": [],
    }
    return document, result


def test_extracts_exact_source_and_preserves_paragraph_rhythm():
    document, payload = example()
    result = validate_document_result(payload, document)
    article = extract_detected_articles(result, document)[0]
    assert article["body"] == "先看做饭习惯。\n\n再安排操作顺序。"
    assert article["title"] == "厨房布局"
    assert article["locator"] == "line:1"


def test_multiple_tables_nonstandard_columns_keep_distinct_sources():
    text = (
        "## 工作表一\n| 文案名 | 笔记内容 |\n| --- | --- |\n| 同名 | 开头<br>结尾 |\n"
        "## 工作表二\n| 选题 | 内容 |\n| --- | --- |\n| 同名 | 另一篇完整正文 |"
    )
    units = document_units(text, "文章.xlsx")
    titles = [unit for unit in units if unit["text"] == "同名"]
    assert len(titles) == 2 and titles[0]["location"] != titles[1]["location"]
    assert any(unit["text"] == "开头\n结尾" for unit in units)
    assert not any(unit["text"] == "---" for unit in units)


def test_csv_multiline_cell_and_parsed_csv_markdown():
    units = document_units('文案名,内容\n题目,"开头\n结尾"\n', "文章.csv")
    assert units[-1]["text"] == "开头\n结尾"
    units = document_units("## 表格\n| 名称 | 内容 |\n| --- | --- |\n| 标题 | 正文 |", "文章.csv")
    assert units[-1]["text"] == "正文"


@pytest.mark.parametrize("problem", ["unknown_unit", "duplicate", "reorder", "industry", "hint", "empty", "rewrite"])
def test_rejects_invalid_agent_source_selection(problem):
    document, payload = example()
    article = payload["articles"][0]
    if problem == "unknown_unit":
        article["body_units"] = [999]
    elif problem == "duplicate":
        payload["articles"].append(deepcopy(article))
    elif problem == "reorder":
        article["body_units"] = [2, 1]
    elif problem == "industry":
        article["industry_slug"] = "invented"
    elif problem == "hint":
        document["industry_hint"] = "education"
    elif problem == "empty":
        payload["articles"] = []
    elif problem == "rewrite":
        article["body"] = "模型伪造的正文"
    with pytest.raises(ValueError):
        validate_content_node_result("ViralDocumentResultV1", payload, ContractDomainContext(viral_document=document))


def test_quote_sheet_is_explicit_issue_not_fake_article():
    document, _ = example()
    result = validate_document_result({"articles": [], "issues": ["只有报价数据，没有完整文章"]}, document)
    assert not result.articles


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_parse_hook_only_schedules_files_with_explicit_reference_purpose(monkeypatch, enabled):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from yuxi.knowledge.manager import KnowledgeBaseManager
    from yuxi.services import viral_document_service
    from yuxi.storage.postgres.manager import pg_manager

    metadata = {"status": "parsed", "processing_params": {"use_as_viral_reference": enabled}}
    kb = SimpleNamespace(parse_file=AsyncMock(return_value=metadata))
    manager = KnowledgeBaseManager.__new__(KnowledgeBaseManager)
    monkeypatch.setattr(manager, "_get_kb_for_database", AsyncMock(return_value=kb))
    schedule = AsyncMock(return_value={"id": "job", "status": "pending"})
    monkeypatch.setattr(viral_document_service, "schedule_reference_file", schedule)
    user = SimpleNamespace(uid="test-user")
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one=lambda: user)))

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(pg_manager, "get_async_session_context", session)
    result = await manager.parse_file("kb", "file", operator_id="test-user")
    assert result["status"] == "parsed"
    if enabled:
        schedule.assert_awaited_once_with(db, user, "kb", "file")
        assert result["reference_preparation"]["status"] == "pending"
    else:
        schedule.assert_not_called()
        assert "reference_preparation" not in result


def test_table_title_cannot_be_paired_with_another_rows_body():
    document = {
        "units": document_units("| 名 | 内容 |\n| --- | --- |\n| 甲 | 甲正文 |\n| 乙 | 乙正文 |", "a.md"),
        "industries": ["decoration"],
    }
    result = {"articles": [{"title_unit": 2, "body_units": [5], "industry_slug": "decoration", "reason": "错误混行"}]}
    with pytest.raises(ValueError, match="同一记录"):
        validate_document_result(result, document)


def test_nested_list_indentation_is_preserved():
    document = {"units": document_units("# 标题\n- 主项\n  - 子项\n\n结尾", "a.md"), "industries": ["decoration"]}
    payload = {
        "articles": [{"title_unit": 0, "body_units": [1, 2, 3], "industry_slug": "decoration", "reason": "列表文章"}]
    }
    result = validate_document_result(payload, document)
    assert extract_detected_articles(result, document)[0]["body"] == "- 主项\n  - 子项\n\n结尾"


def test_original_xlsx_preserves_cell_breaks_and_sheet_identity():
    from io import BytesIO
    from openpyxl import Workbook
    from yuxi.content.model.viral_document import xlsx_document_units

    book = Workbook()
    book.active.append(["文案名", "笔记内容"])
    book.active.append(["同名", "开头\n\n- 内容\n  - 子项"])
    book.create_sheet("第二页").append(["同名", "另一篇正文"])
    stream = BytesIO()
    book.save(stream)
    units = xlsx_document_units(stream.getvalue())
    assert units[3]["text"] == "开头\n\n- 内容\n  - 子项"
    assert units[2]["location"] != units[4]["location"]
