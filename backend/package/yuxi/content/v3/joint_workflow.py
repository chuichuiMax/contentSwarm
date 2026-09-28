"""V3.8 是独立版本，不改写已创建任务的 V3.7 定义。"""

from copy import deepcopy

from yuxi.content.v3.modular_rules import (
    COVER_SKILL,
    DETERMINISTIC_PLAN_WORKFLOW_ID,
    EXPRESSION_GUIDANCE_WORKFLOW_ID,
    GENERATION_SKILLS,
    MODULAR_WORKFLOW_ID,
    REVIEW_SKILL,
    STANDARDIZED_FACTORY_WORKFLOW_ID,
)
from yuxi.content.v3.workflow import WORKFLOW_V3, _agent, _fixed, _human

PLATFORM_WORKFLOW_JOINT_ID = "content-workflow-agent-skill-v1"
WORKFLOW_JOINT = deepcopy(WORKFLOW_V3)
WORKFLOW_JOINT["selection_policy"] = "agent_skill_v1"
removed = {"collect_viral_candidates", "select_viral_reference"}
nodes = []
for node in WORKFLOW_JOINT["nodes"]:
    if node["id"] in removed:
        continue
    if node["id"] == "select_creation_strategy":
        nodes.append(_fixed("prepare_strategy_candidates"))
        node = _agent(
            "select_creation_strategy",
            "content-joint-strategy-agent",
            ("content-joint-strategy-selector", "prepared-viral-reference-selector"),
            "JointStrategyInputV1",
            "JointStrategyDecisionV1",
            state_inputs=(
                "content_brief",
                "evidence_bundle",
                "strategy_candidates",
                "reference_candidates",
                "runtime_config_snapshot",
            ),
            max_tool_calls=1,
            token_budget=14000,
            timeout_seconds=75,
        )
    nodes.append(node)
WORKFLOW_JOINT["nodes"] = nodes
edges = []
for source, target in WORKFLOW_JOINT["edges"]:
    if source in removed:
        continue
    if target == "collect_viral_candidates":
        continue
    if target == "select_viral_reference":
        target = "merge_research_evidence"
    if target == "select_creation_strategy":
        target = "prepare_strategy_candidates"
    edges.append([source, target])
edges.append(["prepare_strategy_candidates", "select_creation_strategy"])
WORKFLOW_JOINT["edges"] = edges


PLATFORM_WORKFLOW_BLUEPRINT_FIRST_ID = "content-workflow-blueprint-first-v1"
WORKFLOW_BLUEPRINT_FIRST = deepcopy(WORKFLOW_JOINT)
WORKFLOW_BLUEPRINT_FIRST["selection_policy"] = "blueprint_first_v1"


# 独立发布，历史任务仍使用原定义；最多检索和复评各一次。
PLATFORM_WORKFLOW_PRICE_RECOVERY_ID = "content-workflow-blueprint-first-v2"
PLATFORM_WORKFLOW_VIRAL_AUTHOR_ID = "content-workflow-blueprint-first-v3"
PLATFORM_WORKFLOW_MODULAR_AUTHOR_ID = MODULAR_WORKFLOW_ID
PLATFORM_WORKFLOW_EXPRESSION_GUIDANCE_ID = EXPRESSION_GUIDANCE_WORKFLOW_ID
BLUEPRINT_FIRST_WORKFLOW_IDS = frozenset(
    {
        PLATFORM_WORKFLOW_BLUEPRINT_FIRST_ID,
        PLATFORM_WORKFLOW_PRICE_RECOVERY_ID,
        PLATFORM_WORKFLOW_VIRAL_AUTHOR_ID,
        PLATFORM_WORKFLOW_MODULAR_AUTHOR_ID,
        PLATFORM_WORKFLOW_EXPRESSION_GUIDANCE_ID,
    }
)
WORKFLOW_PRICE_RECOVERY = deepcopy(WORKFLOW_BLUEPRINT_FIRST)
WORKFLOW_PRICE_RECOVERY["price_recovery"] = True
selection = next(n for n in WORKFLOW_PRICE_RECOVERY["nodes"] if n["id"] == "select_creation_strategy")
selection["output_contract"] = "JointStrategyDecisionV2"
reselection = deepcopy(selection)
reselection.update(id="reselect_creation_strategy", input_contract="ReevaluateJointStrategyInputV1")
reselection["state_inputs"].append("strategy_price_evidence_collection")
recovery_nodes = [
    _agent(
        "research_strategy_prices",
        "content-price-research-agent",
        "content-price-researcher",
        "ResearchStrategyPricesInputV1",
        "StrategyPriceEvidenceResultV1",
        state_inputs=("content_brief", "evidence_bundle", "joint_strategy_decision", "runtime_config_snapshot"),
        knowledge_policy="agent_scope",
        max_tool_calls=3,
        max_retrieval_rounds=1,
        max_knowledge_bases=1,
        max_chunks_per_knowledge_base=8,
        max_chars_per_knowledge_chunk=3200,
        token_budget=7000,
        timeout_seconds=125,
    ),
    _human("confirm_strategy_prices", "high_risk_facts"),
    _fixed("merge_strategy_prices"),
    reselection,
]
position = WORKFLOW_PRICE_RECOVERY["nodes"].index(selection) + 1
WORKFLOW_PRICE_RECOVERY["nodes"][position:position] = recovery_nodes
WORKFLOW_PRICE_RECOVERY["edges"].remove(["select_creation_strategy", "lock_creation_strategy"])
chain = ["select_creation_strategy", *(n["id"] for n in recovery_nodes), "lock_creation_strategy"]
WORKFLOW_PRICE_RECOVERY["edges"].extend([a, b] for a, b in zip(chain, chain[1:]))


# 新任务只使用爆款仿写 Skill；旧工作流定义保持不可变，供既有仿写任务查询和继续运行。
WORKFLOW_VIRAL_AUTHOR = deepcopy(WORKFLOW_PRICE_RECOVERY)
generation = next(node for node in WORKFLOW_VIRAL_AUTHOR["nodes"] if node["id"] == "generate_content")
generation["agent_slug"] = "content-viral-generation-agent"
generation["required_skills"] = ["viral-content-author"]
review = next(node for node in WORKFLOW_VIRAL_AUTHOR["nodes"] if node["id"] == "semantic_review")
review["agent_slug"] = "content-viral-review-agent"
review["required_skills"] = ["viral-content-reviewer"]


# 模块化正式版本只改变创作、审核和首图匹配节点；历史版本定义保持不变。
WORKFLOW_MODULAR_AUTHOR = deepcopy(WORKFLOW_VIRAL_AUTHOR)
WORKFLOW_MODULAR_AUTHOR["selection_policy"] = "modular_viral_author_v1"
modular_generation = next(node for node in WORKFLOW_MODULAR_AUTHOR["nodes"] if node["id"] == "generate_content")
modular_generation["required_skills"] = list(GENERATION_SKILLS)
modular_review = next(node for node in WORKFLOW_MODULAR_AUTHOR["nodes"] if node["id"] == "semantic_review")
modular_review["required_skills"] = [REVIEW_SKILL]
if "runtime_config_snapshot" not in modular_review["state_inputs"]:
    modular_review["state_inputs"].append("runtime_config_snapshot")
modular_visual = next(node for node in WORKFLOW_MODULAR_AUTHOR["nodes"] if node["id"] == "plan_visuals")
modular_visual["required_skills"] = ["content-visual-planner", COVER_SKILL]


# V5 在 V4 模块化创作上增加三类可追溯表达资料；检索结果冻结后再进入同一次正文模型调用。
WORKFLOW_EXPRESSION_GUIDANCE = deepcopy(WORKFLOW_MODULAR_AUTHOR)
WORKFLOW_EXPRESSION_GUIDANCE["expression_knowledge_policy"] = {
    "schema_version": 1,
    "required": True,
    "sources": [
        {
            "name": "我的优势",
            "role": "brand_advantages",
            "usage": "body_evidence",
            "query_terms": ["工长身份", "服务优势", "师傅经验", "做事原则"],
            "max_chunks": 2,
            "max_chars_per_chunk": 1200,
        },
        {
            "name": "表达语气库",
            "role": "tone_reference",
            "usage": "style_reference",
            "query_terms": ["装修工长", "自然口语", "人气表达", "去机械化"],
            "max_chunks": 2,
            "max_chars_per_chunk": 1200,
        },
        {
            "name": "具象表达",
            "role": "concrete_expression",
            "usage": "style_reference",
            "query_terms": ["具象动作", "现场表达", "短段清单", "Emoji排版"],
            "max_chunks": 2,
            "max_chars_per_chunk": 1200,
        },
    ],
}
guided_generation = next(node for node in WORKFLOW_EXPRESSION_GUIDANCE["nodes"] if node["id"] == "generate_content")
guided_generation["state_inputs"].append("expression_guidance")
guided_review = next(node for node in WORKFLOW_EXPRESSION_GUIDANCE["nodes"] if node["id"] == "semantic_review")
guided_review["state_inputs"].append("expression_guidance")


# V6 在任何内容生成模型调用前，用固定规则冻结类型、公式、手法、参考和槽位映射。
PLATFORM_WORKFLOW_DETERMINISTIC_PLAN_ID = DETERMINISTIC_PLAN_WORKFLOW_ID
WORKFLOW_DETERMINISTIC_PLAN = deepcopy(WORKFLOW_EXPRESSION_GUIDANCE)
WORKFLOW_DETERMINISTIC_PLAN["selection_policy"] = "deterministic_creation_plan_v1"
removed_plan_nodes = {"select_creation_strategy", "reselect_creation_strategy", "lock_creation_strategy"}
plan_nodes = []
for node in WORKFLOW_DETERMINISTIC_PLAN["nodes"]:
    if node["id"] in removed_plan_nodes:
        continue
    if node["id"] == "prepare_strategy_candidates":
        node = _fixed("prepare_creation_plan_inputs")
    elif node["id"] == "research_strategy_prices":
        node = deepcopy(node)
        node["input_contract"] = "ResearchCreationPlanPricesInputV1"
        node["state_inputs"] = [
            "content_brief",
            "evidence_bundle",
            "creation_plan_gap_analysis",
            "runtime_config_snapshot",
        ]
    plan_nodes.append(node)
WORKFLOW_DETERMINISTIC_PLAN["nodes"] = plan_nodes
prepare_index = next(
    index
    for index, node in enumerate(WORKFLOW_DETERMINISTIC_PLAN["nodes"])
    if node["id"] == "prepare_creation_plan_inputs"
)
WORKFLOW_DETERMINISTIC_PLAN["nodes"][prepare_index + 1 : prepare_index + 1] = [
    _agent(
        "extract_creation_facts",
        "content-fact-extraction-agent",
        "content-fact-extractor",
        "ExtractCreationFactsInputV1",
        "ExtractedCreationFactsResultV1",
        state_inputs=("content_brief", "creation_plan_gap_analysis", "runtime_config_snapshot"),
        max_tool_calls=1,
        token_budget=5000,
        timeout_seconds=60,
    ),
    _fixed("merge_extracted_creation_facts"),
]
merge_index = next(
    index for index, node in enumerate(WORKFLOW_DETERMINISTIC_PLAN["nodes"]) if node["id"] == "merge_strategy_prices"
)
WORKFLOW_DETERMINISTIC_PLAN["nodes"].insert(merge_index + 1, _fixed("build_creation_plan"))
plan_segment = {
    "prepare_strategy_candidates",
    "select_creation_strategy",
    "research_strategy_prices",
    "confirm_strategy_prices",
    "merge_strategy_prices",
    "reselect_creation_strategy",
    "lock_creation_strategy",
}
WORKFLOW_DETERMINISTIC_PLAN["edges"] = [
    edge for edge in WORKFLOW_DETERMINISTIC_PLAN["edges"] if not set(edge) & plan_segment
]
WORKFLOW_DETERMINISTIC_PLAN["edges"].extend(
    [source, target]
    for source, target in zip(
        [
            "normalize_evidence",
            "prepare_creation_plan_inputs",
            "extract_creation_facts",
            "merge_extracted_creation_facts",
            "research_strategy_prices",
            "confirm_strategy_prices",
            "merge_strategy_prices",
            "build_creation_plan",
        ],
        [
            "prepare_creation_plan_inputs",
            "extract_creation_facts",
            "merge_extracted_creation_facts",
            "research_strategy_prices",
            "confirm_strategy_prices",
            "merge_strategy_prices",
            "build_creation_plan",
            "load_formula_lexicons",
        ],
    )
)


# V8 延续标准化生产包，并让审核结果复用冻结稿件证据映射，不再由模型抄写 Evidence ID。
PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID = STANDARDIZED_FACTORY_WORKFLOW_ID
WORKFLOW_STANDARDIZED_FACTORY = deepcopy(WORKFLOW_DETERMINISTIC_PLAN)
WORKFLOW_STANDARDIZED_FACTORY["selection_policy"] = "standardized_factory_v1"
freeze_index = next(
    index for index, node in enumerate(WORKFLOW_STANDARDIZED_FACTORY["nodes"]) if node["id"] == "freeze_evidence_bundle"
)
WORKFLOW_STANDARDIZED_FACTORY["nodes"][freeze_index + 1 : freeze_index + 1] = [
    _fixed("validate_material_gate"),
    _fixed("freeze_production_pack"),
]
generation = next(node for node in WORKFLOW_STANDARDIZED_FACTORY["nodes"] if node["id"] == "generate_content")
generation["state_inputs"].append("production_pack")
standardized_review = next(node for node in WORKFLOW_STANDARDIZED_FACTORY["nodes"] if node["id"] == "semantic_review")
standardized_review["output_contract"] = "StandardizedContentReviewResultV1"
approval_index = next(
    index for index, node in enumerate(WORKFLOW_STANDARDIZED_FACTORY["nodes"]) if node["id"] == "human_content_approval"
)
WORKFLOW_STANDARDIZED_FACTORY["nodes"][approval_index:approval_index] = [
    _fixed("compose_locked_quote_block"),
    _fixed("validate_composed_content"),
]
WORKFLOW_STANDARDIZED_FACTORY["edges"].remove(["freeze_evidence_bundle", "generate_content"])
WORKFLOW_STANDARDIZED_FACTORY["edges"].extend(
    [
        ["freeze_evidence_bundle", "validate_material_gate"],
        ["validate_material_gate", "freeze_production_pack"],
        ["freeze_production_pack", "generate_content"],
        ["compose_locked_quote_block", "validate_composed_content"],
        ["validate_composed_content", "semantic_review"],
    ]
)


WORKFLOW_SINGLE_BLUEPRINT = deepcopy(WORKFLOW_STANDARDIZED_FACTORY)
WORKFLOW_SINGLE_BLUEPRINT.pop("expression_knowledge_policy", None)
for _route in WORKFLOW_SINGLE_BLUEPRINT["revision_routes"]:
    if _route["to"] == "generate_content":
        _route["max_attempts"] = 1
for _node in WORKFLOW_SINGLE_BLUEPRINT["nodes"]:
    if _node["id"] in {"generate_content", "semantic_review"}:
        _node["state_inputs"].remove("expression_guidance")
    if _node["id"] == "generate_content":
        _node.update(
            agent_slug="content-single-blueprint-author",
            required_skills=["single-blueprint-author"],
            output_contract="SingleBlueprintResultV1",
            prompt="根据本篇唯一蓝图和事实组织内容，提交有序正文块。",
        )
    elif _node["id"] == "semantic_review":
        _node.update(
            agent_slug="content-single-blueprint-reviewer",
            required_skills=["single-blueprint-reviewer"],
            prompt="按同一蓝图与事实审核最终组装稿，定位具体问题。",
        )

# 新候选停用正文模型审核；已发布版本的定义仍由数据库冻结保存。
WORKFLOW_SINGLE_BLUEPRINT["semantic_review_enabled"] = False
WORKFLOW_SINGLE_BLUEPRINT["nodes"] = [
    node for node in WORKFLOW_SINGLE_BLUEPRINT["nodes"] if node["id"] != "semantic_review"
]
WORKFLOW_SINGLE_BLUEPRINT["edges"] = [
    edge for edge in WORKFLOW_SINGLE_BLUEPRINT["edges"] if "semantic_review" not in edge
]
WORKFLOW_SINGLE_BLUEPRINT["edges"].append(["validate_composed_content", "human_content_approval"])
