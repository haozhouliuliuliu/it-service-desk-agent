"""共享 fixture：临时数据库 + 种子数据 + 工具上下文。"""

from __future__ import annotations

from pathlib import Path

import pytest

from it_agent.context import ToolContext
from it_agent.db import init_database
from it_agent.policies import PolicyIndex
from it_agent.store import DeskStore
from it_agent.tools import ToolRegistry, build_registry

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = REPO_ROOT / "seed_data"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "agent.db"
    init_database(path, SEED_DIR)
    return path


@pytest.fixture
def store(db_path: Path) -> DeskStore:
    return DeskStore(db_path)


@pytest.fixture
def policy_index() -> PolicyIndex:
    return PolicyIndex.from_dir(SEED_DIR / "policies")


@pytest.fixture
def context(store: DeskStore, policy_index: PolicyIndex) -> ToolContext:
    return ToolContext(store=store, policies=policy_index)


@pytest.fixture
def registry() -> ToolRegistry:
    return build_registry()
