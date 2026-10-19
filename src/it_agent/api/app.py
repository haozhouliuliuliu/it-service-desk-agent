"""FastAPI 应用（M3）：任务接口、状态/事件、人工确认、任务锁与后台执行。

接口层只做参数校验与调用 Runner；业务状态全部来自数据库。
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .. import __version__
from ..agent import TaskNotFound, TaskState, TaskStatus
from ..agent.runner import RESUMABLE_ERROR_CODES
from ..agent.state import ApprovalStatus
from ..config import Settings
from ..errors import AgentError, ConfigError
from ..logging_config import setup_logging
from ..runtime import Runtime, build_runtime
from .locks import TaskLockRegistry
from .models import (
    CreateTaskIn,
    DecisionIn,
    HealthOut,
    InputIn,
    TaskAccepted,
    TaskListOut,
)

LOGGER = logging.getLogger("it_agent.api")


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def create_app(
    settings: Settings | None = None,
    *,
    client=None,
    configure_logging: bool = True,
) -> FastAPI:
    active = settings or Settings.from_env()
    if configure_logging:
        setup_logging(active.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = build_runtime(active, client=client)
        recovered = runtime.runner.recover_interrupted()
        app.state.runtime = runtime
        app.state.locks = TaskLockRegistry()
        app.state.recovered = recovered
        LOGGER.info("startup", extra={"recovered_tasks": recovered, "model": runtime.runner.client.model})
        try:
            yield
        finally:
            runtime.close()
            LOGGER.info("shutdown")

    application = FastAPI(
        title="企业 IT 服务台与资产支持 Agent",
        version=__version__,
        summary="多步骤工具调用 Agent：任务、事件、人工确认",
        description="全部数据虚构；写操作必须人工确认；不使用真实企业系统。",
        lifespan=lifespan,
    )
    application.state.settings = active

    _register_handlers(application)
    _register_middleware(application)
    _register_routes(application)
    return application


# ---------------------------------------------------------------- errors


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message})


def _register_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api(request: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(TaskNotFound)
    async def handle_not_found(request: Request, exc: TaskNotFound) -> JSONResponse:
        return _error(404, "TASK_NOT_FOUND", str(exc))

    @app.exception_handler(AgentError)
    async def handle_agent(request: Request, exc: AgentError) -> JSONResponse:
        return _error(400, "AGENT_ERROR", str(exc))

    @app.exception_handler(ConfigError)
    async def handle_config(request: Request, exc: ConfigError) -> JSONResponse:
        return _error(503, "CONFIG_ERROR", str(exc))

    @app.exception_handler(sqlite3.Error)
    async def handle_db(request: Request, exc: sqlite3.Error) -> JSONResponse:
        LOGGER.exception("database_error")
        return _error(503, "DATABASE_ERROR", "数据库暂时不可用")

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": [str(part) for part in error.get("loc", [])], "msg": str(error.get("msg", ""))}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"code": "VALIDATION_ERROR", "message": "请求参数不合法", "errors": details},
        )


def _register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def access_log(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            LOGGER.exception("request_failed", extra={"request_id": request_id, "path": request.url.path})
            raise
        duration = (time.perf_counter() - started) * 1000
        response.headers["x-request-id"] = request_id
        LOGGER.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration, 1),
            },
        )
        return response


# ---------------------------------------------------------------- helpers


def _runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def _locks(request: Request) -> TaskLockRegistry:
    return request.app.state.locks


def _execute(
    runtime: Runtime,
    locks: TaskLockRegistry,
    task_id: str,
    action: str,
    *,
    text: str | None = None,
    approval_id: str | None = None,
    decision: str | None = None,
    decided_by: str | None = None,
) -> None:
    with locks.hold(task_id) as acquired:
        if not acquired:
            LOGGER.info("task_skipped_locked", extra={"task_id": task_id, "action": action})
            return
        try:
            if action == "run":
                runtime.runner.run(task_id)
            elif action == "input":
                assert text is not None
                runtime.runner.submit_input(task_id, text)
            elif action == "approval":
                assert approval_id is not None and decision is not None and decided_by is not None
                runtime.runner.decide_approval(task_id, approval_id, decision=decision, decided_by=decided_by)
            elif action == "resume":
                runtime.runner.resume(task_id)
            state = runtime.repository.get_task(task_id)
            LOGGER.info("task_action_done", extra={"task_id": task_id, "status": state.status})
        except AgentError as exc:
            LOGGER.info("task_action_rejected", extra={"task_id": task_id, "reason": str(exc)})
        except Exception:
            LOGGER.exception("task_action_failed", extra={"task_id": task_id})


def _get_task(runtime: Runtime, task_id: str) -> TaskState:
    return runtime.repository.get_task(task_id)


# ---------------------------------------------------------------- routes


def _register_routes(app: FastAPI) -> None:
    @app.get("/healthz", response_model=HealthOut, tags=["ops"])
    def healthz(request: Request) -> HealthOut:
        runtime = _runtime(request)
        runtime.repository.count_tasks()  # 触发一次数据库读取
        return HealthOut(
            status="ok",
            version=__version__,
            model=runtime.runner.client.model,
            recovered_tasks=getattr(request.app.state, "recovered", 0),
        )

    @app.post("/api/tasks", response_model=TaskAccepted, status_code=202, tags=["tasks"])
    def create_task_endpoint(request: Request, payload: CreateTaskIn, background: BackgroundTasks) -> TaskAccepted:
        runtime = _runtime(request)
        state = runtime.runner.prepare_task(payload.message)
        background.add_task(_execute, runtime, _locks(request), state.task_id, "run")
        return TaskAccepted(
            task_id=state.task_id,
            status=state.status,
            detail_url=f"/api/tasks/{state.task_id}",
        )

    @app.get("/api/tasks", response_model=TaskListOut, tags=["tasks"])
    def list_tasks_endpoint(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
        status: str | None = None,
    ) -> TaskListOut:
        runtime = _runtime(request)
        if status is not None and status not in {item.value for item in TaskStatus}:
            raise ApiError(400, "INVALID_STATUS", f"未知状态：{status}")
        items = runtime.repository.list_tasks(limit=limit, offset=offset, status=status)
        total = runtime.repository.count_tasks(status=status)
        return TaskListOut(items=tuple(items), total=total, limit=limit, offset=offset)

    @app.get("/api/tasks/{task_id}", response_model=TaskState, tags=["tasks"])
    def get_task_endpoint(request: Request, task_id: str) -> TaskState:
        return _get_task(_runtime(request), task_id)

    @app.get("/api/tasks/{task_id}/events", tags=["tasks"])
    def get_events_endpoint(
        request: Request,
        task_id: str,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> list[dict]:
        runtime = _runtime(request)
        _get_task(runtime, task_id)
        return [event.model_dump(mode="json") for event in runtime.repository.events(task_id, limit)]

    @app.get("/api/tasks/{task_id}/tool-calls", tags=["tasks"])
    def get_tool_calls_endpoint(request: Request, task_id: str) -> list[dict]:
        runtime = _runtime(request)
        _get_task(runtime, task_id)
        return [call.model_dump(mode="json") for call in runtime.repository.tool_calls(task_id)]

    @app.get("/api/tools", tags=["tools"])
    def list_tools_endpoint(request: Request) -> list[dict]:
        return _runtime(request).registry.catalog()

    @app.post("/api/tasks/{task_id}/input", response_model=TaskAccepted, status_code=202, tags=["tasks"])
    def submit_input_endpoint(
        request: Request, task_id: str, payload: InputIn, background: BackgroundTasks
    ) -> TaskAccepted:
        runtime = _runtime(request)
        locks = _locks(request)
        state = _get_task(runtime, task_id)
        if locks.is_busy(task_id):
            raise ApiError(409, "TASK_BUSY", "任务正在执行，请稍后再提交")
        if state.status != TaskStatus.WAITING_FOR_INPUT.value:
            raise ApiError(400, "NOT_WAITING_FOR_INPUT", f"任务当前状态为 {state.status}，不需要补充信息")
        background.add_task(_execute, runtime, locks, task_id, "input", text=payload.text)
        return TaskAccepted(task_id=task_id, status=TaskStatus.RUNNING.value, detail_url=f"/api/tasks/{task_id}")

    @app.post(
        "/api/tasks/{task_id}/approvals/{approval_id}",
        response_model=TaskAccepted,
        tags=["tasks"],
    )
    def decide_approval_endpoint(
        request: Request,
        task_id: str,
        approval_id: str,
        payload: DecisionIn,
        background: BackgroundTasks,
    ) -> TaskAccepted:
        runtime = _runtime(request)
        locks = _locks(request)
        state = _get_task(runtime, task_id)
        approval = runtime.repository.get_approval(approval_id)
        if approval is None or runtime.repository.approval_task(approval_id) != task_id:
            raise ApiError(404, "APPROVAL_NOT_FOUND", f"确认请求不存在：{approval_id}")
        if locks.is_busy(task_id):
            raise ApiError(409, "TASK_BUSY", "任务正在执行，请稍后再确认")

        if approval.status == ApprovalStatus.EXECUTED.value:
            return TaskAccepted(task_id=task_id, status=state.status, detail_url=f"/api/tasks/{task_id}")
        if approval.status == ApprovalStatus.REJECTED.value:
            raise ApiError(409, "APPROVAL_REJECTED", "该操作已经被拒绝，不能再次执行")
        if state.status != TaskStatus.WAITING_FOR_APPROVAL.value:
            raise ApiError(400, "NOT_WAITING_FOR_APPROVAL", f"任务当前状态为 {state.status}，不需要确认")

        background.add_task(
            _execute,
            runtime,
            locks,
            task_id,
            "approval",
            approval_id=approval_id,
            decision=payload.decision,
            decided_by=payload.decided_by,
        )
        return TaskAccepted(task_id=task_id, status=TaskStatus.RUNNING.value, detail_url=f"/api/tasks/{task_id}")

    @app.post("/api/tasks/{task_id}/resume", response_model=TaskAccepted, status_code=202, tags=["tasks"])
    def resume_endpoint(request: Request, task_id: str, background: BackgroundTasks) -> TaskAccepted:
        runtime = _runtime(request)
        locks = _locks(request)
        state = _get_task(runtime, task_id)
        if locks.is_busy(task_id):
            raise ApiError(409, "TASK_BUSY", "任务正在执行，请勿重复恢复")
        if state.status in {
            TaskStatus.COMPLETED.value,
            TaskStatus.LIMIT_REACHED.value,
        }:
            return TaskAccepted(task_id=task_id, status=state.status, detail_url=f"/api/tasks/{task_id}")
        if state.status != TaskStatus.FAILED.value:
            raise ApiError(400, "NOT_RESUMABLE", f"任务当前状态为 {state.status}，不能恢复")
        if state.error_code not in RESUMABLE_ERROR_CODES:
            raise ApiError(409, "NOT_RESUMABLE", f"错误类型 {state.error_code} 不允许自动恢复")

        background.add_task(_execute, runtime, locks, task_id, "resume")
        return TaskAccepted(task_id=task_id, status=TaskStatus.RUNNING.value, detail_url=f"/api/tasks/{task_id}")
