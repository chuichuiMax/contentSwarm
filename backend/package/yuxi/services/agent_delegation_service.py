"""固定内容工作流向管理端 Agent 委派节点的唯一服务。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import ValidationError

from yuxi.agents.buildin import agent_manager
from yuxi.agents.context import normalize_agent_context_config, prepare_agent_runtime_context
from yuxi.content.control.errors import ContentApplicationError
from yuxi.content.execution_trace import build_execution_preview
from yuxi.content.model.contracts import (
    ContentAgentNodeInputV2,
    ContentNodeResultCollector,
    ContractDomainContext,
    ContractDomainValidationError,
    get_contract_model,
    get_input_contract_model,
)
from yuxi.repositories.agent_repository import AgentRepository, user_can_access_agent
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.content_repository import ContentRepository
from yuxi.services.run_queue_service import append_run_stream_event
from yuxi.storage.postgres.models_business import Agent, User
from yuxi.storage.postgres.models_content import ContentNodeRun


@dataclass(frozen=True, slots=True)
class AgentDelegationRequest:
    task_id: str
    parent_content_run_id: str
    node_run: ContentNodeRun
    user: User
    agent_slug: str
    required_skills: tuple[str, ...]
    input_contract: str
    input_payload: dict[str, Any]
    input_snapshot_hash: str
    domain_context: ContractDomainContext
    governance_values: dict[str, Any]
    prompt: str
    output_contract: str
    result_tool_name: str = "submit_content_node_result"
    knowledge_policy: str = "frozen_evidence_only"
    timeout_seconds: int = 120
    max_execution_steps: int = 12
    max_tool_calls: int = 4
    token_budget: int = 8000
    max_retrieval_rounds: int = 0
    max_knowledge_bases: int = 0
    max_chunks_per_knowledge_base: int = 0
    max_chars_per_knowledge_chunk: int = 0
    prohibited_actions: tuple[str, ...] = ()
    cancel_event: asyncio.Event | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class AgentDelegationResult:
    delegated_agent_run_id: str
    output: dict[str, Any]
    runtime_config_snapshot: dict[str, Any]


def _bounded_run_identifier(value: str) -> str:
    """AgentRun 的标识列上限为 64；超长时保留类型前缀并使用稳定摘要。"""

    if len(value) <= 64:
        return value
    prefix = value.partition(":")[0]
    digest_length = 64 - len(prefix) - 1
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:digest_length]
    return f"{prefix}:{digest}"


def build_runtime_config_snapshot(*, agent: Agent, context, request: AgentDelegationRequest) -> dict[str, Any]:
    snapshot = {
        "schema_version": 2,
        "agent": {
            "slug": agent.slug,
            "backend_id": agent.backend_id,
            "config_version": int(agent.config_version or 1),
        },
        "model": str(getattr(context, "model", "") or ""),
        "skills": list(getattr(context, "_runtime_skill_snapshots", []) or []),
        "tools": list(getattr(context, "_required_skill_tools", []) or []) + [request.result_tool_name],
        "mcps": list(getattr(context, "_required_skill_mcps", []) or []),
        "knowledges": list(getattr(context, "knowledges", []) or []),
        "limits": {
            "timeout_seconds": request.timeout_seconds,
            "max_execution_steps": request.max_execution_steps,
            "max_tool_calls": request.max_tool_calls,
            "token_budget": request.token_budget,
            "model_call_timeout_seconds": getattr(context, "model_call_timeout_seconds", None),
            "model_retry_times": getattr(context, "model_retry_times", None),
            "max_chars_per_knowledge_chunk": request.max_chars_per_knowledge_chunk,
        },
        "output_contract": request.output_contract,
        "input_contract": request.input_contract,
    }
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot["snapshot_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return snapshot


class AgentDelegationService:
    KNOWLEDGE_NODE_IDS = {
        "collect_missing_evidence",
        "collect_strategy_product_evidence",
        "collect_business_rule_evidence",
        "collect_price_evidence",
        "collect_compliance_evidence",
        "collect_viral_candidates",
        "semantic_review",
    }
    KNOWLEDGE_TOOL_NAMES = {"list_kbs", "get_mindmap", "query_kb", "open_kb_document", "find_kb_document"}
    RESEARCH_KNOWLEDGE_NAMES = {
        "collect_business_rule_evidence": {"品牌知识库", "平台规则"},
        "collect_price_evidence": {"价格库"},
        "collect_compliance_evidence": {"封禁词库"},
        "collect_viral_candidates": {"爆款库"},
    }

    def __init__(self, db: AsyncSession):
        self.db = db
        self.agent_repo = AgentRepository(db)
        self.run_repo = AgentRunRepository(db)
        self.content_repo = ContentRepository(db)

    async def execute(self, request: AgentDelegationRequest) -> AgentDelegationResult:
        agent, backend = await self._resolve_agent(request)
        thread_id = _bounded_run_identifier(
            f"content:{request.task_id}:{request.node_run.node_id}:{request.node_run.attempt}"
        )
        child_run_id = f"run_{uuid.uuid4().hex}"
        child_request_id = _bounded_run_identifier(
            f"content-node:{request.parent_content_run_id}:{request.node_run.id}"
        )
        context = backend.context_schema(thread_id=thread_id, uid=str(request.user.uid))
        normalized_config = await normalize_agent_context_config(
            (agent.config_json or {}).get("context", {}),
            db=self.db,
            user=request.user,
            context_schema=backend.context_schema,
        )
        context.update_from_dict(normalized_config)
        context.thread_id = thread_id
        context.uid = str(request.user.uid)
        context.run_id = child_run_id
        context.request_id = child_request_id
        context._content_task_id = request.task_id
        context._content_parent_run_id = request.parent_content_run_id
        context._content_parent_thread_id = request.task_id
        context._content_node_id = request.node_run.node_id
        context._content_node_attempt = request.node_run.attempt
        context._content_delegated_agent_run_id = child_run_id
        context.required_skills = list(request.required_skills)
        self._apply_node_constraints(context, request)
        await prepare_agent_runtime_context(context, context_schema=backend.context_schema)
        self._restrict_research_knowledge_scope(context, request.node_run.node_id)
        self._apply_knowledge_tool_scope(context)
        activated_scope = set(getattr(context, "_required_skill_closure", []) or [])
        if not set(request.required_skills).issubset(activated_scope):
            raise ContentApplicationError(
                "required_skill_not_prepared",
                "未能在推理前准备全部必需 Skills",
                "invalid",
            )

        runtime_snapshot = build_runtime_config_snapshot(agent=agent, context=context, request=request)
        visible_payload = get_input_contract_model(request.input_contract).model_validate(request.input_payload)
        node_input_payload = {
            "task_id": request.task_id,
            "parent_run_id": request.parent_content_run_id,
            "node_id": request.node_run.node_id,
            "attempt": request.node_run.attempt,
            "input_contract": request.input_contract,
            "input_snapshot_hash": request.input_snapshot_hash,
            "payload": visible_payload.model_dump(mode="json"),
            "runtime_config_snapshot": runtime_snapshot,
            "node_responsibility": request.prompt,
            "prohibited_actions": list(request.prohibited_actions),
            "output_json_schema": get_contract_model(request.output_contract).model_json_schema(),
        }
        node_input = ContentAgentNodeInputV2.model_validate(node_input_payload)
        collector = ContentNodeResultCollector(
            contract_name=request.output_contract,
            domain_context=request.domain_context,
            runtime_context=context,
        )
        context._content_node_tool_scope = runtime_snapshot["tools"]
        context._content_node_result_tool_name = request.result_tool_name
        context._content_node_output_contract = request.output_contract
        context._content_node_result_collector = collector
        context._content_node_input = node_input
        context._content_node_governance = request.governance_values
        context._content_node_max_tool_calls = request.max_tool_calls
        context._content_node_token_budget = request.token_budget
        context._content_max_retrieval_rounds = request.max_retrieval_rounds
        context._content_max_knowledge_bases = request.max_knowledge_bases
        context._content_max_chunks_per_knowledge_base = request.max_chunks_per_knowledge_base
        context._content_max_chars_per_knowledge_chunk = request.max_chars_per_knowledge_chunk

        child_run = await self.run_repo.create_run(
            run_id=child_run_id,
            thread_id=thread_id,
            agent_id=agent.slug,
            uid=str(request.user.uid),
            request_id=child_request_id,
            parent_agent_run_id=request.parent_content_run_id,
            run_type="content_node_agent",
            input_payload={
                "task_id": request.task_id,
                "node_run_id": request.node_run.id,
                "node_id": request.node_run.node_id,
                "input": node_input.model_dump(mode="json"),
                "runtime_config_snapshot": runtime_snapshot,
            },
        )
        await self.content_repo.attach_delegated_agent_run(request.node_run, child_run.id)
        request.node_run.input_snapshot = {
            **(getattr(request.node_run, "input_snapshot", None) or {}),
            "input_contract": request.input_contract,
            "input_snapshot_hash": request.input_snapshot_hash,
            "visible_payload": visible_payload.model_dump(mode="json"),
            "runtime_config_snapshot": runtime_snapshot,
        }
        await self.run_repo.mark_running(child_run.id)
        await self.db.commit()
        started_at = time.monotonic()
        await self._emit_agent_event(
            context,
            "content.agent.started",
            {
                "agent_slug": agent.slug,
                "input_contract": request.input_contract,
                "input_snapshot_hash": request.input_snapshot_hash,
                "input_preview": build_execution_preview(visible_payload.model_dump(mode="json")),
                "runtime_config_snapshot": runtime_snapshot,
            },
        )

        try:
            graph = await backend.get_graph(context=context)
            await self._invoke_graph(graph, context, request, node_input)
            activated = set(getattr(context, "_activated_required_skills", []) or [])
            if not activated_scope.issubset(activated):
                raise ContentApplicationError(
                    "required_skill_not_activated",
                    "Agent 结束时仍有必需 Skill 未激活",
                    "invalid",
                )
            output = collector.finalize()
        except asyncio.CancelledError:
            await self._record_node_failure(request.node_run, child_run.id, "cancelled", "", "内容父 Run 已取消")
            await self._mark_terminal(child_run.id, "cancelled", "cancelled", "内容父 Run 已取消")
            await self._emit_agent_event(
                context,
                "content.agent.failed",
                {
                    "agent_slug": agent.slug,
                    "error_type": "cancelled",
                    "duration_ms": int((time.monotonic() - started_at) * 1000),
                },
            )
            raise
        except TimeoutError as exc:
            await self._record_node_failure(request.node_run, child_run.id, "agent_timeout", "", str(exc))
            await self._mark_terminal(child_run.id, "failed", "timeout", str(exc))
            await self._emit_agent_event(
                context,
                "content.agent.failed",
                {
                    "agent_slug": agent.slug,
                    "error_type": "agent_timeout",
                    "duration_ms": int((time.monotonic() - started_at) * 1000),
                },
            )
            raise ContentApplicationError("agent_timeout", str(exc), "invalid") from exc
        except Exception as exc:
            error_type, field_path, safe_message = self._safe_error_details(exc)
            await self._record_node_failure(
                request.node_run,
                child_run.id,
                error_type,
                field_path,
                safe_message,
            )
            await self._mark_terminal(child_run.id, "failed", error_type, safe_message)
            await self._emit_agent_event(
                context,
                "content.agent.failed",
                {
                    "agent_slug": agent.slug,
                    "error_type": error_type,
                    "error_field_path": field_path,
                    "duration_ms": int((time.monotonic() - started_at) * 1000),
                },
            )
            raise

        await self.run_repo.set_terminal_status(child_run.id, status="completed")
        await self.db.commit()
        await self._emit_agent_event(
            context,
            "content.agent.completed",
            {
                "agent_slug": agent.slug,
                "duration_ms": int((time.monotonic() - started_at) * 1000),
                "output_preview": build_execution_preview(output),
            },
        )
        return AgentDelegationResult(child_run.id, output, runtime_snapshot)

    async def _resolve_agent(self, request: AgentDelegationRequest):
        agent = await self.agent_repo.get_by_slug(request.agent_slug)
        if agent is None:
            raise ContentApplicationError("agent_not_found", f"Agent 不存在: {request.agent_slug}", "not_found")
        if not agent.enabled:
            raise ContentApplicationError("agent_disabled", f"Agent 已停用: {request.agent_slug}", "conflict")
        if not user_can_access_agent(request.user, agent):
            raise ContentApplicationError("agent_forbidden", f"无权访问 Agent: {request.agent_slug}", "not_found")
        try:
            backend = agent_manager.get_agent(agent.backend_id)
        except KeyError as exc:
            raise ContentApplicationError(
                "agent_backend_unavailable",
                f"Agent 后端不可用: {agent.backend_id}",
                "conflict",
            ) from exc
        return agent, backend

    @staticmethod
    def _apply_node_constraints(context, request: AgentDelegationRequest) -> None:
        if request.knowledge_policy == "none" or request.knowledge_policy == "frozen_evidence_only":
            context.knowledges = []
        elif request.knowledge_policy == "agent_scope":
            if request.node_run.node_id not in AgentDelegationService.KNOWLEDGE_NODE_IDS:
                raise ContentApplicationError(
                    "knowledge_node_forbidden",
                    f"节点 {request.node_run.node_id} 不允许检索知识库",
                    "invalid",
                )
            if context.knowledges and os.environ.get("LITE_MODE", "").lower() in {"true", "1"}:
                raise ContentApplicationError(
                    "knowledge_capability_unavailable",
                    "LITE_MODE 不支持 Agent 已配置的知识库检索",
                    "conflict",
                )
        else:
            raise ContentApplicationError("knowledge_policy_invalid", "Agent 节点知识库策略无效", "invalid")
        if hasattr(context, "max_execution_steps"):
            configured = getattr(context, "max_execution_steps", request.max_execution_steps)
            current = int(configured or request.max_execution_steps)
            context.max_execution_steps = min(current, request.max_execution_steps)

    @classmethod
    def _apply_knowledge_tool_scope(cls, context) -> None:
        if getattr(context, "knowledges", None):
            return
        context._required_skill_tools = [
            name for name in getattr(context, "_required_skill_tools", []) or [] if name not in cls.KNOWLEDGE_TOOL_NAMES
        ]

    @classmethod
    def _restrict_research_knowledge_scope(cls, context, node_id: str) -> None:
        allowed_names = cls.RESEARCH_KNOWLEDGE_NAMES.get(node_id)
        if allowed_names is None:
            return
        visible = [
            item
            for item in (getattr(context, "_visible_knowledge_bases", []) or [])
            if isinstance(item, dict) and item.get("name") in allowed_names
        ]
        context._visible_knowledge_bases = visible
        context.knowledges = [str(item["kb_id"]) for item in visible if item.get("kb_id")]

    @staticmethod
    async def _invoke_graph(
        graph,
        context,
        request: AgentDelegationRequest,
        node_input: ContentAgentNodeInputV2 | None = None,
    ) -> dict[str, Any]:
        prompt = request.prompt
        if node_input is not None:
            prompt = json.dumps(
                node_input.model_dump(
                    mode="json",
                    exclude={"output_json_schema", "runtime_config_snapshot"},
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        invocation = asyncio.create_task(
            graph.ainvoke(
                {"messages": [prompt]},
                context=context,
                config={
                    "configurable": {"thread_id": context.thread_id, "uid": context.uid},
                    "recursion_limit": request.max_execution_steps,
                },
            )
        )
        cancel_waiter = asyncio.create_task(request.cancel_event.wait()) if request.cancel_event else None
        try:
            wait_set = {invocation}
            if cancel_waiter is not None:
                wait_set.add(cancel_waiter)
            done, _ = await asyncio.wait(wait_set, timeout=request.timeout_seconds, return_when=asyncio.FIRST_COMPLETED)
            if invocation in done:
                return invocation.result()
            invocation.cancel()
            await asyncio.gather(invocation, return_exceptions=True)
            if cancel_waiter is not None and cancel_waiter in done:
                raise asyncio.CancelledError
            raise TimeoutError(f"Agent 节点执行超时（{request.timeout_seconds}s）")
        finally:
            if cancel_waiter is not None:
                cancel_waiter.cancel()
                await asyncio.gather(cancel_waiter, return_exceptions=True)

    async def _mark_terminal(self, run_id: str, status: str, error_type: str, error_message: str) -> None:
        await self.run_repo.set_terminal_status(
            run_id,
            status=status,
            error_type=error_type,
            error_message=error_message,
        )
        await self.db.commit()

    @staticmethod
    async def _emit_agent_event(context, event_type: str, payload: dict[str, Any]) -> None:
        common = {
            "task_id": context._content_task_id,
            "parent_run_id": context._content_parent_run_id,
            "node_id": context._content_node_id,
            "attempt": context._content_node_attempt,
            "delegated_agent_run_id": context._content_delegated_agent_run_id,
            **payload,
        }
        await append_run_stream_event(
            context.run_id,
            event_type,
            common,
            thread_id=context.thread_id,
        )
        await append_run_stream_event(
            context._content_parent_run_id,
            event_type,
            common,
            thread_id=context._content_parent_thread_id,
        )

    async def _record_node_failure(
        self,
        node_run: ContentNodeRun,
        delegated_run_id: str,
        error_type: str,
        field_path: str,
        error_message: str,
    ) -> None:
        await self.content_repo.finish_node_run(
            node_run,
            status="cancelled" if error_type == "cancelled" else "failed",
            output_snapshot={
                "delegated_agent_run_id": delegated_run_id,
                "error_field_path": field_path,
            },
            error_type=error_type,
            error_message=error_message,
        )

    @staticmethod
    def _safe_error_details(exc: Exception) -> tuple[str, str, str]:
        if isinstance(exc, ContractDomainValidationError):
            return exc.code, exc.field_path, str(exc)
        if isinstance(exc, ValidationError):
            first = exc.errors()[0] if exc.errors() else {}
            field_path = ".".join(str(item) for item in first.get("loc") or [])
            return "contract_validation_error", field_path, str(first.get("msg") or "结枔契约校验失败")
        if isinstance(exc, ContentApplicationError):
            return exc.code, "", exc.message
        return type(exc).__name__, "", str(exc)


__all__ = [
    "AgentDelegationRequest",
    "AgentDelegationResult",
    "AgentDelegationService",
    "build_runtime_config_snapshot",
]
