from yuxi.utils.line_breaks import normalize_published_body, segment_unbroken_body


def test_segment_unbroken_body_inserts_paragraph_breaks():
    raw = "第一句。 第二句！第三句？结尾。"
    assert segment_unbroken_body(raw) == "第一句。\n\n第二句！\n\n第三句？\n\n结尾。"


def test_normalize_published_body_fixes_zaijian_typo_and_formats():
    raw = (
        "想看别人家吊顶灯槽怎么留，可以参考鸿扬在手工地。"
        "长沙正在挑装修公司的，别只看效果图。"
    )
    out = normalize_published_body(raw)
    assert "在手工地" not in out
    assert "在建工地" in out
    assert "\n\n" in out
    assert out.startswith("想看别人家吊顶灯槽怎么留，可以参考鸿扬在建工地。")
