"""工具参数模型：Pydantic 校验 + 供 LLM 使用的 JSON Schema。

所有模型 ``extra="forbid"``：模型多传字段会被拒绝，而不是被静默忽略。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TicketCategoryLiteral = Literal["hardware", "software", "network", "account", "other"]
PriorityLiteral = Literal["P1", "P2", "P3", "P4"]
TicketStatusLiteral = Literal["open", "in_progress", "pending", "resolved", "closed"]


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchPoliciesArgs(ToolArgs):
    query: str = Field(min_length=1, max_length=200, description="检索关键词，例如：新员工 电脑 领用 规定")
    limit: int = Field(default=3, ge=1, le=5, description="返回的片段数量，最多 5 条")


class QueryInventoryArgs(ToolArgs):
    category: Literal["笔记本电脑", "显示器", "扩展坞", "手机"] | None = Field(
        default=None, description="设备类别；不传则返回全部类别"
    )
    only_available: bool = Field(default=True, description="是否只返回有可用库存的型号")


class GetAssetArgs(ToolArgs):
    asset_id: str | None = Field(default=None, min_length=3, max_length=40, description="设备编号，例如 ASSET-1001")
    assignee: str | None = Field(default=None, min_length=1, max_length=40, description="领用人姓名或员工编号")

    @model_validator(mode="after")
    def _one_of(self) -> GetAssetArgs:
        if (self.asset_id is None) == (self.assignee is None):
            raise ValueError("asset_id 与 assignee 必须且只能提供一个")
        return self


class GetTicketArgs(ToolArgs):
    ticket_id: str = Field(pattern=r"^INC-\d{4}-\d{4}$", description="工单编号，例如 INC-2026-0001")


class ListTicketsArgs(ToolArgs):
    status: TicketStatusLiteral | None = Field(default=None, description="按状态过滤")
    assignee: str | None = Field(default=None, max_length=40, description="按处理人姓名或员工编号过滤")
    limit: int = Field(default=10, ge=1, le=20, description="最多返回多少条")


class CreateTicketArgs(ToolArgs):
    title: str = Field(min_length=4, max_length=80, description="工单标题")
    description: str = Field(min_length=5, max_length=500, description="问题或需求描述")
    category: TicketCategoryLiteral = Field(description="工单分类")
    priority: PriorityLiteral = Field(description="优先级：P1 最高，P4 最低")
    requester: str = Field(min_length=1, max_length=40, description="申请人姓名或员工编号")


class UpdateTicketArgs(ToolArgs):
    ticket_id: str = Field(pattern=r"^INC-\d{4}-\d{4}$", description="要修改的工单编号")
    status: TicketStatusLiteral | None = Field(default=None, description="新状态")
    priority: PriorityLiteral | None = Field(default=None, description="新优先级")
    assignee: str | None = Field(default=None, max_length=40, description="新处理人姓名或员工编号")
    comment: str | None = Field(default=None, max_length=300, description="追加一条处理备注")

    @model_validator(mode="after")
    def _at_least_one(self) -> UpdateTicketArgs:
        if all(value is None for value in (self.status, self.priority, self.assignee, self.comment)):
            raise ValueError("status/priority/assignee/comment 至少提供一个")
        return self


class RequestUserInputArgs(ToolArgs):
    question: str = Field(min_length=3, max_length=200, description="需要用户补充的信息，一次只问一个主题")
    options: list[str] = Field(default_factory=list, max_length=4, description="可选项，最多 4 个；没有就留空")
