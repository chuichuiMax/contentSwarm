"""内容生产平台的通用行业、方向和变量目录。"""

from __future__ import annotations

from typing import Any


XHS_CHANNEL_PROFILE_ID = "channel-xiaohongshu"
XHS_CHANNEL_VERSION_ID = "channel-xiaohongshu-v1"

CONTENT_TYPES = [
    {
        "code": "CT01",
        "name": "案例/成果展示",
        "description": "通过真实背景、过程和结果证明能力",
        "supported_goals": ["traffic", "acquire", "brand"],
        "required_variable_codes": ["audience", "process", "result"],
        "evidence_policy": {"result": "required", "number": "required_if_used"},
        "default_narrative_axes": ["before_after_result", "process_to_result"],
        "default_body_formula_codes": ["C02"],
    },
    {
        "code": "CT02",
        "name": "价格/方案透明",
        "description": "解释价格构成、方案边界和选择依据",
        "supported_goals": ["educate", "acquire"],
        "required_variable_codes": ["product", "price", "advantages"],
        "evidence_policy": {"price": "required", "promise": "confirm"},
        "default_narrative_axes": ["price_composition", "option_tradeoff"],
        "default_body_formula_codes": ["C01"],
    },
    {
        "code": "CT03",
        "name": "避坑/风险提示",
        "description": "揭示常见误区、风险及正确做法",
        "supported_goals": ["traffic", "educate", "acquire"],
        "required_variable_codes": ["pain", "process", "result"],
        "evidence_policy": {"risk": "knowledge_or_manual", "number": "required_if_used"},
        "default_narrative_axes": ["mistake_vs_correct", "risk_consequence"],
        "default_body_formula_codes": ["C03"],
    },
    {
        "code": "CT04",
        "name": "攻略/效率优化",
        "description": "提供可执行步骤，降低成本或提升效率",
        "supported_goals": ["traffic", "educate"],
        "required_variable_codes": ["audience", "pain", "process"],
        "evidence_policy": {"result": "required_if_claim"},
        "default_narrative_axes": ["cost_vs_efficiency", "steps_to_result"],
        "default_body_formula_codes": ["C03"],
    },
    {
        "code": "CT05",
        "name": "过程/能力证明",
        "description": "展示标准流程、专业细节和交付能力",
        "supported_goals": ["acquire", "brand"],
        "required_variable_codes": ["process", "advantage", "result"],
        "evidence_policy": {"process": "required", "result": "required_if_used"},
        "default_narrative_axes": ["detail_proves_capability", "process_reduces_risk"],
        "default_body_formula_codes": ["C02"],
    },
    {
        "code": "CT06",
        "name": "知识/问题教育",
        "description": "解释概念、判断标准和专业正解",
        "supported_goals": ["educate", "brand"],
        "required_variable_codes": ["pain", "process"],
        "evidence_policy": {"professional_claim": "knowledge_or_manual"},
        "default_narrative_axes": ["misconception_vs_fact", "question_to_standard"],
        "default_body_formula_codes": ["C03"],
    },
    {
        "code": "CT07",
        "name": "人设/品牌主张",
        "description": "表达经历、立场、原则和服务边界",
        "supported_goals": ["brand", "acquire"],
        "required_variable_codes": ["persona_fact", "advantage"],
        "evidence_policy": {"persona_fact": "confirmed_or_evidence"},
        "default_narrative_axes": ["experience_to_principle", "boundary_builds_trust"],
        "default_body_formula_codes": ["C04"],
    },
]

VARIABLES = [
    ("audience", "目标人群", "list", "normal", False),
    ("location", "地域", "string", "normal", False),
    ("product", "产品或服务", "string", "normal", False),
    ("price", "价格", "money", "high_risk", True),
    ("duration", "周期", "duration", "sensitive", True),
    ("quantity", "数量", "number", "sensitive", True),
    ("number", "量化数字", "number", "high_risk", True),
    ("pain", "用户痛点", "list", "normal", False),
    ("pain_points", "用户痛点兼容字段", "list", "normal", False),
    ("result", "结果", "string", "high_risk", True),
    ("process", "过程", "list", "sensitive", True),
    ("advantage", "差异优势", "list", "normal", False),
    ("advantages", "差异优势兼容字段", "list", "normal", False),
    ("persona_fact", "人物经历", "string", "high_risk", True),
    ("scene", "场景", "string", "normal", False),
    ("brand_name", "品牌名称", "string", "normal", False),
    ("emotion", "情绪表达", "string", "normal", False),
    ("suspense", "悬念表达", "string", "normal", False),
    ("advice", "忠告表达", "string", "normal", False),
    ("call_to_action", "行动指令", "string", "normal", False),
]

INDUSTRY_CONFIG: dict[str, dict[str, Any]] = {
    "food": {
        "aliases": ["门店案例", "菜单价格透明", "到店避坑", "探店攻略", "后厨能力", "餐饮知识", "主理人故事"],
        "fields": [
            ("signature_item", "招牌产品", "product"),
            ("average_spend", "客单价", "price"),
            ("service_process", "服务流程", "process"),
            ("customer_result", "顾客反馈", "result"),
        ],
        "terms": ["现点现做", "到店体验", "招牌风味", "后厨流程"],
        "persona": "门店主理人",
    },
    "education": {
        "aliases": ["学习案例", "课程方案透明", "选课避坑", "学习攻略", "教学能力", "知识讲解", "教师人设"],
        "fields": [
            ("course", "课程", "product"),
            ("tuition", "课程价格", "price"),
            ("course_stage", "课程阶段", "audience"),
            ("teaching_process", "教学流程", "process"),
            ("learning_result", "学习结果", "result"),
        ],
        "terms": ["学习路径", "阶段目标", "课堂反馈", "因材施教"],
        "persona": "授课老师",
    },
    "beauty": {
        "aliases": ["护理案例", "项目价格透明", "护理避坑", "变美攻略", "服务能力", "护理知识", "手艺人人设"],
        "fields": [
            ("service_item", "护理项目", "product"),
            ("service_price", "项目价格", "price"),
            ("service_process", "服务流程", "process"),
            ("care_result", "护理结果", "result"),
            ("skin_need", "护理需求", "pain"),
        ],
        "terms": ["肤质评估", "护理流程", "个体差异", "居家养护"],
        "persona": "护理师",
    },
    "retail": {
        "aliases": ["产品案例", "价格方案透明", "选购避坑", "使用攻略", "产品能力", "选购知识", "品牌人设"],
        "fields": [
            ("product_name", "产品", "product"),
            ("sale_price", "销售价格", "price"),
            ("product_specs", "产品参数", "process"),
            ("usage_result", "使用结果", "result"),
            ("buyer_need", "购买需求", "pain"),
        ],
        "terms": ["规格参数", "使用场景", "选购标准", "售后边界"],
        "persona": "品牌选品人",
    },
    "professional-services": {
        "aliases": ["服务案例", "方案价格透明", "决策避坑", "办事攻略", "交付能力", "专业知识", "顾问人设"],
        "fields": [
            ("service_name", "专业服务", "product"),
            ("service_fee", "服务费用", "price"),
            ("service_boundary", "服务边界", "process"),
            ("delivery_result", "交付结果", "result"),
            ("client_problem", "客户问题", "pain"),
        ],
        "terms": ["服务边界", "交付节点", "风险提示", "判断依据"],
        "persona": "专业顾问",
    },
    "decoration": {
        "aliases": [
            "装修案例分享",
            "装修报价清单",
            "装修避坑分享",
            "装修省钱攻略",
            "工艺施工展示",
            "装修知识科普",
            "装修人设自荐",
        ],
        "fields": [
            ("project_type", "户型", "product"),
            ("area", "面积", "quantity"),
            ("budget", "预算", "price"),
            ("duration", "工期", "duration"),
            ("craft_and_materials", "工艺材料", "process"),
            ("owner_pain", "居住痛点", "pain"),
            ("project_result", "完工结果", "result"),
        ],
        "terms": ["节点验收", "材料说明", "工艺标准", "预算边界"],
        "persona": "设计师或工长",
    },
}

DECORATION_LEXICON_CATEGORIES = [
    "户型",
    "面积",
    "装修风格",
    "空间",
    "房屋状态",
    "业主阶段",
    "家庭结构",
    "居住痛点",
    "装修需求",
    "预算区间",
    "报价构成",
    "费用项目",
    "材料品类",
    "材料品牌",
    "材料性能",
    "工艺节点",
    "施工流程",
    "拆改",
    "水电",
    "泥瓦",
    "木工",
    "油漆",
    "防水",
    "收口",
    "验收",
    "工期",
    "现场管理",
    "设计方案",
    "收纳",
    "采光",
    "动线",
    "色彩",
    "软装",
    "风险避坑",
]


def content_form_fields(config: dict[str, Any], *, pro: bool) -> list[dict[str, Any]]:
    fields = [
        {
            "key": "brand_name",
            "label": "品牌、门店或主理人",
            "type": "text",
            "required": True,
            "variable_code": "brand_name",
        },
        {"key": "audience", "label": "目标人群", "type": "tags", "required": True, "variable_code": "audience"},
        {"key": "pain", "label": "用户问题", "type": "tags", "required": True, "variable_code": "pain"},
        {"key": "advantage", "label": "差异化优势", "type": "tags", "required": True, "variable_code": "advantage"},
    ]
    if pro:
        fields.extend(
            [
                {"key": "persona_profile_version_id", "label": "人设档案", "type": "persona", "required": False},
                {"key": "channel_profile_version_id", "label": "发布渠道", "type": "channel", "required": True},
                {"key": "attachments", "label": "真实素材", "type": "materials", "required": False},
                {"key": "required_terms", "label": "必须包含", "type": "tags", "required": False},
                {"key": "forbidden_terms", "label": "禁止表达", "type": "tags", "required": False},
            ]
        )
    fields.extend(
        {
            "key": key,
            "label": label,
            "type": "textarea",
            "required": variable in {"product", "process"},
            "variable_code": variable,
            "evidence_required": variable in {"price", "duration", "quantity", "result", "process"},
        }
        for key, label, variable in config["fields"]
    )
    return fields


__all__ = [
    "CONTENT_TYPES",
    "DECORATION_LEXICON_CATEGORIES",
    "INDUSTRY_CONFIG",
    "VARIABLES",
    "XHS_CHANNEL_PROFILE_ID",
    "XHS_CHANNEL_VERSION_ID",
    "content_form_fields",
]
