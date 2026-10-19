"""工具注册表：白名单、参数校验、权限判断与错误收敛。

模型只能"提议"工具与参数；是否允许执行由这里决定。
写工具在没有 ``ApprovalToken`` 时一律拒绝，确认逻辑在 M3 的审批服务里。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from ..errors import AgentError
from ..store import Conflict, NotFound, StoreError
from .schemas import ToolArgs

LOGGER = logging.getLogger("it_agent.tools")


class ToolPermission(StrEnum):
    READ = "read"
    WRITE = "write"
    CONTROL = "control"


class ToolErrorCode(StrEnum):
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    TOOL_ERROR = "TOOL_ERROR"
    INTERNAL = "INTERNAL"


class ToolErrorInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: ToolErrorCode
    message: str
    details: tuple[str, ...] = ()


class ToolResult(BaseModel):
    """统一返回结构：成功给 data，失败给 error，模型据此决定下一步。"""

    model_config = ConfigDict(frozen=True)

    ok: bool
    data: Any | None = None
    error: ToolErrorInfo | None = None

    @classmethod
    def success(cls, data: Any) -> ToolResult:
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, code: ToolErrorCode, message: str, details: tuple[str, ...] = ()) -> ToolResult:
        return cls(ok=False, error=ToolErrorInfo(code=code, message=message, details=details))


class ApprovalToken(BaseModel):
    """人工确认后由审批服务签发的一次性凭证。"""

    model_config = ConfigDict(frozen=True)

    action_id: str
    tool: str
    args_hash: str


Handler = Callable[[Any, BaseModel], Any]


def canonical_args(tool: str, args: dict[str, Any]) -> bytes:
    payload = {"tool": tool, "args": args}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def args_fingerprint(tool: str, args: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_args(tool, args)).hexdigest()


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    permission: ToolPermission
    arg_model: type[ToolArgs]
    handler: Handler = field(repr=False)

    @property
    def requires_approval(self) -> bool:
        return self.permission is ToolPermission.WRITE

    def parameters(self) -> dict[str, Any]:
        schema = self.arg_model.model_json_schema()
        schema.pop("title", None)
        return schema

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters(),
            },
        }


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec]) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            if spec.name in self._specs:
                raise ValueError(f"工具重复注册：{spec.name}")
            self._specs[spec.name] = spec

    def names(self) -> list[str]:
        return sorted(self._specs)

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [self._specs[name].openai_schema() for name in self.names()]

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "permission": spec.permission.value,
                "requires_approval": spec.requires_approval,
                "parameters": spec.parameters(),
            }
            for spec in (self._specs[name] for name in self.names())
        ]

    def invoke(
        self,
        name: str,
        raw_args: dict[str, Any] | None,
        context: Any,
        *,
        approval: ApprovalToken | None = None,
    ) -> ToolResult:
        spec = self._specs.get(name)
        if spec is None:
            return ToolResult.failure(ToolErrorCode.TOOL_NOT_FOUND, f"未注册的工具：{name}")

        try:
            args = spec.arg_model.model_validate(raw_args or {})
        except ValidationError as exc:
            details = tuple(
                f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
                for error in exc.errors()[:5]
            )
            return ToolResult.failure(ToolErrorCode.INVALID_ARGUMENTS, "工具参数不合法", details)

        normalized = args.model_dump(mode="json")
        if spec.requires_approval:
            if approval is None:
                return ToolResult.failure(
                    ToolErrorCode.PERMISSION_DENIED,
                    "写操作必须经过人工确认，当前没有有效确认凭证",
                )
            if approval.tool != name or approval.args_hash != args_fingerprint(name, normalized):
                return ToolResult.failure(
                    ToolErrorCode.PERMISSION_DENIED,
                    "确认凭证与本次调用的工具或参数不一致",
                )

        try:
            data = spec.handler(context, args)
        except NotFound as exc:
            return ToolResult.failure(ToolErrorCode.NOT_FOUND, str(exc))
        except Conflict as exc:
            return ToolResult.failure(ToolErrorCode.CONFLICT, str(exc))
        except StoreError as exc:
            return ToolResult.failure(ToolErrorCode.TOOL_ERROR, str(exc))
        except AgentError as exc:
            return ToolResult.failure(ToolErrorCode.TOOL_ERROR, str(exc))
        except Exception:
            LOGGER.exception("tool_failed", extra={"tool": name})
            return ToolResult.failure(ToolErrorCode.INTERNAL, "工具执行失败，请稍后重试或改走人工")

        LOGGER.info("tool_ok", extra={"tool": name, "permission": spec.permission.value})
        return ToolResult.success(data)

    def build_approval(self, action_id: str, name: str, raw_args: dict[str, Any]) -> ApprovalToken:
        """仅在人工确认后调用；用于构造一次性凭证。"""

        spec = self._specs.get(name)
        if spec is None:
            raise ValueError(f"未注册的工具：{name}")
        args = spec.arg_model.model_validate(raw_args)
        return ApprovalToken(
            action_id=action_id,
            tool=name,
            args_hash=args_fingerprint(name, args.model_dump(mode="json")),
        )
