"""知识库工具模块"""

import inspect
from typing import Any

from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field

from yuxi.agents.toolkits.registry import tool
from yuxi.knowledge.base import KnowledgeBase
from yuxi.knowledge.schemas import (
    FindInputSchema,
    FindOutputSchema,
    OpenInputSchema,
    OpenOutputSchema,
    SearchInputSchema,
    SearchOutputSchema,
)
from yuxi.utils import logger

# ========== 通用知识库工具函数 ==========


def _get_knowledge_base():
    from yuxi import knowledge_base

    return knowledge_base


class ListKBsInput(BaseModel):
    """列出用户可访问的知识库输入模型"""

    # Langchain 的 runtime 注入机制要求必须有参数
    dummy: str = Field(default="", description="Dummy parameter - ignore")  # Add this


@tool(category="knowledge", tags=["知识库"], args_schema=ListKBsInput)
async def list_kbs(dummy: str, runtime: ToolRuntime) -> str:  # Now has 2 params
    """列出当前用户可访问的知识库列表

    返回用户基于权限可访问的知识库名称列表。这个列表是根据用户的角色和部门信息过滤后的结果，
    但不包括用户在当前对话中未启用的知识库。

    Returns:
        用户可访问的知识库名称列表（字符串格式）
    """
    # 从 runtime.context 获取用户信息
    runtime_context = runtime.context
    uid = getattr(runtime_context, "uid", None)
    if not uid:
        return "无法获取用户信息"

    # 打印 runtime—context 中的所有信息以进行调试
    logger.debug(f"Runtime context: {runtime_context.__dict__}")

    enabled_kb_names = getattr(runtime_context, "knowledges", None)

    try:
        from yuxi.agents.backends.knowledge_base_backend import resolve_visible_knowledge_bases_for_context

        available_kbs = await resolve_visible_knowledge_bases_for_context(runtime_context)
    except Exception as e:
        logger.error(f"获取用户知识库列表失败: {e}")
        return f"获取知识库列表失败: {str(e)}"

    all_kb_names = [kb["name"] for kb in available_kbs]

    logger.debug(f"用户 {uid} 可访问的知识库列表: {all_kb_names}")
    logger.debug(f"用户 {uid} 当前对话启用的知识库列表: {enabled_kb_names}")

    if not available_kbs:
        return "当前没有可访问的知识库"

    # 格式化输出（包含名称和描述）
    kb_list = []
    for kb in available_kbs:
        name = kb.get("name", "")
        desc = kb.get("description") or "无描述"
        kb_list.append({"kb_id": kb.get("kb_id"), "name": name, "description": desc})

    return kb_list


class GetMindmapInput(BaseModel):
    """获取思维导图输入模型"""

    kb_name: str = Field(description="知识库名称，用于指定要获取思维导图的知识库")


@tool(category="knowledge", tags=["知识库"], args_schema=GetMindmapInput)
async def get_mindmap(kb_name: str, runtime: ToolRuntime) -> str:
    """获取指定知识库的思维导图结构

    当用户想要了解知识库的整体结构、文件分类、知识架构时使用此工具。
    返回知识库的思维导图层级结构。

    Args:
        kb_name: 知识库名称

    Returns:
        知识库的思维导图结构（文本格式）
    """
    if not kb_name:
        return "请提供知识库名称"

    # 获取所有检索器
    knowledge_base = _get_knowledge_base()
    retrievers = knowledge_base.get_retrievers()

    # 查找对应的知识库
    target_kb_id = None
    target_info = None
    for kb_id, info in retrievers.items():
        if info["name"] == kb_name:
            target_kb_id = kb_id
            target_info = info
            break

    if not target_kb_id:
        return f"知识库 '{kb_name}' 不存在"

    try:
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        kb = await kb_repo.get_by_kb_id(target_kb_id)

        if kb is None:
            return f"知识库 {target_info['name']} 不存在"

        mindmap_data = kb.mindmap

        if not mindmap_data:
            return f"知识库 {target_info['name']} 还没有生成思维导图。"

        # 将思维导图数据转换为文本格式
        def mindmap_to_text(node, level=0):
            """递归将思维导图JSON转换为层级文本"""
            indent = "  " * level
            text = f"{indent}- {node.get('content', '')}\n"
            for child in node.get("children", []):
                text += mindmap_to_text(child, level + 1)
            return text

        mindmap_text = f"知识库 {target_info['name']} 的思维导图结构：\n\n"
        mindmap_text += mindmap_to_text(mindmap_data)

        return mindmap_text

    except Exception as e:
        logger.error(f"获取思维导图失败: {e}")
        return f"获取思维导图失败: {str(e)}"


QueryKBInput = SearchInputSchema
OpenKBDocumentInput = OpenInputSchema
FindKBDocumentInput = FindInputSchema


async def _resolve_visible_knowledge_bases_for_query(runtime: ToolRuntime | None) -> list[dict[str, Any]]:
    if runtime is None:
        return []

    context = getattr(runtime, "context", None)
    if context is None:
        return []

    visible_kbs = getattr(context, "_visible_knowledge_bases", None)
    if isinstance(visible_kbs, list):
        return visible_kbs

    try:
        from yuxi.agents.backends.knowledge_base_backend import resolve_visible_knowledge_bases_for_context

        return await resolve_visible_knowledge_bases_for_context(context)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"解析会话可见知识库失败: {exc}")
        return []


def _find_query_target(
    *,
    kb_id: str,
    retrievers: dict[str, Any],
    visible_kbs: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    if not visible_kbs:
        return None, None, "无法获取当前会话可访问的知识库"

    normalized_kb_id = str(kb_id or "").strip()
    visible_kb_ids = {str(kb.get("kb_id") or "").strip() for kb in visible_kbs}
    if normalized_kb_id not in visible_kb_ids:
        return None, None, f"知识库资源 '{normalized_kb_id}' 不存在或当前会话未启用"

    target_info = retrievers.get(normalized_kb_id)
    if target_info is None:
        return None, None, f"知识库资源 '{normalized_kb_id}' 不存在"
    return target_info, normalized_kb_id, None


def _attach_content_knowledge_provenance(
    runtime: ToolRuntime | None,
    *,
    kb_id: str,
    kb_name: str,
    output: dict[str, Any],
) -> None:
    context = getattr(runtime, "context", None)
    if context is None or not getattr(context, "_content_node_output_contract", None):
        return

    retrieved = dict(getattr(context, "_content_retrieved_knowledge_results", {}) or {})
    for item in output.get("results") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        metadata = dict(item.get("metadata") or {})
        metadata.update(
            {
                "knowledge_base_id": kb_id,
                "knowledge_base_name": kb_name,
                "document_id": str(item.get("file_id") or ""),
                "document_name": str(metadata.get("source") or item.get("file_id") or ""),
                "chunk_id": str(item["id"]),
            }
        )
        item["metadata"] = metadata
        records = list(retrieved.get(str(item["id"])) or [])
        record = {
            "source_id": str(item["id"]),
            "content": str(item.get("content") or ""),
            "metadata": metadata,
        }
        if record not in records:
            records.append(record)
        retrieved[str(item["id"])] = records
    context._content_retrieved_knowledge_results = retrieved


async def _emit_content_knowledge_event(
    runtime: ToolRuntime | None,
    kb_id: str,
    query_text: str,
    output: dict[str, Any],
) -> None:
    context = getattr(runtime, "context", None)
    run_id = str(getattr(context, "run_id", "") or "").strip()
    if not run_id or not getattr(context, "_content_node_output_contract", None):
        return
    results = output.get("results") if isinstance(output.get("results"), list) else []
    source_ids = [
        str(item.get("id") or item.get("file_id"))
        for item in results
        if isinstance(item, dict) and (item.get("id") or item.get("file_id"))
    ]
    from yuxi.services.run_queue_service import append_content_runtime_event
    from yuxi.content.execution_trace import build_knowledge_result_preview

    await append_content_runtime_event(
        context,
        "content.knowledge.retrieved",
        {
            "knowledge_base_id": kb_id,
            "query_text": query_text,
            "source_ids": source_ids,
            "result_count": len(results),
            "results": build_knowledge_result_preview(results),
        },
    )
    await append_content_runtime_event(
        context,
        "content.tool.completed",
        {"tool_name": "query_kb", "knowledge_base_id": kb_id, "result_count": len(results)},
    )


async def _reject_content_knowledge_query(
    runtime: ToolRuntime | None,
    *,
    kb_id: str,
    reason_code: str,
    limit: int,
    used: int,
    message: str,
) -> str:
    context = getattr(runtime, "context", None)
    if context is not None:
        context._content_force_result_submission_reason = reason_code
    run_id = str(getattr(context, "run_id", "") or "").strip()
    if run_id and getattr(context, "_content_node_output_contract", None):
        from yuxi.services.run_queue_service import append_content_runtime_event

        await append_content_runtime_event(
            context,
            "content.tool.rejected",
            {
                "tool_name": "query_kb",
                "knowledge_base_id": kb_id,
                "reason_code": reason_code,
                "limit": limit,
                "used": used,
            },
        )
    return message


@tool(category="knowledge", tags=["知识库"], args_schema=QueryKBInput)
async def query_kb(kb_id: str, query_text: str, file_name: str | None = None, runtime: ToolRuntime = None) -> Any:
    """在指定知识库中检索内容

    当用户需要查询具体内容时使用此工具。kb_id 是知识库资源 ID，也就是 kb_id；返回结果中的
    file_id 可继续用于 find_kb_document 或 open_kb_document。
    """
    if not kb_id:
        return "请提供 kb_id"
    if not query_text:
        return "请提供查询内容"

    context = getattr(runtime, "context", None)
    knowledge_base = _get_knowledge_base()
    retrievers = knowledge_base.get_retrievers()
    visible_kbs = await _resolve_visible_knowledge_bases_for_query(runtime)
    target_info, target_kb_id, target_error = _find_query_target(
        kb_id=kb_id,
        retrievers=retrievers,
        visible_kbs=visible_kbs,
    )
    if target_error:
        return target_error

    if context is not None and getattr(context, "_content_node_output_contract", None):
        rounds = int(getattr(context, "_content_retrieval_rounds_used", 0) or 0)
        maximum_rounds = int(getattr(context, "_content_max_retrieval_rounds", 0) or 0)
        if maximum_rounds and rounds >= maximum_rounds:
            return await _reject_content_knowledge_query(
                runtime,
                kb_id=target_kb_id,
                reason_code="retrieval_round_limit_reached",
                limit=maximum_rounds,
                used=rounds,
                message=(
                    f"本节点知识检索轮次预算已用完（{rounds}/{maximum_rounds}），本次查询未执行。"
                    "请停止继续检索，基于已有结果调用 submit_content_node_result；"
                    "证据不足时按 Skill 要求写入 unresolved_questions。"
                ),
            )
        queried = set(getattr(context, "_content_queried_knowledge_bases", set()) or set())
        maximum_bases = int(getattr(context, "_content_max_knowledge_bases", 0) or 0)
        if target_kb_id not in queried and maximum_bases and len(queried) >= maximum_bases:
            return await _reject_content_knowledge_query(
                runtime,
                kb_id=target_kb_id,
                reason_code="knowledge_base_limit_reached",
                limit=maximum_bases,
                used=len(queried),
                message=(
                    f"本节点不同知识库预算已用完（{len(queried)}/{maximum_bases}），本次查询未执行。"
                    "请只使用已检索结果调用 submit_content_node_result；"
                    "证据不足时按 Skill 要求写入 unresolved_questions。"
                ),
            )
        queried.add(target_kb_id)
        context._content_retrieval_rounds_used = rounds + 1
        context._content_queried_knowledge_bases = queried
        if maximum_rounds and rounds + 1 >= maximum_rounds:
            context._content_force_result_submission_reason = "retrieval_round_limit_reached"

    if context is not None and getattr(context, "_content_node_output_contract", None):
        from yuxi.services.run_queue_service import append_content_runtime_event

        await append_content_runtime_event(
            context,
            "content.tool.called",
            {"tool_name": "query_kb", "knowledge_base_id": kb_id, "input_preview": {"query_text": query_text}},
        )
    try:
        retriever = target_info["retriever"]
        kwargs = {}
        if file_name:
            kwargs["file_name"] = file_name

        if inspect.iscoroutinefunction(retriever):
            result = await retriever(query_text, **kwargs)
        else:
            result = retriever(query_text, **kwargs)

        if isinstance(result, dict) and result.get("kb_id") == target_kb_id and isinstance(result.get("results"), list):
            output = SearchOutputSchema(**result).model_dump()
        else:
            output = KnowledgeBase.build_search_output(target_kb_id, result)
        maximum_chunks = int(getattr(context, "_content_max_chunks_per_knowledge_base", 0) or 0)
        if maximum_chunks and isinstance(output.get("results"), list):
            output["results"] = output["results"][:maximum_chunks]
        maximum_chars = int(getattr(context, "_content_max_chars_per_knowledge_chunk", 0) or 0)
        if maximum_chars and isinstance(output.get("results"), list):
            for item in output["results"]:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if isinstance(content, str) and len(content) > maximum_chars:
                    item["content"] = content[:maximum_chars]
        _attach_content_knowledge_provenance(
            runtime,
            kb_id=target_kb_id,
            kb_name=str(target_info.get("name") or target_kb_id),
            output=output,
        )
        await _emit_content_knowledge_event(runtime, target_kb_id, query_text, output)
        return output

    except Exception as e:
        logger.error(f"检索失败: {e}")
        return f"检索失败: {str(e)}"


@tool(category="knowledge", tags=["知识库"], args_schema=OpenKBDocumentInput)
async def open_kb_document(
    kb_id: str,
    file_id: str,
    line: int | None = None,
    offset: int | None = None,
    window_size: int = 1800,
    runtime: ToolRuntime = None,
) -> dict[str, Any] | str:
    """按行窗口打开知识库文档原文

    当 query_kb 返回的片段不足以回答问题，或需要查看某个文档的上下文时使用。
    kb_id 是知识库资源 ID，也就是 kb_id；file_id 是知识库文件 ID。
    """
    normalized_kb_id = str(kb_id or "").strip()
    normalized_file_id = str(file_id or "").strip()
    if not normalized_kb_id:
        return "请提供 kb_id"
    if not normalized_file_id:
        return "请提供 file_id"

    visible_kbs = await _resolve_visible_knowledge_bases_for_query(runtime)
    if not visible_kbs:
        return "无法获取当前会话可访问的知识库"

    visible_kb_ids = {str(kb.get("kb_id") or "").strip() for kb in visible_kbs}
    if normalized_kb_id not in visible_kb_ids:
        return f"知识库资源 '{normalized_kb_id}' 不存在或当前会话未启用"

    knowledge_base = _get_knowledge_base()
    retrievers = knowledge_base.get_retrievers()
    target_info = retrievers.get(normalized_kb_id)
    if target_info is None:
        return f"知识库资源 '{normalized_kb_id}' 不存在"

    metadata = target_info.get("metadata") if isinstance(target_info, dict) else None
    kb_type = str((metadata or {}).get("kb_type") or "").strip().lower()
    if kb_type == "dify":
        return "Dify 知识库为外部只读检索源，当前不支持通过 Open 打开全文"

    try:
        start_offset = int(line) - 1 if line is not None else int(offset or 0)
        window = await knowledge_base.open_file_content(
            normalized_kb_id,
            normalized_file_id,
            offset=start_offset,
            limit=window_size,
        )
        return OpenOutputSchema(kb_id=normalized_kb_id, file_id=normalized_file_id, **window).model_dump()

    except Exception as e:
        logger.error(f"打开知识库文档失败: {e}")
        return f"打开知识库文档失败: {str(e)}"


@tool(category="knowledge", tags=["知识库"], args_schema=FindKBDocumentInput)
async def find_kb_document(
    kb_id: str,
    file_id: str,
    patterns: list[str],
    use_regex: bool = False,
    case_sensitive: bool = False,
    max_windows: int = 5,
    window_size: int = 80,
    runtime: ToolRuntime = None,
) -> dict[str, Any] | str:
    """在已知知识库文件内做关键词或正则定位。

    当 query_kb 已找到候选文件，但需要在该文件内定位术语、指标、章节或实体时使用。
    """
    normalized_kb_id = str(kb_id or "").strip()
    normalized_file_id = str(file_id or "").strip()
    if not normalized_kb_id:
        return "请提供 kb_id"
    if not normalized_file_id:
        return "请提供 file_id"
    if not patterns:
        return "请提供 patterns"

    visible_kbs = await _resolve_visible_knowledge_bases_for_query(runtime)
    if not visible_kbs:
        return "无法获取当前会话可访问的知识库"

    visible_kb_ids = {str(kb.get("kb_id") or "").strip() for kb in visible_kbs}
    if normalized_kb_id not in visible_kb_ids:
        return f"知识库资源 '{normalized_kb_id}' 不存在或当前会话未启用"

    knowledge_base = _get_knowledge_base()
    retrievers = knowledge_base.get_retrievers()
    target_info = retrievers.get(normalized_kb_id)
    if target_info is None:
        return f"知识库资源 '{normalized_kb_id}' 不存在"

    metadata = target_info.get("metadata") if isinstance(target_info, dict) else None
    kb_type = str((metadata or {}).get("kb_type") or "").strip().lower()
    if kb_type == "dify":
        return "Dify 知识库为外部只读检索源，当前不支持通过 Find 检索全文"

    try:
        result = await knowledge_base.find_file_content(
            normalized_kb_id,
            normalized_file_id,
            patterns,
            use_regex=use_regex,
            case_sensitive=case_sensitive,
            max_windows=max_windows,
            window_size=window_size,
        )
        return FindOutputSchema(kb_id=normalized_kb_id, file_id=normalized_file_id, **result).model_dump()
    except Exception as e:
        logger.error(f"知识库文档内检索失败: {e}")
        return f"知识库文档内检索失败: {str(e)}"


def get_common_kb_tools() -> list:
    """获取通用知识库工具列表

    返回 5 个通用工具：
    - list_kbs: 列出用户可访问的知识库
    - get_mindmap: 获取指定知识库的思维导图
    - query_kb: 在指定知识库中检索
    - find_kb_document: 在指定文件内定位关键词或正则模式
    - open_kb_document: 按 file_id 分段打开知识库文档
    """
    return [list_kbs, get_mindmap, query_kb, find_kb_document, open_kb_document]
