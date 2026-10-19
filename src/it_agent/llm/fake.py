"""脚本化模型：用于自动化测试与本地演示，**不是真实模型能力**。

真实模型评估必须使用 ``OpenAICompatibleClient``，两者的证据在仓库里分开存放。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .client import ChatResponse, ToolCall


@dataclass
class ScriptedClient:
    model: str = "scripted"
    responses: list[ChatResponse | Exception] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __init__(
        self,
        responses: Sequence[ChatResponse | Exception] | None = None,
        *,
        model: str = "scripted",
    ) -> None:
        self.model = model
        self.responses = list(responses or [])
        self.calls = []

    def queue(self, *responses: ChatResponse | Exception) -> ScriptedClient:
        self.responses.extend(responses)
        return self

    def complete(self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]) -> ChatResponse:
        self.calls.append({"messages": list(messages), "tools": list(tools)})
        if not self.responses:
            raise RuntimeError("ScriptedClient 没有更多预置响应")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def answer(content: str) -> ChatResponse:
    return ChatResponse(content=content, tool_calls=())


def call(name: str, arguments: dict[str, Any] | None = None, *, call_id: str | None = None) -> ToolCall:
    arguments = arguments or {}
    return ToolCall(
        id=call_id or f"call_{name}_{len(arguments)}",
        name=name,
        raw_arguments=json.dumps(arguments, ensure_ascii=False),
        arguments=arguments,
    )


def tool_response(*calls: ToolCall, content: str | None = None) -> ChatResponse:
    return ChatResponse(content=content, tool_calls=tuple(calls))
