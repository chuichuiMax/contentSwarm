from copy import deepcopy

import pytest

from yuxi.content.model.viral_assets import (
    BLUEPRINT_FIELDS,
    ViralArticleSource,
    extract_article_records,
    validate_prepared_asset,
)


def source(**changes):
    return ViralArticleSource.model_validate(
        {
            "kb_id": "kb-test",
            "file_id": "file-test",
            "locator": "sheet:1/row:2",
            "industry_slug": "decoration",
            "title": "装修动线怎么安排",
            "body": "先看生活习惯。\n再看厨房动线。\n你更关注哪一处？",
            "full_source_hash": "a" * 64,
            "source_file_version": "file-version-1",
            "completeness": "complete",
            "viral_basis": "测试用已审核样例，审核记录 R1",
            **changes,
        }
    )


def prepared(article):
    anchor = {"section": "body", "start": 0, "end": 8, "quote": article.body[:8]}
    blueprint = dict.fromkeys(BLUEPRINT_FIELDS, "从原文观察到的结构")
    blueprint.update(
        title_slot_sequence=["主题", "问题"],
        content_block_sequence=["习惯", "动线", "互动"],
        list_pattern={"type": "none"},
    )
    return {
        "status": "prepared",
        "source_hash": article.source_hash,
        "reference_card": {
            "content_type_code": "CT06",
            "content_type_reason": "主体解释施工动线，依据正文锚点",
            "audience": "装修业主",
            "scene": "厨房布局",
            "goal": "经验分享",
            "channel": "图文",
            "summary": "从习惯出发解释动线",
            "required_slots": [],
            "anchors": [anchor],
        },
        "reference_blueprint": blueprint,
        "blueprint_anchors": {name: [anchor] for name in BLUEPRINT_FIELDS},
        "issues": [],
    }


def test_multiple_articles_in_same_file_have_distinct_identity():
    first = source()
    second = source(locator="sheet:1/row:3")
    assert first.article_id != second.article_id


def test_article_identity_is_stable_but_text_changes_version():
    first = source()
    second = source(body="更新后的完整原文")
    assert first.article_id == second.article_id
    assert first.source_hash != second.source_hash


def test_valid_preparation_keeps_original_structure_and_source():
    article = source()
    payload = prepared(article)
    assert validate_prepared_asset(payload, article).reference_blueprint["list_pattern"]["type"] == "none"


def test_unique_quotes_resolve_to_exact_positions_without_model_counting():
    article = source()
    payload = prepared(article)
    payload["reference_card"]["anchors"] = [{"section": "body", "quote": "再看厨房动线。"}]
    result = validate_prepared_asset(payload, article)
    anchor = result.reference_card.anchors[0]
    assert article.body[anchor.start : anchor.end] == "再看厨房动线。"


def test_ambiguous_quotes_require_explicit_coordinates():
    article = source(body="重复。重复。")
    payload = prepared(article)
    payload["reference_card"]["anchors"] = [{"section": "body", "quote": "重复。"}]
    with pytest.raises(ValueError, match="多处匹配"):
        validate_prepared_asset(payload, article)


@pytest.mark.parametrize(
    "problem",
    ["unverified", "wrong_version", "missing_blueprint", "false_quote", "empty_anchor", "missing_card", "issues"],
)
def test_preparation_does_not_publish_incomplete_or_untraceable_assets(problem):
    article = source(completeness="unverified") if problem == "unverified" else source()
    payload = deepcopy(prepared(article))
    if problem == "wrong_version":
        payload["source_hash"] = "0" * 64
    elif problem == "missing_blueprint":
        del payload["reference_blueprint"]["opening_hook"]
    elif problem == "false_quote":
        payload["reference_card"]["anchors"][0]["quote"] = "原文没有的内容"
    elif problem == "empty_anchor":
        payload["blueprint_anchors"]["opening_hook"] = []
    elif problem == "missing_card":
        payload["reference_card"] = None
    elif problem == "issues":
        payload["issues"] = ["原文被截断"]
    with pytest.raises(ValueError):
        validate_prepared_asset(payload, article)


def test_uncertain_source_returns_explicit_review_issue():
    article = source(completeness="unverified")
    payload = {"status": "needs_review", "source_hash": article.source_hash, "issues": ["无法确认完整文章范围"]}
    assert validate_prepared_asset(payload, article).status == "needs_review"


def test_markdown_table_preserves_article_boundaries_and_line_breaks():
    raw = "| 标题 | 正文 |\n| --- | --- |\n| 第一篇 | 开头<br>结尾 |\n| 第二篇 | 完整正文 |"
    records = extract_article_records(raw, layout="markdown_table", title_column="标题", body_column="正文")
    assert len(records) == 2
    assert records[0]["body"] == "开头\n结尾"
    assert records[0]["locator"] != records[1]["locator"]


def test_csv_multiline_body_is_one_article():
    records = extract_article_records(
        'title,body\n第一篇,"开头\n结尾"\n', layout="csv", title_column="title", body_column="body"
    )
    assert records == [{"locator": "csv:record:1", "title": "第一篇", "body": "开头\n结尾"}]


def test_single_layout_does_not_merge_multiple_articles():
    with pytest.raises(ValueError, match="多个一级标题"):
        extract_article_records("# 第一篇\n正文\n# 第二篇\n正文", layout="single")


def test_incomplete_table_row_is_rejected_instead_of_silently_dropped():
    with pytest.raises(ValueError, match="不完整"):
        extract_article_records("title,body\n第一篇,\n", layout="csv", title_column="title", body_column="body")


@pytest.mark.parametrize(
    "field,value", [("content_type_code", None), ("content_type_code", "价格营销"), ("content_type_reason", "")]
)
def test_decoration_reference_requires_exact_type_and_reason(field, value):
    article = source()
    payload = prepared(article)
    payload["reference_card"][field] = value
    with pytest.raises(ValueError):
        validate_prepared_asset(payload, article)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [f"CT{i:02d}" for i in range(1, 8)])
async def test_reference_search_filters_type_before_ranking_and_limit(monkeypatch, code):
    from types import SimpleNamespace
    from sqlalchemy.dialects import postgresql
    from yuxi.services import content_viral_assets

    async def allowed(_user):
        return ["kb-test"]

    monkeypatch.setattr(content_viral_assets, "accessible_asset_kbs", allowed)
    queries = []

    class DB:
        async def execute(self, statement):
            queries.append(statement)
            return SimpleNamespace(scalars=lambda: [])

    result = await content_viral_assets.search_ready_viral_assets(
        DB(), SimpleNamespace(uid="user"), industry_slug="decoration", query="长沙装修报价",
        kb_ids=["kb-test"], limit=5, content_type_code=code,
    )
    assert result == []
    sql = str(queries[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    where = sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0]
    assert "content_type_code" in where and code in where
    assert "industry_slug = 'decoration'" in where
