from yuxi.services.creative_style_service import bundled_creative_style_rows


def test_bundled_creative_styles_come_from_the_prompt_catalog():
    rows = bundled_creative_style_rows()
    names = {(row["content_type_name"], row["name"]) for row in rows}

    assert ("工艺施工展示", "信息流") in names
    assert ("工艺施工展示", "工艺介绍") in names
    assert ("装修案例分享", "信息流类型") in names
    assert ("装修案例分享", "设计风格亮点介绍（2）") in names
    assert all(row["prompt"] for row in rows)
    assert all(row["content_type_name"] in {"工艺施工展示", "装修案例分享"} for row in rows)
