from __future__ import annotations

import random
import re
from typing import Any

BRAND_NAME = "鸿扬家装"
CONSTRUCTION_BRAND = "鸿扬家装"
# 获客文案口径：鸿扬是定制化家装，禁止写成整装/标准化整装。
BRAND_POSITIONING = "定制化家装"
DECORATION_QUOTE_KEYS = ("基础", "木制品", "主材")
LAYOUT_FIELD_KEYS = ("房屋布局", "户型布局", "户型")
# PC / 小程序业务变量表单：存在则固定排在最前（其余保持原相对顺序）
FORM_FIELD_PRIORITY_KEYS = ("外框面积", "基础", "木制品", "主材")
REVIEW_NOTE_ROLE_KEYS = ("设计师", "预算师", "项目经理", "客户经理", "工匠")
REVIEW_NOTES_WRITING_INSTRUCTION = (
    "以业主第一人称评价设计师、预算师、项目经理、客户经理等项目成员；"
    "模仿简报 style_excerpts 的语气、结构和用词，不得抄录样例中的他人事实；"
    "标题不要出现楼盘、小区或项目案名，可以赞美表扬所属店面或门店；"
    "写内部可归档的真实好评，不要写成获客种草、员工自荐或销售转化文案。"
)
DECORATION_CTA_LAYOUT = (
    "收尾必须独立成块、真实换行，禁止把城市/小区/面积揉成「在常德朋友，如果也在为洋湖1号这类112㎡…」一句墙字；"
    "版式：📍〔城市或同城〕｜准备装修的朋友👋／一句主题设问／空行后2～4条▫️共性痛点（禁编该套房缺陷）／"
    "空行后💬互动提问＋欢迎在评论区聊聊～＋一句轻收束🏠。"
)
DECORATION_WRITING_INSTRUCTION = (
    "标题要有吸引点：情绪、悬念、反差或利益点至少占一项，禁止楼盘+面积+风格的说明书式平铺；"
    "正文不要展开某套房的案例故事（旧况、改造过程、完工效果叙事），每套房子不同，细节写错容易失真；"
    "正文优先用信息卡点写清：小区名称、房屋面积、房屋布局（仅简报有填时）、风格、项目施工鸿扬家装；"
    "卡点文字必须与简报/证据原文一致（面积只用锁定的具体㎡，如142㎡，禁止写130-150㎡这类区间）；"
    "写出的每个卡点与数字都要在 paragraph_evidence 挂载对应 evidence_bundle.items[].id"
    "（短码如 E01），禁止手写 ev_ 长串或编造 ID；"
    "若有可用于正文的业务知识证据，至少再挂一条；"
    "必须写出鸿扬家装品牌优势（定位为定制化家装，禁止写整装或标准化整装），"
    "并带明确引流点（同城咨询、留言、评论区聊聊等；成品禁用「私信」「报价」等平台封禁词，以 evidence 中 forbidden_replacement_map 为准）；"
    + DECORATION_CTA_LAYOUT
    + "费用称谓一律写「预算价」，禁止写「合同价」（证据或词库原文是合同价时只改称谓，数字保持原样）；"
    "成品标题/正文/话题不要出现「口径」，对外写钱花在哪、费用怎么拆、预算清不清楚；"
    "项目阶段为泥木阶段时，成品写「泥瓦」不写「泥木」；工艺类型或证据未出现木工时不得补写木工；"
    "封面/副标/话题也不得出现整装、标准化整装；事实只来自简报与冻结证据，不得编造户型缺陷、改造前后效果或他人案例细节。"
)
QUOTATION_LIST_TYPE_NAMES = frozenset({"装修报价清单", "报价清单"})
QUOTATION_LIST_WRITING_INSTRUCTION = (
    "内容类型为装修报价清单：标题必须让人一眼看懂在说什么，句子通顺、语义完整，禁止词库堆砌或看不懂的标题；"
    "正例：142㎡旧房翻新，钱要花在哪？；反例：旧房翻新业主130-150㎡预算不踩坑。"
    "旧房改造对外可写旧房翻新；主题落在钱花在哪、费用怎么拆、避隐形增项，不要吹嘘最低价；"
    "正文把基础/木制品/主材等费用仅作参考信息卡点展示，明确费用数字不是鸿扬核心卖点，禁止主推「更便宜、低价、性价比碾压」；"
    "正文重点写鸿扬家装品牌优势：定制化家装、透明施工、自有/规范工艺、售后与靠谱服务，用品牌与交付能力收尾引流；"
    + DECORATION_CTA_LAYOUT
    + "成品标题/正文/话题必须规避平台封禁词库问题词（见 evidence forbidden_replacement_map），"
    "引流只用同城咨询、留言、评论区等安全表达，不得出现「私信」「报价」等表内问题词；词库原文含问题词时须改写后再写入；"
    "费用称谓一律写「预算价」，禁止写「合同价」（证据原文是合同价时只改称谓、数字保持原样）；"
    "成品不要出现「口径」；禁止把鸿扬写成整装或标准化整装；仍须写清小区、面积（锁定的具体㎡，禁止区间）、风格、项目施工鸿扬家装等信息卡点，并正确挂载 Evidence ID；"
    "不展开某套房案例故事，不编造数字与改造情节。"
)
CRAFT_SHOWCASE_TYPE_NAMES = frozenset({"工艺施工展示", "工艺展示"})
# 工厂规则 CT05=施工报价（需锁定报价字段）；工艺展示对应 CT06。
CRAFT_SHOWCASE_DIRECTION_CODE = "CT06"
CONTENT_TYPE_NAME_TO_DIRECTION = {
    "工艺施工展示": "CT06",
    "工艺展示": "CT06",
    "装修报价清单": "CT02",
    "报价清单": "CT02",
    "装修避坑分享": "CT03",
    "避坑分享": "CT03",
    "装修省钱攻略": "CT04",
    "省钱攻略": "CT04",
    "装修案例分享": "CT01",
    "案例分享": "CT01",
    "装修知识科普": "CT06",
    "知识科普": "CT06",
    "人设自荐": "CT07",
    "装修人设自荐": "CT07",
}
LEGACY_QUOTE_TYPE_NAME = "施工报价"
MANAGED_CONTENT_TYPE_NAMES = frozenset(CONTENT_TYPE_NAME_TO_DIRECTION)
QUOTE_CONTENT_TYPE_NAMES = QUOTATION_LIST_TYPE_NAMES | {LEGACY_QUOTE_TYPE_NAME}


def is_managed_content_type_name(name: str | None) -> bool:
    return str(name or "").strip() in MANAGED_CONTENT_TYPE_NAMES


def is_quote_content_type_name(name: str | None) -> bool:
    return str(name or "").strip() in QUOTE_CONTENT_TYPE_NAMES


# 工艺展示禁用报价转化（C01）与实景案例流量（C02），优先干货工艺讲解。
CRAFT_SHOWCASE_BLOCKED_BODY_FORMULAS = frozenset({"C01", "C02"})
CRAFT_SHOWCASE_PREFERRED_BODY_FORMULAS = ("C03", "C04")
# T02 会把「听劝/真香」和「远超预期」硬拼进标题，不适合工艺验收口吻。
CRAFT_SHOWCASE_BLOCKED_TITLE_FORMULAS = frozenset({"T02"})
CRAFT_SHOWCASE_PREFERRED_TITLE_FORMULAS = ("T03", "T05", "T07", "T04")
STAGE_CRAFT_TOPICS = {
    "水电阶段": "水电施工与隐蔽验收",
    "拆改阶段": "拆改施工",
    "泥木阶段": "泥瓦施工",
    "油漆阶段": "油漆施工",
    "竣工交付": "竣工验收",
}
CRAFT_SHOWCASE_WRITING_INSTRUCTION = (
    "内容类型为工艺施工展示：标题必须是普通人能读完的口语句，点明工序或当前阶段主题"
    "（水电阶段写水电验收细节，不要写成整屋案例；泥木阶段对外写泥瓦验收/泥瓦施工，不要写泥木，"
    "工艺或证据没出现木工时不要补写木工）。禁止词库硬拼"
    "（反例：水电施工听劝，规范验收远超预期；正例：水电做得好不好，验收细节见分晓／"
    "水电藏进墙之前，这些细节要验清／旧房翻新，水电验收千万别走过场）。"
    "正文主线是工艺科普与标准讲解：写清工艺类型、工艺名称或项目阶段，说明规范做法、关键细节和为什么重要；"
    "不要强调装修风格，也不要把风格、小区、面积写成资料卡；有工艺名称时写成"
    "「在定制化家装项目中，鸿扬家装采用〔工艺名称〕，属于〔工艺类型〕。从〔关键环节〕……每个环节都按工艺规范落实」。"
    "正例：在定制化家装项目中，鸿扬家装采用HYB-强电箱内空开安装工艺，属于安全用电系统。"
    "从空开选型、回路划分到接线、标识，每个环节都严格按照工艺规范落实，不赶工、不省步骤，扎实做好用电安全的每一处细节。"
    "禁止写成装修案例分享：不得展开某套房旧况→改造过程→完工效果叙事，不得虚构客户经历或前后对比故事；"
    "品牌优势可写定制化家装（禁止整装/标准化整装）；"
    + DECORATION_CTA_LAYOUT
    + "费用称谓一律写「预算价」，禁止写「合同价」；成品不要出现「口径」；成品规避平台封禁词库问题词（见 evidence forbidden_replacement_map）；卡点与数字须挂载正确 Evidence ID。"
)


def content_direction_from_form_values(values: dict[str, Any] | None) -> str | None:
    if not isinstance(values, dict):
        return None
    name = str(values.get("mp_content_type_name") or "").strip()
    return CONTENT_TYPE_NAME_TO_DIRECTION.get(name)


def content_direction_from_brief(brief: dict[str, Any] | None) -> str | None:
    if not isinstance(brief, dict):
        return None
    # 表单内容类型名称优先：历史任务可能把工艺展示误存成 CT05（施工报价）。
    for key in ("form_values", "business_variables"):
        section = brief.get(key)
        direction = content_direction_from_form_values(section if isinstance(section, dict) else None)
        if direction:
            return direction
    code = str(brief.get("content_type_code") or "").strip()
    if code in CONTENT_TYPE_NAME_TO_DIRECTION.values():
        return code
    return None


def resolve_task_content_type_code(task: Any) -> str | None:
    """任务页所选内容类型优先于自动方向，用来锁定对应爆款库。"""
    direction = content_direction_from_brief(getattr(task, "brief_json", None) or {})
    if direction:
        return direction
    code = str(getattr(task, "content_type_code", None) or "").strip()
    if code in CONTENT_TYPE_NAME_TO_DIRECTION.values():
        return code
    return None


def _filter_formulas_for_craft_showcase(
    direction_code: str,
    codes: list[str] | tuple[str, ...],
    *,
    blocked: frozenset[str],
    preferred: tuple[str, ...],
) -> list[str]:
    ordered = [str(code).strip() for code in codes if str(code).strip()]
    if direction_code != CRAFT_SHOWCASE_DIRECTION_CODE:
        return ordered
    filtered = [code for code in ordered if code not in blocked]
    return filtered or list(preferred)


def filter_body_formulas_for_content_direction(
    direction_code: str,
    codes: list[str] | tuple[str, ...],
) -> list[str]:
    """按内容方向收窄正文公式；工艺展示不得落入案例/报价转化公式。"""
    return _filter_formulas_for_craft_showcase(
        direction_code,
        codes,
        blocked=CRAFT_SHOWCASE_BLOCKED_BODY_FORMULAS,
        preferred=CRAFT_SHOWCASE_PREFERRED_BODY_FORMULAS,
    )


def filter_title_formulas_for_content_direction(
    direction_code: str,
    codes: list[str] | tuple[str, ...],
) -> list[str]:
    """按内容方向收窄标题公式；工艺展示禁用听劝/远超预期类情绪硬拼公式。"""
    return _filter_formulas_for_craft_showcase(
        direction_code,
        codes,
        blocked=CRAFT_SHOWCASE_BLOCKED_TITLE_FORMULAS,
        preferred=CRAFT_SHOWCASE_PREFERRED_TITLE_FORMULAS,
    )


def prioritize_form_fields(
    fields: list[dict[str, Any]],
    *,
    name_keys: tuple[str, ...] = ("key", "name", "label", "variable_name"),
) -> list[dict[str, Any]]:
    """Stable-sort so priority field names appear first when present."""
    rank = {name: index for index, name in enumerate(FORM_FIELD_PRIORITY_KEYS)}
    fallback = len(FORM_FIELD_PRIORITY_KEYS)

    def field_name(item: dict[str, Any]) -> str:
        for key in name_keys:
            value = str(item.get(key) or "").strip()
            if value:
                return value
        return ""

    indexed = list(enumerate(fields))
    indexed.sort(key=lambda pair: (rank.get(field_name(pair[1]), fallback), pair[0]))
    return [item for _, item in indexed]


def configured_form_fields(
    variables: list[dict[str, Any]],
    *,
    service_entry: str,
    port: str,
    edition: str,
) -> list[dict[str, Any]]:
    return [
        {
            "key": item["name"],
            "label": item["name"],
            "type": "textarea",
            "required": True,
            "variable_code": item.get("variable_code"),
        }
        for item in variables
        if item.get("enabled")
        and item.get("service_entry") == service_entry
        and port in (item.get("ports") or [])
        and edition in (item.get("editions") or [])
    ]


FIELD_SELECT_OPTIONS: dict[str, list[str]] = {
    "设计风格": [
        "复合写意",
        "写意木构",
        "江南印象",
        "东方古雅",
        "轻欧简美",
        "欧美香颂",
        "欧式田园",
        "异域风情",
        "新装饰主义",
        "北欧之光",
        "意境东方",
        "复古风潮",
        "雅致现代",
        "工业再造",
        "优雅缤纷",
        "极简侘寂",
        "仿材未来",
        "艺术室界",
    ],
    "项目阶段": ["拆改阶段", "水电阶段", "泥木阶段", "油漆阶段", "竣工交付"],
}

FIELD_PLACEHOLDERS: dict[str, str] = {
    "目标人群": "请选择目标人群",
    "居住人口": "请选择居住人口",
    "工艺类型": "请选择工艺类型",
    "工艺名称": "请选择工艺名称",
    "外框面积": "请输入外框面积，如 120",
    "楼盘信息": "示例：洋湖天序",
    "项目阶段": "请选择项目阶段",
    "岗位": "请输入岗位",
    "从业年限": "请输入从业年限",
}


def catalog_select_options(
    *,
    target_audiences: list[str] | None = None,
    resident_populations: list[str] | None = None,
    process_types: list[str] | None = None,
    process_names_by_type: dict[str, list[str]] | None = None,
) -> dict[str, list[str]] | None:
    options: dict[str, list[str]] = {}
    if target_audiences:
        options["目标人群"] = list(target_audiences)
    if resident_populations:
        options["居住人口"] = list(resident_populations)
    if process_types:
        options["工艺类型"] = list(process_types)
    if process_names_by_type:
        # 字段标记为下拉；前端会按所选工艺类型再筛选具体工艺名称。
        flattened: list[str] = []
        for names in process_names_by_type.values():
            for name in names:
                if name and name not in flattened:
                    flattened.append(name)
        if flattened:
            options["工艺名称"] = flattened
    return options or None


REGION_FIELD_NAMES = frozenset({"所在区域"})


def configured_business_variable_fields(
    bindings: list[dict[str, Any]],
    *,
    service_entry: str,
    content_type_id: str | None,
    port: str,
    select_options: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Build studio/MP form fields from business-variable bindings."""
    wanted_type_id = (content_type_id or "").strip()
    option_catalog = {**FIELD_SELECT_OPTIONS, **(select_options or {})}
    fields: list[dict[str, Any]] = []
    for item in bindings:
        if not item.get("enabled"):
            continue
        if item.get("service_entry") != service_entry:
            continue
        if port not in (item.get("ports") or []):
            continue
        binding_type_id = (item.get("content_type_id") or "").strip()
        if service_entry == "好评笔记":
            if binding_type_id:
                continue
        elif binding_type_id != wanted_type_id:
            continue
        name = str(item.get("variable_name") or "").strip()
        if not name:
            continue
        options = option_catalog.get(name)
        if name in REGION_FIELD_NAMES:
            field_type = "region"
            placeholder = "请选择所在区域"
        elif options:
            field_type = "select"
            placeholder = FIELD_PLACEHOLDERS.get(name) or f"请选择{name}"
        else:
            field_type = "text"
            placeholder = FIELD_PLACEHOLDERS.get(name) or f"请输入{name}"
        field: dict[str, Any] = {
            "key": name,
            "label": name,
            "name": name,
            "type": field_type,
            "required": bool(item.get("required")),
            "variable_code": item.get("variable_code") or name,
            "variable_id": item.get("variable_id"),
            "placeholder": placeholder,
            "content_type_id": binding_type_id or None,
            "service_entry": service_entry,
            "ports": [port],
            "enabled": True,
        }
        if options:
            field["options"] = options
        fields.append(field)
    return prioritize_form_fields(fields)


_AREA_RANGE = re.compile(r"^(\d+)\s*[-~～到至]\s*(\d+)\s*(?:㎡|m²|m2|平米|平)?$", re.I)
_AREA_PLUS = re.compile(r"^(\d+)\s*(?:㎡|m²|m2|平米|平)?\s*(?:以上|起)$", re.I)
_AREA_CONCRETE = re.compile(r"^(\d+)\s*(?:㎡|m²|m2|平米|平)?$", re.I)
_OPEN_AREA_SPAN = 100


def frame_area_band(text: str) -> tuple[int, int] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    ranged = _AREA_RANGE.fullmatch(raw)
    if ranged:
        low, high = int(ranged.group(1)), int(ranged.group(2))
        return (high, low) if low > high else (low, high)
    plus = _AREA_PLUS.fullmatch(raw)
    if plus:
        floor = int(plus.group(1))
        return floor + 1, floor + _OPEN_AREA_SPAN
    return None


def parse_concrete_area_sqm(text: str) -> int | None:
    raw = str(text or "").strip()
    if not raw or frame_area_band(raw):
        return None
    matched = _AREA_CONCRETE.fullmatch(raw)
    return int(matched.group(1)) if matched else None


def lock_house_area_sqm(*candidates: str, rng: random.Random | None = None) -> str:
    """把外框面积区间锁成其中一个具体㎡，标题和正文共用同一数字。"""
    texts = [str(item or "").strip() for item in candidates if str(item or "").strip()]
    band = next((item for text in texts if (item := frame_area_band(text))), None)
    if band is None:
        return texts[0] if texts else ""
    low, high = band
    for text in texts:
        number = parse_concrete_area_sqm(text)
        if number is not None and low <= number <= high:
            return f"{number}㎡"
    number = (rng or random).randint(low, high)
    return f"{number}㎡"


def map_service_entry_form_values(service_entry: str, form_values: dict[str, Any]) -> dict[str, Any]:
    values = {str(key): value for key, value in form_values.items()}
    community = str(values.get("楼盘信息") or "").strip()
    frame_area = lock_house_area_sqm(
        str(values.get("house_area") or "").strip(),
        str(values.get("area") or "").strip(),
        str(values.get("外框面积") or "").strip(),
    )
    if frame_area:
        values["外框面积"] = frame_area
    style = str(values.get("设计风格") or "").strip()
    region = str(values.get("所在区域") or "").strip()
    layout = next(
        (str(values.get(key) or "").strip() for key in LAYOUT_FIELD_KEYS if str(values.get(key) or "").strip()),
        "",
    )
    budget_text = "；".join(
        f"{label} {values[label]}".strip() for label in DECORATION_QUOTE_KEYS if str(values.get(label) or "").strip()
    )
    persona_text = "，".join(
        f"{label} {values[label]}".strip() for label in REVIEW_NOTE_ROLE_KEYS if str(values.get(label) or "").strip()
    )

    if service_entry == "装修家居":
        product = community or f"{BRAND_POSITIONING}项目"
        process = budget_text or f"{style} {frame_area}".strip() or f"{BRAND_POSITIONING}交付"
        content_type_name = str(values.get("mp_content_type_name") or "").strip()
        process_type = str(values.get("工艺类型") or "").strip()
        process_name = str(values.get("工艺名称") or "").strip()
        project_stage = str(values.get("项目阶段") or "").strip()
        stage_topic = STAGE_CRAFT_TOPICS.get(project_stage, project_stage)
        craft_text = "；".join(
            part for part in (process_type, process_name, stage_topic if not process_type else "") if part
        )
        is_quotation_list = content_type_name in QUOTATION_LIST_TYPE_NAMES
        is_craft_showcase = content_type_name in CRAFT_SHOWCASE_TYPE_NAMES
        if is_quotation_list:
            pain = f"{community or '业主'}关心装修预算怎么花、怕隐形增项，更需要看清品牌与交付是否靠谱"
            advantage = "；".join(
                part
                for part in (
                    f"{BRAND_NAME}品牌与{BRAND_POSITIONING}交付",
                    f"项目施工{CONSTRUCTION_BRAND}",
                    "透明工艺与售后服务（费用数字仅作参考，不以低价作为卖点）",
                )
                if part
            )
            values["writing_instruction"] = QUOTATION_LIST_WRITING_INSTRUCTION
        elif is_craft_showcase:
            pain = f"业主关心{process_type or stage_topic or '施工'}是否规范、细节是否到位、会不会走过场"
            advantage = "；".join(
                part
                for part in (
                    craft_text,
                    f"项目施工{CONSTRUCTION_BRAND}",
                    f"{BRAND_NAME}{BRAND_POSITIONING}与透明施工",
                )
                if part
            )
            if craft_text:
                process = craft_text
            values["writing_instruction"] = CRAFT_SHOWCASE_WRITING_INSTRUCTION
        else:
            pain = f"{community or '业主'}关注{frame_area or '户型'}装修落地"
            advantage = "；".join(
                part
                for part in (
                    style,
                    f"项目施工{CONSTRUCTION_BRAND}",
                    f"{BRAND_NAME}{BRAND_POSITIONING}与透明服务",
                )
                if part
            )
            values["writing_instruction"] = DECORATION_WRITING_INSTRUCTION
        audience = [region] if region else ["装修业主"]
        result = " ".join(part for part in (community, frame_area, layout, style, f"施工{CONSTRUCTION_BRAND}") if part)
        if region or community:
            values["location"] = region or community
        if community:
            values["case_background"] = community
        values["community_name"] = community
        values["house_area"] = frame_area
        values["house_layout"] = layout
        values["design_style"] = style
        values["construction_brand"] = CONSTRUCTION_BRAND
        values["brand_positioning"] = BRAND_POSITIONING
        scene = (project_stage or style) if is_craft_showcase else (style or project_stage)
        if scene:
            values["renovation_scene"] = scene
            values["scene"] = scene
        if is_quotation_list:
            values["quote_type"] = "budget"
    else:
        product = "业主好评笔记"
        process = persona_text or "项目成员服务"
        pain = "业主记录装修交付中项目成员的真实服务体验"
        advantage = persona_text or "项目成员服务"
        audience = ["业主"]
        result = persona_text
        values["voice"] = "业主第一人称"
        values["location"] = region
        values["writing_instruction"] = REVIEW_NOTES_WRITING_INSTRUCTION

    return {
        **values,
        "brand_name": BRAND_NAME,
        "audience": audience,
        "pain": pain,
        "advantage": advantage,
        "project_type": product,
        "area": frame_area,
        "budget": budget_text,
        "craft_and_materials": process,
        "owner_pain": pain,
        "project_result": result,
        "persona": persona_text,
    }
