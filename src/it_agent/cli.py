"""命令行入口：初始化、工具、检索、跑任务、查看与人工确认。"""

from __future__ import annotations

import argparse
import json

from . import __version__
from .agent import AgentRepository, TaskState, TaskStatus
from .config import Settings
from .db import init_database
from .errors import AgentError
from .policies import PolicyIndex
from .runtime import build_runtime
from .tools import build_registry

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_WAITING = 2
EXIT_CONFIG = 3


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

    run = subparsers.add_parser("run", help="创建一个任务并执行")
    run.add_argument("text", help="用户的自然语言任务")
    run.add_argument("--task-id", default=None, help="指定任务号（测试/复现用）")
    run.add_argument("--json", action="store_true", help="以 JSON 输出任务状态")

    task = subparsers.add_parser("task", help="查看任务状态、事件与工具调用")
    task.add_argument("task_id")
    task.add_argument("--json", action="store_true")

    approve = subparsers.add_parser("approve", help="确认待执行的写操作")
    approve.add_argument("task_id")
    approve.add_argument("approval_id")
    approve.add_argument("--by", default="cli-user", dest="decided_by")

    reject = subparsers.add_parser("reject", help="拒绝待执行的写操作")
    reject.add_argument("task_id")
    reject.add_argument("approval_id")
    reject.add_argument("--by", default="cli-user", dest="decided_by")

    supply = subparsers.add_parser("input", help="补充 Agent 需要的信息")
    supply.add_argument("task_id")
    supply.add_argument("text")

    serve = subparsers.add_parser("serve", help="启动 HTTP API")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    return parser


def _cmd_init_db() -> int:
    settings = Settings.from_env()
    init_database(settings.db_path, settings.seed_dir)
    print(f"已初始化：{settings.db_path}")
    return EXIT_OK


def _cmd_tools(as_json: bool) -> int:
    catalog = build_registry().catalog()
    if as_json:
        print(json.dumps(catalog, ensure_ascii=False, indent=2))
        return EXIT_OK
    for item in catalog:
        flag = "需确认" if item["requires_approval"] else "只读" if item["permission"] == "read" else "控制"
        print(f"{item['name']:<20} {flag:<6} {item['description']}")
    return EXIT_OK


def _cmd_search(query: str, limit: int) -> int:
    settings = Settings.from_env()
    index = PolicyIndex.from_dir(settings.seed_dir / "policies")
    results = index.search(query, limit=limit)
    if not results:
        print("没有匹配的制度条款")
        return EXIT_OK
    for item in results:
        print(f"[{item['doc_id']}] {item['citation']}  score={item['score']}")
        print(f"  {item['snippet']}")
    return EXIT_OK


def _print_state(state: TaskState) -> None:
    print(f"任务 {state.task_id}  状态：{state.status}")
    if state.final_answer:
        print(f"最终答复：{state.final_answer}")
    if state.pending_question:
        print(f"待补充信息：{state.pending_question}")
    if state.pending_approval:
        approval = state.pending_approval
        print(f"待确认操作：{approval.summary}")
        print(f"  approval_id = {approval.approval_id}（approve / reject）")
    if state.error_code:
        print(f"错误：{state.error_code} {state.error_message or ''}")
    print(f"模型交互 {state.step_count} 轮 / 工具调用 {state.tool_call_count} 次 / 模型 {state.model}")


def _exit_for(state: TaskState) -> int:
    if state.status == TaskStatus.COMPLETED.value:
        return EXIT_OK
    if state.status in {TaskStatus.WAITING_FOR_APPROVAL.value, TaskStatus.WAITING_FOR_INPUT.value}:
        return EXIT_WAITING
    return EXIT_FAILED


def _cmd_run(text: str, task_id: str | None, as_json: bool) -> int:
    runtime = build_runtime()
    try:
        state = runtime.runner.create_task(text, task_id=task_id)
    finally:
        runtime.close()
    if as_json:
        print(state.model_dump_json(indent=2))
    else:
        _print_state(state)
    return _exit_for(state)


def _cmd_task(task_id: str, as_json: bool) -> int:
    settings = Settings.from_env()
    repository = AgentRepository(settings.db_path)
    state = repository.get_task(task_id)
    events = repository.events(task_id)
    calls = repository.tool_calls(task_id)
    if as_json:
        print(
            json.dumps(
                {
                    "task": state.model_dump(mode="json"),
                    "events": [event.model_dump(mode="json") for event in events],
                    "tool_calls": [call.model_dump(mode="json") for call in calls],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return EXIT_OK
    _print_state(state)
    print(f"事件 {len(events)} 条 / 工具调用 {len(calls)} 条")
    for call in calls:
        flag = "ok" if call.ok else (call.error_code or "error")
        print(f"  - {call.tool_name} [{flag}] {json.dumps(call.args, ensure_ascii=False)}")
    return EXIT_OK


def _cmd_decide(task_id: str, approval_id: str, decision: str, decided_by: str) -> int:
    runtime = build_runtime()
    try:
        state = runtime.runner.decide_approval(task_id, approval_id, decision=decision, decided_by=decided_by)
    finally:
        runtime.close()
    _print_state(state)
    return _exit_for(state)


def _cmd_input(task_id: str, text: str) -> int:
    runtime = build_runtime()
    try:
        state = runtime.runner.submit_input(task_id, text)
    finally:
        runtime.close()
    _print_state(state)
    return _exit_for(state)


def _cmd_serve(host: str | None, port: int | None) -> int:
    settings = Settings.from_env()
    if port is not None and not 1 <= port <= 65535:
        print("错误：--port 必须在 1..65535 之间")
        return EXIT_CONFIG
    try:
        import uvicorn
    except ImportError:
        print('错误：未安装服务端依赖，请执行 pip install -e ".[server]"')
        return EXIT_CONFIG

    from .api import create_app

    application = create_app(settings)
    listen_host = host or settings.host
    listen_port = port or settings.port
    print(f"API 启动：http://{listen_host}:{listen_port}  文档：/docs")
    uvicorn.run(
        application,
        host=listen_host,
        port=listen_port,
        log_config=None,
        access_log=False,
    )
    return EXIT_OK


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
        if args.command == "run":
            return _cmd_run(args.text, args.task_id, args.json)
        if args.command == "task":
            return _cmd_task(args.task_id, args.json)
        if args.command == "approve":
            return _cmd_decide(args.task_id, args.approval_id, "approve", args.decided_by)
        if args.command == "reject":
            return _cmd_decide(args.task_id, args.approval_id, "reject", args.decided_by)
        if args.command == "input":
            return _cmd_input(args.task_id, args.text)
        if args.command == "serve":
            return _cmd_serve(args.host, args.port)
    except AgentError as exc:
        print(f"错误：{exc}")
        return EXIT_CONFIG

    parser.print_help()
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
