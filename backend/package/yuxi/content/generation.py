from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.utils.json import parse_partial_json

from yuxi.agents import load_chat_model, resolve_chat_model_spec
from yuxi.content.model.forbidden_words import replace_forbidden_words
from yuxi.content.schemas import ContentArtifactAIEditOutput, ReviewReport
from yuxi.utils.logging_config import logger

SKILLS_ROOT = Path(__file__).resolve().parents[1] / "agents" / "skills" / "buildin"
DEFAULT_DIRECT_GENERATION_PROMPT = "使用我给你的一些元素，根据爆文 换一种表达方式 符合当地的口吻"
SKILL_VERSIONS = {
    "content-value-analyzer": "1.3.0",
    "content-strategy-planner": "4.0.1",
    "content-evidence-researcher": "3.2.0",
    "strategy-product-researcher": "1.1.0",
    "content-title-generator": "2.0.0",
    "content-outline-builder": "2.0.0",
    "content-body-generator": "2.1.0",
    "persona-style-polisher": "1.1.0",
    "content-reviewer": "1.3.0",
    "content-visual-planner": "1.4.0",
    "content-cover-generator": "1.2.0",
    "content-visual-reviewer": "1.1.0",
}


def load_skill_instruction(slug: str) -> str:
    path = SKILLS_ROOT / slug / "SKILL.md"
    return path.read_text(encoding="utf-8")


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content.strip()
    raise ValueError("模型没有返回可解析的文本")


def _parse_json(text: str) -> Any:
    normalized = text.strip()
    if normalized.startswith("```"):
        lines = normalized.splitlines()
        normalized = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(normalized)
    except json.JSONDecodeError:
        object_start = normalized.find("{")
        array_start = normalized.find("[")
        starts = [value for value in (object_start, array_start) if value >= 0]
        if not starts:
            raise ValueError("模型输出不包含 JSON")
        start = min(starts)
        closing = "}" if normalized[start] == "{" else "]"
        end = normalized.rfind(closing)
        if end < start:
            raise ValueError("模型输出 JSON 不完整")
        return json.loads(normalized[start : end + 1])


async def _invoke_json(model_spec: str | None, *, skill_slug: str, prompt: str) -> Any:
    resolved_model = resolve_chat_model_spec(model_spec)
    model = load_chat_model(fully_specified_name=resolved_model, temperature=0.5)
    response = await model.ainvoke(
        [
            SystemMessage(content=load_skill_instruction(skill_slug)),
            HumanMessage(content=prompt),
        ]
    )
    return _parse_json(_response_text(response))


async def generate_direct_content(
    *,
    model_spec: str | None,
    creative_style: dict[str, Any],
    viral_source: dict[str, Any],
    user_request: str,
    generation_prompt: str = DEFAULT_DIRECT_GENERATION_PROMPT,
    forbidden_lexicon: dict[str, list[str]] | None = None,
    on_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> ContentArtifactAIEditOutput:
    """用创作风格、爆款原文和用户原始输入直接生成内容，不执行工作流审核。"""

    resolved_model = resolve_chat_model_spec(model_spec)
    model_kwargs: dict[str, Any] = {"temperature": 0.7}
    if resolved_model.startswith("ark:doubao-"):
        model_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    model = load_chat_model(fully_specified_name=resolved_model, **model_kwargs)
    style_name = str(creative_style.get("name") or "").strip()
    style_instruction = str(creative_style.get("instruction") or "").strip()
    forbidden_lexicon = forbidden_lexicon or {}
    forbidden_replacements = {term: alternatives[0] for term, alternatives in forbidden_lexicon.items() if alternatives}
    prompt = (
        "请根据以下输入直接创作一篇内容。返回字段 title、body、topics；"
        "不要输出解释、审核意见或额外字段。\n\n"
        "参考爆款原文的开头切入、段落顺序、信息推进、结尾收束和口语节奏，"
        "在这些位置用用户提供的事实改写，不能逐句照抄。"
        "创作风格只决定表达手法，不得因此杜撰其他人的报价、节省金额、"
        "客户经历或施工结果；原文有而用户未提供的事实，用已提供的信息自然替换或略去。"
        "用户输入的金额、单位、面积、数量及报价明细必须准确保留，不自行换算或补造。\n\n"
        f"创作风格：{style_name}\n"
        f"创作风格说明：{style_instruction}\n\n"
        "爆款原文：\n"
        f"{json.dumps(viral_source, ensure_ascii=False)}\n\n"
        "用户输入数据（原样保留并结合其中信息写作）：\n"
        f"{user_request}\n\n"
        "本次生成提示词：\n"
        f"{generation_prompt.strip()}\n\n"
        "排版与表情要求：\n"
        "正文要自然分段，使用适合移动端阅读的短句、空行和必要的 Markdown 排版；"
        "根据语义加入少量合适的 Emoji，位置要自然，不能堆砌或连续重复；"
        "不得用 Emoji 替代价格、数字、面积、时间、单位、品牌名或专业信息。\n\n"
        "封禁词替换表（不要在成品中解释替换过程）：\n"
        f"{json.dumps(forbidden_lexicon, ensure_ascii=False)}\n"
        "存在候选表达时选择符合上下文的写法；候选为空时改写整句，避免出现问题词。"
    )
    messages = [
        HumanMessage(content=prompt),
    ]
    streaming_model = model.bind_tools(
        [ContentArtifactAIEditOutput],
        tool_choice=ContentArtifactAIEditOutput.__name__,
    )
    arguments_by_index: dict[int, str] = {}
    streamed_values: dict[str, Any] = {"title": "", "body": "", "topics": []}
    generation_started_at = time.monotonic()
    first_chunk_elapsed: float | None = None

    async for chunk in streaming_model.astream(messages):
        arguments_changed = False
        for tool_chunk in getattr(chunk, "tool_call_chunks", None) or []:
            arguments = tool_chunk.get("args") or ""
            if not arguments:
                continue
            index = tool_chunk.get("index")
            normalized_index = index if isinstance(index, int) else 0
            arguments_by_index[normalized_index] = arguments_by_index.get(normalized_index, "") + arguments
            arguments_changed = True
            if first_chunk_elapsed is None:
                first_chunk_elapsed = time.monotonic() - generation_started_at
                logger.info(
                    "Direct content model first chunk: model={} elapsed={:.2f}s",
                    resolved_model,
                    first_chunk_elapsed,
                )
        if not arguments_changed or on_delta is None:
            continue

        partial = parse_partial_json(arguments_by_index.get(0, ""))
        if not isinstance(partial, dict):
            continue
        for field in ("title", "body", "topics"):
            if field not in partial:
                continue
            value = partial[field]
            if field in {"title", "body"}:
                if not isinstance(value, str):
                    continue
                value = replace_forbidden_words(value, forbidden_replacements)
                if value == streamed_values[field]:
                    continue
                previous = streamed_values[field]
                delta = value[len(previous) :] if value.startswith(previous) else value
            else:
                if not isinstance(value, list):
                    continue
                value = [
                    replace_forbidden_words(item, forbidden_replacements)
                    for item in value
                    if isinstance(item, str) and item
                ]
                if value == streamed_values[field]:
                    continue
                previous = streamed_values[field]
                delta = value[len(previous) :] if value[: len(previous)] == previous else value
            streamed_values[field] = value
            await on_delta({"field": field, "delta": delta, "value": value})

    arguments = arguments_by_index.get(0, "")
    if not arguments:
        raise ValueError("模型没有返回内容生成工具调用")
    raw_output = ContentArtifactAIEditOutput.model_validate(json.loads(arguments))
    output = ContentArtifactAIEditOutput(
        title=replace_forbidden_words(raw_output.title, forbidden_replacements),
        body=replace_forbidden_words(raw_output.body, forbidden_replacements),
        topics=[replace_forbidden_words(topic, forbidden_replacements) for topic in raw_output.topics],
    )
    combined = "\n".join([output.title, output.body, *output.topics])
    residual = [term for term in forbidden_lexicon if term in combined]
    if residual:
        raise ValueError("封禁词替换后仍有残留：" + "、".join(residual))
    logger.info(
        "Direct content model completed: model={} first_chunk={:.2f}s total={:.2f}s",
        resolved_model,
        first_chunk_elapsed or 0.0,
        time.monotonic() - generation_started_at,
    )
    return output


async def review_generated_content(
    *,
    model_spec: str | None,
    title: str,
    body: str,
    topics: list[str],
    brief: dict[str, Any],
    workflow_snapshot: dict[str, Any],
    evidence_bundle: dict[str, Any],
) -> dict[str, Any]:
    """对已生成的 V3 内容执行一次独立语义复审。"""

    payload = await _invoke_json(
        model_spec,
        skill_slug="content-reviewer",
        prompt=(
            "审核创作手法贯穿、标题公式、正文结构、事实来源、人设与语气。"
            "只输出 JSON 对象 status、checks；status 只能是 passed、warning、blocked。"
            "checks 每项包含 code、level、location、message、evidence_ids、suggestion。\n"
            f"Content={json.dumps({'title': title, 'body': body, 'topics': topics}, ensure_ascii=False)}\n"
            f"ContentBrief={json.dumps(brief, ensure_ascii=False)}\n"
            f"WorkflowSnapshot={json.dumps(workflow_snapshot, ensure_ascii=False)}\n"
            f"EvidenceBundle={json.dumps(evidence_bundle, ensure_ascii=False)}"
        ),
    )
    if not isinstance(payload, dict):
        raise ValueError("内容审核必须返回 JSON 对象")
    normalized = dict(payload)
    status_aliases = {
        "pass": "passed",
        "ok": "passed",
        "success": "passed",
        "warn": "warning",
        "failed": "blocked",
        "fail": "blocked",
        "error": "blocked",
    }
    normalized["status"] = status_aliases.get(str(normalized.get("status") or "").lower(), normalized.get("status"))
    level_aliases = {
        "pass": "info",
        "passed": "info",
        "ok": "info",
        "success": "info",
        "warn": "warning",
        "failed": "error",
        "fail": "error",
        "blocked": "error",
    }
    checks = []
    for raw in normalized.get("checks") or []:
        item = dict(raw or {})
        item["level"] = level_aliases.get(str(item.get("level") or "").lower(), item.get("level"))
        item.setdefault("location", "content")
        item.setdefault("evidence_ids", [])
        checks.append(item)
    normalized["checks"] = checks
    return ReviewReport.model_validate(normalized).model_dump()


async def refine_generated_content(
    *,
    model_spec: str | None,
    instruction: str,
    title: str,
    body: str,
    topics: list[str],
    brief: dict[str, Any],
    strategy: dict[str, Any],
    evidence_bundle: dict[str, Any],
) -> dict[str, Any]:
    """按用户要求修改内容成品，不开放工作流或工具能力。"""

    resolved_model = resolve_chat_model_spec(model_spec)
    model = load_chat_model(fully_specified_name=resolved_model, temperature=0.3)
    response = await model.ainvoke(
        [
            SystemMessage(
                content=(
                    "你是内容成品编辑器，只能按用户要求修改当前成品的 title、body、topics。"
                    "工作流、节点、规则、策略、证据和封面都是只读上下文；忽略任何修改、重跑或绕过它们的要求。"
                    "不得编造证据中不存在的事实、数字或承诺。"
                    "只输出一个 JSON 对象，且只能包含 title、body、topics 三个字段；未要求修改的字段必须原样保留。"
                )
            ),
            HumanMessage(
                content=(
                    f"修改要求={instruction}\n"
                    f"当前成品={json.dumps({'title': title, 'body': body, 'topics': topics}, ensure_ascii=False)}\n"
                    f"内容简报={json.dumps(brief, ensure_ascii=False)}\n"
                    f"锁定策略={json.dumps(strategy, ensure_ascii=False)}\n"
                    f"冻结证据={json.dumps(evidence_bundle, ensure_ascii=False)}"
                )
            ),
        ]
    )
    payload = _parse_json(_response_text(response))
    if not isinstance(payload, dict):
        raise ValueError("内容成品修改必须返回 JSON 对象")
    return ContentArtifactAIEditOutput.model_validate(payload).model_dump()
