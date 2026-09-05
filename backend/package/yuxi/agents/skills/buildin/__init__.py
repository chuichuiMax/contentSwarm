from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BuiltinSkillSpec:
    slug: str
    source_dir: Path
    description: str = ""
    version: str = "1.0.0"
    tool_dependencies: tuple[str, ...] = ()
    mcp_dependencies: tuple[str, ...] = ()
    skill_dependencies: tuple[str, ...] = ()


_SKILLS_ROOT = Path(__file__).resolve().parent

BUILTIN_SKILLS: list[BuiltinSkillSpec] = [
    BuiltinSkillSpec(
        slug="humanizer-zh",
        source_dir=_SKILLS_ROOT / "humanizer-zh",
        description="降低中文内容的机械腔与模板化表达，同时保留原文事实、语气和格式。",
        version="1.0.0",
    ),
    BuiltinSkillSpec(
        slug="content-strategy-planner",
        source_dir=_SKILLS_ROOT / "content-strategy-planner",
        description="根据 SOP1 输入和正式规则，一次选择方向、创作手法及标题正文公式。",
        version="4.0.1",
        tool_dependencies=("get_creation_rule_bundle",),
    ),
    BuiltinSkillSpec(
        slug="content-value-analyzer",
        source_dir=_SKILLS_ROOT / "content-value-analyzer",
        description="从已冻结证据中识别内容价值、候选方向，并从候选集中确定唯一主叙事轴。",
        version="1.3.0",
    ),
    BuiltinSkillSpec(
        slug="content-evidence-researcher",
        source_dir=_SKILLS_ROOT / "content-evidence-researcher",
        description="按锁定策略检索真实业务资料、平台合规替换表与爆款结构参考。",
        version="3.3.0",
        tool_dependencies=("get_business_facts", "query_kb", "open_kb_document", "find_kb_document"),
    ),
    BuiltinSkillSpec(
        slug="content-business-rule-researcher",
        source_dir=_SKILLS_ROOT / "content-business-rule-researcher",
        description="只检索与锁定公式相关的品牌业务事实与平台业务规则。",
        version="1.1.0",
        tool_dependencies=("query_kb",),
    ),
    BuiltinSkillSpec(
        slug="content-price-researcher",
        source_dir=_SKILLS_ROOT / "content-price-researcher",
        description="只检索价格库并保留适用范围、计价单位和价格口径。",
        version="1.1.0",
        tool_dependencies=("query_kb",),
    ),
    BuiltinSkillSpec(
        slug="content-compliance-researcher",
        source_dir=_SKILLS_ROOT / "content-compliance-researcher",
        description="只从封禁词库读取完整问题词与常用表达映射。",
        version="1.1.0",
        tool_dependencies=("query_kb",),
    ),
    BuiltinSkillSpec(
        slug="viral-candidate-researcher",
        source_dir=_SKILLS_ROOT / "viral-candidate-researcher",
        description="按当前任务变量检索多篇爆款候选，不在检索阶段决定最终参考。",
        version="1.1.0",
        tool_dependencies=("query_kb",),
    ),
    BuiltinSkillSpec(
        slug="viral-reference-selector",
        source_dir=_SKILLS_ROOT / "viral-reference-selector",
        description="按当前输入变量选择唯一可填充的爆款参考，并动态提取结构蓝图。",
        version="2.1.0",
    ),
    BuiltinSkillSpec(
        slug="strategy-product-researcher",
        source_dir=_SKILLS_ROOT / "strategy-product-researcher",
        description="按锁定策略和公式槽位定向检索产品、价格、案例与爆款结构参考。",
        version="1.1.0",
        tool_dependencies=("get_business_facts", "query_kb", "open_kb_document", "find_kb_document"),
    ),
    BuiltinSkillSpec(
        slug="content-title-generator",
        source_dir=_SKILLS_ROOT / "content-title-generator",
        description="按锁定标题公式生成候选，并从确定性校验通过的候选中选择最终标题。",
        version="2.1.0",
    ),
    BuiltinSkillSpec(
        slug="content-body-generator",
        source_dir=_SKILLS_ROOT / "content-body-generator",
        description="使用人工锁定标题、正文公式和同源证据生成正文与话题。",
        version="2.2.0",
    ),
    BuiltinSkillSpec(
        slug="content-human-expression",
        source_dir=_SKILLS_ROOT / "content-human-expression",
        description="在不改变事实、公式和证据的前提下，优化自然表达并按知识库映射替换封禁词。",
        version="1.8.0",
    ),
    BuiltinSkillSpec(
        slug="viral-structure-rewriter",
        source_dir=_SKILLS_ROOT / "viral-structure-rewriter",
        description=(
            "按已冻结的唯一爆款结构蓝图重构标题、大纲和正文，"
            "保留真实 Emoji 的位置和功能，并使用真实业务证据替换原文内容。"
        ),
        version="1.5.0",
    ),
    BuiltinSkillSpec(
        slug="viral-layout-formatter",
        source_dir=_SKILLS_ROOT / "viral-layout-formatter",
        description="按已冻结爆款的真实信息块、列表类型和段落节奏排版正文。",
        version="1.4.0",
    ),
    BuiltinSkillSpec(
        slug="content-outline-builder",
        source_dir=_SKILLS_ROOT / "content-outline-builder",
        description="把锁定的正文公式、槽位与证据编译为可执行大纲。",
        version="2.0.0",
    ),
    BuiltinSkillSpec(
        slug="persona-style-polisher",
        source_dir=_SKILLS_ROOT / "persona-style-polisher",
        description="在不改变事实与证据的前提下按 PersonaProfile 优化表达。",
        version="1.1.0",
    ),
    BuiltinSkillSpec(
        slug="content-reviewer",
        source_dir=_SKILLS_ROOT / "content-reviewer",
        description="审核公式执行、事实一致性、人设语气和内容风险。",
        version="1.8.0",
        tool_dependencies=(
            "query_kb",
            "open_kb_document",
            "find_kb_document",
        ),
    ),
    BuiltinSkillSpec(
        slug="content-visual-planner",
        source_dir=_SKILLS_ROOT / "content-visual-planner",
        description="按内容快照和渠道规范产出结构化视觉方案。",
        version="1.4.0",
    ),
    BuiltinSkillSpec(
        slug="content-cover-generator",
        source_dir=_SKILLS_ROOT / "content-cover-generator",
        description="校验锁定视觉方案并提交唯一封面任务。",
        version="1.2.0",
        tool_dependencies=("create_content_cover_job",),
    ),
    BuiltinSkillSpec(
        slug="content-visual-reviewer",
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
