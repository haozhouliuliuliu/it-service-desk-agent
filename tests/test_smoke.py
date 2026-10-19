"""冒烟测试：包、CLI 与命令分发。"""

from __future__ import annotations

import pytest

from it_agent import __version__
from it_agent.cli import main


def test_version() -> None:
    assert __version__ == "0.5.0"


def test_cli_version_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "0.5.0" in capsys.readouterr().out


def test_cli_help_runs() -> None:
    assert main([]) == 0


def test_cli_tools_lists_all(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tools"]) == 0
    output = capsys.readouterr().out
    assert "search_policies" in output
    assert "create_ticket" in output
    assert "需确认" in output


def test_cli_search_hits_policy(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["search", "电脑领用"]) == 0
    assert "电脑领用制度" in capsys.readouterr().out
