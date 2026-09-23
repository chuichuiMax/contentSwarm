from yuxi.content.v3.formula_lexicons import get_formula_lexicon_requirements, lexicon_kb_names


def test_locked_formulas_use_title_and_body_lexicon_knowledge_bases():
    requirements = get_formula_lexicon_requirements("FRT09", "FRB08")

    assert requirements["title"]
    assert requirements["body"]
    assert {item["knowledge_base_name"] for item in requirements["title"]} == {"标题资料库"}
    assert {item["knowledge_base_name"] for item in requirements["body"]} == {"正文资料库"}
    assert requirements["title"][0]["filename"] == "搜索定位资料库-定位词库.txt"
    assert lexicon_kb_names("title") == ("标题资料库", "标题词库")
    assert lexicon_kb_names("body") == ("正文资料库", "正文词库")
