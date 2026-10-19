"""运行时装配：把配置、数据库、工具、模型与 Runner 接起来。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .agent import AgentRepository, AgentRunner
from .config import Settings
from .context import ToolContext
from .db import init_database
from .llm.client import ChatClient, LLMSettings, OpenAICompatibleClient
from .policies import PolicyIndex
from .store import DeskStore
from .tools import ToolRegistry, build_registry


@dataclass
class Runtime:
    settings: Settings
    repository: AgentRepository
    registry: ToolRegistry
    context: ToolContext
    runner: AgentRunner
    client: ChatClient = field(repr=False)

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()


def build_runtime(
    settings: Settings | None = None,
    *,
    client: ChatClient | None = None,
    llm_settings: LLMSettings | None = None,
) -> Runtime:
    active = settings or Settings.from_env()
    init_database(active.db_path, active.seed_dir)

    repository = AgentRepository(active.db_path)
    store = DeskStore(active.db_path)
    policies = PolicyIndex.from_dir(active.seed_dir / "policies")
    registry = build_registry()
    context = ToolContext(store=store, policies=policies)

    if client is None:
        llm = llm_settings or LLMSettings.from_env()
        client = OpenAICompatibleClient(llm)
        retries = llm.max_retries
    else:
        retries = llm_settings.max_retries if llm_settings is not None else 2

    runner = AgentRunner(
        repository=repository,
        registry=registry,
        context=context,
        client=client,
        max_tool_calls=active.max_tool_calls,
        max_steps=active.max_steps,
        max_llm_retries=retries,
    )
    return Runtime(
        settings=active,
        repository=repository,
        registry=registry,
        context=context,
        runner=runner,
        client=client,
    )
