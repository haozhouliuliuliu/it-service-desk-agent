# 架构与数据流（M2）

> 本文描述 Agent 的核心链路：模型决策、工具执行、状态持久化、人工确认。
> 代码中没有"先查库存、再查制度、最后建单"的固定顺序——顺序由模型每轮根据工具结果决定。

## 1. 组件

```text
┌────────────┐    POST /tasks (M3)     ┌──────────────────────────────┐
│  HTTP API  │ ──────────────────────► │  AgentRunner                 │
│  FastAPI   │ ◄────────────────────── │  while: 模型 → 工具 → 回灌   │
└────────────┘   状态/事件/确认         └───────┬──────────────┬───────┘
                                                │              │
                                     ┌──────────▼──────┐  ┌────▼─────────┐
                                     │ ToolRegistry    │  │ SQLite       │
                                     │ 白名单/Schema    │  │ tasks        │
                                     │ 参数校验/权限    │  │ messages     │
                                     └───────┬─────────┘  │ events       │
                                             │            │ tool_calls   │
                                    ┌────────▼────────┐   │ approvals    │
                                    │ ToolContext     │   └──────────────┘
                                    │ DeskStore       │
                                    │ PolicyIndex     │
                                    └─────────────────┘
```

| 模块 | 职责 |
|---|---|
| `llm/client.py` | OpenAI 兼容调用；超时/HTTP 错误映射成带 code 的 `LLMError` |
| `agent/runner.py` | 核心循环、上限、暂停/恢复、确认执行、状态迁移 |
| `agent/repository.py` | 任务/消息/事件/工具调用/确认的持久化 |
| `tools/registry.py` | 工具白名单、Pydantic 校验、写操作凭证校验 |
| `tools/builtin.py` | 7 个业务工具 + `request_user_input` |
| `store.py` / `policies.py` | 业务数据读写与制度检索 |

## 2. 一轮循环

```text
1. 读取任务消息（system + user + 历史 assistant/tool）
2. 调模型（带 tools schema）
3. 若失败：可重试则退避重试，否则 failed（记录 error_code）
4. 保存 assistant 消息；step_count += 1
5. 若无 tool_calls 且有内容 → completed，保存 final_answer
6. 若有 tool_calls：逐个处理
   a. 计数 +1；未知工具/参数非法 → 错误作为 tool 消息回灌
   b. 只读工具 → 执行 → 保存 → 回灌
   c. 写工具 → 参数校验 → 查找同任务同参数的历史确认
        - 已执行 → 直接复用结果（不重复写）
        - 已拒绝 → 返回拒绝错误
        - 不存在 → 创建 pending approval，任务等待确认
   d. request_user_input → 任务等待补充
7. 回到 1，直到结束条件
```

结束条件：模型给出最终答复、等待确认、等待补充、达到上限、不可恢复错误。

## 3. 状态机

```text
running ──► waiting_for_input ──► running
   │              │
   │              └──► failed（输入阶段异常）
   ├──► waiting_for_approval ──► running（approve/reject）
   │              └──► failed
   ├──► completed        （终态）
   ├──► failed ──► running（仅可恢复错误）
   └──► limit_reached    （终态）
```

- 状态迁移集中在 `_ALLOWED_TRANSITIONS`，非法迁移直接抛错；
- `completed` 只由"模型不再请求工具且有内容"触发，**不解析自然语言**；
- 进程重启时 `running` → `failed(INTERRUPTED)`，可通过 `resume` 继续；
- `limit_reached` 不可恢复，避免无限续跑。

## 4. 上限与重试

| 限制 | 默认 | 行为 |
|---|---|---|
| 工具调用 | 8 次 | 超限 → `limit_reached`（`TOOL_CALL_LIMIT`） |
| 模型交互 | 12 轮 | 超限 → `limit_reached`（`STEP_LIMIT`） |
| LLM 传输重试 | 2 次 | 仅对超时/连接/429/5xx 退避重试 |
| 工具错误 | 不自动重试 | 错误回灌模型，由模型改参数/换工具/转人工 |

## 5. 人工确认

```text
模型调用 create_ticket
  → 参数校验 → 创建 approvals(status=pending)，任务 waiting_for_approval
  → 此阶段没有执行工具，数据库无新工单
用户 approve
  → 校验 approval 属于该任务且状态 pending
  → 用 args_hash 重建 ApprovalToken
  → registry 校验 token 后执行工具
  → 保存 tool_call、回灌 tool 消息、approval=executed
  → 任务继续循环
用户 reject
  → approval=rejected，回灌"用户拒绝，未写入"，任务继续
```

- 同一任务、同一工具、同一参数只有一条 approval（数据库唯一约束）；
- 重复 approve 已执行的操作直接返回当前状态，不会二次写入；
- 模型重复提交同一写操作时，命中已执行 approval 并复用结果（`approval_replayed` 事件）；
- 确认只针对一个具体操作，不构成后续写操作的授权。

## 6. 数据表

| 表 | 关键字段 | 用途 |
|---|---|---|
| `tasks` | task_id, status, user_input, final_answer, error_code, step_count, tool_call_count | 任务状态 |
| `task_messages` | role, content, tool_calls_json, tool_call_id, seq | 可重建的对话历史 |
| `agent_events` | seq, type, payload_json | 执行事件（可审计） |
| `tool_calls` | call_id, tool_name, args_json, result_json, ok | 每次工具调用 |
| `approvals` | approval_id, call_id, args_hash, summary, status, result_json | 写操作确认 |

事件类型：`task_created`、`llm_request`、`llm_error`、`llm_response`、`tool_call_requested`、
`tool_call_finished`、`approval_requested`、`approval_rejected`、`approval_executed`、
`approval_replayed`、`input_requested`、`status_changed`、`task_finished`、`task_resumed`。

## 7. 重启语义

- 启动时调用 `recover_interrupted()`，把遗留 `running` 标为 `failed(INTERRUPTED)`；
- `resume` 只允许可恢复错误（中断、超时、5xx、限流）；
- 已完成的写操作不会因恢复而重复执行（approval 唯一约束 + 已执行检查）；
- **不做"自动全部恢复"的承诺**：需要显式 resume，失败任务如实标记。

## 8. 并发边界

当前是单进程假设：Runner 串行处理一个任务，SQLite 用 WAL + busy_timeout。
同一任务并发执行由 API 层的任务锁限制；多 worker 需要数据库抢占（见已知限制）。

## 9. HTTP 接口层（M3）

```text
FastAPI 路由
  ├─ 参数校验（Pydantic）
  ├─ 任务锁（进程内 threading.Lock）
  ├─ BackgroundTasks → AgentRunner
  └─ 读取 Repository 返回状态/事件/工具调用
```

- 创建任务时先 `prepare_task` 落库并返回 `task_id`，循环在后台线程执行；
- 所有写接口（input / approval / resume）先检查任务锁，忙则 `409 TASK_BUSY`；
- 状态、事件、工具调用直接查库，接口不做业务推断；
- 启动时执行 `recover_interrupted()`，把 `running` 标为 `failed(INTERRUPTED)`；
- 关闭时关闭模型客户端；SQLite 连接按调用开闭，WAL 允许并发读。

幂等入口：

| 操作 | 幂等策略 |
|---|---|
| approve 已执行 | 直接返回，不再执行 |
| 模型重复写 | `approvals` 唯一约束 + 已执行复用 |
| input 非等待态 | 400，不排队 |
| resume 完成/超限 | 202 空操作 |
| 并发执行 | 锁 + 409 |

接口文档见 `docs/api.md`。

## 10. 部署（M5）

```text
docker compose up --build
  ├── 镜像：python:3.12-slim，安装 .[server]
  ├── 非 root 用户 uid 10001，/app/data 为数据卷
  ├── 健康检查：GET /healthz（含数据库读取）
  └── 端口 8000，环境变量注入模型配置
```

- 镜像内包含 `seed_data/`，启动时自动初始化虚构数据；
- SQLite 与产物位于 `agent-data` 命名卷，重启后历史任务仍在；
- 模型密钥只通过环境变量注入，不写入镜像层；
- 容器以单 worker 运行（任务锁与 SQLite 都是单进程假设），不要加 `--workers`。

CI 会真实构建镜像、启动容器、检查 `/healthz`、`/api/tools` 与运行用户，不只检查 Dockerfile 语法。
