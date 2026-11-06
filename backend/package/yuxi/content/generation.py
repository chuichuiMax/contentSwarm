from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from yuxi.agents import load_chat_model, resolve_chat_model_spec
from yuxi.utils.line_breaks import normalize_escaped_newlines
from yuxi.content.model.forbidden_words import replace_forbidden_words
from yuxi.content.schemas import ContentArtifactAIEditOutput, ReviewReport
from yuxi.content.v3.modular_rules import merge_pinned_topics, topic_region_name
from yuxi.utils.logging_config import logger

SKILLS_ROOT = Path(__file__).resolve().parents[1] / "agents" / "skills" / "buildin"
DEFAULT_DIRECT_GENERATION_PROMPT = "使用我给你的一些元素，根据爆文 换一种表达方式 符合当地的口吻"
DIRECT_TITLE_MAX_CHARS = 20
DIRECT_TOPIC_LIMIT = 10
_TOPIC_REGION_KEYS = ("所在区域", "city", "location", "region", "serviceCity")
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


_TITLE_SUFFIX_FRAGMENTS = (
    "费用",
    "明细",
    "清单",
    "攻略",
    "案例",
    "来啦",
    "参考",
    "透明",
    "施工",
    "装修",
)


_HASHTAG = re.compile(r"[#＃]([0-9A-Za-z_\u4e00-\u9fff]{1,20})")
_TAG_LABEL_LINE = re.compile(r"^(?:发布标签|标签|话题)\s*[:：]\s*(.*)$")
_TAG_TOKEN = re.compile(r"^[0-9A-Za-z_\u4e00-\u9fff]{2,16}$")
_SENTENCE_PUNCT = re.compile(r"[。！？!?；;]")


def _tag_tokens(text: str) -> list[str]:
    return [part for part in re.split(r"[\s,，、#＃]+", text.strip()) if part]


def detach_publish_tags(body: str, topics: list[str]) -> tuple[str, list[str]]:
    """把正文里的发布标签移出正文，并入话题列表。"""

    extracted: list[str] = []
    kept: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        labeled = _TAG_LABEL_LINE.match(stripped)
        if labeled:
            extracted.extend(_tag_tokens(labeled.group(1)))
            continue
        tags = _HASHTAG.findall(stripped)
        if tags:
            extracted.extend(tags)
            rest = _HASHTAG.sub("", stripped)
            rest = re.sub(r"[\s,，、]+", " ", rest).strip()
            if rest:
                kept.append(rest)
            continue
        kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    if kept:
        last = kept[-1].strip()
        tokens = _tag_tokens(last)
        if (
            len(tokens) >= 2
            and all(_TAG_TOKEN.fullmatch(token) for token in tokens)
            and not _SENTENCE_PUNCT.search(last)
        ):
            extracted.extend(tokens)
            kept.pop()
            while kept and not kept[-1].strip():
                kept.pop()
    return "\n".join(kept).strip(), [*topics, *extracted]


def append_topics_within_limit(topics: list[str], extras: list[str], *, limit: int = DIRECT_TOPIC_LIMIT) -> list[str]:
    """在已有话题后补足标签，总数不超过上限。"""

    merged: list[str] = []
    seen: set[str] = set()
    for item in [*topics, *extras]:
        text = str(item or "").strip().lstrip("#").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        merged.append(text)
        if len(merged) >= limit:
            break
    return merged


def _region_from_user_request(user_request: str) -> str:
    try:
        payload = json.loads(user_request)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    sources: list[Any] = []
    for key in ("businessVariables", "business_variables", "form_values"):
        section = payload.get(key)
        if isinstance(section, dict):
            sources.extend(section.get(name) for name in _TOPIC_REGION_KEYS)
    facts = payload.get("facts")
    if isinstance(facts, dict):
        sources.append(facts.get("location"))
    for value in sources:
        if name := topic_region_name(value):
            return name
    return ""


def _direct_request_content_type(user_request: str) -> tuple[str, str]:
    try:
        data = json.loads(user_request)
    except json.JSONDecodeError:
        return "", ""
    content_type = data.get("contentType") if isinstance(data, dict) else {}
    if not isinstance(content_type, dict):
        return "", ""
    return str(content_type.get("contentTypeCode") or "").strip(), str(content_type.get("typeName") or "").strip()


def _extend_incomplete_title_suffix(head: str, full: str, *, max_overflow: int = 2) -> str:
    """硬截断后若落在常见双字词中间（如「费|用」），补全词尾，避免句式残缺。"""

    if len(head) >= len(full):
        return head
    tail = full[len(head) : len(head) + max_overflow]
    if not tail:
        return head
    for suffix in _TITLE_SUFFIX_FRAGMENTS:
        if len(suffix) < 2:
            continue
        if head.endswith(suffix[0]) and tail.startswith(suffix[1:]):
            completed = head + suffix[1:]
            if len(completed) <= len(head) + max_overflow:
                return completed
    return head


def limit_content_title(title: str, *, limit: int = DIRECT_TITLE_MAX_CHARS) -> str:
    """把标题收到指定字数内；超长时优先停在后半段的句末标点。"""

    text = title.strip()
    if len(text) <= limit:
        return text
    window = text[:limit]
    cut = max(window.rfind(mark) for mark in "？?！!。")
    if cut >= limit // 2:
        return window[: cut + 1]
    return _extend_incomplete_title_suffix(window, text)


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


def _direct_generation_prompt(
    *,
    style_block: str,
    viral_source: dict[str, Any],
    user_request: str,
    generation_prompt: str,
    forbidden_lexicon: dict[str, list[str]],
    knowledge_context: str,
    image_attached: bool = False,
    reference_knowledge: str = "",
) -> str:
    del style_block, viral_source
    forbidden_block = (
        "封禁词替换表（成品中不要解释替换）：\n"
        f"{json.dumps(forbidden_lexicon, ensure_ascii=False)}\n"
        "有候选表达就换用候选；没有候选就改写该句，避免出现问题词。"
    )
    image_line = (
        "已附上第一张图片。文案以用户生成提示词和这张图片为主，只写提示词要求、并且图片里能看到的内容。\n\n"
        if image_attached
        else ""
    )
    reference_block = (
        "已命中的知识库用来写文案。有所选内容类型的知识库时只用该库；没有时使用没有绑定内容类型的知识库。"
        "与业务数据或图片不一致的事实不要写入：\n"
        f"{reference_knowledge.strip()}\n\n"
        if reference_knowledge.strip()
        else ""
    )
    image_topic_rule = (
        "再根据这张图片生成 3 到 5 个小红书类型标签，只写图片里能看出的场景、工艺或风格。"
        "所有标签合计不超过 10 个。"
        if image_attached
        else ""
    )
    if knowledge_context.strip():
        topic_rule = (
            (
                "话题包含鸿扬家装和装修避坑；能确定城市时再包含「城市名+装修公司推荐」，"
                "例如长沙装修公司推荐、株洲装修公司推荐。"
                f"{image_topic_rule}"
            )
            if image_attached
            else (
                "话题写 8 到 10 个，像装修日记标签，包含鸿扬家装和装修避坑；"
                "能确定城市时再包含「城市名+装修公司推荐」，例如长沙装修公司推荐、株洲装修公司推荐。"
            )
        ) + "标签只写入 topics，不要写进正文，不要带 # 号。\n"
        knowledge_block = (
            "用好评笔记知识库里的业主口吻、服务场景和工艺细节写文案。"
            "表单里没有的金额、小区、工期和人名不要写入：\n"
            f"{knowledge_context.strip()}\n\n"
        )
    else:
        topic_rule = (
            "话题必须包含装修避坑；用户数据里能确定城市时，还要包含「城市名+装修公司推荐」，"
            "例如长沙装修公司推荐、株洲装修公司推荐。"
            f"{image_topic_rule}"
            "标签只写入 topics，不要写进正文，不要带 # 号。\n"
        )
        knowledge_block = ""
    return (
        "按「用户生成提示词」和图片写文案，不要另起标题公式或统一结尾。"
        "返回字段 title、body、topics；不要输出解释。\n"
        f"{topic_rule}\n"
        f"{image_line}"
        "用户生成提示词：\n"
        f"{generation_prompt.strip()}\n\n"
        "业务数据只用来核对事实。提示词和内容类型知识库没有要求的金额、小区、人名和经历不要补写。\n"
        f"{user_request}\n\n"
        f"{reference_block}"
        f"{knowledge_block}"
        f"{forbidden_block}"
    )


def prepare_reference_image(data: bytes, content_type: str) -> tuple[str, str]:
    """把参考图压成模型可接收的 JPEG data URL 内容。"""

    import base64
    from io import BytesIO

    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((1600, 1600))
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            quality = 85
            shrunk = False
            while True:
                buffer = BytesIO()
                image.save(buffer, format="JPEG", quality=quality, optimize=True)
                small_enough = buffer.tell() <= 480_000
                if small_enough or (quality <= 70 and (shrunk or max(image.size) <= 1024)):
                    break
                if quality > 70:
                    quality -= 8
                    continue
                image.thumbnail((1024, 1024))
                shrunk = True
                quality = 75
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return "image/jpeg", encoded
    except (UnidentifiedImageError, OSError, ValueError):
        mime = content_type if str(content_type).startswith("image/") else "image/jpeg"
        return mime, base64.b64encode(data).decode("ascii")


def response_token_usage(response: Any) -> dict[str, int | None]:
    """读取模型返回的 token 用量。没有用量时各字段为空。"""

    usage = getattr(response, "usage_metadata", None) or {}
    if not usage:
        meta = getattr(response, "response_metadata", None) or {}
        raw = meta.get("token_usage") or meta.get("usage") or {}
        usage = raw if isinstance(raw, dict) else {}

    def pick(*keys: str) -> int | None:
        for key in keys:
            value = usage.get(key) if isinstance(usage, dict) else None
            if isinstance(value, int):
                return value
        return None

    input_tokens = pick("input_tokens", "prompt_tokens")
    output_tokens = pick("output_tokens", "completion_tokens")
    total_tokens = pick("total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def model_rejects_image(exc: BaseException) -> bool:
    text = str(exc)
    return "not a VLM" in text or "text-only prompts" in text


def model_rejects_named_tool_choice(exc: BaseException) -> bool:
    text = str(exc)
    return (
        "Specifying functions for tool_choice" in text
        or "20015" in text
        or "Thinking mode does not support this tool_choice" in text
    )


def _json_object(text: str) -> dict[str, Any] | None:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _article_fields(payload: dict[str, Any]) -> dict[str, Any] | None:
    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "").strip()
    if not title or not body:
        return None
    topics = payload.get("topics") if isinstance(payload.get("topics"), list) else []
    return {"title": title, "body": body, "topics": [str(topic) for topic in topics]}


def _plain_article(text: str) -> dict[str, Any] | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    title = lines[0].lstrip("#").strip()
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else title
    if not title or not body:
        return None
    return {"title": title, "body": body, "topics": []}


def _response_text_content(response: Any) -> str:
    content = getattr(response, "content", "")
    if isinstance(content, list):
        return "\n".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content).strip()
    return content.strip() if isinstance(content, str) else ""


def _output_arguments(response: Any) -> dict[str, Any] | None:
    calls = list(getattr(response, "tool_calls", None) or [])
    calls.extend(getattr(response, "invalid_tool_calls", None) or [])
    extra = getattr(response, "additional_kwargs", None) or {}
    for call in extra.get("tool_calls") or []:
        function = call.get("function") if isinstance(call, dict) else None
        if isinstance(function, dict):
            calls.append({"args": function.get("arguments")})
    for call in calls:
        arguments = call.get("args") if isinstance(call, dict) else None
        payload = _json_object(arguments) if isinstance(arguments, str) else arguments
        article = _article_fields(payload) if isinstance(payload, dict) else None
        if article:
            return article
    text = _response_text_content(response)
    article = _article_fields(_json_object(text) or {})
    if article:
        return article
    return _plain_article(text)


def _append_copy_instruction(message: HumanMessage) -> HumanMessage:
    note = (
        "请直接输出文案。第一行写标题，空一行后写正文，正文里不要写标签。"
        "正文结束后空一行，写一行「发布标签：」，同一行用空格分隔标签。"
        "不要调用工具，不要输出解释。"
    )
    content = message.content
    if isinstance(content, str):
        return HumanMessage(content=f"{content}\n\n{note}")
    parts = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text":
            parts.append({**part, "text": f"{part.get('text', '')}\n\n{note}"})
        else:
            parts.append(part)
    return HumanMessage(content=parts)


def _direct_generation_message(prompt: str, reference_image: tuple[bytes, str] | None) -> HumanMessage:
    if not reference_image:
        return HumanMessage(content=prompt)
    data, content_type = reference_image
    mime, encoded = prepare_reference_image(data, content_type)
    return HumanMessage(
        content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
        ]
    )


async def generate_direct_content(
    *,
    model_spec: str | None,
    creative_style: dict[str, Any],
    viral_source: dict[str, Any],
    user_request: str,
    generation_prompt: str = DEFAULT_DIRECT_GENERATION_PROMPT,
    forbidden_lexicon: dict[str, list[str]] | None = None,
    knowledge_context: str = "",
    reference_knowledge: str = "",
    reference_image: tuple[bytes, str] | None = None,
    usage_out: dict[str, Any] | None = None,
) -> ContentArtifactAIEditOutput:
    """用创作风格、爆款原文和用户原始输入直接生成内容，不执行工作流审核。"""

    resolved_model = resolve_chat_model_spec(model_spec)
    model_kwargs: dict[str, Any] = {"temperature": 0.7}
    if resolved_model.startswith(("ark:doubao-", "deepseek:")):
        model_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    model = load_chat_model(fully_specified_name=resolved_model, **model_kwargs)
    style = creative_style if isinstance(creative_style, dict) else {}
    style_name = str(style.get("name") or "").strip()
    style_instruction = str(style.get("instruction") or "").strip()
    style_block = (
        f"创作风格：{style_name}\n创作风格说明：{style_instruction}\n\n"
        if style_name or style_instruction
        else ""
    )
    forbidden_lexicon = forbidden_lexicon or {}
    forbidden_replacements = {term: alternatives[0] for term, alternatives in forbidden_lexicon.items() if alternatives}
    prompt = _direct_generation_prompt(
        style_block=style_block,
        viral_source=viral_source,
        user_request=user_request,
        generation_prompt=generation_prompt,
        forbidden_lexicon=forbidden_lexicon,
        knowledge_context=knowledge_context.strip(),
        image_attached=bool(reference_image),
        reference_knowledge=reference_knowledge,
    )
    messages = [
        _direct_generation_message(prompt, reference_image),
    ]
    generation_started_at = time.monotonic()
    bound_model = model.bind_tools(
        [ContentArtifactAIEditOutput],
        tool_choice=ContentArtifactAIEditOutput.__name__,
    )
    image_sent = bool(reference_image)
    image_omitted_reason = ""

    async def invoke(next_messages: list[HumanMessage]):
        nonlocal bound_model
        try:
            return await bound_model.ainvoke(next_messages)
        except Exception as exc:
            if not model_rejects_named_tool_choice(exc):
                raise
            logger.info("Direct content model cannot force a tool, retrying with auto: model=%s", resolved_model)
            bound_model = model.bind_tools([ContentArtifactAIEditOutput], tool_choice="auto")
            return await bound_model.ainvoke(next_messages)

    try:
        response = await invoke(messages)
    except Exception as exc:
        if not reference_image or not model_rejects_image(exc):
            raise
        logger.info("Direct content model cannot read images, retrying with text only: model=%s", resolved_model)
        image_sent = False
        image_omitted_reason = "当前模型不能看图，已改为只根据文字生成"
        prompt = _direct_generation_prompt(
            style_block=style_block,
            viral_source=viral_source,
            user_request=user_request,
            generation_prompt=generation_prompt,
            forbidden_lexicon=forbidden_lexicon,
            knowledge_context=knowledge_context.strip(),
            image_attached=False,
            reference_knowledge=reference_knowledge,
        )
        response = await invoke([_direct_generation_message(prompt, None)])
    arguments = _output_arguments(response)
    if arguments is None:
        logger.info("Direct content model returned no copy fields, retrying as plain text: model=%s", resolved_model)
        response = await model.ainvoke([_append_copy_instruction(messages[-1] if image_sent else _direct_generation_message(prompt, None))])
        arguments = _output_arguments(response)
    if not isinstance(arguments, dict):
        raise ValueError("模型没有返回内容生成工具调用")
    raw_output = ContentArtifactAIEditOutput.model_validate(arguments)
    final_title = normalize_escaped_newlines(replace_forbidden_words(raw_output.title, forbidden_replacements)).strip()
    body_text = normalize_escaped_newlines(replace_forbidden_words(raw_output.body, forbidden_replacements)).strip()
    body_topics = [
        normalize_escaped_newlines(replace_forbidden_words(topic, forbidden_replacements))
        for topic in raw_output.topics
    ]
    final_body, body_topics = detach_publish_tags(body_text, body_topics)
    output = ContentArtifactAIEditOutput(
        title=final_title,
        body=final_body,
        topics=merge_pinned_topics(
            body_topics,
            _region_from_user_request(user_request),
            limit=DIRECT_TOPIC_LIMIT,
        ),
    )
    combined = "\n".join([output.title, output.body, *output.topics])
    residual = [term for term in forbidden_lexicon if term in combined]
    if residual:
        raise ValueError("封禁词替换后仍有残留：" + "、".join(residual))
    tokens = response_token_usage(response)
    elapsed_seconds = round(time.monotonic() - generation_started_at, 2)
    if usage_out is not None:
        usage_out.clear()
        usage_out.update(
            {
                "model": resolved_model,
                "prompt": prompt,
                "image_attached": image_sent,
                "image_omitted_reason": image_omitted_reason,
                "elapsed_seconds": elapsed_seconds,
                **tokens,
            }
        )
    logger.info(
        "Direct content model completed: model={} input_tokens={} output_tokens={} total_tokens={} image={} total={:.2f}s",
        resolved_model,
        tokens["input_tokens"],
        tokens["output_tokens"],
        tokens["total_tokens"],
        image_sent,
        elapsed_seconds,
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


async def ai_replace_forbidden_terms(
    *,
    model_spec: str | None,
    title: str,
    body: str,
    topics: list[str],
    residual_terms: list[str],
    alternatives: dict[str, list[str]],
) -> dict[str, Any]:
    """对机械替换后仍残留的封禁词，由模型按常用表达自然改写。"""

    resolved_model = resolve_chat_model_spec(model_spec)
    model = load_chat_model(fully_specified_name=resolved_model, temperature=0.2)
    response = await model.ainvoke(
        [
            SystemMessage(
                content=(
                    "你是内容合规改写助手。只替换指定的平台封禁问题词，保持原意、口吻和排版。"
                    "不得改动任何数字、计价单位、项目参数、身份资质与报价范围。"
                    "优先使用给定的常用表达；若无可用表达则改为自然同义说法。"
                    "只输出 JSON 对象，字段只能是 title、body、topics。"
                )
            ),
            HumanMessage(
                content=(
                    f"需替换的问题词={json.dumps(residual_terms, ensure_ascii=False)}\n"
                    f"常用表达={json.dumps(alternatives, ensure_ascii=False)}\n"
                    f"当前成品={json.dumps({'title': title, 'body': body, 'topics': topics}, ensure_ascii=False)}"
                )
            ),
        ]
    )
    payload = _parse_json(_response_text(response))
    if not isinstance(payload, dict):
        raise ValueError("封禁词 AI 替换必须返回 JSON 对象")
    return ContentArtifactAIEditOutput.model_validate(payload).model_dump()

