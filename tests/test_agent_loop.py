"""Agent 核心循环测试：单步、多步、澄清、写确认、失败与上限。"""

from __future__ import annotations

from pathlib import Path

import pytest

from it_agent.agent import TaskStatus
from it_agent.agent.repository import AgentRepository
from it_agent.errors import AgentError
from it_agent.llm.client import ChatResponse, LLMError
from it_agent.llm.fake import answer, call, tool_response

from .support import build_env

CREATE_ARGS = {
    "title": "新员工设备申请",
    "description": "研发新同事下周一入职，需要笔记本与显示器。",
    "category": "hardware",
    "priority": "P3",
    "requester": "EMP-1005",
}


def _ticket_count(store) -> int:
    return len(store.list_tickets(limit=50))


def test_single_tool_then_answer(tmp_path: Path) -> None:
    env = build_env(
        tmp_path,
        [
            tool_response(call("query_inventory", {"category": "笔记本电脑"})),
            answer("笔记本电脑有 4 台可用库存。"),
        ],
    )
    state = env.runner.create_task("现在笔记本还有库存吗？")
    assert state.status == TaskStatus.COMPLETED.value
    assert state.tool_call_count == 1
    assert "4 台" in (state.final_answer or "")
    calls = env.repository.tool_calls(state.task_id)
    assert [item.tool_name for item in calls] == ["query_inventory"]
    assert calls[0].ok is True


def test_multi_step_chain(tmp_path: Path) -> None:
    env = build_env(
        tmp_path,
        [
            tool_response(call("search_policies", {"query": "新员工 电脑 领用"})),
            tool_response(call("query_inventory", {"category": "笔记本电脑"})),
            answer("依据《电脑领用制度》· 领用条件，新员工需先提交申请；当前可用笔记本 4 台。"),
        ],
    )
    state = env.runner.create_task("下周有新员工入职，帮我确认电脑库存和领用规定。")
    assert state.status == TaskStatus.COMPLETED.value
    assert [item.tool_name for item in env.repository.tool_calls(state.task_id)] == [
        "search_policies",
        "query_inventory",
    ]
    assert state.step_count == 3


def test_tool_result_is_fed_back_to_model(tmp_path: Path) -> None:
    env = build_env(
        tmp_path,
        [
            tool_response(call("query_inventory", {"category": "笔记本电脑"})),
            answer("已看到库存。"),
        ],
    )
    env.runner.create_task("查一下笔记本库存")
    second_call_messages = env.client.calls[1]["messages"]
    tool_messages = [message for message in second_call_messages if message["role"] == "tool"]
    assert len(tool_messages) == 1
    assert '"total_available": 4' in tool_messages[0]["content"]


def test_clarification_pauses_and_resumes(tmp_path: Path) -> None:
    env = build_env(
        tmp_path,
        [
            tool_response(
                call("request_user_input", {"question": "新员工是研发岗还是职能岗？", "options": ["研发", "职能"]})
            ),
            answer("研发岗按标准配置 ThinkPad T14；已记录。"),
        ],
    )
    state = env.runner.create_task("给新员工准备一台电脑")
    assert state.status == TaskStatus.WAITING_FOR_INPUT.value
    assert state.pending_question is not None

    resumed = env.runner.submit_input(state.task_id, "研发岗")
    assert resumed.status == TaskStatus.COMPLETED.value
    user_messages = [m for m in env.repository.messages(state.task_id) if m["role"] == "user"]
    assert user_messages[-1]["content"] == "研发岗"


def test_write_requires_approval_then_executes(tmp_path: Path) -> None:
    env = build_env(
        tmp_path,
        [
            tool_response(call("create_ticket", CREATE_ARGS)),
            answer("工单已创建，等待 IT 处理。"),
        ],
    )
    state = env.runner.create_task("帮我给新员工创建一张设备申请工单")
    assert state.status == TaskStatus.WAITING_FOR_APPROVAL.value
    assert state.pending_approval is not None
    assert _ticket_count(env.store) == 20  # 未确认，未写入

    approval_id = state.pending_approval.approval_id
    approved = env.runner.decide_approval(state.task_id, approval_id, decision="approve", decided_by="张会计")
    assert approved.status == TaskStatus.COMPLETED.value
    assert _ticket_count(env.store) == 21
    assert env.repository.get_approval(approval_id).status == "executed"


def test_rejection_does_not_write(tmp_path: Path) -> None:
    env = build_env(
        tmp_path,
        [
            tool_response(call("create_ticket", CREATE_ARGS)),
            answer("已按您的意思不创建工单。"),
        ],
    )
    state = env.runner.create_task("创建一张设备申请工单")
    approval_id = state.pending_approval.approval_id
    rejected = env.runner.decide_approval(state.task_id, approval_id, decision="reject", decided_by="张会计")
    assert rejected.status == TaskStatus.COMPLETED.value
    assert _ticket_count(env.store) == 20
    assert env.repository.get_approval(approval_id).status == "rejected"


def test_duplicate_approval_does_not_duplicate_ticket(tmp_path: Path) -> None:
    env = build_env(
        tmp_path,
        [
            tool_response(call("create_ticket", CREATE_ARGS)),
            answer("已创建。"),
        ],
    )
    state = env.runner.create_task("创建一张设备申请工单")
    approval_id = state.pending_approval.approval_id
    first = env.runner.decide_approval(state.task_id, approval_id, decision="approve", decided_by="张会计")
    second = env.runner.decide_approval(state.task_id, approval_id, decision="approve", decided_by="张会计")
    assert first.status == TaskStatus.COMPLETED.value
    assert second.status == TaskStatus.COMPLETED.value
    assert _ticket_count(env.store) == 21


def test_repeated_same_write_is_replayed_not_duplicated(tmp_path: Path) -> None:
    env = build_env(
        tmp_path,
        [
            tool_response(call("create_ticket", CREATE_ARGS)),
            tool_response(call("create_ticket", CREATE_ARGS, call_id="call_repeat")),
            answer("工单已存在，未重复创建。"),
        ],
    )
    state = env.runner.create_task("创建一张设备申请工单")
    approval_id = state.pending_approval.approval_id
    final = env.runner.decide_approval(state.task_id, approval_id, decision="approve", decided_by="张会计")
    assert final.status == TaskStatus.COMPLETED.value
    assert _ticket_count(env.store) == 21
    approvals = env.repository.tool_calls(state.task_id)
    assert len(approvals) == 2


def test_tool_call_limit(tmp_path: Path) -> None:
    env = build_env(
        tmp_path,
        [
            tool_response(call("query_inventory", {}, call_id="c1")),
            tool_response(call("query_inventory", {}, call_id="c2")),
            tool_response(call("query_inventory", {}, call_id="c3")),
            answer("不应到达"),
        ],
        max_tool_calls=2,
    )
    state = env.runner.create_task("反复查库存")
    assert state.status == TaskStatus.LIMIT_REACHED.value
    assert state.error_code == "TOOL_CALL_LIMIT"
    assert state.tool_call_count == 2
    assert len(env.client.calls) == 3


def test_step_limit(tmp_path: Path) -> None:
    env = build_env(
        tmp_path,
        [
            tool_response(call("query_inventory", {}, call_id="c1")),
            tool_response(call("list_tickets", {}, call_id="c2")),
            answer("不应到达"),
        ],
        max_steps=2,
    )
    state = env.runner.create_task("查库存再查工单")
    assert state.status == TaskStatus.LIMIT_REACHED.value
    assert state.error_code == "STEP_LIMIT"
    assert state.step_count == 2
    assert len(env.client.calls) == 2


def test_unknown_tool_error_is_fed_back(tmp_path: Path) -> None:
    env = build_env(
        tmp_path,
        [
            tool_response(call("run_sql", {"sql": "DROP TABLE tickets"})),
            answer("该操作不可用，已改用标准查询。"),
        ],
    )
    state = env.runner.create_task("删掉工单表")
    assert state.status == TaskStatus.COMPLETED.value
    calls = env.repository.tool_calls(state.task_id)
    assert calls[0].ok is False
    assert calls[0].error_code == "TOOL_NOT_FOUND"
    assert "DROP TABLE" not in env.client.calls[1]["messages"][-1]["content"]


def test_invalid_arguments_are_fed_back(tmp_path: Path) -> None:
    env = build_env(
        tmp_path,
        [
            tool_response(call("get_ticket", {"ticket_id": "BAD"})),
            answer("请提供正确的工单号。"),
        ],
    )
    state = env.runner.create_task("查一下工单 BAD")
    assert state.status == TaskStatus.COMPLETED.value
    calls = env.repository.tool_calls(state.task_id)
    assert calls[0].error_code == "INVALID_ARGUMENTS"


def test_llm_retry_then_success(tmp_path: Path) -> None:
    env = build_env(
        tmp_path,
        [LLMError("LLM_HTTP_5XX", "上游 500", retryable=True), answer("重试后成功。")],
        max_llm_retries=2,
    )
    state = env.runner.create_task("你好")
    assert state.status == TaskStatus.COMPLETED.value
    assert env.sleeps == [0.5]
    event_types = [event.type for event in env.repository.events(state.task_id)]
    assert "llm_error" in event_types


def test_llm_non_retryable_fails_fast(tmp_path: Path) -> None:
    env = build_env(
        tmp_path,
        [LLMError("LLM_HTTP_401", "鉴权失败", retryable=False), answer("不应到达")],
        max_llm_retries=2,
    )
    state = env.runner.create_task("你好")
    assert state.status == TaskStatus.FAILED.value
    assert state.error_code == "LLM_HTTP_401"
    assert len(env.client.calls) == 1
    assert env.sleeps == []


def test_llm_retries_exhausted(tmp_path: Path) -> None:
    env = build_env(
        tmp_path,
        [
            LLMError("LLM_TIMEOUT", "超时", retryable=True),
            LLMError("LLM_TIMEOUT", "超时", retryable=True),
            LLMError("LLM_TIMEOUT", "超时", retryable=True),
        ],
        max_llm_retries=2,
    )
    state = env.runner.create_task("你好")
    assert state.status == TaskStatus.FAILED.value
    assert state.error_code == "LLM_TIMEOUT"
    assert env.sleeps == [0.5, 1.0]


def test_empty_response_fails(tmp_path: Path) -> None:
    env = build_env(tmp_path, [ChatResponse(content=None, tool_calls=())])
    state = env.runner.create_task("你好")
    assert state.status == TaskStatus.FAILED.value
    assert state.error_code == "EMPTY_RESPONSE"


def test_persistence_across_repository_instances(tmp_path: Path) -> None:
    env = build_env(
        tmp_path,
        [
            tool_response(call("query_inventory", {})),
            answer("库存已查。"),
        ],
    )
    state = env.runner.create_task("查库存")
    fresh = AgentRepository(env.db_path)
    reloaded = fresh.get_task(state.task_id)
    assert reloaded.status == TaskStatus.COMPLETED.value
    assert reloaded.final_answer == "库存已查。"
    assert len(fresh.messages(state.task_id)) == 5
    assert len(fresh.tool_calls(state.task_id)) == 1
    assert len(fresh.events(state.task_id)) > 0


def test_interrupted_task_can_resume(tmp_path: Path) -> None:
    env = build_env(tmp_path, [answer("继续完成了。")])
    state = env.repository.create_task("TASK-resume", "查库存", "scripted")
    assert state.status == TaskStatus.RUNNING.value
    marked = env.repository.mark_interrupted()
    assert marked == 1
    failed = env.repository.get_task("TASK-resume")
    assert failed.status == TaskStatus.FAILED.value
    assert failed.error_code == "INTERRUPTED"

    resumed = env.runner.resume("TASK-resume")
    assert resumed.status == TaskStatus.COMPLETED.value


def test_terminal_task_rejects_input(tmp_path: Path) -> None:
    env = build_env(tmp_path, [answer("完成。")])
    state = env.runner.create_task("你好")
    with pytest.raises(AgentError):
        env.runner.submit_input(state.task_id, "补充")


def test_approval_belongs_to_task(tmp_path: Path) -> None:
    env = build_env(
        tmp_path,
        [
            tool_response(call("create_ticket", CREATE_ARGS)),
            answer("已创建。"),
        ],
    )
    state = env.runner.create_task("创建工单")
    approval_id = state.pending_approval.approval_id
    with pytest.raises(AgentError):
        env.runner.decide_approval("TASK-other", approval_id, decision="approve", decided_by="张会计")


def test_events_are_ordered(tmp_path: Path) -> None:
    env = build_env(
        tmp_path,
        [tool_response(call("query_inventory", {})), answer("完成")],
    )
    state = env.runner.create_task("查库存")
    events = env.repository.events(state.task_id)
    assert [event.seq for event in events] == sorted(event.seq for event in events)
    types = [event.type for event in events]
    assert types[0] == "task_created"
    assert "status_changed" in types
