"""HTTP 请求/响应模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..agent.state import TaskState


class CreateTaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=500, description="用户的自然语言任务")


class InputIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=500, description="补充的信息")


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    decided_by: str = Field(min_length=1, max_length=64)


class TaskAccepted(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    status: str
    detail_url: str


class TaskListOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[TaskState, ...]
    total: int
    limit: int
    offset: int


class HealthOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    version: str
    model: str
    recovered_tasks: int = 0
