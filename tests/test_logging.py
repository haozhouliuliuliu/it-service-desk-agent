"""日志测试：JSON 字段、文本附加字段、重复初始化。"""

from __future__ import annotations

import json
import logging

import pytest

from it_agent.logging_config import JsonFormatter, setup_logging


def _record(**extra) -> logging.LogRecord:
    record = logging.LogRecord("it_agent.test", logging.INFO, __file__, 1, "job", (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_includes_fields() -> None:
    payload = json.loads(JsonFormatter().format(_record(task_id="TASK-1", status="running")))
    assert payload["message"] == "job"
    assert payload["task_id"] == "TASK-1"
    assert payload["status"] == "running"


def test_text_formatter_appends_extra() -> None:
    setup_logging("INFO", force=True)
    formatter = logging.getLogger().handlers[0].formatter
    line = formatter.format(_record(task_id="TASK-2"))
    assert "task_id=TASK-2" in line


@pytest.fixture
def restore_logging():
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield
    root.handlers = handlers
    root.setLevel(level)
    root._recon_configured = False  # type: ignore[attr-defined]


def test_setup_logging_is_idempotent(restore_logging) -> None:
    setup_logging("DEBUG", force=True)
    first = list(logging.getLogger().handlers)
    setup_logging("INFO", json_format=True)
    assert logging.getLogger().handlers == first
    assert logging.getLogger().level == logging.INFO
