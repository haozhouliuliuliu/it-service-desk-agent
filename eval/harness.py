"""固定测试集执行器（M4）。

用法：
    python -m eval.harness --output evidence/real-model
    python -m eval.harness --only D1,M1 --output out/eval

- real 用例：启动真实 uvicorn + 真实 LLM，通过 HTTP API 驱动，记录完整轨迹；
- scripted 用例：进程内用脚本化模型验证上限/超时等程序行为（报告中明确标注）。
"""

from __future__ import annotations

import argparse
import json
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from it_agent.agent import AgentRepository, AgentRunner, TaskStatus
from it_agent.api import create_app
from it_agent.config import Settings
from it_agent.context import ToolContext
from it_agent.db import init_database
from it_agent.llm.client import LLMError, LLMSettings
from it_agent.llm.fake import ScriptedClient, answer, call, tool_response
from it_agent.policies import PolicyIndex
from it_agent.store import DeskStore
from it_agent.tools import build_registry

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "eval" / "cases.json"
SEED_DIR = ROOT / "seed_data"
TERMINAL = {
    TaskStatus.COMPLETED.value,
    TaskStatus.FAILED.value,
    TaskStatus.LIMIT_REACHED.value,
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_server(app) -> tuple[uvicorn.Server, int]:
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False, lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="eval-uvicorn", daemon=True)
    thread.start()
    deadline = time.time() + 15
    while time.time() < deadline:
        if server.started:
            return server, port
        time.sleep(0.1)
    raise RuntimeError("uvicorn 启动超时")


def _contains(text: str, needle: str) -> bool:
    return needle.casefold() in (text or "").casefold()


def _judge(case: dict[str, Any], actual: dict[str, Any], db_path: Path) -> tuple[bool, list[str]]:
    expect = case.get("expect", {})
    failures: list[str] = []

    if actual["status"] != expect.get("status", "completed"):
        failures.append(f"状态期望 {expect.get('status')}，实际 {actual['status']}")
    if "error_code" in expect and actual.get("error_code") != expect["error_code"]:
        failures.append(f"error_code 期望 {expect['error_code']}，实际 {actual.get('error_code')}")

    tool_names = [tool["tool_name"] for tool in actual.get("tools", [])]
    for required in expect.get("required_tools", []):
        if required not in tool_names:
            failures.append(f"缺少必需工具：{required}")
    for forbidden in expect.get("forbidden_tools", []):
        if forbidden in tool_names:
            failures.append(f"出现了禁止的工具：{forbidden}")

    answer = actual.get("final_answer") or ""
    for needle in expect.get("final_contains_all", []):
        if not _contains(answer, needle):
            failures.append(f"最终答复缺少：{needle}")
    any_of = expect.get("final_contains_any", [])
    if any_of and not any(_contains(answer, needle) for needle in any_of):
        failures.append(f"最终答复不包含任一关键词：{any_of}")

    if actual.get("tickets_delta") != expect.get("tickets_delta", 0):
        failures.append(f"工单数量变化期望 {expect.get('tickets_delta', 0)}，实际 {actual.get('tickets_delta')}")

    for ticket_id, status in expect.get("ticket_status", {}).items():
        row = (
            sqlite3.connect(db_path).execute("SELECT status FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
        )
        if row is None or row[0] != status:
            actual_status = row[0] if row else "不存在"
            failures.append(f"工单 {ticket_id} 状态期望 {status}，实际 {actual_status}")

    expected_errors = set(expect.get("tool_error_codes", []))
    actual_errors = {tool.get("error_code") for tool in actual.get("tools", []) if tool.get("error_code")}
    missing = expected_errors - actual_errors
    if missing:
        failures.append(f"缺少预期工具错误：{sorted(missing)}")

    return (not failures, failures)


def _count_tickets(db_path: Path) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])
    finally:
        connection.close()


def _run_real_case(case: dict[str, Any], client: httpx.Client, db_path: Path) -> dict[str, Any]:
    started = time.time()
    before = _count_tickets(db_path)
    transcript: list[dict[str, Any]] = []
    actions = deque(case.get("actions", []))
    failure: str | None = None
    fallback_used = 0

    response = client.post("/api/tasks", json={"message": case["message"]})
    response.raise_for_status()
    task_id = response.json()["task_id"]
    transcript.append({"step": "create", "task_id": task_id, "http": response.status_code})

    state: dict[str, Any] | None = None
    deadline = started + float(case.get("timeout_seconds", 180))
    while time.time() < deadline:
        current = client.get(f"/api/tasks/{task_id}").json()
        status = current["status"]
        if status in TERMINAL:
            state = current
            break
        if status == TaskStatus.WAITING_FOR_INPUT.value:
            if actions and actions[0].get("type") == "input":
                action = actions.popleft()
                text = action["text"]
                step = "input"
            elif fallback_used < 2:
                fallback_used += 1
                text = case.get(
                    "fallback_input",
                    "没有更多信息了，请基于已有信息给出结论；如果无法执行，请直接说明原因。",
                )
                step = "fallback_input"
            else:
                failure = "模型连续追问，未在有限补充内完成任务"
                break
            posted = client.post(f"/api/tasks/{task_id}/input", json={"text": text})
            transcript.append({"step": step, "text": text, "http": posted.status_code})
        elif status == TaskStatus.WAITING_FOR_APPROVAL.value:
            pending = current.get("pending_approval") or {}
            approval_id = pending.get("approval_id")
            if actions and actions[0].get("type") in {"approve", "reject"}:
                action = actions.popleft()
                decision = "approve" if action["type"] == "approve" else "reject"
                for attempt in range(int(action.get("repeat", 1))):
                    posted = client.post(
                        f"/api/tasks/{task_id}/approvals/{approval_id}",
                        json={"decision": decision, "decided_by": action.get("by", "eval")},
                    )
                    transcript.append(
                        {
                            "step": f"{decision}_{attempt + 1}",
                            "approval_id": approval_id,
                            "summary": pending.get("summary"),
                            "http": posted.status_code,
                        }
                    )
            elif case.get("auto_reject"):
                posted = client.post(
                    f"/api/tasks/{task_id}/approvals/{approval_id}",
                    json={"decision": "reject", "decided_by": "eval"},
                )
                transcript.append({"step": "reject", "approval_id": approval_id, "http": posted.status_code})
            else:
                failure = "任务请求人工确认，但用例没有准备确认动作"
                break
        time.sleep(1.0)
    else:
        failure = "任务超时"

    if state is None:
        state = client.get(f"/api/tasks/{task_id}").json()
    tools = client.get(f"/api/tasks/{task_id}/tool-calls").json()
    events = client.get(f"/api/tasks/{task_id}/events").json()
    after = _count_tickets(db_path)

    actual = {
        "task_id": task_id,
        "status": state.get("status"),
        "final_answer": state.get("final_answer"),
        "error_code": state.get("error_code"),
        "error_message": state.get("error_message"),
        "step_count": state.get("step_count"),
        "tool_call_count": state.get("tool_call_count"),
        "pending_question": state.get("pending_question"),
        "pending_approval": state.get("pending_approval"),
        "tools": [
            {
                "tool_name": tool["tool_name"],
                "args": tool["args"],
                "ok": tool["ok"],
                "error_code": tool["error_code"],
                "result": tool["result"],
            }
            for tool in tools
        ],
        "tickets_delta": after - before,
        "event_types": [event["type"] for event in events],
    }
    if failure:
        actual["harness_error"] = failure
    passed, failures = _judge(case, actual, db_path)
    if failure:
        failures.insert(0, failure)

    return {
        "id": case["id"],
        "category": case["category"],
        "title": case["title"],
        "mode": "real",
        "message": case["message"],
        "expected": case.get("expect", {}),
        "actual": actual,
        "transcript": transcript,
        "passed": passed,
        "failures": failures,
        "duration_s": round(time.time() - started, 1),
    }


def _scripted_responses(case: dict[str, Any]) -> list:
    responses = []
    for index, item in enumerate(case.get("script", [])):
        if "tool" in item:
            responses.append(tool_response(call(item["tool"], item.get("args", {}), call_id=f"{case['id']}-{index}")))
        elif "answer" in item:
            responses.append(answer(item["answer"]))
        elif "error" in item:
            error = item["error"]
            responses.append(
                LLMError(
                    error["code"],
                    error.get("message", "模拟错误"),
                    retryable=bool(error.get("retryable", False)),
                )
            )
    return responses


def _run_scripted_case(case: dict[str, Any], workdir: Path) -> dict[str, Any]:
    started = time.time()
    db_path = workdir / f"{case['id']}.db"
    init_database(db_path, SEED_DIR)
    repository = AgentRepository(db_path)
    store = DeskStore(db_path)
    registry = build_registry()
    context = ToolContext(store=store, policies=PolicyIndex.from_dir(SEED_DIR / "policies"))
    client = ScriptedClient(_scripted_responses(case))
    runner = AgentRunner(
        repository=repository,
        registry=registry,
        context=context,
        client=client,
        max_tool_calls=int(case.get("max_tool_calls", 8)),
        max_steps=int(case.get("max_steps", 12)),
        max_llm_retries=int(case.get("max_llm_retries", 2)),
        sleep=lambda _seconds: None,
    )
    before = _count_tickets(db_path)
    state = runner.create_task(case["message"])
    after = _count_tickets(db_path)
    tools = repository.tool_calls(state.task_id)

    actual = {
        "task_id": state.task_id,
        "status": state.status,
        "final_answer": state.final_answer,
        "error_code": state.error_code,
        "error_message": state.error_message,
        "step_count": state.step_count,
        "tool_call_count": state.tool_call_count,
        "tools": [
            {
                "tool_name": tool.tool_name,
                "args": tool.args,
                "ok": tool.ok,
                "error_code": tool.error_code,
                "result": tool.result,
            }
            for tool in tools
        ],
        "tickets_delta": after - before,
        "event_types": [event.type for event in repository.events(state.task_id)],
    }
    passed, failures = _judge(case, actual, db_path)
    return {
        "id": case["id"],
        "category": case["category"],
        "title": case["title"],
        "mode": "scripted",
        "message": case["message"],
        "expected": case.get("expect", {}),
        "actual": actual,
        "transcript": [],
        "passed": passed,
        "failures": failures,
        "duration_s": round(time.time() - started, 1),
    }


def _write_report(run_dir: Path, config: dict[str, Any], results: list[dict[str, Any]]) -> None:
    real = [item for item in results if item["mode"] == "real"]
    scripted = [item for item in results if item["mode"] == "scripted"]
    multi = [item for item in real if item["category"] == "multi_step"]
    real_passed = sum(1 for item in real if item["passed"])
    multi_passed = sum(1 for item in multi if item["passed"])

    lines = [
        "# 真实模型评估报告",
        "",
        f"- 运行 ID：`{config['run_id']}`",
        f"- 模型：`{config['model']}`（{config['api_base']}，temperature={config['temperature']}）",
        f"- 开始时间：{config['started_at']}",
        f"- 限制：工具 {config['max_tool_calls']} 次 / 交互 {config['max_steps']} 轮",
        f"- 用例：{len(results)}（real {len(real)} / scripted {len(scripted)}）",
        "",
        "## 汇总",
        "",
        f"- 真实模型通过：**{real_passed}/{len(real)}**",
        f"- 多步骤通过：**{multi_passed}/{len(multi)}**",
        f"- 全部通过：**{sum(1 for item in results if item['passed'])}/{len(results)}**",
        "",
        "| 用例 | 类别 | 模式 | 结果 | 工具 | 失败原因 |",
        "|---|---|---|---|---|---|",
    ]
    for item in results:
        tools = ", ".join(tool["tool_name"] for tool in item["actual"].get("tools", [])) or "-"
        reason = "；".join(item["failures"]) if item["failures"] else "-"
        mark = "通过" if item["passed"] else "失败"
        lines.append(f"| {item['id']} | {item['category']} | {item['mode']} | {mark} | {tools} | {reason} |")

    lines += ["", "## 失败详情", ""]
    failed = [item for item in results if not item["passed"]]
    if not failed:
        lines.append("无。")
    else:
        for item in failed:
            lines.append(f"### {item['id']} {item['title']}")
            lines.append("")
            lines.append(f"- 失败：{'; '.join(item['failures'])}")
            lines.append(f"- 状态：{item['actual'].get('status')}")
            lines.append(f"- 最终答复：{item['actual'].get('final_answer') or '（空）'}")
            lines.append("")

    lines += [
        "## 说明",
        "",
        "- `real` 用例通过真实 HTTP API + 真实 LLM 执行，轨迹见同目录 `cases.json`；",
        "- `scripted` 用例只验证程序侧上限/超时/未知工具处理，不代表模型能力；",
        "- 判定基于状态、工具调用、数据库副作用与关键文本，不依赖模型措辞；",
        "- 本报告不包含 API 密钥。",
    ]
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行固定测试集")
    parser.add_argument("--output", type=Path, default=ROOT / "evidence" / "real-model")
    parser.add_argument("--only", default=None, help="逗号分隔的用例 ID")
    parser.add_argument("--skip-scripted", action="store_true")
    args = parser.parse_args(argv)

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if args.only:
        wanted = {item.strip() for item in args.only.split(",")}
        cases = [case for case in cases if case["id"] in wanted]
    if args.skip_scripted:
        cases = [case for case in cases if case["mode"] != "scripted"]

    llm = LLMSettings.from_env()
    run_id = datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S")
    run_dir = args.output / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="it-agent-eval-"))

    settings = Settings(db_path=workdir / "eval.db", seed_dir=SEED_DIR)
    app = create_app(settings, configure_logging=False)
    server, port = _start_server(app)
    base_url = f"http://127.0.0.1:{port}"

    config = {
        "run_id": run_id,
        "started_at": _now(),
        "model": llm.model,
        "api_base": llm.api_base,
        "temperature": llm.temperature,
        "max_tool_calls": settings.max_tool_calls,
        "max_steps": settings.max_steps,
        "git_commit": _git_commit(),
        "base_url": base_url,
        "python": sys.version.split()[0],
    }
    (run_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    results: list[dict[str, Any]] = []
    try:
        with httpx.Client(base_url=base_url, timeout=60) as client:
            for case in cases:
                try:
                    if case["mode"] == "scripted":
                        result = _run_scripted_case(case, workdir)
                    else:
                        result = _run_real_case(case, client, settings.db_path)
                except Exception as exc:  # 单个用例异常不能中断整轮评估
                    result = {
                        "id": case["id"],
                        "category": case["category"],
                        "title": case["title"],
                        "mode": case["mode"],
                        "message": case["message"],
                        "expected": case.get("expect", {}),
                        "actual": {"status": None, "final_answer": None, "tools": [], "tickets_delta": 0},
                        "transcript": [],
                        "passed": False,
                        "failures": [f"harness 异常：{exc}"],
                        "duration_s": 0,
                    }
                results.append(result)
                print(
                    f"[{result['mode']:8}] {result['id']:<3} "
                    f"{'PASS' if result['passed'] else 'FAIL'} {result['title']} "
                    f"{result['failures'] if result['failures'] else ''}",
                    flush=True,
                )
    finally:
        server.should_exit = True
        time.sleep(0.5)

    (run_dir / "cases.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(run_dir, config, results)

    real = [item for item in results if item["mode"] == "real"]
    multi = [item for item in real if item["category"] == "multi_step"]
    real_passed = sum(1 for item in real if item["passed"])
    multi_passed = sum(1 for item in multi if item["passed"])
    print(f"\n真实模型：{real_passed}/{len(real)}，多步骤：{multi_passed}/{len(multi)}")
    print(f"证据：{run_dir}")

    gate = (
        len(real) >= 10
        and real_passed >= int(len(real) * 0.8)
        and len(multi) >= 5
        and multi_passed >= int(len(multi) * 0.8)
        and all(item["passed"] for item in results if item["category"] in {"approval", "security"})
    )
    return 0 if gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
