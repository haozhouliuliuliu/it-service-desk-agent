"""命令行入口：初始化数据、查看工具、检索制度。

Agent 运行与 HTTP 服务在 M2/M3 加入。
"""

from __future__ import annotations

import argparse
import json

from . import __version__
from .config import Settings
from .db import init_database
from .errors import AgentError
from .policies import PolicyIndex
from .tools import build_registry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="it-agent", description="企业 IT 服务台与资产支持 Agent")
    parser.add_argument("--version", action="version", version=f"it-service-desk-agent {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init-db", help="用 seed_data 初始化 SQLite 业务数据")

    tools = subparsers.add_parser("tools", help="列出已注册工具")
    tools.add_argument("--json", action="store_true", help="输出完整 JSON（含参数 schema）")

    search = subparsers.add_parser("search", help="检索虚构制度")
    search.add_argument("query", help="关键词")
    search.add_argument("--limit", type=int, default=3)
    return parser


def _cmd_init_db() -> int:
    settings = Settings.from_env()
    init_database(settings.db_path, settings.seed_dir)
    print(f"已初始化：{settings.db_path}")
    return 0


def _cmd_tools(as_json: bool) -> int:
    catalog = build_registry().catalog()
    if as_json:
        print(json.dumps(catalog, ensure_ascii=False, indent=2))
        return 0
    for item in catalog:
        flag = "需确认" if item["requires_approval"] else "只读" if item["permission"] == "read" else "控制"
        print(f"{item['name']:<20} {flag:<6} {item['description']}")
    return 0


def _cmd_search(query: str, limit: int) -> int:
    settings = Settings.from_env()
    index = PolicyIndex.from_dir(settings.seed_dir / "policies")
    results = index.search(query, limit=limit)
    if not results:
        print("没有匹配的制度条款")
        return 0
    for item in results:
        print(f"[{item['doc_id']}] {item['citation']}  score={item['score']}")
        print(f"  {item['snippet']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init-db":
            return _cmd_init_db()
        if args.command == "tools":
            return _cmd_tools(args.json)
        if args.command == "search":
            return _cmd_search(args.query, args.limit)
    except AgentError as exc:
        print(f"错误：{exc}")
        return 1

    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
