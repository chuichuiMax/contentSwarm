from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any, Literal

AiCoverPromptVariant = Literal[
    "editorial",
    "p1",
    "p2",
    "p3",
    "p7",
    "p8",
    "p9",
    "p10",
    "p11",
    "p12",
]

AI_COVER_VARIANTS: tuple[AiCoverPromptVariant, ...] = (
    "editorial",
    "p1",
    "p2",
    "p3",
    "p7",
    "p8",
    "p9",
    "p10",
    "p11",
    "p12",
)

AI_COVER_CASE_SHARE_TYPE_CODES: frozenset[str] = frozenset({"CT01"})
AI_COVER_CASE_SHARE_TYPE_NAMES: frozenset[str] = frozenset({"装修案例分享", "案例分享"})

# 精选封面模板展示位（1-based）→ AI 提示词变体；未配置的位置在指定模式下需显式传 ai_cover_prompt_variant。
AI_COVER_TEMPLATE_SLOT_PROMPTS: dict[int, AiCoverPromptVariant] = {
    2: "p1",
    3: "p2",
    4: "p3",
}

AI_COVER_OUTPUT_REQUIREMENTS = """
【最终输出要求】
仅输出一张 1080×1440 像素、3:4 比例、完整不透明的 720p 小红书装修封面。
不要生成多个方案，不要拼图，不要附加说明。

【必须避免】
错别字、乱码、漏字、额外文案、文字重复、标题贴边、文字裁切、重点信息不突出、字体比例失衡、颜色混乱、厚重遮罩、廉价营销感、过度锐化、空间结构改变、家具变形、虚构 Logo、水印和低清晰度。"""

AI_COVER_BASE_INTRO = "请在我上传的装修实景图上添加文字，生成一张高端家装内容封面。"

AI_COVER_IMAGE_ONLY_PROMPT = f"""{AI_COVER_BASE_INTRO}

仅优化背景图的构图、光影与小红书封面氛围；保留原图主体、空间结构、家具、材质、灯光、色彩、拍摄视角和画面比例。
不要生成任何汉字、字母、数字、Logo、水印或装饰文字。

{AI_COVER_OUTPUT_REQUIREMENTS}"""

AI_COVER_PROMPT = f"""{AI_COVER_BASE_INTRO}

所有传入的封面文字必须准确、完整，不得改写、遗漏或增加其他文案。可以换行，但不能改变文字顺序。

【排版设计】
本次只设计一种排版，并只输出一张完整封面。先分析当前图片的留白、主体、明暗和空间透视，再从以下方向中选择最适合底图的一种，不需要说明所选方案：
1. 左侧或右侧留白对齐：文字集中放在自然留白一侧，形成稳定的信息列。
2. 杂志刊头：标题靠近顶部或上方三分之一处，副标题形成精致的编辑式层级。
3. 上下分区：主标题与副标题分处画面上下区域，通过留白建立呼应。
4. 居中聚焦：文字围绕视觉中心集中排列，适合主体对称或中部留白的底图。
5. 错位阶梯：标题和副标题按不同缩进逐层排列，形成有节奏的错落关系。
6. 大字或大数字锚点：突出标题中的核心词，或副标题中的关键数字，其余文字作为辅助信息围绕排布。
7. 侧边竖向版式：在画面边缘建立窄文字区，可使用竖向排列或纵向阅读节奏，但中文必须自然易读。
8. 底部信息带：标题位于中上区域，副标题集中在底部安全区，不使用厚重色块。
9. 对角动线：文字沿画面已有的斜线、家具边缘或空间动线错位展开，不旋转中文正文。
10. 框景留白：使用细线、轻量局部底色或留白边界组织文字，不制作厚重卡片或大面积遮罩。
11. 左右呼应：标题与副标题分列在画面两侧，通过字号和位置形成平衡，同时避开主体。
12. 透视跟随：顺应墙面、柜体或空间透视方向安排文字区域，但文字本身保持端正、清晰。

不要机械套用固定位置或字号比例。标题和副标题需要有明确的大小区别；可以突出副标题中的关键数字或核心词，但所有传入文字必须完整、清晰可读，不得拆散到难以连续阅读。排版应有设计感，同时避免遮挡关键家具、灯具和装修细节。不要生成话题标签。

【画布安全区】
image2 的生成画布会在输出阶段等比例居中适配为最终 3:4 封面，因此上下必须预留额外裁切缓冲，不能只保证生成画布内可见。
把画布上方 0%—12% 和下方 88%—100% 视为禁止放置文字的区域，任何标题、副标题、文字底色或装饰文字都不得进入。
所有文字的完整外接边界必须位于横向 7%—93%、纵向 12%—88% 的内部安全区，不得触碰、越过或被裁切。
杂志刊头必须整体下移到底部低于画布高度 12% 的位置；底部信息带必须整体上移到顶部高于画布高度 88% 的位置。
上下分区、侧边竖向、大字和大数字版式同样必须遵守内部安全区，禁止使用贴顶、贴底或出血式文字。
如果文字过长或空间不足，优先缩小字号、合理换行或移动整组文字，不得把任何文字推出画布。
输出前检查上、下、左、右四条边，确认标题和副标题均完整可见。

【字体与颜色】
使用简洁、现代、有品质感的中文字体。根据文字所在区域选择颜色：浅色背景使用深炭灰或深咖色，深色背景使用暖白色。字号根据图片尺寸、文字长度和留白灵活调整，确保缩小到手机封面尺寸后仍清晰可读。背景较复杂时，可以加入轻微的局部渐变增强对比，不使用厚重遮罩或明显描边。

【底图保护】
仅添加文字及必要的局部明暗调整。
保留原图的空间结构、家具、材质、灯光、色彩、拍摄视角和画面比例，不重新生成室内场景。
不添加虚构 Logo、水印或额外装饰。

{AI_COVER_OUTPUT_REQUIREMENTS}"""

_PROMPT_P1 = f"""{AI_COVER_BASE_INTRO}

文字内容为「{{line1}}
{{line2}}」，两行字，字体风格为手写风，笔画带有明显笔触痕迹，有长短、粗细变化，部分笔画倾斜或断裂，营造出动感、活泼、醒目的视觉效果。

【底图保护】
仅添加文字及必要的局部明暗调整；保留原图空间结构与主体细节。

{AI_COVER_OUTPUT_REQUIREMENTS}"""

_PROMPT_P2 = f"""{AI_COVER_BASE_INTRO}

3D 立体白色文字「{{line1}}」，文字边缘为鎏金色，具备三层丰富立体效果，中间部分是玻璃质感的凸描边，底部红色框内有金色文字「{{line2}}」，营造强烈的促销视觉效果。

【底图保护】
仅添加文字及必要的局部明暗调整；保留原图空间结构与主体细节。

{AI_COVER_OUTPUT_REQUIREMENTS}"""

_PROMPT_P3 = f"""{AI_COVER_BASE_INTRO}

顶部居中两行：第一行「{{line1}}」，第二行「{{line2}}」。融合文艺钢笔、西方古典与行草题韵风格，字体为暖棕色，结构含书法行笔节奏与西式连笔优雅；可在标题上下加入轻微装饰弧线，不遮挡主体。

【底图保护】
仅添加文字及必要的局部明暗调整；保留原图空间结构与主体细节。

{AI_COVER_OUTPUT_REQUIREMENTS}"""

_PROMPT_P7 = f"""{AI_COVER_BASE_INTRO}

字体风格：书法字。
「{{line1}}{{line2}}」，风格狂野，笔画飘逸，带有枯笔飞白，墨色浓淡对比强烈，字形结构夸张呈现张力。

【底图保护】
仅添加文字及必要的局部明暗调整；保留原图空间结构与主体细节。

{AI_COVER_OUTPUT_REQUIREMENTS}"""

_PROMPT_P8 = f"""{AI_COVER_BASE_INTRO}

字体风格：动漫体。
「{{line1}}  {{line2}}」，俏皮、醒目有趣；粗笔画错落有致，整体风格活泼。字体颜色要和所选背景图颜色匹配，符合人体设计视觉。
两行字，动漫风格的字体，笔画爆发状，动态有张力，笔画末端粗，笔画错落有致，整体风格活泼俏皮、醒目有趣。

【底图保护】
仅添加文字及必要的局部明暗调整；保留原图空间结构与主体细节。

{AI_COVER_OUTPUT_REQUIREMENTS}"""

_PROMPT_P9 = f"""{AI_COVER_BASE_INTRO}

字体风格：大笔触手写风。
字体颜色要和所选背景图颜色匹配，符合人体设计视觉。文字内容为「{{copy_text}}」，根据文案内容排版，字体风格为手写风，笔画带有明显笔触痕迹，有长短、粗细变化，部分笔画倾斜或断裂，营造出动感、活泼、醒目的视觉效果。

【底图保护】
仅添加文字及必要的局部明暗调整；保留原图空间结构与主体细节。

{AI_COVER_OUTPUT_REQUIREMENTS}"""

_PROMPT_P10 = f"""{AI_COVER_BASE_INTRO}

字体风格：现代宋体广告字，强对比信息排版。
字体颜色要和所选背景图颜色匹配，保证清晰醒目、阅读舒适。重点字搭配强调元素，颜色控制在三种以内，突出关键词和信息层级。
文案内容为「{{copy_text}}」，字形结构：方正饱满，字面宽大，结构紧凑，以厚重有力的无衬线粗黑体为主。核心标题明显放大，品牌信息与补充说明适当缩小，通过字号、字重和疏密变化建立阅读顺序。
笔画特征：笔画粗壮扎实，横竖明确，边缘清晰利落，粗细相对统一。重点词采用强调元素或不规则圆瓣色块；配合轻微错位阴影增强辨识度。辅助强调线使用自然、简洁的马克笔笔触。
结构布局：采用分层排版，主标题使用大字与重点色块组合；说明文字以较小字号排列；整体以左对齐为主，局部灵活错落，保持充足行距与边距，形成直接、有力、易于快速阅读的装修宣传封面风格。

【底图保护】
仅添加文字及必要的局部明暗调整；保留原图空间结构与主体细节。

{AI_COVER_OUTPUT_REQUIREMENTS}"""

_PROMPT_P11 = f"""{AI_COVER_BASE_INTRO}

字体风格：现代宋体／明朝体，搭配简洁无衬线英文，极简建筑杂志排版。
字体颜色要和所选背景图颜色匹配，保证清晰醒目、阅读舒适。以纯白或暖白文字为主，统一文字色彩，通过字号、字重、字距和留白区分信息层级。
文案内容为「{{copy_text}}」，字形结构：中文主标题与项目名称采用端正舒展的宋体，结构匀称，字面清晰，具有书刊标题的文化感。英文副标题采用简洁无衬线字体，面积数字采用较醒目的窄体无衬线字，通过中英文与数字的字体差异形成层次。
笔画特征：中文笔画横细竖粗，起收笔利落，带有清晰的三角衬线，转折精致，粗细对比自然。英文笔画均匀、轻盈，面积数字适当加粗。文字采用干净的平面效果，保持清晰边缘与细节。
结构布局：纵向分层排版，顶部信息分置左右；中文主标题位于中上部居中；英文副标题在中部拆行居中；项目名称在下部；底部署名小字号。整体以中轴对齐、疏朗留白为主，呈现安静、专业、具有设计事务所作品集气质的封面风格。

【底图保护】
仅添加文字及必要的局部明暗调整；保留原图空间结构与主体细节。

{AI_COVER_OUTPUT_REQUIREMENTS}"""

_PROMPT_P12 = f"""{AI_COVER_BASE_INTRO}

适用范围：
当内容类型为【装修案例分享】且选用本封面模板时，严格执行以下规则。先提取文案，再进行排版。参考封面只用于参考字体、配色和布局，不提取参考图中的文字。

文案提取：
封面只允许出现两行文字，每行内容必须来自本次提供的正文。

第一行：提取正文“工地地址”中的楼盘或小区名称。
只保留楼盘或小区名称，不包含城市、区县、街道、楼栋、房号、业主称呼、面积、宣传语及描述性句子。

第二行：提取正文中的面积和设计风格，组合为：
[面积数值]㎡[设计风格名称]实景
面积单位统一写成“㎡”，不重复添加单位；设计风格只保留名称，不加入评价、感受或介绍句。
方括号内为待提取字段，最终图片不得出现括号、字段名、占位符或“+”号。

不得将正文标题、文章开头、业主故事、营销文案或历史对话内容作为这两行文字。
如果正文缺少楼盘名称、面积或设计风格，先指出缺少的字段，暂不生成图片，不得猜测或用其他句子替代。

【正文字段原文（供对照提取，不得整段照搬上封面）】
{{body_fields}}

【已定稿两行（须逐字排版，不得改写、增删或替换）】
第一行：{{line1}}
第二行：{{line2}}

字体风格：
现代特粗无衬线黑体，字形方正饱满，笔画粗壮扎实，重心稳定，具有醒目的装修案例封面效果。主标题明显大于副标题，保持清晰的主次关系。

字体颜色：
主标题使用明黄色，副标题使用纯白色，描边及阴影使用黑色。文字与装饰统一控制在黄、白、黑三种颜色以内。

字形结构：
字面宽大，结构紧凑，保持字体原有比例，不横向拉伸，不人为挤压。
主标题参考字号110—145px，副标题参考字号60—80px；实际字号根据文案长度适配，以完整显示和阅读清晰为优先，不强求两行等宽。

笔画特征：
采用粗细均匀、边缘清楚的特粗黑体笔画。
主标题黑色描边参考5px，副标题黑色描边参考3px，保留字形内部空间。
阴影为黑色，不透明度60%，向右下各偏移3px，模糊半径参考4px，避免形成厚重光晕。

结构布局：
画布为1080×1440像素，两行文字位于顶部，分别水平居中，共用画面中轴x=540px。
文字组整体放在距顶部100—350px的区域内。

左右安全边距各不少于90px。
每一行的完整可见范围，包括文字、描边和阴影，总宽度不得超过900px。

字距按最终1080px宽画布设置：相邻字符额外增加15px字符间距。15px指字距，不是行距，也不是通过插入空格实现；“㎡”作为一个完整字符处理。
两行文字可见边缘之间保留约30px的垂直间隔。

排版前检查每行总宽度。若超过900px，保持15px字距，适当缩小该行字号，直到完整落入安全区域。
不得让文字延伸出画布，不得通过裁切文字、删除文字、横向压缩或拉伸字体来适配宽度。
两行分别居中，不为追求等宽而额外扩大字距。

背景处理：
使用本次指定图片作为底图，保留空间结构、家具比例、材质和自然光影。文字直接叠加，不添加厚重遮罩、额外图标、Logo或水印。

最终输出要求：
信息齐全且检查通过后，仅输出一张1080×1440像素、3:4比例、完整不透明的封面。
画面只出现上述两行提取后的文字。
不要生成多个方案，不要拼图，不要附加说明。

必须避免：
文案提取错误、套用正文长句、错别字、乱码、漏字、额外文案、文字重复、标题贴边、文字裁切、重点不突出、字体比例失衡、颜色混乱、厚重遮罩、廉价营销感、过度锐化、空间结构改变、家具变形、虚构Logo、水印和低清晰度。"""


class AiCoverSiteHeaderError(ValueError):
    """提示词十二：正文缺少楼盘、面积或设计风格。"""


def format_ai_cover_copy_text(*, title: str, subtitle: str = "") -> str:
    title = title.strip()
    subtitle = subtitle.strip()
    if title and subtitle:
        return f"{title}，{subtitle}"
    return title or subtitle


def format_ai_cover_two_lines(*, title: str, subtitle: str = "") -> tuple[str, str]:
    return title.strip(), subtitle.strip()


def _normalize_site_community_name(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    for marker in ("业主", "先生", "女士", "小姐", "老师"):
        if marker in text:
            text = text.split(marker, 1)[0].strip()
    while True:
        matched = re.match(r"^[\u4e00-\u9fff]{2,10}[省市区县](.+)$", text)
        if not matched:
            break
        remainder = matched.group(1).strip()
        if not remainder or remainder == text:
            break
        text = remainder
    for marker in ("栋", "单元", "室"):
        if marker in text:
            text = text.split(marker, 1)[0].strip()
    text = re.sub(r"\d+号?$", "", text).strip()
    return text[:48]


def _normalize_frame_area_sqm(raw: str) -> str:
    text = raw.strip().replace("m²", "㎡").replace("m2", "㎡")
    if not text:
        return ""
    if "㎡" in text:
        numeric = text.split("㎡", 1)[0].strip()
    else:
        numeric = text
    if "-" in numeric:
        numeric = numeric.split("-", 1)[0].strip()
    numeric = re.sub(r"[^\d.]", "", numeric)
    if not numeric:
        return ""
    return f"{numeric}㎡"


def build_site_header_lines(
    *,
    title: str,
    subtitle: str,
    form_values: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """提示词十二：从正文业务字段提取顶栏双行，缺字段则拒绝生成。"""
    del title, subtitle
    form_values = form_values or {}
    site_raw = str(form_values.get("工地地址") or form_values.get("楼盘信息") or "").strip()
    area_raw = str(form_values.get("外框面积") or "").strip()
    style = str(form_values.get("设计风格") or "").strip()
    missing: list[str] = []
    if not site_raw:
        missing.append("工地地址/楼盘名称")
    if not area_raw:
        missing.append("外框面积")
    if not style:
        missing.append("设计风格")
    if missing:
        raise AiCoverSiteHeaderError(f"AI 封面提示词十二缺少必填字段：{'、'.join(missing)}")
    line1 = _normalize_site_community_name(site_raw)
    if not line1:
        raise AiCoverSiteHeaderError("AI 封面提示词十二无法从工地地址提取楼盘或小区名称")
    area = _normalize_frame_area_sqm(area_raw)
    if not area:
        raise AiCoverSiteHeaderError("AI 封面提示词十二外框面积无效")
    line2 = f"{area}{style}实景"[:60]
    return line1, line2


def _build_p12_prompt(*, line1: str, line2: str, form_values: dict[str, Any] | None) -> str:
    form_values = form_values or {}
    site = str(form_values.get("工地地址") or form_values.get("楼盘信息") or "").strip()
    area = str(form_values.get("外框面积") or "").strip()
    style = str(form_values.get("设计风格") or "").strip()
    body_fields = (
        f"工地地址/楼盘信息：{site}\n外框面积：{area}\n设计风格：{style}"
    )
    return _PROMPT_P12.format(body_fields=body_fields, line1=line1, line2=line2)


def ai_cover_uses_embedded_copy(variant: str) -> bool:
    return variant != "editorial"


def is_case_share_content_type(*, content_type_code: str = "", content_type_name: str = "") -> bool:
    code = content_type_code.strip().upper()
    name = content_type_name.strip()
    return code in AI_COVER_CASE_SHARE_TYPE_CODES or name in AI_COVER_CASE_SHARE_TYPE_NAMES


def ai_cover_eligible_variants(
    *,
    content_type_code: str = "",
    content_type_name: str = "",
) -> tuple[AiCoverPromptVariant, ...]:
    if is_case_share_content_type(content_type_code=content_type_code, content_type_name=content_type_name):
        return AI_COVER_VARIANTS
    return tuple(variant for variant in AI_COVER_VARIANTS if variant != "p12")


@dataclass(frozen=True)
class AiCoverJobSelection:
    prompt: str
    variant: AiCoverPromptVariant
    render_copy_with_image2: bool
    extra_parameters: dict[str, Any] = field(default_factory=dict)


def _build_style_prompt(template: str, *, title: str, subtitle: str) -> str:
    line1, line2 = format_ai_cover_two_lines(title=title, subtitle=subtitle)
    copy_text = format_ai_cover_copy_text(title=title, subtitle=subtitle)
    return template.format(line1=line1, line2=line2, copy_text=copy_text)


def resolve_ai_cover_variant(
    *,
    pool: tuple[AiCoverPromptVariant, ...],
    prompt_random: bool = True,
    prompt_variant: str | None = None,
    featured_template_index: int | None = None,
) -> AiCoverPromptVariant:
    if prompt_random:
        return random.choice(pool)
    explicit = (prompt_variant or "").strip()
    if explicit:
        if explicit not in pool:
            raise ValueError(f"AI 封面指定提示词不在当前内容类型可用范围内：{explicit}")
        return explicit  # type: ignore[return-value]
    if featured_template_index:
        mapped = AI_COVER_TEMPLATE_SLOT_PROMPTS.get(int(featured_template_index))
        if mapped and mapped in pool:
            return mapped
    raise ValueError("AI 封面指定模式下需选择已映射的封面模板或有效提示词变体")


def job_selection_for_variant(
    variant: AiCoverPromptVariant,
    *,
    title: str,
    subtitle: str = "",
    form_values: dict[str, Any] | None = None,
) -> AiCoverJobSelection:
    if variant == "editorial":
        return AiCoverJobSelection(
            prompt=AI_COVER_PROMPT,
            variant="editorial",
            render_copy_with_image2=True,
        )
    if variant == "p12":
        line1, line2 = build_site_header_lines(title=title, subtitle=subtitle, form_values=form_values)
        return AiCoverJobSelection(
            prompt=_build_p12_prompt(line1=line1, line2=line2, form_values=form_values),
            variant="p12",
            render_copy_with_image2=True,
        )
    builders = {
        "p1": _PROMPT_P1,
        "p2": _PROMPT_P2,
        "p3": _PROMPT_P3,
        "p7": _PROMPT_P7,
        "p8": _PROMPT_P8,
        "p9": _PROMPT_P9,
        "p10": _PROMPT_P10,
        "p11": _PROMPT_P11,
    }
    return AiCoverJobSelection(
        prompt=_build_style_prompt(builders[variant], title=title, subtitle=subtitle),
        variant=variant,
        render_copy_with_image2=True,
    )


def ai_cover_prompt_controls(visual_material: dict[str, Any] | None) -> dict[str, Any]:
    vm = visual_material or {}
    random_flag = vm.get("ai_cover_prompt_random")
    index = vm.get("featured_cover_template_index")
    return {
        "prompt_random": True if random_flag is None else bool(random_flag),
        "prompt_variant": str(vm.get("ai_cover_prompt_variant") or "").strip() or None,
        "featured_template_index": int(index) if index else None,
    }


def select_ai_cover_prompt(
    *,
    title: str,
    subtitle: str = "",
    form_values: dict[str, Any] | None = None,
    content_type_code: str = "",
    content_type_name: str = "",
    prompt_random: bool = True,
    prompt_variant: str | None = None,
    featured_template_index: int | None = None,
) -> AiCoverJobSelection:
    pool = ai_cover_eligible_variants(
        content_type_code=content_type_code,
        content_type_name=content_type_name,
    )
    variant = resolve_ai_cover_variant(
        pool=pool,
        prompt_random=prompt_random,
        prompt_variant=prompt_variant,
        featured_template_index=featured_template_index,
    )
    return job_selection_for_variant(
        variant,
        title=title,
        subtitle=subtitle,
        form_values=form_values,
    )


__all__ = [
    "AiCoverSiteHeaderError",
    "AI_COVER_IMAGE_ONLY_PROMPT",
    "AI_COVER_OUTPUT_REQUIREMENTS",
    "AI_COVER_PROMPT",
    "AI_COVER_CASE_SHARE_TYPE_CODES",
    "AI_COVER_CASE_SHARE_TYPE_NAMES",
    "AI_COVER_TEMPLATE_SLOT_PROMPTS",
    "AI_COVER_VARIANTS",
    "AiCoverJobSelection",
    "AiCoverPromptVariant",
    "ai_cover_eligible_variants",
    "job_selection_for_variant",
    "resolve_ai_cover_variant",
    "ai_cover_prompt_controls",
    "ai_cover_uses_embedded_copy",
    "is_case_share_content_type",
    "build_site_header_lines",
    "format_ai_cover_copy_text",
    "select_ai_cover_prompt",
]
