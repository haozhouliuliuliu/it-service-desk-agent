"""LLM 客户端测试：请求构造、工具解析、HTTP 错误映射（不发真实请求）。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from it_agent.errors import ConfigError
from it_agent.llm.client import LLMError, LLMSettings, OpenAICompatibleClient

ENV = {
    "LLM_API_BASE": "https://api.example.invalid/v1",
    "LLM_API_KEY": "sk-test-key",
    "LLM_MODEL": "demo-model",
    "LLM_TIMEOUT_SECONDS": "30",
    "LLM_MAX_RETRIES": "1",
}


def _settings(**overrides: Any) -> LLMSettings:
    payload = {
        "api_base": "https://api.example.invalid/v1",
        "api_key": "sk-test-key",
        "model": "demo-model",
    }
    payload.update(overrides)
    return LLMSettings(**payload)


def _client(handler, **overrides: Any) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(_settings(**overrides), transport=httpx.MockTransport(handler))


def _ok(message: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": message}]})


def test_settings_from_env() -> None:
    settings = LLMSettings.from_env(ENV)
    assert settings.model == "demo-model"
    assert settings.max_retries == 1
    assert settings.timeout_seconds == 30


def test_missing_key_is_rejected() -> None:
    with pytest.raises(ConfigError):
        LLMSettings.from_env({"LLM_MODEL": "x"})


def test_bad_retries_is_rejected() -> None:
    with pytest.raises(ConfigError):
        LLMSettings.from_env({**ENV, "LLM_MAX_RETRIES": "many"})


def test_key_not_in_repr() -> None:
    assert "sk-test-key" not in repr(_settings())


def test_request_shape() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return _ok({"content": "ok"})

    tools = [{"type": "function", "function": {"name": "x", "parameters": {"type": "object"}}}]
    with _client(handler) as client:
        response = client.complete([{"role": "user", "content": "hi"}], tools)

    assert captured["url"] == "https://api.example.invalid/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-test-key"
    assert captured["body"]["model"] == "demo-model"
    assert captured["body"]["tools"] == tools
    assert captured["body"]["tool_choice"] == "auto"
    assert response.content == "ok"


def test_tool_call_parsing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok(
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "query_inventory", "arguments": '{"category": "手机"}'},
                    }
                ],
            }
        )

    with _client(handler) as client:
        response = client.complete([], [])
    assert response.content is None
    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.name == "query_inventory"
    assert call.arguments == {"category": "手机"}
    assert call.parse_error is None


def test_malformed_tool_arguments_are_marked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok(
            {
                "content": None,
                "tool_calls": [{"id": "c1", "function": {"name": "get_ticket", "arguments": "{bad"}}],
            }
        )

    with _client(handler) as client:
        response = client.complete([], [])
    assert response.tool_calls[0].arguments is None
    assert response.tool_calls[0].parse_error


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (500, "LLM_HTTP_5XX", True),
        (503, "LLM_HTTP_5XX", True),
        (429, "LLM_HTTP_429", True),
        (401, "LLM_HTTP_401", False),
        (404, "LLM_HTTP_404", False),
        (422, "LLM_HTTP_4XX", False),
    ],
)
def test_http_status_mapping(status: int, code: str, retryable: bool) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="nope")

    with _client(handler) as client, pytest.raises(LLMError) as excinfo:
        client.complete([], [])
    assert excinfo.value.code == code
    assert excinfo.value.retryable is retryable


def test_timeout_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    with _client(handler) as client, pytest.raises(LLMError) as excinfo:
        client.complete([], [])
    assert excinfo.value.code == "TIMEOUT"
    assert excinfo.value.retryable is True


def test_connection_error_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with _client(handler) as client, pytest.raises(LLMError) as excinfo:
        client.complete([], [])
    assert excinfo.value.code == "CONNECTION_ERROR"


def test_non_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>")

    with _client(handler) as client, pytest.raises(LLMError) as excinfo:
        client.complete([], [])
    assert excinfo.value.code == "LLM_INVALID_RESPONSE"


def test_missing_choices() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "x"})

    with _client(handler) as client, pytest.raises(LLMError) as excinfo:
        client.complete([], [])
    assert excinfo.value.code == "LLM_INVALID_RESPONSE"


def test_empty_response_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok({"content": None})

    with _client(handler) as client, pytest.raises(LLMError) as excinfo:
        client.complete([], [])
    assert excinfo.value.code == "LLM_EMPTY_RESPONSE"
