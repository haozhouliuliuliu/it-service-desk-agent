"""任务状态与对外视图模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class TaskStatus(StrEnum):
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    LIMIT_REACHED = "limit_reached"

    @property
    def terminal(self) -> bool:
        return self in {TaskStatus.COMPLETED, TaskStatus.LIMIT_REACHED}


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"


class ApprovalView(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_id: str
    call_id: str
    tool_name: str
    args: dict[str, Any]
    summary: str
    status: str
    created_at: str


class TaskState(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    status: str
    user_input: str
    final_answer: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    pending_question: str | None = None
    step_count: int = 0
    tool_call_count: int = 0
    model: str = ""
    created_at: str = ""
    updated_at: str = ""
    pending_approval: ApprovalView | None = None


class EventView(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int
    type: str
    payload: dict[str, Any]
    created_at: str


class ToolCallView(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: str
    tool_name: str
    args: dict[str, Any]
    result: dict[str, Any]
    ok: bool
    error_code: str | None = None
    created_at: str
