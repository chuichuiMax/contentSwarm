"""Skills 中间件 - 处理 skills 提示词注入、依赖展开、动态激活"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from pathlib import PurePosixPath
from typing import Annotated, Any, NotRequired, TypedDict

from deepagents.middleware._utils import append_to_system_message
from deepagents.middleware.skills import SKILLS_SYSTEM_PROMPT
from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langchain.tools.tool_node import ToolCallRequest
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.mcp.service import get_enabled_mcp_tools
from yuxi.agents.skills.repository import SkillRepository
from yuxi.agents.skills.service import (
    is_valid_skill_slug,
    list_accessible_skills,
    normalize_string_list,
    user_can_access_skill,
)
from yuxi.agents.toolkits import get_all_tool_instances
from yuxi import config as sys_config
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.logging_config import logger

# =============================================================================
# 类型定义
# =============================================================================


class SkillPromptMetadata(TypedDict):
    name: str
    description: str
    path: str
    instructions: str
    version: str
    content_hash: str


class SkillDependencyNode(TypedDict):
    tools: list[str]
    mcps: list[str]
    skills: list[str]


# =============================================================================
# 运行时数据加载函数
# =============================================================================


async def _list_skills_from_db(db: AsyncSession | None = None, user=None) -> list:
    """从数据库加载 skills 列表"""
    if db is not None:
        if user is not None:
            return await list_accessible_skills(db, user)
        repo = SkillRepository(db)
        return await repo.list_enabled()

    async with pg_manager.get_async_session_context() as session:
        if user is not None:
            return await list_accessible_skills(session, user)
        repo = SkillRepository(session)
        return await repo.list_enabled()


def build_prompt_metadata(skills: list) -> dict[str, SkillPromptMetadata]:
    def read_instructions(item) -> str:
        explicit = getattr(item, "instructions", None)
        if isinstance(explicit, str):
            return explicit.strip()
        raw_path = getattr(item, "dir_path", None)
        if not isinstance(raw_path, str) or not raw_path.strip():
            return ""
        directory = Path(raw_path)
        if not directory.is_absolute():
            directory = Path(sys_config.save_dir) / directory
        try:
            return (directory / "SKILL.md").read_text(encoding="utf-8").strip()
        except (FileNotFoundError, IsADirectoryError, OSError):
            return ""

    return {
        item.slug: {
            "name": item.name,
            "description": item.description,
            "path": f"/home/gem/skills/{item.slug}/SKILL.md",
            "instructions": read_instructions(item),
            "version": str(getattr(item, "version", None) or "unversioned"),
            "content_hash": str(getattr(item, "content_hash", None) or ""),
        }
        for item in skills
        if item.slug
    }


def build_dependency_map(skills: list) -> dict[str, SkillDependencyNode]:
    result: dict[str, SkillDependencyNode] = {}
    for item in skills:
        if not item.slug:
            continue
        result[item.slug] = {
            "tools": normalize_string_list(item.tool_dependencies or []),
            "mcps": normalize_string_list(item.mcp_dependencies or []),
            "skills": normalize_string_list(item.skill_dependencies or []),
        }
    return result


async def get_prompt_metadata(db: AsyncSession | None = None, user=None) -> dict[str, SkillPromptMetadata]:
    """获取提示词元数据（直接从数据库加载）"""
    return build_prompt_metadata(await _list_skills_from_db(db, user))


async def get_dependency_map(db: AsyncSession | None = None, user=None) -> dict[str, SkillDependencyNode]:
    """获取依赖关系映射（直接从数据库加载）"""
    return build_dependency_map(await _list_skills_from_db(db, user))


def expand_skill_closure(
    slugs: list[str] | None,
    dependency_map: dict[str, SkillDependencyNode],
) -> list[str]:
    """展开 skills 依赖闭包，返回包含所有依赖的列表"""
    ordered_roots = normalize_string_list(slugs)
    if not ordered_roots:
        return []

    result: list[str] = []
    seen: set[str] = set()

    def dfs(slug: str, stack: set[str]) -> None:
        if slug in stack:
            logger.warning(f"Cycle detected in skill dependencies, skip: {' -> '.join([*stack, slug])}")
            return
        if slug in seen:
            return

        node = dependency_map.get(slug)
        if not node:
            logger.warning(f"Skill dependency target not found in DB, skip: {slug}")
            return

        seen.add(slug)
        result.append(slug)
        next_stack = set(stack)
        next_stack.add(slug)
        for dep in node.get("skills", []):
            dfs(dep, next_stack)

    for root in ordered_roots:
        dfs(root, set())
    return result


class RequiredSkillResolutionError(ValueError):
    """必需 Skill 无法在当前 Agent 权限内完整激活。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def expand_required_skill_closure(
    slugs: list[str] | None,
    dependency_map: dict[str, SkillDependencyNode],
) -> list[str]:
    """严格展开 required Skills；任何缺失或循环都必须显式失败。"""

    result: list[str] = []
    seen: set[str] = set()

    def dfs(slug: str, stack: tuple[str, ...]) -> None:
        if slug in stack:
            cycle = " -> ".join((*stack, slug))
            raise RequiredSkillResolutionError("required_skill_cycle", f"Skill 依赖存在循环: {cycle}")
        if slug in seen:
            return
        node = dependency_map.get(slug)
        if node is None:
            raise RequiredSkillResolutionError("required_skill_missing", f"必需 Skill 不存在或不可访问: {slug}")
        seen.add(slug)
        result.append(slug)
        for dependency in node.get("skills", []):
            dfs(dependency, (*stack, slug))

    for root in normalize_string_list(slugs):
        dfs(root, ())
    return result


async def resolve_runtime_skills_for_context(context, *, db: AsyncSession | None = None, user=None) -> dict[str, Any]:
    skill_items = await _list_skills_from_db(db, user)
    dependency_map = build_dependency_map(skill_items)
    prompt_metadata = build_prompt_metadata(skill_items)
    available = set(dependency_map)
    selected = normalize_string_list(getattr(context, "skills", None))
    context_skills = [slug for slug in selected if slug in available]
    required_roots = normalize_string_list(getattr(context, "required_skills", None))
    if required_roots and db is not None and user is not None:
        all_items = await SkillRepository(db).list_all()
        all_by_slug = {item.slug: item for item in all_items if item.slug}
        inspected: set[str] = set()

        def validate_access(slug: str) -> None:
            if slug in inspected:
                return
            inspected.add(slug)
            item = all_by_slug.get(slug)
            if item is None:
                raise RequiredSkillResolutionError("required_skill_missing", f"必需 Skill 不存在: {slug}")
            if not item.enabled:
                raise RequiredSkillResolutionError("required_skill_disabled", f"必需 Skill 已停用: {slug}")
            if not user_can_access_skill(user, item):
                raise RequiredSkillResolutionError("required_skill_unauthorized", f"无权使用必需 Skill: {slug}")
            for dependency in normalize_string_list(item.skill_dependencies or []):
                validate_access(dependency)

        for slug in required_roots:
            validate_access(slug)
    required_closure = expand_required_skill_closure(required_roots, dependency_map)
    if required_roots:
        unauthorized = [slug for slug in required_roots if slug not in context_skills]
        if unauthorized:
            raise RequiredSkillResolutionError(
                "required_skill_unauthorized",
                f"Agent 未授权必需 Skill: {', '.join(unauthorized)}",
            )
    prompt_skills = expand_skill_closure(context_skills, dependency_map)
    for slug in required_closure:
        if slug not in prompt_skills:
            prompt_skills.append(slug)

    missing_instructions = [slug for slug in required_closure if not prompt_metadata[slug]["instructions"]]
    if missing_instructions:
        raise RequiredSkillResolutionError(
            "required_skill_instructions_unavailable",
            f"必需 Skill 的 SKILL.md 不可读: {', '.join(missing_instructions)}",
        )

    required_tools: list[str] = []
    required_mcps: list[str] = []
    for slug in required_closure:
        node = dependency_map[slug]
        required_tools.extend(node.get("tools", []))
        required_mcps.extend(node.get("mcps", []))
    required_tools = normalize_string_list(required_tools)
    required_mcps = normalize_string_list(required_mcps)
    tool_allowlist = set(normalize_string_list(getattr(context, "skill_tool_allowlist", None)))
    unauthorized_tools = [name for name in required_tools if name not in tool_allowlist]
    if unauthorized_tools:
        raise RequiredSkillResolutionError(
            "required_skill_tool_unauthorized",
            f"Agent 未授权 Skill 依赖工具: {', '.join(unauthorized_tools)}",
        )
    installed_tools = {tool.name for tool in get_all_tool_instances()}
    missing_tools = [name for name in required_tools if name not in installed_tools]
    if missing_tools:
        raise RequiredSkillResolutionError(
            "required_skill_tool_unavailable",
            f"Skill 依赖工具不可用: {', '.join(missing_tools)}",
        )
    authorized_mcps = set(normalize_string_list(getattr(context, "mcps", None)))
    unauthorized_mcps = [name for name in required_mcps if name not in authorized_mcps]
    if unauthorized_mcps:
        raise RequiredSkillResolutionError(
            "required_skill_mcp_unavailable",
            f"Skill 依赖 MCP 不可用: {', '.join(unauthorized_mcps)}",
        )

    return {
        "context_skills": context_skills,
        "prompt_skills": prompt_skills,
        "readable_skills": prompt_skills,
        "runtime_skill_metadata": prompt_metadata,
        "runtime_skill_dependency_map": dependency_map,
        "required_skill_closure": required_closure,
        "required_skill_tools": required_tools,
        "required_skill_mcps": required_mcps,
        "runtime_skill_snapshots": [
            {
                "slug": slug,
                "version": prompt_metadata[slug]["version"],
                "content_hash": prompt_metadata[slug]["content_hash"],
            }
            for slug in required_closure
        ],
    }


def _activated_skills_reducer(left: list[str] | None, right: list[str] | None) -> list[str]:
    """合并 activated_skills 列表"""
    merged: list[str] = []
    seen: set[str] = set()
    for group in (left or [], right or []):
        for value in group:
            if not isinstance(value, str):
                continue
            slug = value.strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            merged.append(slug)
    return merged


class SkillsState(AgentState):
    """Skills 状态定义"""

    activated_skills: NotRequired[Annotated[list[str], _activated_skills_reducer]]


class SkillsMiddleware(AgentMiddleware):
    """Skills 中间件 - 处理 skills 提示词注入、依赖展开、动态激活

    职责：
    - Skills 提示词注入（直接从数据库加载）
    - 依赖展开（用户配置 + 动态激活）
    - 工具/MCP 动态加载
    """

    state_schema = SkillsState

    def __init__(
        self,
        *,
        skills_context_name: str = "skills",
        enable_skills_prompt: bool = True,
        skills_sources_for_prompt: list[str] | None = None,
    ):
        """初始化中间件

        Args:
            skills_context_name: 上下文中的 skills 列表字段名称（默认 "skills"）
            enable_skills_prompt: 是否启用 skills 提示段注入（默认 True）
            skills_sources_for_prompt: skills 来源路径（用于提示词展示，默认 ["/home/gem/skills/"]）
        """
        super().__init__()
        self.skills_context_name = skills_context_name
        self.enable_skills_prompt = enable_skills_prompt
        self.skills_sources_for_prompt = skills_sources_for_prompt or ["/home/gem/skills/"]

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        """包装模型调用，处理 skills 提示词注入、动态激活和依赖展开"""
        runtime_context = request.runtime.context
        required_skills = normalize_string_list(getattr(runtime_context, "_required_skill_closure", None))

        if self.enable_skills_prompt:
            prompt_skills = getattr(runtime_context, "_prompt_skills", None)
            if isinstance(prompt_skills, list):
                prompt_skills = normalize_string_list(prompt_skills)
                if prompt_skills:
                    skills_meta = self._collect_prompt_metadata(prompt_skills, runtime_context)
                    skills_section = self._build_skills_section(skills_meta)
                    system_message = append_to_system_message(getattr(request, "system_message", None), skills_section)
                    request = request.override(system_message=system_message)
            if required_skills:
                required_section = self._build_required_skills_section(required_skills, runtime_context)
                system_message = append_to_system_message(getattr(request, "system_message", None), required_section)
                request = request.override(system_message=system_message)

        state = request.state if isinstance(request.state, dict) else {}
        activated = state.get("activated_skills", []) or []
        if not isinstance(activated, list):
            activated = []

        readable_skills = self._get_readable_skills(runtime_context)
        activated = [slug for slug in normalize_string_list(activated) if slug in readable_skills]
        for slug in required_skills:
            if slug not in readable_skills:
                raise RequiredSkillResolutionError(
                    "required_skill_not_activated",
                    f"必需 Skill 未进入可读范围: {slug}",
                )
            if slug not in activated:
                activated.append(slug)
        setattr(runtime_context, "_activated_required_skills", list(required_skills))
        await self._emit_required_skill_events(required_skills, runtime_context)

        deps_bundle = self._build_dependency_bundle(activated, runtime_context)

        enabled_tools = []

        if deps_bundle["tools"]:
            all_tools = get_all_tool_instances()
            required_tool_names = set(deps_bundle["tools"])
            enabled_tools = [t for t in all_tools if t.name in required_tool_names]

        if deps_bundle["mcps"]:
            mcp_tools = await self._get_mcp_tools_from_context(
                runtime_context,
                extra_mcps=deps_bundle["mcps"],
            )
            enabled_tools.extend(mcp_tools)

        # 合并工具：保留原有工具 + 追加依赖的新工具
        if enabled_tools:
            existing_tool_names = {t.name for t in request.tools or []}
            merged_tools = list(request.tools or [])
            for t in enabled_tools:
                if t.name not in existing_tool_names:
                    merged_tools.append(t)
            request = request.override(tools=merged_tools)

        content_node_scope = getattr(runtime_context, "_content_node_tool_scope", None)
        if isinstance(content_node_scope, list):
            allowed_names = set(normalize_string_list(content_node_scope))
            scoped_tools = [tool for tool in request.tools or [] if tool.name in allowed_names]
            request = request.override(tools=scoped_tools)

        return await handler(request)

    async def _emit_required_skill_events(self, required_skills: list[str], runtime_context) -> None:
        if not required_skills:
            return
        emitted = getattr(runtime_context, "_emitted_required_skill_events", None)
        if not isinstance(emitted, set):
            emitted = set()
            setattr(runtime_context, "_emitted_required_skill_events", emitted)
        snapshots = {
            item.get("slug"): item
            for item in getattr(runtime_context, "_runtime_skill_snapshots", [])
            if isinstance(item, dict) and item.get("slug")
        }
        run_id = str(getattr(runtime_context, "run_id", "") or "").strip()
        if not run_id:
            raise RequiredSkillResolutionError("required_skill_run_missing", "必需 Skill 激活缺少子 Run ID")
        from yuxi.services.run_queue_service import append_content_runtime_event

        for slug in required_skills:
            if slug in emitted:
                continue
            snapshot = snapshots.get(slug) or {}
            await append_content_runtime_event(
                runtime_context,
                "content.skill.activated",
                {
                    "skill_slug": slug,
                    "skill_version": snapshot.get("version") or "unversioned",
                    "content_hash": snapshot.get("content_hash") or "",
                },
            )
            emitted.add(slug)

    def _build_dependency_bundle(self, activated_skills: list[str], runtime_context) -> dict[str, list[str]]:
        """根据直接激活的 skills 构建依赖包（不包含闭包展开的依赖）"""
        dependency_map = self._get_runtime_dependency_map(runtime_context)

        tools: list[str] = []
        mcps: list[str] = []
        seen_tools: set[str] = set()
        seen_mcps: set[str] = set()

        for slug in activated_skills:
            dep = dependency_map.get(slug, {})
            for tool_name in dep.get("tools", []):
                if tool_name in seen_tools:
                    continue
                seen_tools.add(tool_name)
                tools.append(tool_name)
            for mcp_name in dep.get("mcps", []):
                if mcp_name in seen_mcps:
                    continue
                seen_mcps.add(mcp_name)
                mcps.append(mcp_name)

        return {"tools": tools, "mcps": mcps, "skills": activated_skills}

    def _collect_prompt_metadata(self, slugs: list[str], runtime_context) -> list[SkillPromptMetadata]:
        """收集指定 slugs 的提示词元数据"""
        prompt_metadata = self._get_runtime_prompt_metadata(runtime_context)

        result: list[SkillPromptMetadata] = []
        seen: set[str] = set()

        for slug in slugs:
            if not isinstance(slug, str):
                continue
            normalized = slug.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)

            item = prompt_metadata.get(normalized)
            if not item:
                logger.debug(f"Skill slug not found in prompt metadata, skip: {normalized}")
                continue
            result.append(dict(item))

        return result

    def _build_required_skills_section(self, slugs: list[str], runtime_context) -> str:
        metadata = self._get_runtime_prompt_metadata(runtime_context)
        sections = [
            "以下 Skill 是本节点已强制激活的完整执行指令，必须遵守；SKILL.md 全文已经注入，不要再调用 read_file："
        ]
        for slug in slugs:
            item = metadata.get(slug) or {}
            instructions = str(item.get("instructions") or "").strip()
            if not instructions:
                raise RequiredSkillResolutionError(
                    "required_skill_instructions_unavailable",
                    f"必需 Skill 的 SKILL.md 不可读: {slug}",
                )
            sections.append(f'\n<required-skill slug="{slug}">\n{instructions}\n</required-skill>')
        return "\n".join(sections)

    async def _get_mcp_tools_from_context(
        self,
        context,
        *,
        extra_mcps: list[str] | None = None,
    ) -> list:
        """从上下文配置中获取 MCP 工具列表"""
        import asyncio

        # MCP 工具（并行加载）
        mcps = getattr(context, "mcps", None) or []
        all_mcp_names: list[str] = []
        for server_name in mcps:
            if isinstance(server_name, str):
                all_mcp_names.append(server_name)
        for server_name in extra_mcps or []:
            if isinstance(server_name, str):
                all_mcp_names.append(server_name)

        # 去重
        unique_mcp_names = list(dict.fromkeys(all_mcp_names))

        async def load_mcp_tools(server_name: str) -> list:
            """加载单个 MCP 服务器的工具"""
            try:
                mcp_tools = await get_enabled_mcp_tools(server_name)
                if not mcp_tools:
                    logger.warning(f"SkillsMiddleware: mcp dependency unavailable, skip: {server_name}")
                return mcp_tools
            except Exception as e:
                logger.warning(f"SkillsMiddleware: failed to load mcp dependency '{server_name}': {e}")
                return []

        # 并行加载所有 MCP 工具
        results = await asyncio.gather(*[load_mcp_tools(name) for name in unique_mcp_names])
        selected_tools = []
        for tools in results:
            selected_tools.extend(tools)

        return selected_tools

    def _process_tool_call_result(self, result: Any, request: ToolCallRequest) -> Any:
        """处理工具调用结果，检查并处理 skill 动态激活"""
        if request.tool_call.get("name") != "read_file":
            return result

        args = request.tool_call.get("args") or {}
        file_path = args.get("file_path") if isinstance(args, dict) else None
        slug = self._extract_skill_slug_from_skill_md_path(file_path)

        if not slug:
            return result

        if not self._is_visible_skill_slug(request, slug):
            logger.warning(f"SkillsMiddleware: deny skill activation for invisible slug: {slug}")
            return result

        logger.debug(f"SkillsMiddleware: activated skill by read_file: {slug}")
        return self._merge_activated_skill_update(result, slug)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ):
        """包装工具调用，处理 skill 动态激活"""
        if redundant_read := self._handle_redundant_required_skill_read(request):
            return redundant_read
        self._claim_content_tool_call(request)
        result = await handler(request)
        return self._process_tool_call_result(result, request)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ):
        """同步版本的工具调用包装"""
        if redundant_read := self._handle_redundant_required_skill_read(request):
            return redundant_read
        self._claim_content_tool_call(request)
        result = handler(request)
        return self._process_tool_call_result(result, request)

    def _handle_redundant_required_skill_read(self, request: ToolCallRequest) -> ToolMessage | None:
        """阻止内容节点重复读取已完整注入的必需 Skill，同时保留严格文件权限。"""
        runtime_context = request.runtime.context
        if getattr(runtime_context, "_content_node_max_tool_calls", None) is None:
            return None
        if request.tool_call.get("name") != "read_file":
            return None

        scope = getattr(runtime_context, "_content_node_tool_scope", None)
        if not isinstance(scope, list) or "read_file" in set(normalize_string_list(scope)):
            return None

        args = request.tool_call.get("args") or {}
        file_path = args.get("file_path") if isinstance(args, dict) else None
        slug = self._extract_skill_slug_from_skill_md_path(file_path)
        required_skills = set(normalize_string_list(getattr(runtime_context, "_required_skill_closure", []) or []))
        self._consume_content_tool_call_budget(runtime_context)
        if slug and slug in required_skills:
            logger.info(f"内容 Agent 忽略对已注入必需 Skill 的重复 read_file: {slug}")
            content = (
                f"Skill `{slug}` 的完整指令已经在本节点运行前注入并激活，无需再次调用 read_file。"
                "请直接依据已注入的 Skill、节点 payload 和当前许可工具继续执行。"
            )
        else:
            content = (
                "当前内容节点不开放 read_file；本节点所需 Skill 全文已在运行前注入并激活。"
                "请停止读取文件，直接依据已注入的 Skill、节点 payload 和当前许可工具继续执行。"
            )
        return ToolMessage(
            content=content,
            tool_call_id=str(request.tool_call.get("id") or "content-required-skill-read"),
            name="read_file",
        )

    @staticmethod
    def _claim_content_tool_call(request: ToolCallRequest) -> None:
        runtime_context = request.runtime.context
        configured = getattr(runtime_context, "_content_node_max_tool_calls", None)
        if configured is None:
            return
        tool_name = str(request.tool_call.get("name") or "")
        scope = getattr(runtime_context, "_content_node_tool_scope", None)
        if isinstance(scope, list) and tool_name not in set(normalize_string_list(scope)):
            raise RuntimeError(f"内容 Agent 工具 {tool_name} 不在当前节点许可范围")
        result_tool_name = str(getattr(runtime_context, "_content_node_result_tool_name", "submit_content_node_result"))
        if tool_name == result_tool_name:
            return
        SkillsMiddleware._consume_content_tool_call_budget(runtime_context)

    @staticmethod
    def _consume_content_tool_call_budget(runtime_context) -> None:
        configured = getattr(runtime_context, "_content_node_max_tool_calls", None)
        if configured is None:
            return
        maximum = int(configured)
        used = int(getattr(runtime_context, "_content_node_tool_calls_used", 0) or 0)
        if used >= maximum:
            raise RuntimeError(f"内容 Agent 工具调用超过节点上限（{maximum}）")
        used += 1
        setattr(runtime_context, "_content_node_tool_calls_used", used)
        if used >= maximum:
            setattr(runtime_context, "_content_force_result_submission_reason", "tool_call_limit_reached")

    def _extract_skill_slug_from_skill_md_path(self, file_path: Any) -> str | None:
        """从文件路径中提取 skill slug"""
        if not isinstance(file_path, str):
            return None
        raw = file_path.strip()
        if not raw:
            return None
        pure = PurePosixPath(raw if raw.startswith("/") else f"/{raw}")
        parts = [p for p in pure.parts if p not in ("/", "")]
        slug: str | None = None
        if (
            len(parts) == 5
            and parts[0] == "home"
            and parts[1] == "gem"
            and parts[2] == "skills"
            and parts[4] == "SKILL.md"
        ):
            slug = parts[3]

        if not is_valid_skill_slug(slug):
            return None
        return slug

    def _get_readable_skills(self, runtime_context) -> set[str]:
        selected = getattr(runtime_context, "_readable_skills", [])
        return set(normalize_string_list(selected if isinstance(selected, list) else []))

    def _get_runtime_prompt_metadata(self, runtime_context) -> dict[str, SkillPromptMetadata]:
        metadata = getattr(runtime_context, "_runtime_skill_metadata", {})
        return metadata if isinstance(metadata, dict) else {}

    def _get_runtime_dependency_map(self, runtime_context) -> dict[str, SkillDependencyNode]:
        dependency_map = getattr(runtime_context, "_runtime_skill_dependency_map", {})
        return dependency_map if isinstance(dependency_map, dict) else {}

    def _is_visible_skill_slug(self, request: ToolCallRequest, slug: str) -> bool:
        """检查 slug 是否可见"""
        return slug in self._get_readable_skills(request.runtime.context)

    def _merge_activated_skill_update(self, result: Any, slug: str):
        """合并动态激活的 skill 更新"""
        if isinstance(result, Command):
            update = dict(result.update or {})
            current = update.get("activated_skills") or []
            update["activated_skills"] = _activated_skills_reducer(current, [slug])
            return Command(graph=result.graph, update=update, resume=result.resume, goto=result.goto)

        if isinstance(result, ToolMessage):
            return Command(update={"messages": [result], "activated_skills": [slug]})

        return result

    def _format_skills_locations(self, sources: list[str]) -> str:
        """格式化 skills 位置信息"""
        locations = []
        for i, source_path in enumerate(sources):
            name = PurePosixPath(source_path.rstrip("/")).name.capitalize()
            suffix = " (higher priority)" if i == len(sources) - 1 else ""
            locations.append(f"**{name} Skills**: `{source_path}`{suffix}")
        return "\n".join(locations)

    def _format_skills_list(self, skills_meta: list[dict[str, str]]) -> str:
        """格式化 skills 列表"""
        if not skills_meta:
            return f"(No skills available yet. You can create skills in {' or '.join(self.skills_sources_for_prompt)})"

        lines = []
        for skill in skills_meta:
            lines.append(f"- **{skill['name']}**: {skill['description']}")
            lines.append(f"  -> Read `{skill['path']}` for full instructions")
        return "\n".join(lines)

    def _build_skills_section(self, skills_meta: list[dict[str, str]]) -> str:
        """构建 skills 提示段"""
        skills_locations = self._format_skills_locations(self.skills_sources_for_prompt)
        skills_list = self._format_skills_list(skills_meta)
        return SKILLS_SYSTEM_PROMPT.format(
            skills_locations=skills_locations,
            skills_load_warnings="",
            skills_list=skills_list,
        )
