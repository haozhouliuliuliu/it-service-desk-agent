"""LLM 层：OpenAI 兼容客户端与脚本化模型。"""

from __future__ import annotations

from .client import ChatClient, ChatResponse, LLMError, LLMSettings, OpenAICompatibleClient, ToolCall
from .fake import ScriptedClient, answer, call, tool_response

__all__ = [
    "ChatClient",
    "ChatResponse",
    "LLMError",
    "LLMSettings",
    "OpenAICompatibleClient",
    "ScriptedClient",
    "ToolCall",
    "answer",
    "call",
    "tool_response",
]
