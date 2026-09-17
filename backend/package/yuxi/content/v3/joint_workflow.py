"""V3.8 是独立版本，不改写已创建任务的 V3.7 定义。"""

from copy import deepcopy

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
BLUEPRINT_FIRST_WORKFLOW_IDS = frozenset({PLATFORM_WORKFLOW_BLUEPRINT_FIRST_ID, PLATFORM_WORKFLOW_PRICE_RECOVERY_ID})
WORKFLOW_PRICE_RECOVERY = deepcopy(WORKFLOW_BLUEPRINT_FIRST)
WORKFLOW_PRICE_RECOVERY["price_recovery"] = True
selection = next(n for n in WORKFLOW_PRICE_RECOVERY["nodes"] if n["id"] == "select_creation_strategy")
selection["output_contract"] = "JointStrategyDecisionV2"
reselection = deepcopy(selection)
reselection.update(id="reselect_creation_strategy", input_contract="ReevaluateJointStrategyInputV1")
reselection["state_inputs"].append("strategy_price_evidence_collection")
recovery_nodes = [
    _agent(
        "research_strategy_prices", "content-price-research-agent", "content-price-researcher",
        "ResearchStrategyPricesInputV1", "StrategyPriceEvidenceResultV1",
        state_inputs=("content_brief", "evidence_bundle", "joint_strategy_decision", "runtime_config_snapshot"),
        knowledge_policy="agent_scope", max_tool_calls=3, max_retrieval_rounds=1,
        max_knowledge_bases=1, max_chunks_per_knowledge_base=8, max_chars_per_knowledge_chunk=3200,
        token_budget=7000, timeout_seconds=125,
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
