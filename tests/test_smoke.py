"""冒烟测试：保证包可安装、CLI 可启动。"""

from __future__ import annotations

import pytest

from it_agent import __version__
from it_agent.cli import main


def test_version() -> None:
    assert __version__ == "0.1.0"


def test_cli_version_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_cli_help_runs() -> None:
    assert main([]) == 0
