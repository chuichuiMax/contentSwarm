from langchain.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from yuxi import config as sys_config
from yuxi.models.providers.cache import model_cache
from yuxi.utils import get_docker_safe_url
from yuxi.utils.logging_config import logger


def _normalize_tool_call_chunks(message) -> None:
    """把工具调用续片里空字符串的 name/id 归一化为 None。

    LangGraph v3 流式累积对 tool_call 字段是“后值覆盖”：部分 OpenAI 兼容提供商
    （siliconflow、阿里云百炼等）在续片里把 name/id 下发为空字符串 ""，会覆盖首片
    的真实值（siliconflow 丢 name、百炼丢 id），导致工具结果无法按 tool_call_id
    关联、工具状态停留在“进行中”。OpenAI 官方在续片里发 None 不会触发覆盖，这里
    把空串归一化为 None 对齐该行为。待上游修复 v3 协议后可移除。
    """
    for chunk in message.tool_call_chunks:
        if chunk.get("name") == "":
            chunk["name"] = None
        if chunk.get("id") == "":
            chunk["id"] = None


def _attach_provider_reasoning_delta(generation, raw_chunk: dict) -> None:
    """保留兼容接口在 delta 里下发的思考片段。

    ChatOpenAI 只认官方字段，会丢掉 DeepSeek / SiliconFlow 的 reasoning_content。
    思考阶段正文为空，节点空闲计时会把仍在推理的调用当成无输出。思考文本只挂在
    additional_kwargs 上，不写入 content，避免混进正文和可见输出预算。
    """
    if generation is None or getattr(generation, "message", None) is None:
        return
    choices = raw_chunk.get("choices") or []
    nested = raw_chunk.get("chunk")
    if not choices and isinstance(nested, dict):
        choices = nested.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return
    delta = choices[0].get("delta") or {}
    if not isinstance(delta, dict):
        return
    reasoning = delta.get("reasoning_content")
    if not isinstance(reasoning, str) or not reasoning:
        reasoning = delta.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        generation.message.additional_kwargs["reasoning_content"] = reasoning


class _ToolCallChunkFixChatOpenAI(ChatOpenAI):
    """归一化流式 tool_call 续片中的空串 name/id，规避 v3 流式累积缺陷。"""

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        generation = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        if isinstance(chunk, dict):
            _attach_provider_reasoning_delta(generation, chunk)
        return generation

    async def _astream(self, *args, **kwargs):
        async for chunk in super()._astream(*args, **kwargs):
            _normalize_tool_call_chunks(chunk.message)
            yield chunk

    def _stream(self, *args, **kwargs):
        for chunk in super()._stream(*args, **kwargs):
            _normalize_tool_call_chunks(chunk.message)
            yield chunk


def resolve_chat_model_spec(model_spec: str | None, *, fallback: str | None = None) -> str:
    """解析空模型配置，不吞掉已经配置但无效的模型值。

    这里仅处理模型为空时的优先级：请求或配置值、调用方 fallback、系统默认模型；
    具体模型是否存在、是否为聊天模型仍由 model_cache 校验。
    """
    for candidate in (model_spec, fallback, sys_config.default_model):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise ValueError("model spec 不能为空")


def load_chat_model(fully_specified_name: str | None, **kwargs) -> BaseChatModel:
    fully_specified_name = resolve_chat_model_spec(fully_specified_name)

    info = model_cache.get_model_info(fully_specified_name)
    if not info:
        available_specs = model_cache.get_all_specs("chat")
        available_ids = [item.spec for item in available_specs[:10]]
        raise ValueError(
            f"Unknown model spec: '{fully_specified_name}'. "
            f"Available chat models ({len(available_specs)}): {available_ids}"
        )

    if info.model_type != "chat":
        raise ValueError(f"Model {fully_specified_name} is not a chat model (type={info.model_type})")

    api_key = info.api_key
    base_url = get_docker_safe_url(info.base_url)

    if info.headers:
        kwargs.setdefault("default_headers", info.headers)

    logger.debug(f"Loading model {fully_specified_name} with provider_type={info.provider_type}")

    if info.provider_type == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=info.model_id,
            api_key=SecretStr(api_key),
            base_url=base_url,
            **kwargs,
        )
    if info.provider_type == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=info.model_id,
            google_api_key=SecretStr(api_key),
            **kwargs,
        )

    if info.extra:
        if "use_responses_api" in info.extra:
            kwargs.setdefault("use_responses_api", bool(info.extra["use_responses_api"]))
        if "reasoning_effort" in info.extra:
            kwargs.setdefault("reasoning_effort", str(info.extra["reasoning_effort"]))
        if "disable_response_storage" in info.extra:
            kwargs.setdefault("store", not bool(info.extra["disable_response_storage"]))

    return _ToolCallChunkFixChatOpenAI(
        model=info.model_id,
        api_key=SecretStr(api_key),
        base_url=base_url,
        stream_usage=True,
        **kwargs,
    )
