"""Agent 的任务持久化：tasks / messages / events / tool_calls / approvals。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..db import connect
from ..domain import now_iso
from ..errors import AgentError
from .state import (
    ApprovalStatus,
    ApprovalView,
    EventView,
    TaskState,
    TaskStatus,
    ToolCallView,
)


class TaskNotFound(AgentError):
    pass


class AgentRepository:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    # ---------------------------------------------------------------- tasks

    def create_task(self, task_id: str, user_input: str, model: str) -> TaskState:
        at = now_iso()
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO tasks (task_id, status, user_input, model, created_at, updated_at) "
                "VALUES (?, 'running', ?, ?, ?, ?)",
                (task_id, user_input, model, at, at),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> TaskState:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                raise TaskNotFound(f"任务不存在：{task_id}")
            approval = connection.execute(
                "SELECT * FROM approvals WHERE task_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            return self._task_state(row, approval)
        finally:
            connection.close()

    def list_tasks(self, limit: int = 20, offset: int = 0) -> list[TaskState]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC, task_id LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
            return [self._task_state(row, None) for row in rows]
        finally:
            connection.close()

    def update_task(self, task_id: str, **fields: Any) -> None:
        allowed = {
            "status",
            "final_answer",
            "error_code",
            "error_message",
            "pending_question",
            "step_count",
            "tool_call_count",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"不允许更新字段：{', '.join(sorted(unknown))}")
        fields["updated_at"] = now_iso()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        connection = self._connect()
        try:
            connection.execute(f"UPDATE tasks SET {assignments} WHERE task_id = ?", (*fields.values(), task_id))
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _task_state(row: sqlite3.Row, approval: sqlite3.Row | None) -> TaskState:
        pending = None
        if approval is not None:
            pending = ApprovalView(
                approval_id=approval["approval_id"],
                call_id=approval["call_id"],
                tool_name=approval["tool_name"],
                args=json.loads(approval["args_json"]),
                summary=approval["summary"],
                status=approval["status"],
                created_at=approval["created_at"],
            )
        return TaskState(
            task_id=row["task_id"],
            status=row["status"],
            user_input=row["user_input"],
            final_answer=row["final_answer"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            pending_question=row["pending_question"],
            step_count=row["step_count"],
            tool_call_count=row["tool_call_count"],
            model=row["model"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            pending_approval=pending,
        )

    # ---------------------------------------------------------------- messages

    def append_message(
        self,
        task_id: str,
        *,
        role: str,
        content: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str | None = None,
        name: str | None = None,
    ) -> int:
        connection = self._connect()
        try:
            seq = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM task_messages WHERE task_id = ?", (task_id,)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO task_messages (task_id, seq, role, content, tool_calls_json, tool_call_id, name, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    seq,
                    role,
                    content,
                    json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                    tool_call_id,
                    name,
                    now_iso(),
                ),
            )
            connection.commit()
            return int(seq)
        finally:
            connection.close()

    def messages(self, task_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM task_messages WHERE task_id = ? ORDER BY seq", (task_id,)
            ).fetchall()
        finally:
            connection.close()

        result: list[dict[str, Any]] = []
        for row in rows:
            message: dict[str, Any] = {"role": row["role"]}
            if row["role"] == "assistant":
                message["content"] = row["content"] or ""
                if row["tool_calls_json"]:
                    message["tool_calls"] = json.loads(row["tool_calls_json"])
            elif row["role"] == "tool":
                message["content"] = row["content"] or ""
                message["tool_call_id"] = row["tool_call_id"]
            else:
                message["content"] = row["content"] or ""
            result.append(message)
        return result

    # ---------------------------------------------------------------- events

    def add_event(self, task_id: str, event_type: str, payload: dict[str, Any] | None = None) -> int:
        connection = self._connect()
        try:
            seq = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM agent_events WHERE task_id = ?", (task_id,)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO agent_events (task_id, seq, type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, seq, event_type, json.dumps(payload or {}, ensure_ascii=False), now_iso()),
            )
            connection.commit()
            return int(seq)
        finally:
            connection.close()

    def events(self, task_id: str, limit: int = 200) -> list[EventView]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM agent_events WHERE task_id = ? ORDER BY seq LIMIT ?", (task_id, limit)
            ).fetchall()
        finally:
            connection.close()
        return [
            EventView(
                seq=row["seq"],
                type=row["type"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    # ---------------------------------------------------------------- tool calls

    def record_tool_call(
        self,
        task_id: str,
        *,
        call_id: str,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        ok: bool,
        error_code: str | None = None,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO tool_calls (task_id, call_id, tool_name, args_json, result_json, ok, error_code, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(task_id, call_id) DO UPDATE SET result_json = excluded.result_json, ok = excluded.ok, "
                "error_code = excluded.error_code",
                (
                    task_id,
                    call_id,
                    tool_name,
                    json.dumps(args, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                    1 if ok else 0,
                    error_code,
                    now_iso(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def tool_calls(self, task_id: str) -> list[ToolCallView]:
        connection = self._connect()
        try:
            rows = connection.execute("SELECT * FROM tool_calls WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()
        finally:
            connection.close()
        return [
            ToolCallView(
                call_id=row["call_id"],
                tool_name=row["tool_name"],
                args=json.loads(row["args_json"]),
                result=json.loads(row["result_json"]),
                ok=bool(row["ok"]),
                error_code=row["error_code"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    # ---------------------------------------------------------------- approvals

    def create_approval(
        self,
        task_id: str,
        *,
        approval_id: str,
        call_id: str,
        tool_name: str,
        args: dict[str, Any],
        args_hash: str,
        summary: str,
    ) -> ApprovalView:
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO approvals (approval_id, task_id, call_id, tool_name, args_json, args_hash, summary, "
                "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    approval_id,
                    task_id,
                    call_id,
                    tool_name,
                    json.dumps(args, ensure_ascii=False),
                    args_hash,
                    summary,
                    now_iso(),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        approval = self.get_approval(approval_id)
        assert approval is not None
        return approval

    def get_approval(self, approval_id: str) -> ApprovalView | None:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        finally:
            connection.close()
        return self._approval_view(row) if row else None

    def find_approval(self, task_id: str, tool_name: str, args_hash: str) -> ApprovalView | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM approvals WHERE task_id = ? AND tool_name = ? AND args_hash = ?",
                (task_id, tool_name, args_hash),
            ).fetchone()
        finally:
            connection.close()
        return self._approval_view(row) if row else None

    def approval_status(self, approval_id: str) -> tuple[str, str | None, str | None]:
        """返回 (status, result_json, call_id)。"""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT status, result_json, call_id FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise TaskNotFound(f"确认请求不存在：{approval_id}")
        return row["status"], row["result_json"], row["call_id"]

    def approval_task(self, approval_id: str) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute("SELECT task_id FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        finally:
            connection.close()
        return row["task_id"] if row else None

    def update_approval(
        self,
        approval_id: str,
        *,
        status: ApprovalStatus,
        decided_by: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        at = now_iso()
        connection = self._connect()
        try:
            if status is ApprovalStatus.REJECTED:
                connection.execute(
                    "UPDATE approvals SET status = ?, decided_at = ?, decided_by = ? WHERE approval_id = ?",
                    (status.value, at, decided_by, approval_id),
                )
            elif status is ApprovalStatus.EXECUTED:
                connection.execute(
                    "UPDATE approvals SET status = ?, executed_at = ?, result_json = ? WHERE approval_id = ?",
                    (status.value, at, json.dumps(result or {}, ensure_ascii=False), approval_id),
                )
            else:
                connection.execute(
                    "UPDATE approvals SET status = ?, decided_at = ?, decided_by = ? WHERE approval_id = ?",
                    (status.value, at, decided_by, approval_id),
                )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _approval_view(row: sqlite3.Row) -> ApprovalView:
        return ApprovalView(
            approval_id=row["approval_id"],
            call_id=row["call_id"],
            tool_name=row["tool_name"],
            args=json.loads(row["args_json"]),
            summary=row["summary"],
            status=row["status"],
            created_at=row["created_at"],
        )

    # ---------------------------------------------------------------- recovery

    def mark_interrupted(self) -> int:
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE tasks SET status = 'failed', error_code = 'INTERRUPTED', "
                "error_message = '进程重启，任务中断；可调用 resume 继续', updated_at = ? "
                "WHERE status = 'running'",
                (now_iso(),),
            )
            connection.commit()
            return cursor.rowcount
        finally:
            connection.close()

    def status_counts(self) -> dict[str, int]:
        connection = self._connect()
        try:
            rows = connection.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status").fetchall()
        finally:
            connection.close()
        return {row["status"]: row["n"] for row in rows}

    @staticmethod
    def is_terminal(status: str) -> bool:
        return status in {TaskStatus.COMPLETED.value, TaskStatus.LIMIT_REACHED.value}
