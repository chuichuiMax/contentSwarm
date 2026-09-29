from __future__ import annotations

import re
from typing import Any

HANDWRITTEN_QUOTE_NEGATIVE_PROMPT = (
    "印刷字体、电脑字体、JSON、字段名、占位符、乱码、错别字、错误数字、擅自计算、擅自修改金额、"
    "遗漏项目、重复项目、额外项目、文字重叠、文字截断、模糊文字、电子表格、广告海报、手机截图界面、"
    "黑色边框、关闭按钮、状态栏、应用图标、水印、二维码、人物、手掌、卡通风格、矢量插画"
)


def build_handwritten_quote_prompt(snapshot: dict[str, Any]) -> str:
    quote_block = snapshot.get("quote_block") or {}
    title_price = snapshot.get("title_price") or {}
    content = str(quote_block.get("original_content") or "").strip()
    total_label = str(title_price.get("label") or "").strip()
    total_display = str(title_price.get("display_text") or "").strip()
    quote_format = str(snapshot.get("quote_format") or "").strip()
    if not content or not total_label or not total_display:
        raise ValueError("手写报价模板需要完整的报价明细、合计标签和展示价格")

    lines = [item.strip() for item in re.split(r"[；;]", content) if item.strip()]
    quote_lines = [item for item in lines if not item.startswith(total_label)]
    if not quote_lines:
        raise ValueError("手写报价模板需要至少一条报价明细")
    body = "\n".join(quote_lines)

    return (
        "以输入的手写报价例图为唯一视觉参考，生成一张新的手写装修报价单照片。"
        "竖版 3:4，输出 1080×1440 的单张完整图片。\n\n"
        "【参考图继承规则】\n"
        "继承例图的拍摄角度、光线、透视、留白、标题位置、明细区域、"
        "合计区域、主要配色关系、笔迹粗细和人工书写的不规则感。例图仅提供视觉模板，必须清除并替换其中"
        "原有的标题、报价项目、数字、单位、合计和其他文字，不得把新旧报价叠在一起。"
        "如果例图来自手机截图，只保留报价单本体，去除截图黑边、状态栏、关闭按钮、应用图标和水印。\n\n"
        "【可变化的手写字迹】\n"
        "在保持参考图版式和真实手写感的前提下，只选择下面一套协调的字迹方案，不要在同一张图中混用多套：\n"
        "1. 红色粗头马克笔标题，黑色中性笔明细，红色方框或下划线强调合计；\n"
        "2. 深蓝钢笔标题，蓝黑钢笔明细，暗红色签字笔强调合计；\n"
        "3. 黑色软头笔标题，黑色水笔明细，黄色或橙色荧光笔做少量强调；\n"
        "4. 朱红色毛笔字感标题，深灰色针管笔明细，朱红色圈线强调合计；\n"
        "5. 黑色粗记号笔标题，棕黑色细线笔明细，砖红色边框强调合计。\n"
        "字迹可以端正工整、装修师傅速记、略带连笔或粗细不均，但所有汉字、数字和单位必须清晰可辨。\n\n"
        "【可变化的纸张与背景】\n"
        "结合参考图气质，只选择一种纸张和一种背景，保持整张图统一，不要拼贴：\n"
        "1. 白色 A4 纸铺在浅色原木桌面；\n"
        "2. 蓝色横线笔记本放在暖色木桌；\n"
        "3. 浅灰方格纸放在灰色水泥纹或工作台面；\n"
        "4. 米白纸夹在牛皮色文件板上；\n"
        "5. 淡黄色工程记录纸放在白色石材或瓷砖台面；\n"
        "6. 略有自然折痕的白纸放在干净的施工现场木板桌面。\n"
        "如果参考图的纸张、背景或字迹特征很明确，优先选择最接近的方案做轻微变化；背景只能作为陪衬，"
        "不得出现抢眼杂物、人物或施工工具。\n\n"
        "【书写风格】\n"
        "保持参考图中装修师傅现场书写的真实笔迹；标题和最终合计采用所选方案中的醒目颜色与粗细，"
        "报价明细采用所选方案中的正文笔色。文字自然、清晰，不得变成印刷字体或电脑字体。\n\n"
        "【版式】\n"
        "在参考图原标题区域书写大标题“装修人工报价”。"
        f"标题下方按照“{quote_format}”的信息结构逐行书写报价明细；“{quote_format}”只控制布局，"
        "不得作为文字写在纸上。每条明细独占一行，并沿用参考图的列位置和对齐方式。\n\n"
        "只允许逐字书写下面报价正文中的内容，不得计算、纠正、缩写、遗漏、重复、改写或增加任何数字、"
        "单位和文字：\n\n"
        f"<报价正文>\n{body}\n</报价正文>\n\n"
        "在参考图原合计区域沿用相同的框线、强调方式和层级。"
        f"逐字书写合计标签“{total_label}”和展示价格“{total_display}”。"
        "最终合计必须直接采用提供的数据，禁止根据明细重新计算。\n\n"
        "根据明细行数自动调整黑色正文的字号和行距，确保全部内容完整落在纸张范围内，不遮挡、不截断、"
        f"不重叠。画面中只允许出现标题“装修人工报价”、上述报价正文、“{total_label}”和"
        f"“{total_display}”，不得出现其他文字。\n\n"
        "【严格限制】\n"
        "不得显示 JSON、字段名称、模板占位符或报价版式名称；不得出现手机截图黑边、关闭按钮、状态栏、"
        "应用图标、账号、水印、二维码、人物、手掌或施工工具。保持真实摄影、生活化、朴素可信的现场记录感。"
    )


__all__ = ["HANDWRITTEN_QUOTE_NEGATIVE_PROMPT", "build_handwritten_quote_prompt"]
