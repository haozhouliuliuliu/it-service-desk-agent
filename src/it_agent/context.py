"""工具执行上下文：只暴露业务服务，不暴露数据库连接或文件路径。"""

from __future__ import annotations

from dataclasses import dataclass

from .policies import PolicyIndex
from .store import DeskStore


@dataclass(frozen=True, slots=True)
class ToolContext:
    store: DeskStore
    policies: PolicyIndex
