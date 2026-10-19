# 企业 IT 服务台与资产支持 Agent

> 一个可以真实完成多步骤任务的 Agent：模型自主选择工具、程序真实执行、结果回灌模型、写操作必须人工确认。
> 全部业务数据与制度文件均为虚构，不连接任何真实企业系统。

[![CI](https://github.com/haozhouliuliuliu/it-service-desk-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/haozhouliuliuliu/it-service-desk-agent/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## 项目状态

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 仓库骨架、CI、依赖与配置模板 | 🚧 进行中 |
| M1 | 虚构数据、制度检索、7 个业务工具与注册表 | ⏳ 未开始 |
| M2 | Agent 循环、SQLite 状态机、事件与工具历史 | ⏳ 未开始 |
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

## 计划中的工具（7 个业务工具 + 1 个控制工具）

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

## 数据声明

- 公司名、员工、设备编号、工单、制度文件全部虚构；
- 不包含真实密钥；LLM API key 只从环境变量读取，仓库只提供 `.env.example`；
- 不执行任何真实资产变更、付款或采购操作。

## 快速开始

> M1 起可用。当前仓库只有骨架。

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
```

## 文档

- 架构与数据流：`docs/architecture.md`（M2 起）
- 工具手册：`docs/tools.md`（M1 起）
- 测试报告：`docs/testing.md`（M4 起）
- 真实模型评估：`evidence/real-model/`（M4 起）
- 已知限制：`docs/known-limitations.md`（M5 起）

## 许可

[MIT](LICENSE)
