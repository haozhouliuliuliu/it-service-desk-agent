"""OpenAI 兼容的对话客户端（支持 tool calling）。

只负责一次 HTTP 调用：超时/错误映射成带 code 的 ``LLMError``，
是否重试由 Agent 循环决定，这样重试次数与事件都能被记录。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from ..errors import ConfigError


class LLMError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    raw_arguments: str
    arguments: dict[str, Any] | None
    parse_error: str | None = None


@dataclass(frozen=True, slots=True)
class ChatResponse:
    content: str | None
    tool_calls: tuple[ToolCall, ...]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class LLMSettings:
    api_base: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-flash"
    api_key: str = field(default="", repr=False)
    timeout_seconds: float = 60.0
    max_retries: int = 2
    temperature: float = 0.0

    def __post_init__(self) -> None:
        if not self.api_base.startswith(("http://", "https://")):
            raise ConfigError("LLM_API_BASE 必须以 http:// 或 https:// 开头")
        if not self.model:
            raise ConfigError("LLM_MODEL 不能为空")
        if self.timeout_seconds <= 0:
            raise ConfigError("LLM_TIMEOUT_SECONDS 必须大于 0")
        if self.max_retries < 0:
            raise ConfigError("LLM_MAX_RETRIES 不能为负")

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        require_key: bool = True,
    ) -> LLMSettings:
        source = os.environ if env is None else env
        key = (source.get("LLM_API_KEY") or "").strip()
        if require_key and not key:
            raise ConfigError("缺少环境变量 LLM_API_KEY（参考 .env.example）")

        def number(name: str, default: float) -> float:
            raw = (source.get(name) or "").strip()
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError as exc:
                raise ConfigError(f"{name} 必须是数字，收到 {raw!r}") from exc

        retries_raw = (source.get("LLM_MAX_RETRIES") or "").strip()
        try:
            retries = int(retries_raw) if retries_raw else 2
        except ValueError as exc:
            raise ConfigError(f"LLM_MAX_RETRIES 必须是整数，收到 {retries_raw!r}") from exc

        return cls(
            api_base=(source.get("LLM_API_BASE") or "https://api.deepseek.com/v1").strip(),
            model=(source.get("LLM_MODEL") or "deepseek-flash").strip(),
            api_key=key,
            timeout_seconds=number("LLM_TIMEOUT_SECONDS", 60.0),
            max_retries=retries,
            temperature=number("LLM_TEMPERATURE", 0.0),
        )


class ChatClient(Protocol):
    model: str

    def complete(self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]) -> ChatResponse: ...


class OpenAICompatibleClient:
    name = "openai-compatible"

    def __init__(
        self,
        settings: LLMSettings,
        *,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = settings.model
        self._settings = settings
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(settings.timeout_seconds),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenAICompatibleClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def complete(self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]) -> ChatResponse:
        settings = self._settings
        url = settings.api_base.rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = {
            "model": settings.model,
            "messages": list(messages),
            "temperature": settings.temperature,
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"

        try:
            response = self._client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {settings.api_key}"},
            )
        except httpx.TimeoutException as exc:
            raise LLMError("TIMEOUT", f"模型请求超时（{settings.timeout_seconds:g}s）", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise LLMError("CONNECTION_ERROR", f"网络错误：{exc.__class__.__name__}", retryable=True) from exc

        self._raise_for_status(response)
        return self._parse(response)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        detail = (response.text or "").strip().replace("\n", " ")[:200]
        if status in (401, 403):
            raise LLMError("LLM_HTTP_401", f"模型鉴权失败（{status}）：{detail}", retryable=False)
        if status == 404:
            raise LLMError("LLM_HTTP_404", f"模型接口不存在（404）：{detail}", retryable=False)
        if status == 429:
            raise LLMError("LLM_HTTP_429", f"模型限流（429）：{detail}", retryable=True)
        if status >= 500:
            raise LLMError("LLM_HTTP_5XX", f"模型服务端错误（{status}）：{detail}", retryable=True)
        raise LLMError("LLM_HTTP_4XX", f"模型请求被拒绝（{status}）：{detail}", retryable=False)

    @staticmethod
    def _parse(response: httpx.Response) -> ChatResponse:
        try:
            payload = response.json()
        except ValueError as exc:
            raise LLMError("LLM_INVALID_RESPONSE", "模型响应不是合法 JSON", retryable=True) from exc

        try:
            choice = payload["choices"][0]
            message = choice.get("message") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM_INVALID_RESPONSE", "模型响应缺少 choices[0].message", retryable=True) from exc

        content = message.get("content")
        if content is not None and not isinstance(content, str):
            content = str(content)

        calls: list[ToolCall] = []
        for index, item in enumerate(message.get("tool_calls") or []):
            function = item.get("function") or {}
            name = function.get("name") or ""
            raw_arguments = function.get("arguments") or "{}"
            arguments: dict[str, Any] | None = None
            parse_error: str | None = None
            try:
                decoded = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                parse_error = f"工具参数不是合法 JSON：{exc.msg}"
            else:
                if isinstance(decoded, dict):
                    arguments = decoded
                else:
                    parse_error = "工具参数必须是 JSON 对象"
            calls.append(
                ToolCall(
                    id=item.get("id") or f"call_{index}",
                    name=name,
                    raw_arguments=raw_arguments,
                    arguments=arguments,
                    parse_error=parse_error,
                )
            )

        if content is None and not calls:
            raise LLMError("LLM_EMPTY_RESPONSE", "模型既没有返回内容也没有工具调用", retryable=False)

        return ChatResponse(content=content, tool_calls=tuple(calls), raw=payload)
