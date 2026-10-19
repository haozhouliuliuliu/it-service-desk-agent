"""CLI 集成：run / task / approve / reject / input 与退出码。"""

from __future__ import annotations

from pathlib import Path

import pytest

from it_agent.cli import main
from it_agent.config import Settings
from it_agent.llm.fake import ScriptedClient, answer, call, tool_response
from it_agent.runtime import build_runtime

from .conftest import SEED_DIR
from .test_agent_loop import CREATE_ARGS


def _settings(tmp_path: Path) -> Settings:
    return Settings(db_path=tmp_path / "agent.db", seed_dir=SEED_DIR)


def _install_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, responses: list) -> None:
    settings = _settings(tmp_path)
    client = ScriptedClient(responses)
    monkeypatch.setattr("it_agent.cli.build_runtime", lambda: build_runtime(settings, client=client))
    monkeypatch.setenv("APP_DB_PATH", str(settings.db_path))
    monkeypatch.setenv("APP_SEED_DIR", str(SEED_DIR))


def test_run_completes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _install_runtime(
        monkeypatch,
        tmp_path,
        [tool_response(call("query_inventory", {})), answer("库存充足。")],
    )
    assert main(["run", "查一下库存", "--task-id", "TASK-cli1"]) == 0
    output = capsys.readouterr().out
    assert "状态：completed" in output
    assert "库存充足" in output


def test_run_waiting_for_approval_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _install_runtime(
        monkeypatch,
        tmp_path,
        [tool_response(call("create_ticket", CREATE_ARGS)), answer("已创建。")],
    )
    assert main(["run", "创建工单", "--task-id", "TASK-cli2"]) == 2
    output = capsys.readouterr().out
    assert "waiting_for_approval" in output
    assert "待确认操作" in output


def test_approve_via_cli_creates_ticket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _install_runtime(
        monkeypatch,
        tmp_path,
        [tool_response(call("create_ticket", CREATE_ARGS)), answer("已创建。")],
    )
    assert main(["run", "创建工单", "--task-id", "TASK-cli3"]) == 2
    output = capsys.readouterr().out
    approval_id = output.split("approval_id = ")[1].split("（")[0].strip()

    assert main(["approve", "TASK-cli3", approval_id, "--by", "张会计"]) == 0
    assert "completed" in capsys.readouterr().out


def test_reject_via_cli_does_not_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _install_runtime(
        monkeypatch,
        tmp_path,
        [tool_response(call("create_ticket", CREATE_ARGS)), answer("好的，未创建。")],
    )
    assert main(["run", "创建工单", "--task-id", "TASK-cli4"]) == 2
    output = capsys.readouterr().out
    approval_id = output.split("approval_id = ")[1].split("（")[0].strip()

    assert main(["reject", "TASK-cli4", approval_id, "--by", "张会计"]) == 0
    assert "completed" in capsys.readouterr().out


def test_task_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _install_runtime(monkeypatch, tmp_path, [answer("完成。")])
    assert main(["run", "你好", "--task-id", "TASK-cli5"]) == 0
    capsys.readouterr()

    assert main(["task", "TASK-cli5", "--json"]) == 0
    payload = capsys.readouterr().out
    assert '"task_id": "TASK-cli5"' in payload
    assert '"events"' in payload
    assert '"tool_calls"' in payload


def test_input_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _install_runtime(
        monkeypatch,
        tmp_path,
        [
            tool_response(call("request_user_input", {"question": "新员工岗位是？"})),
            answer("研发岗，按标准配置。"),
        ],
    )
    assert main(["run", "给新员工配电脑", "--task-id", "TASK-cli6"]) == 2
    assert "待补充信息" in capsys.readouterr().out

    assert main(["input", "TASK-cli6", "研发岗"]) == 0
    assert "completed" in capsys.readouterr().out


def test_unknown_task_returns_three(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _install_runtime(monkeypatch, tmp_path, [])
    assert main(["task", "TASK-nope"]) == 3
    assert "错误" in capsys.readouterr().out
