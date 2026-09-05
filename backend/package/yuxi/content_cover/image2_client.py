from __future__ import annotations

import asyncio
import base64
import ipaddress
import os
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urljoin, urlparse

import httpx

from yuxi.content_cover.schemas import (
    Image2CapabilityProfile,
    Image2Input,
    Image2Output,
    Image2Request,
    Image2Submission,
)
from yuxi.utils.logging_config import logger


class Image2Error(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class Image2Config:
    base_url: str
    api_key: str
    model: str
    submit_path: str = "/images/generations"
    edit_path: str = "/images/edits"
    status_path: str = "/images/generations/{task_id}"
    timeout_seconds: float = 120
    send_response_format: bool = False
    trusted_output_origins: tuple[str, ...] = ()
    edit_model: str | None = None
    edit_request_format: str = "openai_multipart"

    @classmethod
    def from_values(cls, *, base_url: str, api_key: str, model: str) -> Image2Config:
        base_url = base_url.strip().rstrip("/")
        api_key = api_key.strip()
        model = model.strip()
        if not base_url or not api_key or not model:
            raise Image2Error("IMAGE2_NOT_CONFIGURED", "image2 中转站尚未配置")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise Image2Error("IMAGE2_CONFIG_INVALID", "image2 Base URL 必须是有效的 HTTP(S) 地址")
        try:
            parsed.port
        except ValueError as exc:
            raise Image2Error("IMAGE2_CONFIG_INVALID", "image2 Base URL 端口无效") from exc
        submit_path = (os.getenv("IMAGE2_SUBMIT_PATH") or "/images/generations").strip()
        edit_path = (os.getenv("IMAGE2_EDIT_PATH") or "/images/edits").strip()
        status_path = (os.getenv("IMAGE2_STATUS_PATH") or "/images/generations/{task_id}").strip()
        if not submit_path or not edit_path or not status_path:
            raise Image2Error("IMAGE2_CONFIG_INVALID", "image2 接口路径不能为空")
        if any(urlparse(path).scheme or urlparse(path).netloc for path in (submit_path, edit_path, status_path)):
            raise Image2Error("IMAGE2_CONFIG_INVALID", "image2 接口路径必须是相对路径")
        if "{task_id}" not in status_path:
            raise Image2Error("IMAGE2_CONFIG_INVALID", "IMAGE2_STATUS_PATH 必须包含 {task_id}")
        try:
            timeout_seconds = float(os.getenv("IMAGE2_TIMEOUT_SECONDS", "120"))
        except ValueError as exc:
            raise Image2Error("IMAGE2_CONFIG_INVALID", "IMAGE2_TIMEOUT_SECONDS 必须是数字") from exc
        if timeout_seconds <= 0:
            raise Image2Error("IMAGE2_CONFIG_INVALID", "IMAGE2_TIMEOUT_SECONDS 必须大于 0")
        trusted_output_origins = tuple(
            item.strip().rstrip("/")
            for item in (os.getenv("IMAGE2_TRUSTED_OUTPUT_ORIGINS") or "").split(",")
            if item.strip()
        )
        for origin in trusted_output_origins:
            parsed_origin = urlparse(origin)
            if (
                parsed_origin.scheme not in {"http", "https"}
                or not parsed_origin.hostname
                or parsed_origin.username
                or parsed_origin.password
                or parsed_origin.path
                or parsed_origin.query
                or parsed_origin.fragment
            ):
                raise Image2Error(
                    "IMAGE2_CONFIG_INVALID",
                    "IMAGE2_TRUSTED_OUTPUT_ORIGINS 必须是逗号分隔的 HTTP(S) origin",
                )
            try:
                parsed_origin.port
            except ValueError as exc:
                raise Image2Error(
                    "IMAGE2_CONFIG_INVALID",
                    "IMAGE2_TRUSTED_OUTPUT_ORIGINS 端口无效",
                ) from exc
        edit_model = (os.getenv("IMAGE2_EDIT_MODEL") or "").strip() or None
        edit_request_format = (os.getenv("IMAGE2_EDIT_REQUEST_FORMAT") or "openai_multipart").strip()
        if edit_request_format not in {"openai_multipart", "siliconflow_json"}:
            raise Image2Error(
                "IMAGE2_CONFIG_INVALID",
                "IMAGE2_EDIT_REQUEST_FORMAT 仅支持 openai_multipart 或 siliconflow_json",
            )
        if edit_request_format == "siliconflow_json" and not edit_model:
            raise Image2Error("IMAGE2_CONFIG_INVALID", "SiliconFlow 图片编辑必须配置 IMAGE2_EDIT_MODEL")
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            submit_path=submit_path,
            edit_path=edit_path,
            status_path=status_path,
            timeout_seconds=timeout_seconds,
            send_response_format=os.getenv("IMAGE2_SEND_RESPONSE_FORMAT", "false").lower() in {"1", "true", "yes"},
            trusted_output_origins=trusted_output_origins,
            edit_model=edit_model,
            edit_request_format=edit_request_format,
        )

    @classmethod
    def from_env(cls) -> Image2Config:
        return cls.from_values(
            base_url=os.getenv("IMAGE2_BASE_URL") or "",
            api_key=os.getenv("IMAGE2_API_KEY") or "",
            model=os.getenv("IMAGE2_MODEL") or "",
        )


def image2_is_configured() -> bool:
    try:
        Image2Config.from_env()
    except Image2Error:
        return False
    return True


class Image2Client:
    def __init__(
        self,
        config: Image2Config | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Callable[[str, int], Awaitable[list[str]]] | None = None,
    ):
        self.config = config or Image2Config.from_env()
        self._resolver = resolver or self._resolve_host
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.config.timeout_seconds),
            follow_redirects=False,
            transport=transport,
        )

    async def __aenter__(self) -> Image2Client:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _url(self, path: str) -> str:
        return urljoin(f"{self.config.base_url}/", path.lstrip("/"))

    @staticmethod
    def _data_url(image: Image2Input) -> str:
        encoded = base64.b64encode(image.data).decode("ascii")
        return f"data:{image.content_type};base64,{encoded}"

    @property
    def _is_gpt_image_2(self) -> bool:
        return self.config.model.lower().startswith("gpt-image-2")

    @staticmethod
    def _provider_size(size: str) -> str:
        return "1024x1024" if size == "1080x1080" else "1024x1536"

    def _prompt(self, request: Image2Request) -> str:
        if not request.negative_prompt:
            return request.prompt
        return f"{request.prompt}\n\n必须避免：{request.negative_prompt}"

    def build_payload(self, request: Image2Request) -> dict:
        payload: dict = {
            "model": self.config.model,
            "prompt": self._prompt(request),
            "size": self._provider_size(request.size),
            "n": request.n,
            "quality": "high",
            "output_format": "png",
        }
        if self.config.send_response_format:
            payload["response_format"] = "b64_json"
        reserved = {
            "model",
            "prompt",
            "negative_prompt",
            "size",
            "n",
            "images",
            "image",
            "mask",
            "mode",
            "quality",
            "input_fidelity",
            "output_format",
            "response_format",
            "template_replicate",
        }
        allowed_extra = {"background", "moderation", "output_compression", "user"}
        for key, value in request.extra.items():
            if key not in reserved and key in allowed_extra:
                payload[key] = value
        return payload

    async def probe_capabilities(self) -> Image2CapabilityProfile:
        """Probe relay reachability and model discovery without creating a paid image."""
        body = await self._request_json("GET", self._url("/models"))
        candidates = body.get("data") or body.get("models") or []
        if isinstance(candidates, dict):
            candidates = candidates.get("data") or candidates.get("items") or list(candidates.values())
        model_ids = {
            str(item.get("id") or item.get("model") or item.get("name") or "").strip()
            for item in candidates
            if isinstance(item, dict)
        }
        model_discovered = self.config.model in model_ids
        return Image2CapabilityProfile(
            model=self.config.model,
            reachable=True,
            model_discovered=model_discovered,
            supports_generation=True,
            supports_edit=True,
            supports_multi_reference=True,
            supports_mask=True,
            supports_async=None,
            unsupported_parameters=["input_fidelity"] if self._is_gpt_image_2 else [],
            checked_at=datetime.now(UTC),
            message=(
                "中转站可访问，已发现目标模型"
                if model_discovered
                else "中转站可访问，但模型列表中未发现目标模型；请确认中转站别名配置"
            ),
        )

    @staticmethod
    def _extract_outputs(body: dict) -> list[Image2Output]:
        candidates = (
            body.get("data")
            or body.get("images")
            or body.get("output")
            or body.get("result")
            or body.get("response")
            or []
        )
        if isinstance(candidates, dict):
            candidates = (
                candidates.get("images")
                or candidates.get("data")
                or candidates.get("output")
                or candidates.get("result")
                or [candidates]
            )
        if isinstance(candidates, str):
            candidates = [
                {"url": candidates} if candidates.startswith(("http://", "https://")) else {"b64_json": candidates}
            ]
        outputs: list[Image2Output] = []
        for item in candidates if isinstance(candidates, list) else []:
            if isinstance(item, str):
                if item.startswith(("http://", "https://")):
                    outputs.append(Image2Output(url=item))
                else:
                    outputs.append(Image2Output(b64_data=item))
                continue
            if not isinstance(item, dict):
                continue
            outputs.append(
                Image2Output(
                    url=item.get("url") or item.get("image_url") or item.get("imageUrl"),
                    b64_data=(
                        item.get("b64_json")
                        or item.get("b64Json")
                        or item.get("base64")
                        or item.get("b64")
                        or item.get("data_url")
                        or item.get("dataUrl")
                    ),
                    content_type=item.get("content_type") or item.get("contentType") or item.get("mime_type"),
                )
            )
        return [item for item in outputs if item.url or item.b64_data]

    @staticmethod
    def _normalize(body: dict) -> Image2Submission:
        outputs = Image2Client._extract_outputs(body)
        containers = [body]
        containers.extend(
            item for key in ("data", "result", "output", "response") if isinstance((item := body.get(key)), dict)
        )

        def first(*keys: str):
            return next(
                (container.get(key) for container in containers for key in keys if container.get(key) is not None),
                None,
            )

        raw_status = str(first("status", "state", "task_status") or "").lower()
        error = first("error", "error_message", "fail_reason")
        if isinstance(error, dict):
            error_message = str(error.get("message") or error.get("detail") or error)
        else:
            error_message = str(error or first("message", "detail") or "") or None
        task_id = first("task_id", "taskId", "id", "request_id", "requestId")
        if outputs and raw_status not in {"failed", "error", "cancelled", "canceled"}:
            status = "completed"
        elif raw_status in {"success", "succeeded", "completed", "done"}:
            status = "completed"
        elif raw_status in {"failed", "error", "cancelled", "canceled"}:
            status = "failed"
        elif task_id:
            status = "pending"
        else:
            status = "failed"
            error_message = error_message or "image2 响应中缺少任务 ID 和图片结果"
        return Image2Submission(
            provider_task_id=str(task_id) if task_id else None,
            status=status,
            images=outputs,
            error_message=error_message,
        )

    async def _request_json(self, method: str, url: str, **kwargs) -> dict:
        request_headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Accept": "application/json",
            **kwargs.pop("headers", {}),
        }
        response = None
        for attempt in range(3):
            try:
                response = await self._client.request(method, url, headers=request_headers, **kwargs)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if method == "GET" and attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise Image2Error("IMAGE2_NETWORK_ERROR", "image2 中转站连接失败", retryable=True) from exc
            can_retry = response.status_code == 429 or (method == "GET" and response.status_code >= 500)
            if can_retry and attempt < 2:
                retry_after = response.headers.get("retry-after", "")
                try:
                    delay = min(5.0, max(0.0, float(retry_after)))
                except ValueError:
                    delay = 0.5 * (2**attempt)
                await asyncio.sleep(delay)
                continue
            break
        if response is None:
            raise Image2Error("IMAGE2_NETWORK_ERROR", "image2 中转站连接失败", retryable=True)
        if response.status_code >= 400:
            retryable = response.status_code == 429 or response.status_code >= 500
            try:
                detail = response.json()
                error = detail.get("error") if isinstance(detail, dict) else None
                message = error.get("message") if isinstance(error, dict) else detail.get("message")
            except Exception:
                message = "请求失败"
            safe_message = str(message or "请求失败")[:500]
            for secret in (self.config.api_key, self.config.base_url):
                if secret:
                    safe_message = safe_message.replace(secret, "***")
            raise Image2Error(
                "IMAGE2_UPSTREAM_ERROR",
                f"image2 中转站返回 {response.status_code}：{safe_message}",
                retryable=retryable,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise Image2Error("IMAGE2_INVALID_RESPONSE", "image2 中转站返回了无效 JSON") from exc
        if not isinstance(body, dict):
            raise Image2Error("IMAGE2_INVALID_RESPONSE", "image2 中转站响应必须是 JSON 对象")
        return body

    async def submit(self, request: Image2Request, *, idempotency_key: str | None = None) -> Image2Submission:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        if request.mode != "text_to_image":
            if not request.source_images:
                raise Image2Error("IMAGE2_INPUT_REQUIRED", "image2 编辑请求至少需要一张原图")
            references = [*request.source_images]
            if request.template_image:
                references.append(request.template_image)
            if self.config.edit_request_format == "siliconflow_json":
                if request.mask_image:
                    raise Image2Error("IMAGE2_MODE_UNSUPPORTED", "SiliconFlow JSON 图片编辑不支持蒙版模式")
                if len(references) > 3:
                    raise Image2Error("IMAGE2_INPUT_LIMIT", "SiliconFlow 图片编辑最多支持三张参考图")
                payload = {
                    "model": self.config.edit_model,
                    "prompt": request.prompt,
                    **{("image" if index == 1 else f"image{index}"): self._data_url(item)
                       for index, item in enumerate(references, start=1)},
                }
                if request.negative_prompt:
                    payload["negative_prompt"] = request.negative_prompt
                body = await self._request_json(
                    "POST",
                    self._url(self.config.edit_path),
                    json=payload,
                    headers=headers,
                )
                result = self._normalize(body)
                if result.status == "failed":
                    raise Image2Error("IMAGE2_GENERATION_FAILED", result.error_message or "image2 生成失败")
                return result
            field_name = "image[]" if len(references) > 1 else "image"
            files = [(field_name, (item.file_name, item.data, item.content_type)) for item in references]
            if request.mask_image:
                files.append(
                    (
                        "mask",
                        (
                            request.mask_image.file_name,
                            request.mask_image.data,
                            request.mask_image.content_type,
                        ),
                    )
                )
            form = {
                "model": self.config.model,
                "prompt": self._prompt(request),
                "size": self._provider_size(request.size),
                "n": str(request.n),
                "quality": "high",
                "output_format": "png",
            }
            if self.config.send_response_format:
                form["response_format"] = "b64_json"
            for key in ("background", "output_compression", "user"):
                value = request.extra.get(key)
                if value is not None:
                    form[key] = str(value)
            body = await self._request_json(
                "POST",
                self._url(self.config.edit_path),
                data=form,
                files=files,
                headers=headers,
            )
        else:
            body = await self._request_json(
                "POST",
                self._url(self.config.submit_path),
                json=self.build_payload(request),
                headers=headers,
            )
        result = self._normalize(body)
        if result.status == "failed":
            raise Image2Error("IMAGE2_GENERATION_FAILED", result.error_message or "image2 生成失败")
        return result

    async def poll(self, task_id: str) -> Image2Submission:
        path = self.config.status_path.format(task_id=quote(task_id, safe=""))
        body = await self._request_json("GET", self._url(path))
        if not any(body.get(key) for key in ("task_id", "taskId", "id", "request_id", "requestId")):
            body["task_id"] = task_id
        return self._normalize(body)

    @staticmethod
    async def _resolve_host(host: str, port: int) -> list[str]:
        try:
            records = await asyncio.get_running_loop().getaddrinfo(
                host,
                port,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise Image2Error(
                "IMAGE2_OUTPUT_URL_INVALID",
                "image2 返回的图片地址无法解析",
            ) from exc
        return list({record[4][0] for record in records})

    async def _validate_output_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise Image2Error("IMAGE2_OUTPUT_URL_INVALID", "image2 返回了不安全的图片地址")
        relay = urlparse(self.config.base_url)
        try:
            parsed_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            relay_port = relay.port or (443 if relay.scheme == "https" else 80)
        except ValueError as exc:
            raise Image2Error("IMAGE2_OUTPUT_URL_INVALID", "image2 返回的图片地址端口无效") from exc
        trusted_origins = {(relay.scheme, relay.hostname, relay_port)}
        for origin in self.config.trusted_output_origins:
            configured = urlparse(origin)
            configured_port = configured.port or (443 if configured.scheme == "https" else 80)
            trusted_origins.add((configured.scheme, configured.hostname, configured_port))
        if (parsed.scheme, parsed.hostname, parsed_port) in trusted_origins:
            return True
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            addresses = [ipaddress.ip_address(item) for item in await self._resolver(parsed.hostname, parsed_port)]
        else:
            addresses = [address]
        if not addresses or any(not address.is_global for address in addresses):
            logger.warning(
                "Blocked image2 output host after DNS validation: host={} addresses={}",
                parsed.hostname,
                [str(address) for address in addresses],
            )
            raise Image2Error(
                "IMAGE2_OUTPUT_URL_INVALID",
                f"image2 返回了不允许访问的地址域名：{parsed.hostname}",
            )
        return False

    async def read_output(self, output: Image2Output, *, max_bytes: int = 30 * 1024 * 1024) -> tuple[bytes, str]:
        if output.b64_data:
            raw = output.b64_data
            content_type = output.content_type or "image/png"
            if raw.startswith("data:"):
                header, _, raw = raw.partition(",")
                content_type = header[5:].split(";", 1)[0] or content_type
            try:
                data = base64.b64decode(raw, validate=True)
            except ValueError as exc:
                raise Image2Error("IMAGE2_INVALID_IMAGE", "image2 返回的 base64 图片无效") from exc
            if len(data) > max_bytes:
                raise Image2Error("IMAGE2_IMAGE_TOO_LARGE", "image2 返回的图片超过 30 MB")
            return data, content_type
        if not output.url:
            raise Image2Error("IMAGE2_RESULT_EMPTY", "image2 没有返回图片")
        current_url = urljoin(f"{self.config.base_url}/", output.url)
        try:
            for redirect_count in range(6):
                trusted_origin = await self._validate_output_url(current_url)
                download_headers = {"Accept": "image/*"}
                if trusted_origin:
                    download_headers["Authorization"] = f"Bearer {self.config.api_key}"
                async with self._client.stream("GET", current_url, headers=download_headers) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location or redirect_count == 5:
                            raise Image2Error("IMAGE2_DOWNLOAD_FAILED", "image2 结果图片重定向无效")
                        current_url = urljoin(current_url, location)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "image/png").split(";", 1)[0]
                    if not content_type.startswith("image/") and content_type != "application/octet-stream":
                        raise Image2Error("IMAGE2_INVALID_IMAGE", "image2 返回地址不是图片")
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > max_bytes:
                            raise Image2Error("IMAGE2_IMAGE_TOO_LARGE", "image2 返回的图片超过 30 MB")
                    return bytes(chunks), content_type
            raise Image2Error("IMAGE2_DOWNLOAD_FAILED", "image2 结果图片重定向过多")
        except Image2Error:
            raise
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            raise Image2Error("IMAGE2_DOWNLOAD_FAILED", "image2 结果图片下载失败", retryable=True) from exc
