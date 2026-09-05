from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from yuxi.agents import load_chat_model, resolve_chat_model_spec
from yuxi.content.schemas import ContentArtifactAIEditOutput, ReviewReport

SKILLS_ROOT = Path(__file__).resolve().parents[1] / "agents" / "skills" / "buildin"
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
