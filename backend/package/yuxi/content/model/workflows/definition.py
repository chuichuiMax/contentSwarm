from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

V3_NODE_TYPES = {"deterministic", "agent", "human_review", "external_wait", "revision_router"}
V3_AGENT_NODE_TYPES = {"agent"}
V3_HUMAN_GATE_IDS = {
    "confirm_high_risk_facts",
    "human_content_approval",
    "select_cover",
}
KNOWLEDGE_POLICIES = {"none", "agent_scope", "frozen_evidence_only"}
DEFAULT_CONTRACTS = {
    "ContentAgentNodeInputV1",
    "ContentAgentNodeInputV2",
    "AnalyzeContentValueInputV1",
    "AnalyzeAndSelectDirectionInputV1",
    "SelectCreationStrategyInputV1",
    "SelectContentDirectionInputV1",
    "ExplainStrategyInputV1",
    "CollectMissingEvidenceInputV1",
    "CollectMissingEvidenceInputV2",
    "CollectSelectedStrategyEvidenceInputV1",
    "CollectBusinessRuleEvidenceInputV1",
    "CollectPriceEvidenceInputV1",
    "CollectComplianceEvidenceInputV1",
    "CollectViralCandidatesInputV1",
    "SelectViralReferenceInputV1",
    "RankFormulaCandidatesInputV1",
    "RankFormulaCandidatesInputV2",
    "CollectStrategyProductEvidenceInputV1",
    "GenerateTitleCandidatesInputV1",
    "SelectTitleInputV1",
    "BuildOutlineInputV1",
    "GenerateBodyInputV1",
    "PersonaStylePolishInputV1",
    "GenerateContentInputV1",
    "SemanticReviewInputV1",
    "PlanVisualsInputV1",
    "SubmitCoverJobInputV1",
    "VisualReviewInputV1",
    "ContentValueResultV1",
    "ContentDirectionDecisionResultV1",
    "CreationStrategySelectionResultV1",
    "DirectionSelectionResultV1",
    "StrategyExplanationResultV1",
    "EvidenceCollectionResultV1",
    "BusinessRuleEvidenceCollectionResultV1",
    "PriceEvidenceCollectionResultV1",
    "ComplianceEvidenceCollectionResultV1",
    "ViralCandidateCollectionResultV1",
    "ViralReferenceSelectionResultV1",
    "ProductEvidenceCollectionResultV1",
    "FormulaRankingResultV1",
    "TitleCandidatesResultV1",
    "TitleSelectionResultV1",
    "OutlineResultV1",
    "ContentDraftResultV1",
    "GeneratedContentResultV1",
    "PersonaPolishResultV1",
    "ContentReviewResultV1",
    "VisualPlanResultV1",
    "CoverJobSubmissionResultV1",
    "VisualReviewResultV1",
}


@dataclass(frozen=True, slots=True)
class WorkflowCatalog:
    agents: frozenset[str] = frozenset()
    skills: frozenset[str] = frozenset()
    contracts: frozenset[str] = frozenset(DEFAULT_CONTRACTS)
    backends: frozenset[str] = frozenset({"managed"})


def workflow_definition_hash(definition: dict[str, Any]) -> str:
    canonical = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class WorkflowDefinitionPolicy:
    @classmethod
    def validate(cls, definition: dict[str, Any], *, catalog: WorkflowCatalog | None = None) -> None:
        nodes = definition.get("nodes") if isinstance(definition, dict) else None
        edges = definition.get("edges") if isinstance(definition, dict) else None
        if not isinstance(nodes, list) or not nodes or not isinstance(edges, list):
            raise ValueError("工作流定义必须包含 nodes 和 edges")
        ids = [node.get("id") for node in nodes if isinstance(node, dict)]
        if len(ids) != len(nodes) or len(ids) != len(set(ids)) or any(not node_id for node_id in ids):
            raise ValueError("工作流节点 ID 不能为空或重复")
        node_by_id = {node["id"]: node for node in nodes}
        schema_version = int(definition.get("schema_version") or 0)
        if schema_version != 3:
            raise ValueError("内容生产只支持 V3 工作流")
        for node in nodes:
            if node.get("type") not in V3_NODE_TYPES:
                raise ValueError(f"不支持的工作流节点类型: {node.get('type')}")

        cls._validate_dag(ids, edges)
        cls._validate_v3_nodes(node_by_id, catalog)
        cls._validate_v3_control_flow(edges)
        cls._validate_revision_routes(definition.get("revision_routes") or [], node_by_id)
        cls._validate_runtime_limits(definition)

    @staticmethod
    def _validate_dag(ids: list[str], edges: list[Any]) -> None:
        indegree = {node_id: 0 for node_id in ids}
        outgoing = {node_id: [] for node_id in ids}
        for edge in edges:
            if not isinstance(edge, list) or len(edge) != 2 or edge[0] not in ids or edge[1] not in ids:
                raise ValueError(f"无效的工作流连线: {edge}")
            outgoing[edge[0]].append(edge[1])
            indegree[edge[1]] += 1
        ready = [node_id for node_id, count in indegree.items() if count == 0]
        visited = 0
        while ready:
            source = ready.pop()
            visited += 1
            for target in outgoing[source]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        if visited != len(ids):
            raise ValueError("工作流正常连线不能包含循环依赖")

    @classmethod
    def _validate_v3_nodes(cls, node_by_id: dict[str, dict[str, Any]], catalog: WorkflowCatalog | None) -> None:
        if len(node_by_id) != 26:
            raise ValueError("V3.7 内容与封面工作流必须声明 26 个节点")
        missing_gates = sorted(V3_HUMAN_GATE_IDS - set(node_by_id))
        if missing_gates:
            raise ValueError(f"V3 工作流缺少必选人工关口: {', '.join(missing_gates)}")
        for node_id in V3_HUMAN_GATE_IDS:
            if node_by_id[node_id].get("type") != "human_review":
                raise ValueError(f"必选人工关口 {node_id} 必须使用 human_review 类型")
        if node_by_id.get("lock_creation_strategy", {}).get("type") != "deterministic":
            raise ValueError("lock_creation_strategy 必须用固定规则校验并锁定 Agent 选择")
        if node_by_id.get("load_formula_lexicons", {}).get("type") != "deterministic":
            raise ValueError("load_formula_lexicons 必须用固定规则加载锁定公式对应词库")
        input_contracts = [node.get("input_contract") for node in node_by_id.values() if node.get("type") == "agent"]
        if len(input_contracts) != len(set(input_contracts)):
            raise ValueError("每个 V3 Agent 节点必须声明独立输入契约，禁止共用通用输入")
        for node in node_by_id.values():
            if "skill_slug" in node:
                raise ValueError("V3 Agent 节点禁止使用单个 skill_slug，必须使用 required_skills")
            node_type = node.get("type")
            if node_type == "agent":
                cls._validate_agent_node(node, catalog)
            elif node_type == "deterministic" and not node.get("handler"):
                raise ValueError(f"固定节点 {node['id']} 缺少 handler")
            elif node_type == "external_wait":
                if not node.get("external_job_type") or not node.get("timeout_seconds"):
                    raise ValueError(f"外部等待节点 {node['id']} 缺少任务类型或超时")
            elif node_type == "revision_router" and node["id"] != "revise_if_needed":
                raise ValueError("只有 revise_if_needed 可以使用 revision_router 类型")

    @staticmethod
    def _validate_v3_control_flow(edges: list[Any]) -> None:
        edge_set = {tuple(edge) for edge in edges}
        required = {
            ("deterministic_validate", "revise_if_needed"),
            ("semantic_review", "revise_if_needed"),
            ("select_creation_strategy", "lock_creation_strategy"),
            ("lock_creation_strategy", "load_formula_lexicons"),
            ("load_formula_lexicons", "collect_business_rule_evidence"),
            ("load_formula_lexicons", "collect_price_evidence"),
            ("load_formula_lexicons", "collect_compliance_evidence"),
            ("load_formula_lexicons", "collect_viral_candidates"),
            ("collect_business_rule_evidence", "select_viral_reference"),
            ("collect_price_evidence", "select_viral_reference"),
            ("collect_compliance_evidence", "select_viral_reference"),
            ("collect_viral_candidates", "select_viral_reference"),
            ("select_viral_reference", "merge_research_evidence"),
            ("merge_research_evidence", "confirm_high_risk_facts"),
            ("freeze_evidence_bundle", "generate_content"),
        }
        if not required <= edge_set:
            raise ValueError("V3.7 工作流缺少策略锁定、并发调研汇总、爆款选择或固定回修链路")
        forbidden = {
            ("deterministic_validate", "semantic_review"),
            ("revise_if_needed", "human_content_approval"),
        }
        if edge_set & forbidden:
            raise ValueError("阻断校验不得绕过固定回修路由进入 Agent 或人工审批")

    @staticmethod
    def _validate_agent_node(node: dict[str, Any], catalog: WorkflowCatalog | None) -> None:
        required_skills = node.get("required_skills")
        required = {
            "agent_slug": node.get("agent_slug"),
            "required_skills": required_skills if isinstance(required_skills, list) and required_skills else None,
            "input_contract": node.get("input_contract"),
            "state_inputs": node.get("state_inputs") if isinstance(node.get("state_inputs"), list) else None,
            "output_contract": node.get("output_contract"),
            "backend": node.get("backend"),
            "knowledge_policy": node.get("knowledge_policy"),
            "timeout_seconds": node.get("timeout_seconds"),
            "max_execution_steps": node.get("max_execution_steps"),
            "max_tool_calls": node.get("max_tool_calls"),
            "token_budget": node.get("token_budget"),
            "result_tool_name": node.get("result_tool_name"),
        }
        missing = sorted(key for key, value in required.items() if value in (None, "", [], 0))
        if missing:
            raise ValueError(f"Agent 节点 {node['id']} 缺少配置: {', '.join(missing)}")
        if len(set(required_skills)) != len(required_skills):
            raise ValueError(f"Agent 节点 {node['id']} 的 required_skills 不能重复")
        state_inputs = node["state_inputs"]
        optional_state_inputs = node.get("optional_state_inputs") or []
        if len(state_inputs) != len(set(state_inputs)) or len(optional_state_inputs) != len(set(optional_state_inputs)):
            raise ValueError(f"Agent 节点 {node['id']} 的状态输入不能重复")
        if set(state_inputs) & set(optional_state_inputs):
            raise ValueError(f"Agent 节点 {node['id']} 的必需和可选状态输入不能重叠")
        if node["knowledge_policy"] not in KNOWLEDGE_POLICIES:
            raise ValueError(f"Agent 节点 {node['id']} 的 knowledge_policy 无效")
        for key, maximum in (
            ("timeout_seconds", 900),
            ("max_execution_steps", 100),
            ("max_tool_calls", 50),
            ("token_budget", 200_000),
        ):
            value = node[key]
            if not isinstance(value, int) or value < 1 or value > maximum:
                raise ValueError(f"Agent 节点 {node['id']} 的 {key} 超出允许范围")
        if node["result_tool_name"] != "submit_content_node_result":
            raise ValueError(f"Agent 节点 {node['id']} 必须使用结构化结果工具")
        if catalog is None:
            return
        if node["agent_slug"] not in catalog.agents:
            raise ValueError(f"Agent 节点 {node['id']} 引用了未知 Agent")
        unknown_skills = sorted(set(required_skills) - set(catalog.skills))
        if unknown_skills:
            raise ValueError(f"Agent 节点 {node['id']} 引用了未知或未授权 Skill: {', '.join(unknown_skills)}")
        if node["output_contract"] not in catalog.contracts:
            raise ValueError(f"Agent 节点 {node['id']} 引用了未知输出契约")
        if node["input_contract"] not in catalog.contracts:
            raise ValueError(f"Agent 节点 {node['id']} 引用了未知输入契约")
        if node["backend"] not in catalog.backends:
            raise ValueError(f"Agent 节点 {node['id']} 的运行后端不可用")

    @staticmethod
    def _validate_revision_routes(routes: list[Any], node_by_id: dict[str, dict[str, Any]]) -> None:
        for route in routes:
            if not isinstance(route, dict):
                raise ValueError("revision route 必须是对象")
            source = route.get("from")
            target = route.get("to")
            reasons = route.get("reason_codes")
            attempts = route.get("max_attempts")
            if source != "revise_if_needed" or node_by_id.get(source, {}).get("type") != "revision_router":
                raise ValueError("revision route 只能由固定 revise_if_needed 节点发起")
            if target not in node_by_id or target == source:
                raise ValueError("revision route 指向非法节点")
            if not isinstance(reasons, list) or not reasons or len(reasons) != len(set(reasons)):
                raise ValueError("revision route 必须声明唯一的 reason_codes")
            if not isinstance(attempts, int) or attempts < 1:
                raise ValueError("revision route 必须声明正整数 max_attempts")

    @staticmethod
    def _validate_runtime_limits(definition: dict[str, Any]) -> None:
        limits = definition.get("runtime_limits")
        if not isinstance(limits, dict):
            raise ValueError("V3 工作流必须声明 runtime_limits")
        max_steps = limits.get("max_steps")
        max_revisions = limits.get("max_revision_attempts")
        if not isinstance(max_steps, int) or max_steps < 31 or max_steps > 200:
            raise ValueError("runtime_limits.max_steps 必须为 31～200")
        if not isinstance(max_revisions, int) or max_revisions < 0 or max_revisions > 20:
            raise ValueError("runtime_limits.max_revision_attempts 必须为 0～20")
        declared_revisions = sum(int(item.get("max_attempts") or 0) for item in definition.get("revision_routes") or [])
        if declared_revisions > max_revisions:
            raise ValueError("revision route 重试总数超过 runtime_limits.max_revision_attempts")
