"""Detect stalled model calls while allowing continuous streaming output."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.utils.function_calling import convert_to_openai_tool
from openai import APIConnectionError, InternalServerError, RateLimitError

from yuxi.agents.middlewares.token_usage import ContentTokenBudgetExceeded
from yuxi.services.run_queue_service import append_content_runtime_event

from yuxi.utils import logger


class ModelExecutionBudgetExceeded(ContentTokenBudgetExceeded):
    """单个受控内容节点共用的调用、输入和时间预算已经耗尽。"""


def retryable_content_model_error(exc: Exception) -> bool:
    return isinstance(exc, (TimeoutError, ConnectionError, APIConnectionError, InternalServerError, RateLimitError))


class ContentModelProgress(AsyncCallbackHandler):
    """仅记录输出进展的时间与计数，不记录正文或推理内容。"""

    def __init__(self, context):
        self.context = context

    async def on_llm_new_token(self, token: str, **kwargs) -> None:
        progress = getattr(self.context, "_content_model_progress", None)
        if progress is None:
            return
        chunk = kwargs.get("chunk")
        tool_chunks = getattr(getattr(chunk, "message", None), "tool_call_chunks", None)
        meaningful_tool_output = any(item.get("args") or item.get("name") for item in tool_chunks or [])
        if not token and not meaningful_tool_output:
            return
        now = time.monotonic()
        progress["last_progress_at"] = now
        node_idle_timeout = getattr(self.context, "_content_node_idle_timeout", None)
        if node_idle_timeout is not None:
            self.context._content_node_deadline = now + node_idle_timeout
        progress["chunks"] += 1
        progress["last_progress_ms"] = int((now - progress["started"]) * 1000)
        if progress["chunks"] == 1:
            progress["first_progress_ms"] = progress["last_progress_ms"]
            await append_content_runtime_event(
                self.context,
                "content.model.progress",
                {
                    "call_number": progress["call_number"],
                    "first_progress_ms": progress["first_progress_ms"],
                    "message": "模型已开始返回内容",
                },
            )


class ModelCallTimeoutMiddleware(AgentMiddleware):
    """受控流式节点按无输出时长计时，其他调用保留原有总时限。"""

    def __init__(self, timeout_seconds: float):
        if timeout_seconds <= 0:
            raise ValueError("模型单次调用超时必须大于 0")
        self.timeout_seconds = timeout_seconds

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        started = time.monotonic()
        context = request.runtime.context
        node_id = getattr(context, "_content_node_id", None)
        node_label = "策略选择" if node_id in {"select_creation_strategy", "reselect_creation_strategy"} else "正文生成"
        if node_id == "semantic_review":
            node_label = "内容表情与语义审核"
        controlled = bool(getattr(context, "_content_max_model_calls", None))
        call_number = int(getattr(context, "_content_model_calls", 0)) + 1
        timeout = self.timeout_seconds
        if controlled:
            if call_number > context._content_max_model_calls:
                raise ModelExecutionBudgetExceeded(
                    f"{node_label}已用完两次模型调用额度（连接重试与结果纠错共用），请检查失败明细"
                )
            output_limit = min(
                context._content_node_token_budget // context._content_max_model_calls,
                context._content_node_token_budget - int(getattr(context, "_content_node_tokens_used", 0)),
            )
            if output_limit <= 0:
                raise ModelExecutionBudgetExceeded(f"{node_label}输出预算已耗尽")
            request = request.override(model_settings={**request.model_settings, "max_tokens": output_limit})
        input_chars = sum(len(str(message.content)) for message in getattr(request, "messages", []) or [])
        system_chars = len(str(getattr(getattr(request, "system_message", None), "content", "")))
        schema_chars = len(
            json.dumps(
                [convert_to_openai_tool(tool) for tool in getattr(request, "tools", []) or []],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        if controlled and input_chars + system_chars > 100_000:
            raise ModelExecutionBudgetExceeded(
                f"{node_label}模型输入超过 100000 字符，请检查证据与规则体积；系统未截断事实"
            )
        if context is not None:
            context._content_model_calls = call_number
            context._content_model_progress = {"started": started, "call_number": call_number, "chunks": 0}
        if node_id:
            await append_content_runtime_event(
                context,
                "content.model.started",
                {
                    "call_number": call_number,
                    "input_chars": input_chars,
                    "system_chars": system_chars,
                    "tool_schema_chars": schema_chars,
                    "tool_schema_measurement": "openai_tool",
                    "reasoning_effort": getattr(request.model, "reasoning_effort", None),
                    "timeout_seconds": timeout,
                    "timeout_mode": "idle" if controlled else "total",
                    "applied_skills": getattr(context, "_content_applied_skill_instructions", {}),
                    "message": (
                        f"正在执行{node_label}" if call_number == 1 else "正在恢复或修正当前节点，已保留上游结果"
                    ),
                },
            )
        if node_id:
            logger.info(
                "Content model call started: node={}, reasoning={}, timeout={}s, input_chars={}, tools={}",
                node_id,
                getattr(request.model, "reasoning_effort", None),
                timeout,
                sum(len(str(message.content)) for message in request.messages),
                len(request.tools or []),
            )
        status = "completed"
        error_type = None
        invocation = None
        try:
            if not controlled:
                return await asyncio.wait_for(handler(request), timeout=timeout)
            invocation = asyncio.create_task(handler(request))
            while True:
                progress = context._content_model_progress
                remaining = timeout - (time.monotonic() - progress.get("last_progress_at", started))
                done, _ = await asyncio.wait({invocation}, timeout=max(0, remaining))
                if invocation in done:
                    return invocation.result()
                if time.monotonic() - progress.get("last_progress_at", started) >= timeout:
                    phase = "输出停滞" if progress["chunks"] else "等待模型首个输出时无输出"
                    raise TimeoutError(f"{phase}超过 {timeout:g}s")
        except TimeoutError as exc:
            status, error_type = "timeout", type(exc).__name__
            if controlled:
                raise
            raise TimeoutError(f"模型单次调用超时（{timeout:g}s）") from exc
        except BaseException as exc:
            status, error_type = (
                ("cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"),
                type(exc).__name__,
            )
            raise
        finally:
            if invocation is not None and not invocation.done():
                invocation.cancel()
                await asyncio.gather(invocation, return_exceptions=True)
            if node_id:
                logger.info("Content model call ended: node={}, duration={:.2f}s", node_id, time.monotonic() - started)
                progress = getattr(context, "_content_model_progress", {})
                await append_content_runtime_event(
                    context,
                    "content.model.completed",
                    {
                        "call_number": call_number,
                        "status": status,
                        "error_type": error_type,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                        "first_progress_ms": progress.get("first_progress_ms"),
                        "last_progress_ms": progress.get("last_progress_ms"),
                        "chunks": progress.get("chunks", 0),
                    },
                )
