"""命令行入口（M0 骨架）。

后续里程碑会加入：
- ``agent run``：本地跑单个任务并打印执行轨迹
- ``agent serve``：启动 HTTP API
- ``agent eval``：跑固定测试集与真实模型评估
"""

from __future__ import annotations

import argparse

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="it-agent", description="企业 IT 服务台与资产支持 Agent")
    parser.add_argument("--version", action="version", version=f"it-service-desk-agent {__version__}")
    parser.add_subparsers(dest="command")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
