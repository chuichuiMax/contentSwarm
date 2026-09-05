from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Any

from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import select

from yuxi.agents import BaseAgent
from yuxi.content.control.errors import ContentApplicationError
from yuxi.content.control.workflow.agent_node import AgentNodeHandler
from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
from yuxi.content.control.workflow.external_wait import ExternalWaitNodeHandler
from yuxi.content.control.workflow.revision import (
    RevisionRouteController,
    resolve_revision_reason,
    revision_reason_label,
)
from yuxi.content.evidence_usage import build_evidence_usage_snapshot
from yuxi.content.generation import SKILL_VERSIONS
from yuxi.content.execution_trace import build_execution_preview
from yuxi.content.infrastructure.postgres.decision_snapshot_repository import PostgresDecisionSnapshotRepository
from yuxi.content.model.contracts import StrategySnapshotV1
from yuxi.content.model.formulas.selector import (
    FormulaCandidateDefinition,
    FormulaCandidatePool,
    FormulaSelectionRequest,
    FormulaSelector,
)
from yuxi.content.model.workflows.definition import WorkflowDefinitionPolicy
from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.repositories.content_repository import ContentRepository
from yuxi.services.run_queue_service import append_run_stream_event, has_cancel_signal
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import (
    ContentArtifact,
    ContentCombinationRule,
    ContentFormula,
    ContentNodeRun,
    CreationMethod,
    TitleFormula,
)
from yuxi.utils.datetime_utils import utc_now_naive

from .context import ContentWorkflowContext
from .state import ContentWorkflowState


def _event_payload(state: ContentWorkflowState, node_id: str, status: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": "content.node",
        "task_id": state["task_id"],
        "node_id": node_id,
        "status": status,
        **extra,
    }


def _report_is_blocked(report: dict[str, Any]) -> bool:
    return report.get("status") == "blocked" or any(
        item.get("status") == "blocked" or item.get("level") == "error" for item in report.get("checks") or []
    )


def _parallel_cache_key(node: dict[str, Any], state: ContentWorkflowState) -> str | None:
    if not node.get("parallel_group"):
        return None
    state_keys = [*(node.get("state_inputs") or []), *(node.get("optional_state_inputs") or [])]
    payload = {
        "node_id": node["id"],
        "definition": node,
        "state": {key: state.get(key) for key in state_keys},
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _find_cached_parallel_result(
    completed_node_runs: list[ContentNodeRun], cache_key: str
) -> tuple[ContentNodeRun, dict[str, Any]] | None:
    for item in completed_node_runs:
        result = (item.output_snapshot or {}).get("result")
        if (item.input_snapshot or {}).get("cache_key") == cache_key and isinstance(result, dict):
            return item, dict(result)
    return None


class ContentWorkflowAgent(BaseAgent):
    name = "通用内容生产工作流"
    description = "仅装配 V3 内容工作流，执行固定节点、正式 Agent、Skill 和人工关口。"
    capabilities = []
    context_schema = ContentWorkflowContext

    async def _get_checkpointer(self):
        if self.checkpointer is not None:
            return self.checkpointer
        checkpointer = await self._create_postgres_checkpointer()
        if checkpointer is not None:
            await checkpointer.setup()
            self.checkpointer = checkpointer
            return checkpointer
        return await super()._get_checkpointer()

    async def get_graph(self, context: ContentWorkflowContext | None = None, **kwargs):
        del kwargs
        context = context or self.context_schema()
        definition = context.workflow_definition
        self._validate_definition(definition)
        self._workflow_definition = definition
        graph = StateGraph(ContentWorkflowState)
        nodes = {node["id"]: node for node in definition["nodes"]}
        for node_id, node in nodes.items():
            graph.add_node(node_id, self._node_runner(node))

        incoming = {node_id: 0 for node_id in nodes}
        outgoing = {node_id: 0 for node_id in nodes}
        for source, target in definition["edges"]:
            if nodes[source]["type"] != "revision_router":
                graph.add_edge(source, target)
            incoming[target] += 1
            outgoing[source] += 1
        revision_targets = {
            "semantic_review": "semantic_review",
            "human_content_approval": "human_content_approval",
            **{route["to"]: route["to"] for route in definition.get("revision_routes") or []},
        }
        for node_id, node in nodes.items():
            if node["type"] == "revision_router":
                graph.add_conditional_edges(
                    node_id,
                    self._route_after_revision,
                    revision_targets,
                )
                for target in revision_targets:
                    incoming[target] += 1
                    outgoing[node_id] += 1
        roots = [node_id for node_id, count in incoming.items() if count == 0]
        leaves = [node_id for node_id, count in outgoing.items() if count == 0]
        for node_id in roots:
            graph.add_edge(START, node_id)
        for node_id in leaves:
            graph.add_edge(node_id, END)
        return graph.compile(checkpointer=await self._get_checkpointer())

    @staticmethod
    def _validate_definition(definition: dict[str, Any]) -> None:
        WorkflowDefinitionPolicy.validate(definition)

    @staticmethod
    def _route_after_revision(state: ContentWorkflowState) -> str:
        return state.get("revision_target") or "semantic_review"

    def _node_runner(self, node: dict[str, Any]) -> Callable:
        async def run(state: ContentWorkflowState) -> dict[str, Any]:
            node_id = node["id"]
            run_id = state["run_id"]
            cache_key = _parallel_cache_key(node, state)
            if await has_cancel_signal(run_id):
                raise InterruptedError("内容运行已取消")
            await append_run_stream_event(
                run_id,
                "custom",
                _event_payload(state, node_id, "running"),
                thread_id=state["task_id"],
            )
            async with pg_manager.get_async_session_context() as db:
                repo = ContentRepository(db)
                cached_node_run = None
                if cache_key:
                    completed_node_runs = list(
                        (
                            await db.execute(
                                select(ContentNodeRun)
                                .where(
                                    ContentNodeRun.task_id == state["task_id"],
                                    ContentNodeRun.node_id == node_id,
                                    ContentNodeRun.status == "completed",
                                )
                                .order_by(ContentNodeRun.finished_at.desc())
                            )
                        ).scalars()
                    )
                    cached = _find_cached_parallel_result(completed_node_runs, cache_key)
                    if cached is not None:
                        cached_node_run, cached_result = cached
                node_run = await repo.add_node_run(
                    task_id=state["task_id"],
                    run_id=run_id,
                    node_id=node_id,
                    node_type=node["type"],
                    input_snapshot={"current_node": state.get("current_node"), "cache_key": cache_key},
                )
                if cached_node_run is not None:
                    result = cached_result
                    await repo.finish_node_run(
                        node_run,
                        status="completed",
                        output_snapshot={
                            "updated_fields": sorted(result.keys()),
                            "result": result,
                            "cached_from_node_run_id": cached_node_run.id,
                        },
                    )
                else:
                    result = None
            if result is not None:
                await append_run_stream_event(
                    run_id,
                    "custom",
                    _event_payload(
                        state,
                        node_id,
                        "completed",
                        cached=True,
                        output_preview=build_execution_preview(result),
                    ),
                    thread_id=state["task_id"],
                )
                return result
            try:
                if node["type"] == "agent":
                    async with pg_manager.get_async_session_context() as db:
                        result = await AgentNodeHandler().execute(
                            db=db,
                            node=node,
                            state=state,
                            node_run_id=node_run.id,
                        )
                elif node["type"] == "deterministic":
                    if node["id"] == "save_artifact_snapshot":
                        result = await self._save_artifact(state)
                    else:
                        async with pg_manager.get_async_session_context() as db:
                            result = await V3DeterministicNodeHandler().execute(
                                db=db,
                                node=node,
                                state=state,
                                node_run_id=node_run.id,
                            )
                elif node["type"] == "external_wait":
                    async with pg_manager.get_async_session_context() as db:
                        result = await ExternalWaitNodeHandler().execute(
                            db=db,
                            node=node,
                            state=state,
                        )
                else:
                    result = await self._execute_node(
                        node,
                        state,
                        getattr(self, "_workflow_definition", {}),
                    )
            except GraphInterrupt:
                async with pg_manager.get_async_session_context() as db:
                    repo = ContentRepository(db)
                    persisted = await db.get(type(node_run), node_run.id)
                    if persisted:
                        await repo.finish_node_run(
                            persisted,
                            status=("waiting_external" if node["type"] == "external_wait" else "waiting_human"),
                        )
                raise
            except Exception as exc:
                async with pg_manager.get_async_session_context() as db:
                    repo = ContentRepository(db)
                    persisted = await db.get(type(node_run), node_run.id)
                    if persisted:
                        await repo.finish_node_run(
                            persisted,
                            status="failed",
                            error_type=type(exc).__name__,
                            error_message=str(exc),
                        )
                await append_run_stream_event(
                    run_id,
                    "custom",
                    _event_payload(state, node_id, "failed", message=str(exc)),
                    thread_id=state["task_id"],
                )
                raise
            async with pg_manager.get_async_session_context() as db:
                repo = ContentRepository(db)
                persisted = await db.get(type(node_run), node_run.id)
                if persisted:
                    output_snapshot = {"updated_fields": sorted(result.keys())}
                    if cache_key:
                        output_snapshot["result"] = result
                    if node_id == "validate_title_candidates":
                        output_snapshot["title_validation_report"] = result.get("title_validation_report") or {}
                    await repo.finish_node_run(
                        persisted,
                        status="completed",
                        output_snapshot=output_snapshot,
                    )
            if node_id == "deterministic_validate":
                validation = result.get("validation_report") or {}
                await append_run_stream_event(
                    run_id,
                    "content.validation.completed",
                    {
                        "task_id": state["task_id"],
                        "parent_run_id": run_id,
                        "node_id": node_id,
                        "status": validation.get("status"),
                        "check_count": len(validation.get("checks") or []),
                    },
                    thread_id=state["task_id"],
                )
            await append_run_stream_event(
                run_id,
                "custom",
                _event_payload(
                    state,
                    node_id,
                    "completed",
                    output_preview=build_execution_preview(result),
                ),
                thread_id=state["task_id"],
            )
            if node.get("parallel_group"):
                return result
            return {**result, "current_node": node_id}

        return run

    async def _execute_node(
        self,
        node: dict[str, Any],
        state: ContentWorkflowState,
        definition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        node_type = node["type"]
        if node_type == "human_review":
            return await self._v3_human_review(node, state)
        if node_type == "revision_router":
            previous_node = state.get("current_node")
            title_validation_report = state.get("title_validation_report") or {}
            validation_report = state.get("validation_report") or {}
            review_report = state.get("review_report") or {}
            if previous_node == "validate_title_candidates" and not _report_is_blocked(title_validation_report):
                return {
                    "revision_reason_code": None,
                    "revision_target": "select_title",
                    "revision_status": "continue",
                    "retry_counts": dict(state.get("retry_counts") or {}),
                }
            if previous_node == "deterministic_validate" and not _report_is_blocked(validation_report):
                return {
                    "revision_reason_code": None,
                    "revision_target": "semantic_review",
                    "revision_status": "continue",
                    "retry_counts": dict(state.get("retry_counts") or {}),
                }
            if previous_node == "semantic_review" and not _report_is_blocked(review_report):
                return {
                    "revision_reason_code": None,
                    "revision_target": "human_content_approval",
                    "revision_status": "continue",
                    "retry_counts": dict(state.get("retry_counts") or {}),
                }
            reason_code = resolve_revision_reason(
                title_validation_report=title_validation_report,
                validation_report=validation_report,
                review_report=review_report if previous_node == "semantic_review" else None,
            )
            if reason_code in {"SYSTEM_CONFIGURATION_FAILED", "REVIEW_CONTRACT_VIOLATION"}:
                raise ContentApplicationError(
                    code=reason_code.lower(),
                    message=("内容校验发现系统配置或审核契约错误，已停止执行；不会交给语义 Agent 猜测修复"),
                    kind="conflict",
                )
            decision = RevisionRouteController().decide(
                definition=definition or {},
                reason_code=reason_code,
                retry_counts=state.get("retry_counts") or {},
            )
            if decision.status == "limit_reached":
                raise ContentApplicationError(
                    code="content_revision_limit_reached",
                    message=f"{revision_reason_label(reason_code)}，已达到定点回修次数上限",
                    kind="conflict",
                )
            if decision.status == "continue" or decision.target_node_id is None:
                raise ContentApplicationError(
                    code="content_revision_route_missing",
                    message=f"{revision_reason_label(reason_code)}，没有可执行的定点回修路线",
                    kind="conflict",
                )
            return {
                "revision_reason_code": reason_code,
                "revision_target": decision.target_node_id,
                "revision_status": decision.status,
                "retry_counts": decision.retry_counts,
                "resume_parent_run_id": None,
            }
        raise ValueError(f"V3 工作流不支持节点类型: {node_type}")

    async def _v3_human_review(
        self,
        node: dict[str, Any],
        state: ContentWorkflowState,
    ) -> dict[str, Any]:
        interrupt_type = node["interrupt_type"]
        state_version = int(state.get("state_version") or 0)

        def require_resume(payload: dict[str, Any]) -> dict[str, Any]:
            answer = interrupt(
                {
                    "interrupt_type": interrupt_type,
                    "task_id": state["task_id"],
                    "run_id": state.get("resume_parent_run_id") or state["run_id"],
                    "node_id": node["id"],
                    "expected_state_version": state_version,
                    **payload,
                }
            )
            if not isinstance(answer, dict):
                raise ValueError("人工恢复输入必须是对象")
            expected = {
                "run_id": state.get("resume_parent_run_id") or state["run_id"],
                "node_id": node["id"],
                "expected_state_version": state_version,
            }
            mismatched = [key for key, value in expected.items() if answer.get(key) != value]
            if mismatched:
                raise ValueError(f"人工恢复请求已过期或目标不匹配: {', '.join(mismatched)}")
            return answer

        if interrupt_type == "content_direction":
            options = state.get("content_angles") or []
            answer = require_resume({"options": options})
            code = answer.get("direction_code")
            selected = next((item for item in options if item.get("direction_code") == code), None)
            if selected is None:
                raise ValueError("选定内容方向不在当前候选集中")
            return {
                "selected_angle": selected,
                "state_version": state_version + 1,
                "resume_parent_run_id": None,
            }

        if interrupt_type == "high_risk_facts":
            collection = dict(state.get("evidence_collection") or {})
            items = list(collection.get("evidence_items") or [])
            excluded = [
                item
                for item in items
                if item.get("risk_level") == "high_risk" and item.get("verified_status") != "user_confirmed"
            ]
            excluded_ids = {item.get("id") for item in excluded}
            excluded_source_ids = {str(item.get("source_id")) for item in excluded if item.get("source_id")}
            collection["evidence_items"] = [item for item in items if item.get("id") not in excluded_ids]
            collection["citations"] = [
                item for item in collection.get("citations") or [] if str(item) not in excluded_source_ids
            ]
            unresolved = list(collection.get("unresolved_questions") or [])
            unresolved.extend(
                f"外部高风险资料“{item.get('value') or item.get('id')}”未经用户确认，本次首稿已自动排除"
                for item in excluded
            )
            collection["unresolved_questions"] = list(dict.fromkeys(unresolved))
            return {
                "evidence_collection": collection,
                "state_version": state_version + 1,
                "resume_parent_run_id": None,
            }

        if interrupt_type == "strategy_product_facts":
            collection = dict(state.get("product_evidence_collection") or {})
            items = list(collection.get("evidence_items") or [])
            high_risk_items = [
                item
                for item in items
                if item.get("risk_level") == "high_risk" and item.get("verified_status") != "user_confirmed"
            ]
            if not high_risk_items:
                return {"state_version": state_version + 1, "resume_parent_run_id": None}
            high_risk_ids = {item["id"] for item in high_risk_items}
            answer = require_resume(
                {
                    "evidence_ids": sorted(high_risk_ids),
                    "evidence_items": high_risk_items,
                    "strategy_snapshot_hash": (state.get("strategy_snapshot") or {}).get("snapshot_hash"),
                }
            )
            confirmed = set(answer.get("confirmed_evidence_ids") or [])
            if confirmed != high_risk_ids:
                raise ValueError("价格、优惠、效果承诺等高风险产品事实必须逐项人工确认")
            collection["evidence_items"] = [
                {**item, "verified_status": "user_confirmed"} if item.get("id") in confirmed else item for item in items
            ]
            return {
                "product_evidence_collection": collection,
                "state_version": state_version + 1,
                "resume_parent_run_id": None,
            }

        if interrupt_type == "formula_selection":
            match = state.get("match_decision_snapshot") or {}
            formula_pool = state.get("formula_candidate_pool") or {}
            title_pool = tuple(formula_pool.get("title_formula_codes") or [])
            body_pool = tuple(formula_pool.get("body_formula_codes") or [])
            valid_pairs = formula_pool.get("valid_formula_pairs") or []
            ranking = state.get("formula_rankings") or {}
            title_ranking = tuple(item["formula_code"] for item in ranking.get("title_rankings") or [])
            body_ranking = tuple(item["formula_code"] for item in ranking.get("body_rankings") or [])
            selected_by = "quick_mode"
            if len(valid_pairs) == 1:
                title_ranking = ()
                body_ranking = ()
                selected_by = "deterministic"
            elif state.get("task_mode") == "pro":
                answer = require_resume({"title_formula_codes": title_pool, "body_formula_codes": body_pool})
                title_ranking = (str(answer.get("title_formula_code") or ""),)
                body_ranking = (str(answer.get("body_formula_code") or ""),)
                selected_by = state["uid"]
            definitions = [
                FormulaCandidateDefinition(code=code, kind="title", rule_version_id=state["rule_version_id"])
                for code in title_pool
            ] + [
                FormulaCandidateDefinition(code=code, kind="body", rule_version_id=state["rule_version_id"])
                for code in body_pool
            ]
            decision = FormulaSelector().select(
                FormulaCandidatePool(
                    combination_group_id=match["selected_group_id"],
                    rule_version_id=state["rule_version_id"],
                    title_formula_codes=title_pool,
                    body_formula_codes=body_pool,
                    allowed_formula_pairs=frozenset(
                        (item["title_formula_code"], item["body_formula_code"]) for item in valid_pairs
                    ),
                ),
                definitions,
                FormulaSelectionRequest(
                    agent_title_ranking=title_ranking,
                    agent_body_ranking=body_ranking,
                ),
            )
            if decision.status != "selected":
                raise ValueError("公式候选池无可用标题/正文公式对")
            evidence_hash = str((state.get("evidence_bundle") or {}).get("bundle_hash") or "")
            async with pg_manager.get_async_session_context() as db:
                snapshot = await PostgresDecisionSnapshotRepository(db).save_formula_selection(
                    task_id=state["task_id"],
                    content_run_id=state["run_id"],
                    node_run_id=None,
                    match_snapshot_id=match["id"],
                    rule_version_id=state["rule_version_id"],
                    evidence_bundle_hash=evidence_hash,
                    decision=decision,
                    selected_by=selected_by,
                    delegated_agent_run_id=(state.get("delegated_agent_runs") or {}).get("rank_formula_candidates"),
                )
                strategy_snapshot = await self._build_strategy_snapshot(
                    db=db,
                    state=state,
                    match=match,
                    formula_snapshot_id=snapshot.id,
                    title_formula_code=str(decision.selected_title_formula_code or ""),
                    body_formula_code=str(decision.selected_body_formula_code or ""),
                )
            result = decision.to_dict()
            result["id"] = snapshot.id
            result["eligible_title_formula_codes"] = [item.formula_code for item in decision.eligible_title_formulas]
            result["eligible_body_formula_codes"] = [item.formula_code for item in decision.eligible_body_formulas]
            await append_run_stream_event(
                state["run_id"],
                "content.formula.selected",
                {
                    "task_id": state["task_id"],
                    "parent_run_id": state["run_id"],
                    "node_id": node["id"],
                    "snapshot_id": snapshot.id,
                    "combination_group_id": decision.combination_group_id,
                    "title_formula_code": decision.selected_title_formula_code,
                    "body_formula_code": decision.selected_body_formula_code,
                    "selection_mode": decision.selection_mode,
                },
                thread_id=state["task_id"],
            )
            return {
                "formula_selection_snapshot": result,
                "strategy_snapshot": strategy_snapshot,
                "state_version": state_version + 1,
                "resume_parent_run_id": None,
            }

        if interrupt_type == "title_selection":
            options = [item for item in state.get("title_candidates") or [] if item.get("selectable", True)]
            answer = require_resume({"options": options})
            selected = next((item for item in options if item.get("id") == answer.get("title_id")), None)
            if selected is None:
                raise ValueError("选定标题不在通过校验的候选集中")
            return {
                "selected_title": selected,
                "state_version": state_version + 1,
                "resume_parent_run_id": None,
            }

        if interrupt_type == "content_approval":
            validation_report = state.get("validation_report") or {}
            review_report = state.get("review_report") or {}
            invalid_reports = [
                name
                for name, report in (
                    ("deterministic", validation_report),
                    ("semantic", review_report),
                )
                if report.get("status") not in {"passed", "warning"} or _report_is_blocked(report)
            ]
            if invalid_reports:
                raise ContentApplicationError(
                    code="content_approval_blocked",
                    message=f"最终审批前仍有阻断报告: {', '.join(invalid_reports)}",
                    kind="conflict",
                )
            artifact_payload = {
                "task_id": state["task_id"],
                "run_id": state["run_id"],
                "title": state.get("selected_title") or {},
                "draft": state.get("content_draft") or {},
                "evidence_bundle_hash": (state.get("evidence_bundle") or {}).get("bundle_hash"),
                "formula_selection": state.get("formula_selection_snapshot") or {},
            }
            artifact_hash = hashlib.sha256(
                json.dumps(
                    artifact_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            return {
                "approval_result": {
                    "status": "approved",
                    "note": "固定校验通过后自动批准",
                    "reviewer_uid": "system",
                },
                "artifact_version": {
                    "id": f"cav_{artifact_hash[:32]}",
                    "content_hash": artifact_hash,
                    "status": "approved_content",
                },
                "state_version": state_version + 1,
                "resume_parent_run_id": None,
            }

        if interrupt_type == "cover_selection":
            review = state.get("visual_review") or {}
            passed_ids = {
                item["asset_id"] for item in review.get("assets") or [] if item.get("status") in {"passed", "warning"}
            }
            answer = require_resume({"asset_ids": sorted(passed_ids)})
            asset_id = answer.get("asset_id")
            if asset_id not in passed_ids:
                raise ValueError("只能选择通过视觉审核的封面资产")
            return {
                "selected_cover": {
                    "asset_id": asset_id,
                    "cover_job_id": (state.get("cover_job") or {}).get("cover_job_id"),
                    "review_status": next(
                        item["status"] for item in review.get("assets") or [] if item.get("asset_id") == asset_id
                    ),
                },
                "state_version": state_version + 1,
                "resume_parent_run_id": None,
            }

        raise ValueError(f"未实现的 V3 人工关口: {interrupt_type}")

    @staticmethod
    async def _build_strategy_snapshot(
        *,
        db,
        state: ContentWorkflowState,
        match: dict[str, Any],
        formula_snapshot_id: str,
        title_formula_code: str,
        body_formula_code: str,
    ) -> dict[str, Any]:
        rule_version_id = state["rule_version_id"]
        group = await db.get(ContentCombinationRule, match["selected_group_id"])
        title_formula = (
            await db.execute(
                select(TitleFormula).where(
                    TitleFormula.version_id == rule_version_id,
                    TitleFormula.code == title_formula_code,
                    TitleFormula.enabled.is_(True),
                )
            )
        ).scalar_one_or_none()
        body_formula = (
            await db.execute(
                select(ContentFormula).where(
                    ContentFormula.version_id == rule_version_id,
                    ContentFormula.code == body_formula_code,
                    ContentFormula.enabled.is_(True),
                )
            )
        ).scalar_one_or_none()
        if group is None or title_formula is None or body_formula is None:
            raise ValueError("锁定策略缺少组合组或标题/正文公式定义")
        method_codes = [
            str(item.get("method_code"))
            for item in (group.method_members or [])
            if isinstance(item, dict) and item.get("method_code")
        ] or [str(item) for item in (group.methods or []) if item]
        methods = list(
            (
                await db.execute(
                    select(CreationMethod).where(
                        CreationMethod.version_id == rule_version_id,
                        CreationMethod.code.in_(method_codes),
                        CreationMethod.enabled.is_(True),
                    )
                )
            ).scalars()
        )
        loaded_method_codes = {item.code for item in methods}
        if not method_codes or set(method_codes) - loaded_method_codes:
            raise ValueError("锁定策略缺少创作手法定义")
        method_by_code = {item.code: item for item in methods}
        payload = {
            "content_direction": str(
                (state.get("selected_angle") or {}).get("direction_code")
                or (state.get("selected_angle") or {}).get("content_direction_code")
                or ""
            ),
            "selected_group_id": group.id,
            "creation_methods": method_codes,
            "creation_method_definitions": [
                {
                    "code": method_by_code[code].code,
                    "name": method_by_code[code].name,
                    "method_type": method_by_code[code].method_type,
                    "principle": method_by_code[code].principle,
                    "suitable_scenes": method_by_code[code].suitable_scenes or [],
                    "sentence_patterns": method_by_code[code].sentence_patterns or [],
                    "variable_schema": method_by_code[code].variable_schema or [],
                    "risk_rules": method_by_code[code].risk_rules or [],
                }
                for code in method_codes
            ],
            "title_formula": {
                "code": title_formula.code,
                "name": title_formula.name,
                "core_goal": title_formula.core_goal,
                "reference_examples": title_formula.reference_examples or [],
                "variable_schema": title_formula.variable_schema or [],
                "compatible_methods": title_formula.compatible_methods or [],
                "risk_rules": title_formula.risk_rules or [],
            },
            "body_formula": {
                "code": body_formula.code,
                "name": body_formula.name,
                "structure_schema": body_formula.structure_schema or [],
                "reference_examples": body_formula.reference_examples or [],
                "required_variables": body_formula.required_variables or [],
                "output_schema": body_formula.output_schema or {},
                "compatible_methods": body_formula.compatible_methods or [],
                "risk_rules": body_formula.risk_rules or [],
            },
            "rule_version_id": rule_version_id,
            "match_snapshot_id": str(match["id"]),
            "formula_snapshot_id": formula_snapshot_id,
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload["snapshot_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return StrategySnapshotV1.model_validate(payload).model_dump(mode="json")

    async def _save_artifact(self, state: ContentWorkflowState) -> dict[str, Any]:
        draft = state["content_draft"]
        review = state.get("review_report") or state.get("validation_report") or {"status": "passed", "checks": []}
        approval_rejected = (state.get("approval_result") or {}).get("status") == "rejected"
        artifact_status = "blocked" if review["status"] == "blocked" or approval_rejected else "reviewed"
        strategy_snapshot = state.get("strategy_snapshot") or {}
        evidence_usage_snapshot = build_evidence_usage_snapshot(
            selected_title=state["selected_title"],
            content_draft=draft,
        )
        if not strategy_snapshot.get("snapshot_hash"):
            raise ValueError("保存内容资产前缺少锁定 StrategySnapshot")
        async with pg_manager.get_async_session_context() as db:
            repo = ContentRepository(db)
            task = await repo.get_task(state["task_id"], for_update=True)
            selected_cover = state.get("selected_cover") or {}
            cover_job_id = selected_cover.get("cover_job_id")
            cover_asset_id = selected_cover.get("asset_id")
            hycanvas_design_snapshot = {}
            if selected_cover:
                cover_repo = ContentCoverRepository(db)
                cover_job = await cover_repo.get_job(str(cover_job_id or ""))
                cover_asset = await cover_repo.get_asset(str(cover_asset_id or ""))
                passed_ids = {
                    item["asset_id"]
                    for item in (state.get("visual_review") or {}).get("assets") or []
                    if item.get("status") in {"passed", "warning"}
                }
                if (
                    cover_job is None
                    or cover_job.status != "succeeded"
                    or cover_job.content_task_id != task.id
                    or cover_asset is None
                    or cover_asset.owner_uid != state["uid"]
                    or cover_asset.role != "output"
                    or cover_asset.id not in ((cover_job.result_json or {}).get("asset_ids") or [])
                    or cover_asset.id not in passed_ids
                ):
                    raise ValueError("ArtifactVersion 只能绑定本任务中通过视觉审核的 CoverJob 资产")
                hycanvas_design_snapshot = (cover_job.result_json or {}).get("hycanvas_design_snapshot") or {}
            artifact = await repo.get_artifact_for_task(task.id)
            if artifact is None:
                artifact = ContentArtifact(
                    id=f"ca_{uuid.uuid4().hex}",
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    status=artifact_status,
                    current_version=1,
                    title=state["selected_title"]["text"],
                    body=draft["body"],
                    topics=draft.get("topics") or [],
                    strategy_snapshot=strategy_snapshot,
                    evidence_snapshot=state["evidence_bundle"],
                    evidence_usage_snapshot=evidence_usage_snapshot,
                    review_snapshot=review,
                    cover_asset_id=cover_asset_id,
                    cover_job_id=cover_job_id,
                    hycanvas_design_snapshot=hycanvas_design_snapshot,
                    content_type_snapshot=state.get("content_type") or {},
                    angle_snapshot=state.get("selected_angle") or {},
                    pattern_slot_snapshot={
                        "title_pattern_code": (strategy_snapshot.get("title_formula") or {}).get("code"),
                        "body_pattern_code": (strategy_snapshot.get("body_formula") or {}).get("code"),
                        "title_formula_code": (state.get("formula_selection_snapshot") or {}).get(
                            "selected_title_formula_code"
                        ),
                        "body_formula_code": (state.get("formula_selection_snapshot") or {}).get(
                            "selected_body_formula_code"
                        ),
                        "outline": state.get("content_outline") or {},
                    },
                    persona_snapshot={
                        "profile": state.get("persona_profile") or {},
                        "diff": state.get("persona_diff") or {},
                    },
                    channel_snapshot=state.get("channel_profile") or {},
                    compliance_snapshot={
                        "policies": state.get("compliance_policies") or [],
                        "result": state.get("channel_result") or {},
                        "approval": state.get("approval_result") or {},
                    },
                    runtime_config_snapshot=state.get("runtime_config_snapshot") or {},
                    created_by=state["uid"],
                )
                db.add(artifact)
                await db.flush()
            else:
                artifact.current_version += 1
                artifact.title = state["selected_title"]["text"]
                artifact.body = draft["body"]
                artifact.topics = draft.get("topics") or []
                artifact.strategy_snapshot = strategy_snapshot
                artifact.evidence_snapshot = state["evidence_bundle"]
                artifact.evidence_usage_snapshot = evidence_usage_snapshot
                artifact.review_snapshot = review
                artifact.cover_asset_id = cover_asset_id
                artifact.cover_job_id = cover_job_id
                artifact.hycanvas_design_snapshot = hycanvas_design_snapshot
                artifact.content_type_snapshot = state.get("content_type") or {}
                artifact.angle_snapshot = state.get("selected_angle") or {}
                artifact.pattern_slot_snapshot = {
                    "title_pattern_code": (strategy_snapshot.get("title_formula") or {}).get("code"),
                    "body_pattern_code": (strategy_snapshot.get("body_formula") or {}).get("code"),
                    "title_formula_code": (state.get("formula_selection_snapshot") or {}).get(
                        "selected_title_formula_code"
                    ),
                    "body_formula_code": (state.get("formula_selection_snapshot") or {}).get(
                        "selected_body_formula_code"
                    ),
                    "outline": state.get("content_outline") or {},
                }
                artifact.persona_snapshot = {
                    "profile": state.get("persona_profile") or {},
                    "diff": state.get("persona_diff") or {},
                }
                artifact.channel_snapshot = state.get("channel_profile") or {}
                artifact.compliance_snapshot = {
                    "policies": state.get("compliance_policies") or [],
                    "result": state.get("channel_result") or {},
                    "approval": state.get("approval_result") or {},
                }
                artifact.runtime_config_snapshot = state.get("runtime_config_snapshot") or {}
                artifact.status = artifact_status
                artifact.updated_at = utc_now_naive()
            version = await repo.save_artifact_version(
                artifact=artifact,
                version_id=(state.get("artifact_version") or {}).get("id"),
                source_type="generated",
                model_spec=state.get("model_spec"),
                skill_versions=SKILL_VERSIONS,
                rule_version_id=task.rule_version_id,
                knowledge_snapshot=state["evidence_bundle"],
                review_snapshot=review,
                created_by=state["uid"],
            )
            await repo.add_review_record(
                artifact_version_id=version.id,
                review_type="combined",
                status=review["status"],
                checks=review.get("checks") or [],
                reviewer_uid=(state.get("approval_result") or {}).get("reviewer_uid"),
            )
            task.status = "review_blocked" if artifact_status == "blocked" else "reviewed"
            task.current_stage = "review"
            task.review_json = review
            task.evidence_json = state["evidence_bundle"]
            task.selected_title_json = state["selected_title"]
            await repo.track(
                "content_run_completed",
                uid=state["uid"],
                task_id=task.id,
                run_id=state["run_id"],
                properties={"artifact_id": artifact.id, "review_status": review["status"]},
            )
            return {
                "artifact_id": artifact.id,
                "artifact_version": {
                    "id": version.id,
                    "version": version.version,
                    "content_hash": (state.get("artifact_version") or {}).get("content_hash"),
                    "cover_asset_id": version.cover_asset_id,
                    "cover_job_id": version.cover_job_id,
                    "status": artifact.status,
                },
            }
