# HTTP API（M3）

启动：

```bash
it-agent serve --host 127.0.0.1 --port 8000
# 交互文档：http://127.0.0.1:8000/docs
```

所有接口返回 JSON；错误体统一为 `{"code": "...", "message": "..."}`。

## 1. 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/tasks` | 创建任务，`{"message": "..."}`，返回 `task_id` |
| `GET` | `/api/tasks` | 任务列表，支持 `limit` / `offset` / `status` |
| `GET` | `/api/tasks/{id}` | 任务状态（含待确认信息） |
| `GET` | `/api/tasks/{id}/events` | 执行事件（工具调用、状态变化、确认） |
| `GET` | `/api/tasks/{id}/tool-calls` | 每次工具调用的参数与结果 |
| `POST` | `/api/tasks/{id}/input` | 补充信息，`{"text": "..."}` |
| `POST` | `/api/tasks/{id}/approvals/{approval_id}` | `{"decision":"approve|reject","decided_by":"..."}` |
| `POST` | `/api/tasks/{id}/resume` | 恢复失败任务 |
| `GET` | `/api/tools` | 工具注册表（参数 schema、权限） |
| `GET` | `/healthz` | 健康检查（模型、启动时恢复任务数） |

## 2. 一次完整交互

```bash
# 1) 创建任务
curl -s -X POST http://127.0.0.1:8000/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"message":"下周有研发新员工入职，确认电脑库存和领用规定，并给 EMP-1005 创建设备申请工单"}'
# {"task_id":"TASK-1a2b3c4d","status":"running","detail_url":"/api/tasks/TASK-1a2b3c4d"}

# 2) 查状态
curl -s http://127.0.0.1:8000/api/tasks/TASK-1a2b3c4d
# status=waiting_for_input 或 waiting_for_approval 或 completed

# 3) 需要补充信息时
curl -s -X POST http://127.0.0.1:8000/api/tasks/TASK-1a2b3c4d/input \
  -H 'Content-Type: application/json' -d '{"text":"研发岗，优先 ThinkPad T14"}'

# 4) 待确认时
curl -s http://127.0.0.1:8000/api/tasks/TASK-1a2b3c4d | jq '.pending_approval'
# {"approval_id":"APR-xxxx","summary":"创建工单《...》", ...}

curl -s -X POST http://127.0.0.1:8000/api/tasks/TASK-1a2b3c4d/approvals/APR-xxxx \
  -H 'Content-Type: application/json' \
  -d '{"decision":"approve","decided_by":"张会计"}'

# 5) 看轨迹
curl -s http://127.0.0.1:8000/api/tasks/TASK-1a2b3c4d/tool-calls | jq '.[].tool_name'
curl -s http://127.0.0.1:8000/api/tasks/TASK-1a2b3c4d/events | jq '.[].type'
```

## 3. 状态与界面语义

| 状态 | 接口表现 | 客户端该做什么 |
|---|---|---|
| `running` | 后台执行中 | 轮询 `GET /api/tasks/{id}` |
| `waiting_for_input` | `pending_question` 非空 | 调 `/input` 提交补充 |
| `waiting_for_approval` | `pending_approval` 非空 | 展示 summary，调 `/approvals/{id}` |
| `completed` | `final_answer` 非空 | 结束 |
| `failed` | `error_code` / `error_message` | 可恢复错误调 `/resume`，否则人工处理 |
| `limit_reached` | `error_code=TOOL_CALL_LIMIT/STEP_LIMIT` | 不可自动续跑 |

## 4. 幂等与并发

| 场景 | 行为 |
|---|---|
| 重复创建同一任务 | 每次生成新 `task_id`（任务内容相同也不会合并） |
| 重复确认同一 approval | 已执行 → 直接返回当前状态，不二次写入 |
| 模型重复提交同一写操作 | 命中已执行 approval，复用结果 |
| 任务执行中再次提交 input/approval/resume | `409 TASK_BUSY` |
| 任务不在等待状态时提交 input | `400 NOT_WAITING_FOR_INPUT` |
| 已拒绝的 approval 再次 approve | `409 APPROVAL_REJECTED` |
| 完成/超限任务 resume | `202` 空操作 |
| 服务重启 | 遗留 `running` 标为 `failed(INTERRUPTED)`，需显式 `/resume` |

## 5. 错误码

| HTTP | code | 场景 |
|---|---|---|
| 400 | `AGENT_ERROR` / `NOT_WAITING_FOR_INPUT` / `NOT_WAITING_FOR_APPROVAL` / `NOT_RESUMABLE` / `INVALID_STATUS` | 状态不允许 |
| 404 | `TASK_NOT_FOUND` / `APPROVAL_NOT_FOUND` | 任务或确认不存在/不属于该任务 |
| 409 | `TASK_BUSY` / `APPROVAL_REJECTED` / `NOT_RESUMABLE` | 并发或状态冲突 |
| 422 | `VALIDATION_ERROR` | 请求体不合法 |
| 503 | `CONFIG_ERROR` / `DATABASE_ERROR` | 缺模型配置或数据库不可用 |
