"""应用配置（M1 只涉及数据与工具，LLM 配置在 M2 接入）。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError

DEFAULT_SEED_DIR = Path("seed_data")
DEFAULT_DB_PATH = Path("data/agent.db")


def _env_str(source: Mapping[str, str], name: str, default: str) -> str:
    value = (source.get(name) or "").strip()
    return value or default


def _env_int(source: Mapping[str, str], name: str, default: int) -> int:
    raw = (source.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是整数，收到 {raw!r}") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    db_path: Path = DEFAULT_DB_PATH
    seed_dir: Path = DEFAULT_SEED_DIR
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    max_tool_calls: int = 8
    max_steps: int = 12

    def __post_init__(self) -> None:
        if self.max_tool_calls < 1:
            raise ConfigError("AGENT_MAX_TOOL_CALLS 必须大于 0")
        if self.max_steps < 1:
            raise ConfigError("AGENT_MAX_STEPS 必须大于 0")
        if not 1 <= self.port <= 65535:
            raise ConfigError("APP_PORT 必须在 1..65535 之间")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        return cls(
            db_path=Path(_env_str(source, "APP_DB_PATH", str(DEFAULT_DB_PATH))),
            seed_dir=Path(_env_str(source, "APP_SEED_DIR", str(DEFAULT_SEED_DIR))),
            host=_env_str(source, "APP_HOST", "0.0.0.0"),
            port=_env_int(source, "APP_PORT", 8000),
            log_level=_env_str(source, "APP_LOG_LEVEL", "INFO").upper(),
            max_tool_calls=_env_int(source, "AGENT_MAX_TOOL_CALLS", 8),
            max_steps=_env_int(source, "AGENT_MAX_STEPS", 12),
        )
