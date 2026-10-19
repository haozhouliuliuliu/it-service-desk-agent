"""HTTP API 测试：任务、事件、确认、输入、恢复、锁与错误码。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from it_agent.agent import AgentRepository, TaskStatus
from it_agent.api import create_app
from it_agent.config import Settings
from it_agent.db import init_database
from it_agent.llm.fake import ScriptedClient, answer, call, tool_response
from it_agent.store import DeskStore

from .conftest import SEED_DIR
from .test_agent_loop import CREATE_ARGS


def _settings(tmp_path: Path) -> Settings:
    return Settings(db_path=tmp_path / "agent.db", seed_dir=SEED_DIR)


def _app(tmp_path: Path, responses: list):
    settings = _settings(tmp_path)
    client = ScriptedClient(responses)
    app = create_app(settings, client=client, configure_logging=False)
    return app, settings


def _ticket_count(settings: Settings) -> int:
    return len(DeskStore(settings.db_path).list_tickets(limit=50))


def test_health(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, [])
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["model"] == "scripted"


def test_create_task_runs_to_completion(tmp_path: Path) -> None:
    app, _ = _app(
        tmp_path,
        [tool_response(call("query_inventory", {})), answer("笔记本可用 4 台。")],
    )
    with TestClient(app) as client:
        response = client.post("/api/tasks", json={"message": "查一下笔记本库存"})
        assert response.status_code == 202
        task_id = response.json()["task_id"]

        state = client.get(f"/api/tasks/{task_id}").json()
        assert state["status"] == TaskStatus.COMPLETED.value
        assert "4 台" in state["final_answer"]

        calls = client.get(f"/api/tasks/{task_id}/tool-calls").json()
        assert [item["tool_name"] for item in calls] == ["query_inventory"]

        events = client.get(f"/api/tasks/{task_id}/events").json()
        assert any(event["type"] == "tool_call_finished" for event in events)


def test_write_requires_approval(tmp_path: Path) -> None:
    app, settings = _app(
        tmp_path,
        [tool_response(call("create_ticket", CREATE_ARGS)), answer("已创建。")],
    )
    with TestClient(app) as client:
        task_id = client.post("/api/tasks", json={"message": "创建工单"}).json()["task_id"]
        state = client.get(f"/api/tasks/{task_id}").json()
        assert state["status"] == TaskStatus.WAITING_FOR_APPROVAL.value
        assert state["pending_approval"]["tool_name"] == "create_ticket"
        assert _ticket_count(settings) == 20

        approval_id = state["pending_approval"]["approval_id"]
        response = client.post(
            f"/api/tasks/{task_id}/approvals/{approval_id}",
            json={"decision": "approve", "decided_by": "张会计"},
        )
        assert response.status_code == 200
        assert client.get(f"/api/tasks/{task_id}").json()["status"] == TaskStatus.COMPLETED.value
        assert _ticket_count(settings) == 21


def test_duplicate_approval_is_idempotent(tmp_path: Path) -> None:
    app, settings = _app(
        tmp_path,
        [tool_response(call("create_ticket", CREATE_ARGS)), answer("已创建。")],
    )
    with TestClient(app) as client:
        task_id = client.post("/api/tasks", json={"message": "创建工单"}).json()["task_id"]
        approval_id = client.get(f"/api/tasks/{task_id}").json()["pending_approval"]["approval_id"]
        for _ in range(2):
            response = client.post(
                f"/api/tasks/{task_id}/approvals/{approval_id}",
                json={"decision": "approve", "decided_by": "张会计"},
            )
            assert response.status_code == 200
    assert _ticket_count(settings) == 21


def test_reject_does_not_write(tmp_path: Path) -> None:
    app, settings = _app(
        tmp_path,
        [tool_response(call("create_ticket", CREATE_ARGS)), answer("好的，未创建。")],
    )
    with TestClient(app) as client:
        task_id = client.post("/api/tasks", json={"message": "创建工单"}).json()["task_id"]
        approval_id = client.get(f"/api/tasks/{task_id}").json()["pending_approval"]["approval_id"]
        response = client.post(
            f"/api/tasks/{task_id}/approvals/{approval_id}",
            json={"decision": "reject", "decided_by": "张会计"},
        )
        assert response.status_code == 200
        assert client.get(f"/api/tasks/{task_id}").json()["status"] == TaskStatus.COMPLETED.value
    assert _ticket_count(settings) == 20


def test_clarification_via_api(tmp_path: Path) -> None:
    app, _ = _app(
        tmp_path,
        [
            tool_response(call("request_user_input", {"question": "新员工是什么岗位？"})),
            answer("研发岗按 ThinkPad T14 配置。"),
        ],
    )
    with TestClient(app) as client:
        task_id = client.post("/api/tasks", json={"message": "给新员工配电脑"}).json()["task_id"]
        state = client.get(f"/api/tasks/{task_id}").json()
        assert state["status"] == TaskStatus.WAITING_FOR_INPUT.value
        assert state["pending_question"] == "新员工是什么岗位？"

        response = client.post(f"/api/tasks/{task_id}/input", json={"text": "研发岗"})
        assert response.status_code == 202
        assert client.get(f"/api/tasks/{task_id}").json()["status"] == TaskStatus.COMPLETED.value


def test_input_when_completed_is_rejected(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, [answer("完成")])
    with TestClient(app) as client:
        task_id = client.post("/api/tasks", json={"message": "你好"}).json()["task_id"]
        response = client.post(f"/api/tasks/{task_id}/input", json={"text": "补充"})
    assert response.status_code == 400
    assert response.json()["code"] == "NOT_WAITING_FOR_INPUT"


def test_validation_errors(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, [])
    with TestClient(app) as client:
        empty = client.post("/api/tasks", json={"message": ""})
        assert empty.status_code == 422
        assert empty.json()["code"] == "VALIDATION_ERROR"

        bad_decision = client.post("/api/tasks/TASK-x/approvals/APR-x", json={"decision": "maybe", "decided_by": "x"})
        assert bad_decision.status_code == 422


def test_unknown_task_returns_404(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, [])
    with TestClient(app) as client:
        response = client.get("/api/tasks/TASK-nope")
        assert response.status_code == 404
        assert response.json()["code"] == "TASK_NOT_FOUND"


def test_list_pagination_and_filter(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, [answer("一"), answer("二")])
    with TestClient(app) as client:
        for message in ("任务一", "任务二"):
            client.post("/api/tasks", json={"message": message})
        payload = client.get("/api/tasks", params={"limit": 1, "offset": 0}).json()
        assert payload["total"] == 2
        assert len(payload["items"]) == 1
        completed = client.get("/api/tasks", params={"status": "completed"}).json()
        assert completed["total"] == 2
        bad = client.get("/api/tasks", params={"status": "nope"})
        assert bad.status_code == 400


def test_tools_endpoint(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, [])
    with TestClient(app) as client:
        response = client.get("/api/tools")
    assert response.status_code == 200
    names = {item["name"] for item in response.json()}
    assert len(names) == 8
    assert {"create_ticket", "search_policies", "request_user_input"} <= names


def test_busy_task_returns_409(tmp_path: Path) -> None:
    app, _ = _app(
        tmp_path,
        [tool_response(call("create_ticket", CREATE_ARGS)), answer("已创建。")],
    )
    with TestClient(app) as client:
        task_id = client.post("/api/tasks", json={"message": "创建工单"}).json()["task_id"]
        approval_id = client.get(f"/api/tasks/{task_id}").json()["pending_approval"]["approval_id"]
        locks = client.app.state.locks
        assert locks.try_acquire(task_id)
        try:
            response = client.post(
                f"/api/tasks/{task_id}/approvals/{approval_id}",
                json={"decision": "approve", "decided_by": "张会计"},
            )
        finally:
            locks.release(task_id)
        assert response.status_code == 409
        assert response.json()["code"] == "TASK_BUSY"


def test_approval_from_other_task_returns_404(tmp_path: Path) -> None:
    app, _ = _app(
        tmp_path,
        [tool_response(call("create_ticket", CREATE_ARGS)), answer("另一个任务完成。")],
    )
    with TestClient(app) as client:
        first = client.post("/api/tasks", json={"message": "创建工单"}).json()["task_id"]
        approval_id = client.get(f"/api/tasks/{first}").json()["pending_approval"]["approval_id"]
        second = client.post("/api/tasks", json={"message": "另一个任务"}).json()["task_id"]
        response = client.post(
            f"/api/tasks/{second}/approvals/{approval_id}",
            json={"decision": "approve", "decided_by": "张会计"},
        )
    assert response.status_code == 404


def test_startup_recovery_and_resume(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    init_database(settings.db_path, SEED_DIR)
    repository = AgentRepository(settings.db_path)
    repository.create_task("TASK-stale", "被中断的任务", "scripted")

    app = create_app(settings, client=ScriptedClient([answer("恢复后完成。")]), configure_logging=False)
    with TestClient(app) as client:
        health = client.get("/healthz").json()
        assert health["recovered_tasks"] == 1

        state = client.get("/api/tasks/TASK-stale").json()
        assert state["status"] == TaskStatus.FAILED.value
        assert state["error_code"] == "INTERRUPTED"

        response = client.post("/api/tasks/TASK-stale/resume")
        assert response.status_code == 202
        assert client.get("/api/tasks/TASK-stale").json()["status"] == TaskStatus.COMPLETED.value


def test_resume_completed_is_noop(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, [answer("完成")])
    with TestClient(app) as client:
        task_id = client.post("/api/tasks", json={"message": "你好"}).json()["task_id"]
        response = client.post(f"/api/tasks/{task_id}/resume")
        assert response.status_code == 202
        assert response.json()["status"] == TaskStatus.COMPLETED.value
