from __future__ import annotations

import base64
import hashlib
import io
import json
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from PIL import Image, ImageOps

from yuxi.agents import load_chat_model, resolve_chat_model_spec

from .schemas import ANALYSIS_SCHEMA_VERSION, DEFAULT_VISION_MODEL_SPEC, ImageAnalysisResult

AnalysisRole = Literal["structure_source", "style_reference", "cross_space_style"]

ROLE_INSTRUCTIONS = {
    "structure_source": "重点识别空间类型、墙体、门窗、梁柱、层高、固定设施、空间比例、镜头、透视和必须保留项。",
    "style_reference": (
        "重点识别风格、配色、主辅材、家具形态、软装、灯光、氛围和可迁移设计语言；"
        "不要把户型当成迁移要求。"
    ),
    "cross_space_style": (
        "只提取可跨空间迁移的风格、配色、材质和光照语言，"
        "并在 exclusions 中列出不可迁移的房间类型、结构和家具布局。"
    ),
}


class VisualModelUnavailableError(RuntimeError):
    pass


def analysis_cache_key(asset_sha256: str, role: AnalysisRole, model_spec: str, schema_version: int) -> str:
    raw = f"{asset_sha256}:{role}:{model_spec}:{schema_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def resolve_visual_model_spec(model_spec: str) -> str:
    return resolve_chat_model_spec(model_spec, fallback=DEFAULT_VISION_MODEL_SPEC)


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    content = getattr(response, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts = [str(item.get("text", "")) for item in content if isinstance(item, dict)]
        if "".join(parts).strip():
            return "".join(parts).strip()
    raise ValueError("视觉模型没有返回内容")


def parse_analysis_response(text: str, role: AnalysisRole) -> ImageAnalysisResult:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("视觉模型未返回结构化 JSON")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("视觉分析结果必须是对象")
    data["role"] = role
    return ImageAnalysisResult.model_validate(data)


def _prepare_image(data: bytes) -> str:
    with Image.open(io.BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=88, optimize=True)
    return base64.b64encode(output.getvalue()).decode("ascii")


async def run_visual_analysis(
    data: bytes,
    role: AnalysisRole,
    *,
    model_spec: str = DEFAULT_VISION_MODEL_SPEC,
) -> tuple[ImageAnalysisResult, str]:
    try:
        resolved_model = resolve_visual_model_spec(model_spec)
        model = load_chat_model(fully_specified_name=resolved_model, temperature=0)
    except Exception as exc:
        raise VisualModelUnavailableError("视觉模型配置不可用") from exc
    schema = ImageAnalysisResult.model_json_schema()
    encoded = _prepare_image(data)
    messages = [
        SystemMessage(
            content=(
                "你是室内建筑图片分析器。只能根据图片中可见事实返回 JSON，不得猜测不可见结构。"
                "所有数组字段都必须存在，没有内容时返回空数组；"
                "room_type 必须给出最可能的空间类型，无法判断时写‘未知空间’。"
                "不要输出解释、Markdown 或 JSON 之外的文字。"
            )
        ),
        HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": (
                        f"分析角色：{role}。{ROLE_INSTRUCTIONS[role]}\n"
                        f"严格按此 JSON Schema 返回：{json.dumps(schema, ensure_ascii=False)}"
                    ),
                },
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
            ]
        ),
    ]
    response = await model.ainvoke(messages)
    return parse_analysis_response(_response_text(response), role), resolved_model


__all__ = [
    "ANALYSIS_SCHEMA_VERSION",
    "VisualModelUnavailableError",
    "analysis_cache_key",
    "parse_analysis_response",
    "resolve_visual_model_spec",
    "run_visual_analysis",
]
