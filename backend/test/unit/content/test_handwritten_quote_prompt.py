import pytest
from yuxi.content_cover.handwritten_quote_prompt import build_handwritten_quote_prompt


@pytest.mark.unit
def test_handwritten_quote_prompt_uses_dynamic_quote_data_without_recalculating():
    prompt = build_handwritten_quote_prompt(
        {
            "quote_format": "单价面积",
            "title_price": {"label": "整套人工合计", "display_text": "1.206w"},
            "quote_block": {
                "original_content": (
                    "拆除卫生间：1000元/间×2/间=2000元；石膏板吊顶（平顶）：70元/㎡×10㎡=280元；整套人工合计：12060元"
                )
            },
        }
    )

    assert "拆除卫生间：1000元/间×2/间=2000元" in prompt
    assert "石膏板吊顶（平顶）：70元/㎡×10㎡=280元" in prompt
    assert "整套人工合计：12060元" not in prompt
    assert "唯一视觉参考" in prompt
    assert "清除并替换其中原有" in prompt
    assert "可变化的手写字迹" in prompt
    assert "红色粗头马克笔标题" in prompt
    assert "深蓝钢笔标题" in prompt
    assert "可变化的纸张与背景" in prompt
    assert "蓝色横线笔记本" in prompt
    assert "牛皮色文件板" in prompt
    assert "只选择一种纸张和一种背景" in prompt
    assert "合计标签“整套人工合计”" in prompt
    assert "展示价格“1.206w”" in prompt
    assert "禁止根据明细重新计算" in prompt


@pytest.mark.unit
def test_handwritten_quote_prompt_requires_confirmed_quote_fields():
    with pytest.raises(ValueError, match="完整的报价明细"):
        build_handwritten_quote_prompt({"quote_block": {"original_content": "拆除：1000元"}})
