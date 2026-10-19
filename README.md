# 企业 IT 服务台与资产支持 Agent

> 一个可以真实完成多步骤任务的 Agent：模型自主选择工具、程序真实执行、结果回灌模型、写操作必须人工确认。
> 全部业务数据与制度文件均为虚构，不连接任何真实企业系统。

[![CI](https://github.com/haozhouliuliuliu/it-service-desk-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/haozhouliuliuliu/it-service-desk-agent/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## 项目状态

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 仓库骨架、CI、依赖与配置模板 | ✅ v0.1.0 |
| M1 | 虚构数据、制度检索、7 个业务工具与注册表 | ✅ v0.2.0 |
| M2 | Agent 循环、SQLite 状态机、事件与工具历史 | ✅ v0.3.0 |
| M3 | HTTP API、人工确认、幂等写入、安全边界 | ⏳ 未开始 |
| M4 | 固定测试集（30 例）、自动化测试、真实模型评估 | ⏳ 未开始 |
| M5 | 架构文档、工具手册、测试报告、Docker、v1.0.0 | ⏳ 未开始 |

## 与"固定工作流"的区别

本项目的执行顺序由模型根据工具返回结果动态决定，而不是代码里写死的步骤：

```text
用户任务 → 模型选择下一步
              ├─ 调用工具 → 程序校验/执行 → 结果回灌 → 模型再决定
              ├─ 请求澄清 → 任务挂起 → 用户补充 → 继续
              ├─ 申请写操作 → 挂起等人工确认 → 确认后仅执行该操作
              └─ 输出最终答复 → 完成
```

程序负责：工具白名单、参数校验、权限判断、状态流转、事件记录、执行上限与失败处理。
模型只能"提议"，不能直接触碰数据库、文件系统或系统命令。

## 已实现的工具（7 个业务工具 + 1 个控制工具）

| 工具 | 类型 | 说明 |
|---|---|---|
| `search_policies` | 只读 | 检索虚构制度文件，返回文档标题、片段与出处 |
| `query_inventory` | 只读 | 按设备类别查询库存与可用数量 |
| `get_asset` | 只读 | 按设备编号/领用人查询设备状态 |
| `get_ticket` | 只读 | 查询工单详情 |
| `list_tickets` | 只读 | 按状态/处理人列出工单 |
| `create_ticket` | 写入 | 创建工单，必须人工确认 |
| `update_ticket` | 写入 | 修改工单状态/优先级，必须人工确认 |
| `request_user_input` | 控制 | 信息不足时向用户提问，任务进入 `waiting_for_input` |

## Agent 循环（M2）

```text
用户任务 → 模型 → tool_calls → 程序校验/执行 → 结果回灌 → 模型再决定
                ├─ 只读工具：执行后继续
                ├─ 写工具：挂起等待人工确认，确认后只执行该操作
                ├─ request_user_input：挂起等待用户补充
                └─ 无 tool_calls 且有内容：完成
```

| 硬约束 | 值 |
|---|---|
| 单任务工具调用上限 | 8 次（超出 → `limit_reached`） |
| 单任务模型交互上限 | 12 轮（超出 → `limit_reached`） |
| LLM 传输重试 | 2 次（仅超时/连接/429/5xx） |
| 写操作 | 无 `ApprovalToken` 一律拒绝 |
| 重复写操作 | 同任务同工具同参数复用确认结果，不重复写入 |

状态：`running / waiting_for_input / waiting_for_approval / completed / failed / limit_reached`。
进程重启时遗留 `running` 会标记为 `failed(INTERRUPTED)`，需显式 `resume`，不谎称自动恢复。

任务、消息、事件、每次工具调用与确认记录全部落 SQLite，可用 `it-agent task <id>` 回看。

## 数据声明

- 公司名、员工、设备编号、工单、制度文件全部虚构；
- 不包含真实密钥；LLM API key 只从环境变量读取，仓库只提供 `.env.example`；
- 不执行任何真实资产变更、付款或采购操作。

## 快速开始（M2）

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

# 1) 用 seed_data 初始化 SQLite（虚构员工/资产/工单）
it-agent init-db

# 2) 查看注册的工具（含参数 schema）
it-agent tools --json

# 3) 检索制度
it-agent search "新员工 电脑 领用"

# 4) 真实模型跑一个任务（需要 LLM_API_KEY，见 .env.example）
it-agent run "现在笔记本电脑还有多少可用库存？"

# 5) 回看轨迹 / 处理待确认
it-agent task <task_id>
it-agent approve <task_id> <approval_id> --by 张会计
it-agent reject  <task_id> <approval_id> --by 张会计
it-agent input   <task_id> "补充信息"

# 6) 测试
pytest -q
```

数据说明：

- `seed_data/`：虚构制度 Markdown、14 名员工、30 台设备、20 条工单；
- `data/agent.db`：运行期 SQLite（已 gitignore），由 `init-db` 生成；
- 库为空时才导入种子数据，运行期新建的工单不会被种子覆盖。

## 文档

- 工具手册：`docs/tools.md`
- 架构与数据流：`docs/architecture.md`
- 测试报告：`docs/testing.md`（M4 起）
- 真实模型评估：`evidence/real-model/`（M4 起）
- 已知限制：`docs/known-limitations.md`（M5 起）

## 许可

[MIT](LICENSE)
