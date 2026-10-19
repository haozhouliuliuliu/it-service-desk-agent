"""测试辅助：用脚本化模型装配一个真实 Runner（真实工具、真实 SQLite）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from it_agent.agent import AgentRepository, AgentRunner
from it_agent.context import ToolContext
from it_agent.db import init_database
from it_agent.llm.fake import ScriptedClient
from it_agent.policies import PolicyIndex
from it_agent.store import DeskStore
from it_agent.tools import ToolRegistry, build_registry

from .conftest import SEED_DIR


@dataclass
class AgentEnv:
    runner: AgentRunner
    repository: AgentRepository
    client: ScriptedClient
    store: DeskStore
    registry: ToolRegistry
    context: ToolContext
    db_path: Path
    sleeps: list[float] = field(default_factory=list)


def build_env(
    tmp_path: Path,
    responses: list,
    *,
    max_tool_calls: int = 8,
    max_steps: int = 12,
    max_llm_retries: int = 0,
) -> AgentEnv:
    db_path = tmp_path / "agent.db"
    init_database(db_path, SEED_DIR)

    repository = AgentRepository(db_path)
    store = DeskStore(db_path)
    policies = PolicyIndex.from_dir(SEED_DIR / "policies")
    registry = build_registry()
    context = ToolContext(store=store, policies=policies)
    client = ScriptedClient(responses)
    sleeps: list[float] = []

    runner = AgentRunner(
        repository=repository,
        registry=registry,
        context=context,
        client=client,
        max_tool_calls=max_tool_calls,
        max_steps=max_steps,
        max_llm_retries=max_llm_retries,
        backoff_seconds=0.5,
        sleep=sleeps.append,
    )
    return AgentEnv(
        runner=runner,
        repository=repository,
        client=client,
        store=store,
        registry=registry,
        context=context,
        db_path=db_path,
        sleeps=sleeps,
    )
