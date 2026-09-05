from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_content import (
    ContentCombinationRule,
    ContentFormula,
    ContentRuleVersion,
    ContentWorkflowVersion,
    CreationMethod,
    IndustryTemplateVersion,
    TitleFormula,
)
from yuxi.utils.datetime_utils import utc_now_naive

PLATFORM_RULE_VERSION_ID = "content-rules-platform-v1"
PLATFORM_WORKFLOW_VERSION_ID = "content-workflow-enterprise-v1"

CONTENT_GOALS = [
    {"code": "traffic", "name": "流量曝光", "description": "扩大曝光、提升点击和停留"},
    {"code": "educate", "name": "干货教育", "description": "输出方法、建立专业认知"},
    {"code": "acquire", "name": "获客转化", "description": "形成咨询、留资或到店行动"},
    {"code": "brand", "name": "品牌人设", "description": "沉淀品牌信任与长期人设"},
]

METHODS = [
    {
        "code": "M01",
        "name": "数字法",
        "method_type": "core",
        "principle": "用可验证的数字降低决策成本，强化真实、透明和专业度。",
        "suitable_scenes": ["价格拆解", "案例复盘", "周期说明", "效果对比"],
        "sentence_patterns": ["只用{number}，完成{result}", "{number}个关键点，讲清{topic}"],
        "tag_schema": {"core": ["透明", "真实", "量化"]},
        "variable_schema": ["number", "result"],
        "risk_rules": ["数字必须有来源", "不得虚构效果或统计结论"],
    },
    {
        "code": "M02",
        "name": "悬念法",
        "method_type": "core",
        "principle": "通过信息差、反差或反常识认知提升点击和停留。",
        "suitable_scenes": ["避坑", "误区", "揭秘", "反差结果"],
        "sentence_patterns": ["很多人以为{misunderstanding}，其实{truth}", "为什么{phenomenon}？"],
        "tag_schema": {"core": ["反差", "揭秘", "避坑"]},
        "variable_schema": ["pain_points"],
        "risk_rules": ["悬念必须在正文兑现", "不得制造虚假恐慌"],
    },
    {
        "code": "M03",
        "name": "价值法",
        "method_type": "core",
        "principle": "直接表达用户可获得的利益、资源或解决方案。",
        "suitable_scenes": ["清单", "攻略", "解决方案", "服务介绍"],
        "sentence_patterns": ["帮你{result}的{number}个方法", "看完就能{action}"],
        "tag_schema": {"core": ["价值", "攻略", "解决方案"]},
        "variable_schema": ["result", "advantages"],
        "risk_rules": ["收益不得超出证据支持范围"],
    },
    {
        "code": "M04",
        "name": "人群定位法",
        "method_type": "core",
        "principle": "通过地域、身份、需求和垂直标签筛选精准流量。",
        "suitable_scenes": ["本地获客", "垂直人群", "特定阶段需求"],
        "sentence_patterns": ["{audience}一定要看", "在{location}需要{product}的人"],
        "tag_schema": {"core": ["人群", "地域", "垂直需求"]},
        "variable_schema": ["audience"],
        "risk_rules": ["不得使用歧视性或排他性标签"],
    },
    {
        "code": "S01",
        "name": "场景增强",
        "method_type": "enhancer",
        "principle": "把抽象问题还原为具体人物、时间、地点、冲突和前后变化。",
        "suitable_scenes": ["消费决策", "使用过程", "服务现场", "结果复盘"],
        "sentence_patterns": ["当{person}在{scene}遇到{conflict}", "从{before}到{after}"],
        "tag_schema": {"dimensions": ["人物", "时间", "地点", "冲突", "变化"]},
        "variable_schema": ["scene"],
        "risk_rules": ["案例场景不得伪造为真实客户经历"],
    },
]

TITLE_FORMULAS = [
    {
        "code": "T01",
        "name": "细分人群＋数字＋结果",
        "suitable_scenes": ["案例", "经验", "结果复盘"],
        "core_goal": "精准圈定人群并用量化结果降低理解成本",
        "reference_examples": ["新手店主用3步理清开店成本"],
        "variable_schema": ["audience", "number", "result"],
        "compatible_methods": ["M01", "M03", "M04"],
        "risk_rules": ["数字与结果必须有来源"],
    },
    {
        "code": "T02",
        "name": "情绪＋数字＋结果",
        "suitable_scenes": ["体验分享", "反差结果", "真实测评"],
        "core_goal": "情绪带入并快速传达收益",
        "reference_examples": ["没想到，7天就把门店陈列理顺了"],
        "variable_schema": ["emotion", "number", "result"],
        "compatible_methods": ["M01", "M02", "M03"],
        "risk_rules": ["不得夸大情绪和结果"],
    },
    {
        "code": "T03",
        "name": "定位＋精准问题＋利好结果",
        "suitable_scenes": ["搜索获客", "问题解决"],
        "core_goal": "匹配精准搜索意图并给出解决方向",
        "reference_examples": ["杭州小店客流不稳，先做好这一步"],
        "variable_schema": ["audience", "pain_points", "result"],
        "compatible_methods": ["M03", "M04"],
        "risk_rules": ["利好结果必须表达为目标或有证据支撑"],
    },
    {
        "code": "T04",
        "name": "悬念＋数字＋痛点",
        "suitable_scenes": ["揭秘", "避坑", "价格反差"],
        "core_goal": "用信息差放大用户正在面对的问题",
        "reference_examples": ["90%的人都忽略了报价里的这项成本"],
        "variable_schema": ["number", "pain_points"],
        "compatible_methods": ["M01", "M02"],
        "risk_rules": ["比例数字必须有统计来源"],
    },
    {
        "code": "T05",
        "name": "忠告情绪＋人群痛点＋解决方案",
        "suitable_scenes": ["干货", "忠告", "购买建议"],
        "core_goal": "以专业忠告建立信任并给出行动方案",
        "reference_examples": ["准备开店的人，别让选址拖垮第一年"],
        "variable_schema": ["audience", "pain_points", "advantages"],
        "compatible_methods": ["M02", "M03", "M04"],
        "risk_rules": ["避免恐吓式表达"],
    },
    {
        "code": "T06",
        "name": "地域＋品类＋数字反差",
        "suitable_scenes": ["本地生活", "门店", "实景案例"],
        "core_goal": "同时获得地域搜索和反差点击",
        "reference_examples": ["成都60㎡咖啡店，改完多出12个座位"],
        "variable_schema": ["location", "product", "number"],
        "compatible_methods": ["M01", "M02", "M04"],
        "risk_rules": ["地域和数字必须真实"],
    },
    {
        "code": "T07",
        "name": "号召指令＋数字＋价值",
        "suitable_scenes": ["清单", "攻略", "收藏内容"],
        "core_goal": "用明确动作提高收藏和后续使用",
        "reference_examples": ["收藏这份5项开店检查清单"],
        "variable_schema": ["number", "result"],
        "compatible_methods": ["M01", "M03"],
        "risk_rules": ["正文必须完整交付承诺价值"],
    },
]

BODY_FORMULAS = [
    {
        "code": "C01",
        "name": "方案转化类",
        "industry_aliases": {"decoration": "报价转化类"},
        "compatible_methods": ["M01", "M03", "M04"],
        "suitable_scenes": ["服务咨询", "方案介绍", "获客转化"],
        "business_pains": ["信息不透明", "决策成本高", "方案难比较"],
        "structure_schema": ["用户痛点", "事实与数据", "方案反差", "可信背书", "行动入口"],
        "reference_examples": [],
        "required_variables": ["product", "pain_points", "advantages"],
        "output_schema": {"sections": 5, "cta_required": True},
        "risk_rules": ["报价和承诺必须有来源"],
    },
    {
        "code": "C02",
        "name": "实景流量类",
        "industry_aliases": {},
        "compatible_methods": ["M01", "M02", "M04"],
        "suitable_scenes": ["案例展示", "到店体验", "前后对比"],
        "business_pains": ["缺少真实感", "用户无法想象结果"],
        "structure_schema": ["旧况或背景", "关键数据", "过程与反差", "落地结果", "场景延伸"],
        "reference_examples": [],
        "required_variables": ["product", "result"],
        "output_schema": {"sections": 5, "scene_required": True},
        "risk_rules": ["实景和案例身份必须真实"],
    },
    {
        "code": "C03",
        "name": "干货人设类",
        "industry_aliases": {},
        "compatible_methods": ["M02", "M03"],
        "suitable_scenes": ["方法教学", "避坑", "专业解读"],
        "business_pains": ["用户缺少判断标准", "错误认知影响决策"],
        "structure_schema": ["悬念问题", "常见误区", "专业正解", "操作方法", "忠告总结"],
        "reference_examples": [],
        "required_variables": ["pain_points", "advantages"],
        "output_schema": {"sections": 5, "takeaway_required": True},
        "risk_rules": ["专业观点要区分事实与经验"],
    },
    {
        "code": "C04",
        "name": "人设沉淀类",
        "industry_aliases": {},
        "compatible_methods": ["M03", "M04"],
        "suitable_scenes": ["品牌故事", "主理人表达", "长期信任"],
        "business_pains": ["品牌同质化", "用户缺少信任"],
        "structure_schema": ["人设与立场", "行业痛点", "做事原则", "差异化优势", "长期承诺"],
        "reference_examples": [],
        "required_variables": ["brand_name", "pain_points", "advantages"],
        "output_schema": {"sections": 5, "persona_required": True},
        "risk_rules": ["人设和品牌经历不得虚构"],
    },
]

COMBINATION_RULES = [
    {
        "content_goal": "traffic",
        "methods": ["M01", "M02"],
        "title_formula_codes": ["T02", "T04", "T06"],
        "content_formula_code": "C02",
        "compatibility": "compatible",
        "priority": 100,
        "recommendation_reason": "用数字和反差提升点击，再用实景结构承接流量。",
    },
    {
        "content_goal": "educate",
        "methods": ["M02", "M03"],
        "title_formula_codes": ["T04", "T05", "T07"],
        "content_formula_code": "C03",
        "compatibility": "compatible",
        "priority": 100,
        "recommendation_reason": "先提出认知缺口，再完整交付方法价值。",
    },
    {
        "content_goal": "acquire",
        "methods": ["M01", "M04"],
        "title_formula_codes": ["T01", "T03", "T06"],
        "content_formula_code": "C01",
        "compatibility": "compatible",
        "priority": 100,
        "recommendation_reason": "精准圈定人群，用可验证事实降低咨询决策成本。",
    },
    {
        "content_goal": "brand",
        "methods": ["M03", "M04"],
        "title_formula_codes": ["T03", "T05"],
        "content_formula_code": "C04",
        "compatibility": "compatible",
        "priority": 100,
        "recommendation_reason": "用稳定价值主张和清晰人群定位沉淀长期信任。",
    },
]

WORKFLOW_DEFINITION = {
    "slug": "enterprise-content-v1",
    "version": 1,
    "nodes": [
        {"id": "compile_brief", "type": "compile_brief"},
        {"id": "plan_strategy", "type": "skill", "skill": "content-strategy-planner"},
        {"id": "collect_evidence", "type": "tool_group"},
        {"id": "confirm_facts", "type": "human_review", "interrupt_type": "confirm_facts", "optional": True},
        {"id": "generate_titles", "type": "skill", "skill": "content-title-generator"},
        {"id": "select_title", "type": "human_review", "interrupt_type": "select_title"},
        {"id": "generate_body", "type": "skill", "skill": "content-body-generator"},
        {"id": "validate", "type": "validator"},
        {"id": "review", "type": "skill", "skill": "content-reviewer"},
        {"id": "save", "type": "save_artifact"},
    ],
    "edges": [
        ["compile_brief", "plan_strategy"],
        ["plan_strategy", "collect_evidence"],
        ["collect_evidence", "confirm_facts"],
        ["confirm_facts", "generate_titles"],
        ["generate_titles", "select_title"],
        ["select_title", "generate_body"],
        ["generate_body", "validate"],
        ["validate", "review"],
        ["review", "save"],
    ],
}

BASE_QUICK_FIELDS = [
    {"key": "brand_name", "label": "品牌、门店或主理人", "type": "text", "required": True},
    {"key": "product", "label": "核心产品或服务", "type": "text", "required": True},
    {"key": "pain_points", "label": "用户痛点", "type": "tags", "required": True},
    {"key": "result", "label": "可验证数据或结果", "type": "textarea", "required": True},
    {"key": "advantages", "label": "差异化优势", "type": "tags", "required": True},
    {"key": "location", "label": "所在地域", "type": "text", "required": False},
]

BASE_PRO_FIELDS = [
    {"key": "audience", "label": "目标人群", "type": "tags", "required": True},
    {"key": "price_and_period", "label": "价格、数量或服务周期", "type": "textarea", "required": False},
    {"key": "persona", "label": "人设与语气", "type": "textarea", "required": False},
    {"key": "scene", "label": "典型使用场景", "type": "textarea", "required": False},
    {"key": "required_terms", "label": "必须包含", "type": "tags", "required": False},
    {"key": "forbidden_terms", "label": "明确避免", "type": "tags", "required": False},
]

INDUSTRIES = [
    {
        "slug": "food",
        "name": "餐饮与本地生活",
        "description": "门店、菜品、到店体验和本地获客",
        "icon": "Utensils",
        "default_goal": "traffic",
        "field": {"key": "signature_item", "label": "招牌产品或体验", "type": "text", "required": False},
    },
    {
        "slug": "education",
        "name": "教育培训",
        "description": "课程、学习方案、家长沟通和专业人设",
        "icon": "GraduationCap",
        "default_goal": "acquire",
        "field": {"key": "course_stage", "label": "课程阶段与班型", "type": "text", "required": False},
    },
    {
        "slug": "beauty",
        "name": "美业与个人护理",
        "description": "项目体验、门店服务、审美与护理知识",
        "icon": "Sparkles",
        "default_goal": "traffic",
        "field": {"key": "service_process", "label": "服务流程与注意事项", "type": "textarea", "required": False},
    },
    {
        "slug": "retail",
        "name": "零售与电商",
        "description": "产品卖点、使用体验、选购攻略和转化",
        "icon": "ShoppingBag",
        "default_goal": "acquire",
        "field": {"key": "product_specs", "label": "产品规格与关键参数", "type": "textarea", "required": False},
    },
    {
        "slug": "professional-services",
        "name": "专业服务",
        "description": "咨询、企业服务、顾问方案和专业观点",
        "icon": "BriefcaseBusiness",
        "default_goal": "educate",
        "field": {"key": "service_boundary", "label": "服务范围与交付边界", "type": "textarea", "required": False},
    },
    {
        "slug": "decoration",
        "name": "装修与家居",
        "description": "报价、工艺、实景案例和装修避坑",
        "icon": "House",
        "default_goal": "acquire",
        "field": {"key": "craft_and_materials", "label": "工艺、材料与报价依据", "type": "textarea", "required": False},
    },
]


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def brief_variable_map(brief: dict[str, Any]) -> dict[str, Any]:
    brand = brief.get("brand") if isinstance(brief.get("brand"), dict) else {}
    variables = dict(brief.get("business_variables") or {})
    form_values = brief.get("form_values") if isinstance(brief.get("form_values"), dict) else {}
    variables.update({key: value for key, value in form_values.items() if value not in (None, "", [])})
    variables.setdefault("brand_name", brand.get("name"))
    variables.setdefault("audience", brief.get("audience") or form_values.get("audience"))
    variables.setdefault("required_terms", brief.get("required_terms") or [])
    variables.setdefault("forbidden_terms", brief.get("forbidden_terms") or [])
    combined_text = " ".join(str(value) for value in variables.values() if value not in (None, "", []))
    variables.setdefault("number", re.findall(r"\d+(?:\.\d+)?(?:%|元|天|周|月|年|个|次|㎡)?", combined_text))
    variables.setdefault("emotion", "真实体验")
    return variables


async def ensure_content_seed_data(db: AsyncSession) -> None:
    # API 与 Worker 会并行初始化，事务级锁避免重复写入同一版平台种子。
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": "yuxi_content_seed_v1"},
    )
    existing = await db.execute(select(ContentRuleVersion.id).where(ContentRuleVersion.id == PLATFORM_RULE_VERSION_ID))
    if existing.scalar_one_or_none():
        return

    now = utc_now_naive()
    db.add(
        ContentRuleVersion(
            id=PLATFORM_RULE_VERSION_ID,
            tenant_id=None,
            version=1,
            status="published",
            changelog="MVP：四种核心手法、场景增强、七类标题公式、四类正文公式和默认组合矩阵",
            created_by="system",
            created_at=now,
            published_at=now,
        )
    )
    await db.flush()
    for order, item in enumerate(METHODS, start=1):
        db.add(
            CreationMethod(
                id=f"method-{item['code'].lower()}-v1",
                version_id=PLATFORM_RULE_VERSION_ID,
                sort_order=order,
                enabled=True,
                **item,
            )
        )
    for order, item in enumerate(TITLE_FORMULAS, start=1):
        db.add(
            TitleFormula(
                id=f"title-{item['code'].lower()}-v1",
                version_id=PLATFORM_RULE_VERSION_ID,
                sort_order=order,
                enabled=True,
                **item,
            )
        )
    for order, item in enumerate(BODY_FORMULAS, start=1):
        db.add(
            ContentFormula(
                id=f"body-{item['code'].lower()}-v1",
                version_id=PLATFORM_RULE_VERSION_ID,
                sort_order=order,
                enabled=True,
                **item,
            )
        )
    for order, item in enumerate(COMBINATION_RULES, start=1):
        db.add(
            ContentCombinationRule(
                id=f"combination-{item['content_goal']}-v1",
                version_id=PLATFORM_RULE_VERSION_ID,
                conditions={},
                **item,
            )
        )

    db.add(
        ContentWorkflowVersion(
            id=PLATFORM_WORKFLOW_VERSION_ID,
            slug="enterprise-content",
            tenant_id=None,
            version=1,
            status="published",
            definition_json=deepcopy(WORKFLOW_DEFINITION),
            input_schema={"type": "ContentBrief"},
            output_schema={"type": "ContentArtifact"},
            created_by="system",
            created_at=now,
            published_at=now,
        )
    )
    await db.flush()
    for industry in INDUSTRIES:
        quick = deepcopy(BASE_QUICK_FIELDS) + [deepcopy(industry["field"])]
        pro = deepcopy(quick) + deepcopy(BASE_PRO_FIELDS)
        db.add(
            IndustryTemplateVersion(
                id=f"industry-{industry['slug']}-v1",
                slug=industry["slug"],
                tenant_id=None,
                version=1,
                status="published",
                name=industry["name"],
                description=industry["description"],
                icon=industry["icon"],
                quick_form_schema=quick,
                pro_form_schema=pro,
                default_goal=industry["default_goal"],
                default_strategy={},
                default_knowledge_scope=[],
                default_workflow_version_id=PLATFORM_WORKFLOW_VERSION_ID,
                review_policy={
                    "require_sources_for_numbers": True,
                    "block_unsupported_effect_claims": True,
                    "human_title_selection": True,
                },
                created_by="system",
                created_at=now,
                published_at=now,
            )
        )
    await db.commit()
