from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BuiltinSkillSpec:
    slug: str
    source_dir: Path
    display_name: str = ""
    description: str = ""
    version: str = "1.0.0"
    tool_dependencies: tuple[str, ...] = ()
    mcp_dependencies: tuple[str, ...] = ()
    skill_dependencies: tuple[str, ...] = ()


_SKILLS_ROOT = Path(__file__).resolve().parent

BUILTIN_SKILLS: list[BuiltinSkillSpec] = [
    BuiltinSkillSpec(
        slug="single-blueprint-author",
        display_name="爆款原文仿写",
        version="3.0.0",
        source_dir=_SKILLS_ROOT / "single-blueprint-author",
        description="把真实资料代入爆款原文，亲切真诚地仿写",
    ),
    BuiltinSkillSpec(
        slug="single-blueprint-reviewer",
        display_name="单蓝图审核",
        version="1.7.0",
        source_dir=_SKILLS_ROOT / "single-blueprint-reviewer",
        description="核验蓝图采用与事实边界",
    ),
    BuiltinSkillSpec(
        slug="content-fact-extractor",
        display_name="创作事实抽取",
        version="1.0.0",
        source_dir=_SKILLS_ROOT / "content-fact-extractor",
        description="仅从用户原文摘录计划缺少的业务变量，不参与策略选择。",
    ),
    BuiltinSkillSpec(
        slug="viral-document-detector",
        display_name="爆款文档识别",
        version="1.0.1",
        source_dir=_SKILLS_ROOT / "viral-document-detector",
        description="自动识别上传文件中的完整参考文章和行业。",
    ),
    BuiltinSkillSpec(
        slug="content-joint-strategy-selector",
        display_name="内容联合策略选择",
        version="1.6.4",
        source_dir=_SKILLS_ROOT / "content-joint-strategy-selector",
        description="按行业规则一次选择公式、独立手法及已准备的文章级参考。",
    ),
    BuiltinSkillSpec(
        slug="prepared-viral-reference-selector",
        display_name="已准备爆款参考选择",
        version="1.3.3",
        source_dir=_SKILLS_ROOT / "prepared-viral-reference-selector",
        description="基于本次输入评分选择已准备参考卡，不阅读全文或重新提取蓝图。",
    ),
    BuiltinSkillSpec(
        slug="decoration-direction-formula-selector",
        display_name="装修方向公式选择",
        version="1.3.1",
        source_dir=_SKILLS_ROOT / "decoration-direction-formula-selector",
        description="仅在装修已选内容方向内匹配公式，独立评估手法，不生成公式分数。",
    ),
    BuiltinSkillSpec(
        slug="industry-strategy-scorer",
        display_name="行业策略评分",
        version="1.0.1",
        source_dir=_SKILLS_ROOT / "industry-strategy-scorer",
        description="依据本次输入证据和版本化评分标准选择行业公式及创作手法。",
    ),
    BuiltinSkillSpec(
        slug="viral-asset-preparer",
        display_name="爆款素材准备",
        version="2.0.0",
        source_dir=_SKILLS_ROOT / "viral-asset-preparer",
        description="在入库时核验单篇完整原文并生成有原文锚点的参考卡和结构蓝图。",
    ),
    BuiltinSkillSpec(
        slug="humanizer-zh",
        display_name="中文自然表达优化",
        source_dir=_SKILLS_ROOT / "humanizer-zh",
        description="降低中文内容的机械腔与模板化表达，同时保留原文事实、语气和格式。",
        version="1.3.0",
    ),
    BuiltinSkillSpec(
        slug="content-strategy-planner",
        display_name="内容策略规划",
        source_dir=_SKILLS_ROOT / "content-strategy-planner",
        description="根据 SOP1 输入和正式规则，一次选择方向、创作手法及标题正文公式。",
        version="4.1.0",
        tool_dependencies=("get_creation_rule_bundle",),
    ),
    BuiltinSkillSpec(
        slug="content-value-analyzer",
        display_name="内容价值分析",
        source_dir=_SKILLS_ROOT / "content-value-analyzer",
        description="从已冻结证据中识别内容价值、候选方向，并从候选集中确定唯一主叙事轴。",
        version="1.3.0",
    ),
    BuiltinSkillSpec(
        slug="content-evidence-researcher",
        display_name="内容证据调研",
        source_dir=_SKILLS_ROOT / "content-evidence-researcher",
        description="按锁定策略检索真实业务资料、平台合规替换表与爆款结构参考。",
        version="3.3.0",
        tool_dependencies=("get_business_facts", "query_kb", "open_kb_document", "find_kb_document"),
    ),
    BuiltinSkillSpec(
        slug="content-business-rule-researcher",
        display_name="业务规则调研",
        source_dir=_SKILLS_ROOT / "content-business-rule-researcher",
        description="只检索与锁定公式相关的品牌业务事实与平台业务规则。",
        version="1.1.1",
        tool_dependencies=("query_kb",),
    ),
    BuiltinSkillSpec(
        slug="content-price-researcher",
        display_name="价格证据调研",
        source_dir=_SKILLS_ROOT / "content-price-researcher",
        description="只检索价格库并保留适用范围、计价单位和价格口径。",
        version="1.3.0",
        tool_dependencies=("query_kb",),
    ),
    BuiltinSkillSpec(
        slug="content-compliance-researcher",
        display_name="问题词与合规规则调研",
        source_dir=_SKILLS_ROOT / "content-compliance-researcher",
        description="只从封禁词库读取完整问题词与常用表达映射。",
        version="1.1.0",
        tool_dependencies=("query_kb",),
    ),
    BuiltinSkillSpec(
        slug="viral-candidate-researcher",
        display_name="爆款候选检索",
        source_dir=_SKILLS_ROOT / "viral-candidate-researcher",
        description="按当前任务变量检索多篇爆款候选，不在检索阶段决定最终参考。",
        version="1.1.0",
        tool_dependencies=("query_kb",),
    ),
    BuiltinSkillSpec(
        slug="viral-reference-selector",
        display_name="爆款参考选择与结构解析",
        source_dir=_SKILLS_ROOT / "viral-reference-selector",
        description="按当前输入变量选择唯一可填充的爆款参考，并动态提取结构蓝图。",
        version="2.1.0",
    ),
    BuiltinSkillSpec(
        slug="strategy-product-researcher",
        display_name="策略资料调研",
        source_dir=_SKILLS_ROOT / "strategy-product-researcher",
        description="按锁定策略和公式槽位定向检索产品、价格、案例与爆款结构参考。",
        version="1.1.0",
        tool_dependencies=("get_business_facts", "query_kb", "open_kb_document", "find_kb_document"),
    ),
    BuiltinSkillSpec(
        slug="content-title-generator",
        display_name="标题生成",
        source_dir=_SKILLS_ROOT / "content-title-generator",
        description="按锁定标题公式生成候选，并从确定性校验通过的候选中选择最终标题。",
        version="2.1.0",
    ),
    BuiltinSkillSpec(
        slug="content-body-generator",
        display_name="正文生成",
        source_dir=_SKILLS_ROOT / "content-body-generator",
        description="使用人工锁定标题、正文公式和同源证据生成正文与话题。",
        version="2.5.0",
    ),
    BuiltinSkillSpec(
        slug="content-human-expression",
        display_name="人设与 Emoji 表达优化",
        source_dir=_SKILLS_ROOT / "content-human-expression",
        description="在不改变事实、公式和证据的前提下，落实人设语气、情绪与数据事项的 Emoji 功能覆盖和封禁词替换。",
        version="2.4.0",
    ),
    BuiltinSkillSpec(
        slug="viral-structure-rewriter",
        display_name="爆款结构仿写",
        source_dir=_SKILLS_ROOT / "viral-structure-rewriter",
        description=(
            "按已冻结的唯一爆款结构蓝图重构标题、大纲和正文，"
            "保留真实 Emoji 的位置和功能，并使用真实业务证据替换原文内容。"
        ),
        version="1.8.0",
    ),
    BuiltinSkillSpec(
        slug="viral-layout-formatter",
        display_name="爆款排版优化",
        source_dir=_SKILLS_ROOT / "viral-layout-formatter",
        description="按原创公式或已冻结爆款结构，把正文排成适合渠道扫读的信息块、短段与互动收尾。",
        version="1.7.0",
    ),
    BuiltinSkillSpec(
        slug="content-outline-builder",
        display_name="正文大纲生成",
        source_dir=_SKILLS_ROOT / "content-outline-builder",
        description="把锁定的正文公式、槽位与证据编译为可执行大纲。",
        version="2.0.0",
    ),
    BuiltinSkillSpec(
        slug="persona-style-polisher",
        display_name="人设语气优化",
        source_dir=_SKILLS_ROOT / "persona-style-polisher",
        description="在不改变事实与证据的前提下按 PersonaProfile 优化表达。",
        version="1.1.0",
    ),
    BuiltinSkillSpec(
        slug="content-reviewer",
        display_name="内容审核",
        source_dir=_SKILLS_ROOT / "content-reviewer",
        description="审核公式执行、事实一致性、人设语气和内容风险。",
        version="1.13.0",
        tool_dependencies=(
            "query_kb",
            "open_kb_document",
            "find_kb_document",
        ),
    ),
    BuiltinSkillSpec(
        slug="viral-content-author",
        display_name="爆款仿写创作",
        source_dir=_SKILLS_ROOT / "viral-content-author",
        description="按锁定创作类型和唯一爆款结构一次生成标题、大纲与正文，并定点回修。",
        version="1.0.8",
    ),
    BuiltinSkillSpec(
        slug="viral-content-reviewer",
        display_name="爆款仿写审核",
        source_dir=_SKILLS_ROOT / "viral-content-reviewer",
        description="一次审核爆款仿写的结构、报价口径、首尾人设和语义 Emoji。",
        version="1.0.5",
    ),
    BuiltinSkillSpec(
        slug="viral-author-core",
        display_name="爆款仿写编排",
        version="1.4.0",
        source_dir=_SKILLS_ROOT / "viral-author-core",
        description="编排单次爆款仿写生成，并执行事实、策略和输出合并检查。",
    ),
    BuiltinSkillSpec(
        slug="viral-title-author",
        version="1.1.0",
        display_name="爆款标题创作",
        source_dir=_SKILLS_ROOT / "viral-title-author",
        description="按锁定公式和真实证据生成单一卖点的装修小红书标题。",
    ),
    BuiltinSkillSpec(
        slug="viral-body-author",
        display_name="爆款正文仿写",
        version="2.0.0",
        source_dir=_SKILLS_ROOT / "viral-body-author",
        description="参考爆款完整原文，代入本篇资料，换一种亲切真诚、无营销感的表达自然仿写正文。",
    ),
    BuiltinSkillSpec(
        slug="viral-persona-author",
        display_name="人设表达",
        version="1.1.0",
        source_dir=_SKILLS_ROOT / "viral-persona-author",
        description="以真实证据表达工长身份、经验、师傅资源和服务方式。",
    ),
    BuiltinSkillSpec(
        slug="viral-natural-expression",
        display_name="自然口语与去机械化",
        source_dir=_SKILLS_ROOT / "viral-natural-expression",
        description="增加真人口语和少量合适的人气表达，并清除模板腔。",
    ),
    BuiltinSkillSpec(
        slug="viral-layout-expression",
        display_name="小红书格式化表达",
        source_dir=_SKILLS_ROOT / "viral-layout-expression",
        description="组织可直接发布的短段、清单、留白和语义 Emoji。",
    ),
    BuiltinSkillSpec(
        slug="viral-platform-expression",
        display_name="平台表达规范",
        version="1.1.0",
        source_dir=_SKILLS_ROOT / "viral-platform-expression",
        description="执行问题词替换、绝对化限制和克制 CTA。",
    ),
    BuiltinSkillSpec(
        slug="viral-price-author",
        display_name="报价表达",
        version="1.1.0",
        source_dir=_SKILLS_ROOT / "viral-price-author",
        description="按城市、工种、单位、价格类型和范围表达已验证报价。",
    ),
    BuiltinSkillSpec(
        slug="viral-topic-author",
        display_name="相关话题选择",
        source_dir=_SKILLS_ROOT / "viral-topic-author",
        description="从冻结候选池选择十个与正文相关的话题。",
    ),
    BuiltinSkillSpec(
        slug="viral-modular-reviewer",
        display_name="模块化爆款审核",
        version="1.5.0",
        source_dir=_SKILLS_ROOT / "viral-modular-reviewer",
        description="按照创作时冻结的模块规则快照审核完整爆款仿写。",
    ),
    BuiltinSkillSpec(
        slug="viral-cover-matcher",
        display_name="首图内容匹配",
        source_dir=_SKILLS_ROOT / "viral-cover-matcher",
        description="根据文章视觉意图选择与主卖点匹配的真实首图素材。",
        version="1.1.0",
    ),
    BuiltinSkillSpec(
        slug="content-visual-planner",
        display_name="内容视觉规划",
        source_dir=_SKILLS_ROOT / "content-visual-planner",
        description="按内容快照和渠道规范产出字段不重复的结构化视觉方案。",
        version="1.7.0",
    ),
    BuiltinSkillSpec(
        slug="content-cover-generator",
        display_name="内容封面生成",
        source_dir=_SKILLS_ROOT / "content-cover-generator",
        description="校验锁定视觉方案并提交唯一封面任务。",
        version="1.2.0",
        tool_dependencies=("create_content_cover_job",),
    ),
    BuiltinSkillSpec(
        slug="content-visual-reviewer",
        display_name="内容视觉审核",
        source_dir=_SKILLS_ROOT / "content-visual-reviewer",
        description="对封面资产进行安全区、文案、来源和风险审核。",
        version="1.1.0",
    ),
    BuiltinSkillSpec(
        slug="image-gen",
        source_dir=_SKILLS_ROOT / "image-gen",
        description="在 Agent 沙盒中生成图片并保存到 outputs，默认支持 Qwen-Image，也可接入其它图片生成接口。",
        version="2026.06.02",
        tool_dependencies=("present_artifacts",),
    ),
    BuiltinSkillSpec(
        slug="deep-research",
        source_dir=_SKILLS_ROOT / "deep-research",
        description="深度研究编排方法论：澄清范围、拆解规划、并行调度子智能体调研、对抗式核验、综合成带引用的结构化报告。",
        version="2026.06.05",
        tool_dependencies=("tavily_search",),
    ),
    BuiltinSkillSpec(
        slug="mysql-reporter",
        source_dir=_SKILLS_ROOT / "mysql-reporter",
        description="生成 MySQL 查询报表并生成可视化图表。",
        version="2026.06.05",
        mcp_dependencies=("mcp-server-chart",),
    ),
]

_PLATFORM_SKILL_SLUGS = (
    "algorithmic-art",
    "brand-guidelines",
    "canvas-design",
    "claude-api",
    "doc-coauthoring",
    "docx",
    "frontend-design",
    "internal-comms",
    "mcp-builder",
    "pdf",
    "pptx",
    "skill-creator",
    "slack-gif-creator",
    "template-skill",
    "theme-factory",
    "web-artifacts-builder",
    "webapp-testing",
    "xlsx",
)

BUILTIN_SKILLS.extend(
    BuiltinSkillSpec(
        slug=slug,
        source_dir=_SKILLS_ROOT / slug,
        version="2026.09.01",
    )
    for slug in _PLATFORM_SKILL_SLUGS
)
