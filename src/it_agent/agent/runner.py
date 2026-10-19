"""Agent 核心循环。

流程（不是固定业务步骤）：
模型 → 工具调用 → 程序校验/执行 → 结果回灌 → 模型再决定 → …… → 最终答复或暂停。

程序负责的硬约束：
- 最多 ``max_tool_calls`` 次工具调用、``max_steps`` 轮模型交互；
- 写工具没有确认凭证不执行；
- 工具不存在/参数非法只回灌错误，不崩溃；
- LLM 失败有限重试，最终以 ``failed`` 结束，绝不伪装成功。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from ..context import ToolContext
from ..errors import AgentError
from ..llm.client import ChatClient, ChatResponse, LLMError, ToolCall
from ..tools import ToolErrorCode, ToolRegistry, ToolResult, args_fingerprint
from .prompts import SYSTEM_PROMPT
from .repository import AgentRepository, TaskNotFound
from .state import ApprovalStatus, TaskState, TaskStatus

LOGGER = logging.getLogger("it_agent.agent")

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    TaskStatus.RUNNING.value: {
        TaskStatus.WAITING_FOR_INPUT.value,
        TaskStatus.WAITING_FOR_APPROVAL.value,
        TaskStatus.COMPLETED.value,
        TaskStatus.FAILED.value,
        TaskStatus.LIMIT_REACHED.value,
    },
    TaskStatus.WAITING_FOR_INPUT.value: {TaskStatus.RUNNING.value, TaskStatus.FAILED.value},
    TaskStatus.WAITING_FOR_APPROVAL.value: {TaskStatus.RUNNING.value, TaskStatus.FAILED.value},
    TaskStatus.FAILED.value: {TaskStatus.RUNNING.value},
    TaskStatus.COMPLETED.value: set(),
    TaskStatus.LIMIT_REACHED.value: set(),
}


class AgentRunner:
    def __init__(
        self,
        *,
        repository: AgentRepository,
        registry: ToolRegistry,
        context: ToolContext,
        client: ChatClient,
        max_tool_calls: int = 8,
        max_steps: int = 12,
        max_llm_retries: int = 2,
        backoff_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_tool_calls < 1 or max_steps < 1:
            raise ValueError("max_tool_calls 与 max_steps 必须大于 0")
        self.repository = repository
        self.registry = registry
        self.context = context
        self.client = client
        self.max_tool_calls = max_tool_calls
        self.max_steps = max_steps
        self.max_llm_retries = max_llm_retries
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep

    # ---------------------------------------------------------------- 对外入口

    def create_task(self, user_input: str, *, task_id: str | None = None) -> TaskState:
        text = user_input.strip()
        if not text:
            raise AgentError("任务内容不能为空")
        if len(text) > 500:
            raise AgentError("任务内容不能超过 500 字")

        identifier = task_id or f"TASK-{uuid.uuid4().hex[:8]}"
        self.repository.create_task(identifier, text, self.client.model)
        self.repository.append_message(identifier, role="system", content=SYSTEM_PROMPT)
        self.repository.append_message(identifier, role="user", content=text)
        self.repository.add_event(
            identifier,
            "task_created",
            {"model": self.client.model, "max_tool_calls": self.max_tool_calls, "max_steps": self.max_steps},
        )
        return self.run(identifier)

    def run(self, task_id: str) -> TaskState:
        while True:
            task = self.repository.get_task(task_id)
            if task.status != TaskStatus.RUNNING.value:
                return task

            if task.step_count >= self.max_steps:
                return self._finish(
                    task_id,
                    TaskStatus.LIMIT_REACHED,
                    error_code="STEP_LIMIT",
                    error_message=f"达到模型交互上限（{self.max_steps} 轮），任务未完成",
                )

            response = self._call_llm(task)
            if response is None:
                return self.repository.get_task(task_id)

            self.repository.update_task(task_id, step_count=task.step_count + 1)
            self._append_assistant(task_id, response)
            self.repository.add_event(
                task_id,
                "llm_response",
                {
                    "tool_calls": [call.name for call in response.tool_calls],
                    "has_content": bool((response.content or "").strip()),
                },
            )

            if response.tool_calls:
                for index, call in enumerate(response.tool_calls):
                    current = self.repository.get_task(task_id)
                    if current.tool_call_count >= self.max_tool_calls:
                        return self._finish(
                            task_id,
                            TaskStatus.LIMIT_REACHED,
                            error_code="TOOL_CALL_LIMIT",
                            error_message=f"达到工具调用上限（{self.max_tool_calls} 次），任务未完成",
                        )
                    paused = self._handle_tool_call(current, call)
                    if paused:
                        for skipped in response.tool_calls[index + 1 :]:
                            skipped_result = ToolResult.failure(
                                ToolErrorCode.TOOL_ERROR,
                                "因前一个操作需要人工确认或补充信息，本次调用未执行",
                            )
                            self._record_tool_result(
                                task_id,
                                skipped.id,
                                skipped.name,
                                skipped.arguments or {},
                                skipped_result,
                            )
                        return self.repository.get_task(task_id)
                continue

            content = (response.content or "").strip()
            if content:
                self._transition(task_id, TaskStatus.COMPLETED, final_answer=content)
                self.repository.add_event(task_id, "task_completed", {"answer_length": len(content)})
                return self.repository.get_task(task_id)

            return self._finish(
                task_id,
                TaskStatus.FAILED,
                error_code="EMPTY_RESPONSE",
                error_message="模型既没有调用工具也没有给出答复",
            )

    def submit_input(self, task_id: str, text: str) -> TaskState:
        task = self.repository.get_task(task_id)
        if task.status != TaskStatus.WAITING_FOR_INPUT.value:
            raise AgentError(f"任务当前状态为 {task.status}，不在等待补充信息")
        content = text.strip()
        if not content:
            raise AgentError("补充内容不能为空")
        self.repository.append_message(task_id, role="user", content=content)
        self.repository.add_event(task_id, "user_input", {"text": content})
        self.repository.update_task(task_id, pending_question=None)
        self._transition(task_id, TaskStatus.RUNNING)
        return self.run(task_id)

    def decide_approval(self, task_id: str, approval_id: str, *, decision: str, decided_by: str) -> TaskState:
        if decision not in {"approve", "reject"}:
            raise AgentError("decision 必须是 approve 或 reject")
        if not decided_by.strip():
            raise AgentError("decided_by 不能为空")

        task = self.repository.get_task(task_id)
        approval = self.repository.get_approval(approval_id)
        if approval is None:
            raise TaskNotFound(f"确认请求不存在：{approval_id}")
        if self.repository.approval_task(approval_id) != task_id:
            raise AgentError("确认请求不属于该任务")
        if approval.status == ApprovalStatus.EXECUTED.value:
            return task
        if approval.status == ApprovalStatus.REJECTED.value:
            raise AgentError("该操作已经被拒绝，不能再次执行")
        if task.status != TaskStatus.WAITING_FOR_APPROVAL.value:
            raise AgentError(f"任务当前状态为 {task.status}，不在等待确认")

        if decision == "reject":
            self.repository.update_approval(
                approval_id, status=ApprovalStatus.REJECTED, decided_by=decided_by, result=None
            )
            result = ToolResult.failure(ToolErrorCode.PERMISSION_DENIED, "用户拒绝该操作，未执行任何写入")
            self._record_tool_result(task_id, approval.call_id, approval.tool_name, approval.args, result)
            self.repository.add_event(task_id, "approval_rejected", {"approval_id": approval_id, "by": decided_by})
            self._transition(task_id, TaskStatus.RUNNING)
            return self.run(task_id)

        self.repository.update_approval(approval_id, status=ApprovalStatus.APPROVED, decided_by=decided_by)
        token = self.registry.build_approval(approval_id, approval.tool_name, approval.args)
        result = self.registry.invoke(approval.tool_name, approval.args, self.context, approval=token)
        self._record_tool_result(task_id, approval.call_id, approval.tool_name, approval.args, result)
        self.repository.update_approval(
            approval_id,
            status=ApprovalStatus.EXECUTED,
            result=result.model_dump(mode="json"),
        )
        self.repository.add_event(
            task_id,
            "approval_executed",
            {
                "approval_id": approval_id,
                "by": decided_by,
                "tool": approval.tool_name,
                "ok": result.ok,
            },
        )
        self._transition(task_id, TaskStatus.RUNNING)
        return self.run(task_id)

    def resume(self, task_id: str) -> TaskState:
        task = self.repository.get_task(task_id)
        if task.status != TaskStatus.FAILED.value:
            raise AgentError(f"任务当前状态为 {task.status}，只有失败任务可以恢复")
        if task.error_code not in {
            "INTERRUPTED",
            "LLM_TIMEOUT",
            "LLM_CONNECTION_ERROR",
            "LLM_HTTP_429",
            "LLM_HTTP_5XX",
            "LLM_INVALID_RESPONSE",
        }:
            raise AgentError(f"错误类型 {task.error_code} 不允许自动恢复，请人工处理")
        self._transition(task_id, TaskStatus.RUNNING, error_code=None, error_message=None)
        self.repository.add_event(task_id, "task_resumed", {})
        return self.run(task_id)

    def recover_interrupted(self) -> int:
        return self.repository.mark_interrupted()

    # ---------------------------------------------------------------- LLM

    def _call_llm(self, task: TaskState) -> ChatResponse | None:
        messages = self.repository.messages(task.task_id)
        tools = self.registry.schemas()
        for attempt in range(self.max_llm_retries + 1):
            self.repository.add_event(task.task_id, "llm_request", {"attempt": attempt + 1, "messages": len(messages)})
            try:
                return self.client.complete(messages, tools)
            except LLMError as exc:
                self.repository.add_event(
                    task.task_id,
                    "llm_error",
                    {"code": exc.code, "attempt": attempt + 1, "retryable": exc.retryable},
                )
                if not exc.retryable or attempt >= self.max_llm_retries:
                    self._finish(
                        task.task_id,
                        TaskStatus.FAILED,
                        error_code=exc.code,
                        error_message=exc.message,
                    )
                    return None
                self.sleep(self.backoff_seconds * (2**attempt))
            except Exception as exc:  # 未预期异常也不能伪装成功
                LOGGER.exception("llm_unexpected_error", extra={"task_id": task.task_id})
                self.repository.add_event(task.task_id, "llm_error", {"code": "LLM_INTERNAL", "attempt": attempt + 1})
                self._finish(
                    task.task_id,
                    TaskStatus.FAILED,
                    error_code="LLM_INTERNAL",
                    error_message=str(exc)[:300],
                )
                return None
        return None

    # ---------------------------------------------------------------- tools

    def _handle_tool_call(self, task: TaskState, call: ToolCall) -> bool:
        """返回 True 表示循环应暂停（等待确认/补充信息）。"""

        task_id = task.task_id
        self.repository.update_task(task_id, tool_call_count=task.tool_call_count + 1)
        self.repository.add_event(
            task_id,
            "tool_call_requested",
            {"call_id": call.id, "tool": call.name, "arguments": call.arguments},
        )

        spec = self.registry.get(call.name)
        if spec is None:
            result = ToolResult.failure(ToolErrorCode.TOOL_NOT_FOUND, f"未注册的工具：{call.name}")
            self._record_tool_result(task_id, call.id, call.name, call.arguments or {}, result)
            return False

        if call.parse_error or call.arguments is None:
            result = ToolResult.failure(
                ToolErrorCode.INVALID_ARGUMENTS,
                call.parse_error or "工具参数不是合法 JSON 对象",
            )
            self._record_tool_result(task_id, call.id, call.name, {}, result)
            return False

        if spec.requires_approval:
            return self._handle_write(task_id, call)

        result = self.registry.invoke(call.name, call.arguments, self.context)
        self._record_tool_result(task_id, call.id, call.name, call.arguments, result)

        if call.name == "request_user_input" and result.ok:
            question = str((result.data or {}).get("question", ""))
            self.repository.update_task(task_id, pending_question=question)
            self.repository.add_event(task_id, "input_requested", {"question": question})
            self._transition(task_id, TaskStatus.WAITING_FOR_INPUT)
            return True
        return False

    def _handle_write(self, task_id: str, call: ToolCall) -> bool:
        spec = self.registry.get(call.name)
        assert spec is not None
        try:
            normalized = spec.arg_model.model_validate(call.arguments or {}).model_dump(mode="json")
        except ValidationError as exc:
            details = tuple(
                f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
                for error in exc.errors()[:5]
            )
            result = ToolResult.failure(ToolErrorCode.INVALID_ARGUMENTS, "工具参数不合法", details)
            self._record_tool_result(task_id, call.id, call.name, call.arguments or {}, result)
            return False

        digest = args_fingerprint(call.name, normalized)
        existing = self.repository.find_approval(task_id, call.name, digest)
        if existing is not None:
            if existing.status == ApprovalStatus.PENDING.value:
                self._transition(task_id, TaskStatus.WAITING_FOR_APPROVAL)
                return True
            if existing.status == ApprovalStatus.EXECUTED.value:
                stored = self.repository.approval_status(existing.approval_id)[1]
                result = ToolResult.model_validate(json.loads(stored or "{}"))
                self._record_tool_result(task_id, call.id, call.name, normalized, result)
                self.repository.add_event(
                    task_id, "approval_replayed", {"approval_id": existing.approval_id, "call_id": call.id}
                )
                return False
            result = ToolResult.failure(ToolErrorCode.PERMISSION_DENIED, "用户已拒绝过该操作，未执行")
            self._record_tool_result(task_id, call.id, call.name, normalized, result)
            return False

        approval_id = f"APR-{uuid.uuid4().hex[:8]}"
        summary = _summarize(call.name, normalized)
        self.repository.create_approval(
            task_id,
            approval_id=approval_id,
            call_id=call.id,
            tool_name=call.name,
            args=normalized,
            args_hash=digest,
            summary=summary,
        )
        self.repository.add_event(
            task_id,
            "approval_requested",
            {"approval_id": approval_id, "tool": call.name, "summary": summary},
        )
        self._transition(task_id, TaskStatus.WAITING_FOR_APPROVAL)
        return True

    def _record_tool_result(
        self,
        task_id: str,
        call_id: str,
        tool_name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> None:
        payload = result.model_dump(mode="json")
        self.repository.record_tool_call(
            task_id,
            call_id=call_id,
            tool_name=tool_name,
            args=args,
            result=payload,
            ok=result.ok,
            error_code=result.error.code.value if result.error else None,
        )
        self.repository.append_message(
            task_id,
            role="tool",
            content=json.dumps(payload, ensure_ascii=False),
            tool_call_id=call_id,
        )
        self.repository.add_event(
            task_id,
            "tool_call_finished",
            {
                "call_id": call_id,
                "tool": tool_name,
                "ok": result.ok,
                "error": result.error.code.value if result.error else None,
            },
        )

    # ---------------------------------------------------------------- 状态与消息

    def _append_assistant(self, task_id: str, response: ChatResponse) -> None:
        tool_calls = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.raw_arguments},
            }
            for call in response.tool_calls
        ]
        self.repository.append_message(
            task_id,
            role="assistant",
            content=response.content or "",
            tool_calls=tool_calls or None,
        )

    def _finish(self, task_id: str, status: TaskStatus, *, error_code: str, error_message: str) -> TaskState:
        self._transition(task_id, status, error_code=error_code, error_message=error_message)
        self.repository.add_event(task_id, "task_finished", {"status": status.value, "error_code": error_code})
        return self.repository.get_task(task_id)

    def _transition(self, task_id: str, new_status: TaskStatus, **fields: Any) -> None:
        current = self.repository.get_task(task_id)
        allowed = _ALLOWED_TRANSITIONS.get(current.status, set())
        if new_status.value not in allowed and current.status != new_status.value:
            raise AgentError(f"非法状态迁移：{current.status} → {new_status.value}")
        if current.status == new_status.value:
            return
        self.repository.update_task(task_id, status=new_status.value, **fields)
        self.repository.add_event(
            task_id,
            "status_changed",
            {"from": current.status, "to": new_status.value},
        )


def _summarize(tool: str, args: dict[str, Any]) -> str:
    if tool == "create_ticket":
        return (
            f"创建工单《{args.get('title')}》，分类 {args.get('category')}，"
            f"优先级 {args.get('priority')}，申请人 {args.get('requester')}"
        )
    if tool == "update_ticket":
        changes = ", ".join(f"{key}={value}" for key, value in args.items() if key != "ticket_id" and value is not None)
        return f"修改工单 {args.get('ticket_id')}：{changes}"
    return f"{tool} {json.dumps(args, ensure_ascii=False)}"
