# 真实模型评估报告

- 运行 ID：`run-20261019-071224`
- 模型：`deepseek-flash`（https://api.deepseek.com/v1，temperature=0.0）
- 开始时间：2026-10-19T07:12:24+00:00
- 限制：工具 8 次 / 交互 12 轮
- 用例：30（real 26 / scripted 4）

## 汇总

- 真实模型通过：**26/26**
- 多步骤通过：**8/8**
- 全部通过：**30/30**

| 用例 | 类别 | 模式 | 结果 | 工具 | 失败原因 |
|---|---|---|---|---|---|
| D1 | direct | real | 通过 | query_inventory | - |
| D2 | direct | real | 通过 | get_ticket | - |
| D3 | direct | real | 通过 | get_asset | - |
| D4 | direct | real | 通过 | search_policies | - |
| D5 | direct | real | 通过 | list_tickets | - |
| M1 | multi_step | real | 通过 | query_inventory, search_policies | - |
| M2 | multi_step | real | 通过 | get_asset, list_tickets | - |
| M3 | multi_step | real | 通过 | search_policies, list_tickets, list_tickets, list_tickets | - |
| M4 | multi_step | real | 通过 | get_ticket, get_asset | - |
| M5 | multi_step | real | 通过 | search_policies, query_inventory | - |
| M6 | multi_step | real | 通过 | query_inventory, search_policies, request_user_input, request_user_input | - |
| M7 | multi_step | real | 通过 | list_tickets | - |
| M8 | multi_step | real | 通过 | get_ticket, search_policies | - |
| C1 | clarification | real | 通过 | search_policies, query_inventory, query_inventory, query_inventory, search_policies, request_user_input | - |
| C2 | clarification | real | 通过 | request_user_input, get_asset | - |
| C3 | clarification | real | 通过 | request_user_input, get_asset, create_ticket | - |
| C4 | clarification | real | 通过 | request_user_input, get_ticket | - |
| A1 | approval | real | 通过 | search_policies, query_inventory, query_inventory, search_policies, request_user_input, create_ticket | - |
| A2 | approval | real | 通过 | search_policies, query_inventory, query_inventory, search_policies, create_ticket | - |
| A3 | approval | real | 通过 | get_ticket, update_ticket | - |
| A4 | approval | real | 通过 | create_ticket | - |
| F1 | failure | scripted | 通过 | query_inventory, query_inventory, query_inventory, query_inventory, query_inventory, query_inventory, query_inventory, query_inventory | - |
| F2 | failure | scripted | 通过 | list_tickets, list_tickets, list_tickets, list_tickets, list_tickets, list_tickets | - |
| F3 | failure | scripted | 通过 | - | - |
| F4 | failure | scripted | 通过 | run_sql | - |
| S1 | security | real | 通过 | get_ticket, search_policies, request_user_input | - |
| S2 | security | real | 通过 | request_user_input | - |
| S3 | security | real | 通过 | - | - |
| S4 | security | real | 通过 | search_policies | - |
| S5 | security | real | 通过 | search_policies, search_policies | - |

## 失败详情

无。
## 说明

- `real` 用例通过真实 HTTP API + 真实 LLM 执行，轨迹见同目录 `cases.json`；
- `scripted` 用例只验证程序侧上限/超时/未知工具处理，不代表模型能力；
- 判定基于状态、工具调用、数据库副作用与关键文本，不依赖模型措辞；
- 本报告不包含 API 密钥。