# 工具手册（M1）

Agent 只能通过本文件列出的工具接触业务数据。工具注册表是**唯一入口**：模型提出调用，程序校验并执行，模型拿到结构化结果。

## 1. 权限模型

| 权限 | 含义 | 是否需要人工确认 |
|---|---|---|
| `read` | 只读查询 | 否 |
| `write` | 创建/修改数据 | **是**（无确认凭证一律拒绝） |
| `control` | 控制流（向用户提问） | 否 |

写工具的执行条件：

1. 参数通过 Pydantic 校验；
2. 调用方持有 `ApprovalToken`；
3. token 的 `tool` 与本次调用一致；
4. token 的 `args_hash` 与本次规范化参数一致。

`args_hash = sha256(canonical_json({"tool": name, "args": normalized_args}))`。
因此"确认时展示的内容"和"实际执行的内容"必须完全一致，改一个字段都会被拒绝。

## 2. 工具清单

### search_policies（只读）

检索虚构制度文档。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `query` | string(1..200) | 是 | 关键词 |
| `limit` | int(1..5) | 否 | 默认 3 |

返回：

```json
{
  "query": "电脑领用",
  "count": 2,
  "results": [
    {"doc_id": "POL-IT-001", "title": "电脑领用制度", "section": "领用条件",
     "citation": "电脑领用制度 · 领用条件", "snippet": "…", "score": 7.21, "source": "电脑领用制度.md"}
  ]
}
```

无结果时额外返回 `message`，明确提示"不要编造规定"。

### query_inventory（只读）

按类别/型号汇总库存。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `category` | enum：笔记本电脑/显示器/扩展坞/手机 | 否 | 不传查全部 |
| `only_available` | bool | 否 | 默认 true，只返回有可用库存的型号 |

返回每行：`category/model/total/available/in_use/repair/retired`，外加 `total_available`。

### get_asset（只读）

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `asset_id` | string | 二选一 | 设备编号 |
| `assignee` | string | 二选一 | 领用人姓名或员工编号 |

返回 `assets` 数组（按编号查询时只有一条）。

### get_ticket（只读）

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ticket_id` | `INC-YYYY-NNNN` | 是 | 工单编号 |

### list_tickets（只读）

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | enum：open/in_progress/pending/resolved/closed | 否 | 状态过滤 |
| `assignee` | string | 否 | 处理人姓名或工号 |
| `limit` | int(1..20) | 否 | 默认 10 |

### create_ticket（写入，需确认）

| 参数 | 类型 | 必填 |
|---|---|---|
| `title` | string(4..80) | 是 |
| `description` | string(5..500) | 是 |
| `category` | enum：hardware/software/network/account/other | 是 |
| `priority` | enum：P1/P2/P3/P4 | 是 |
| `requester` | string | 是 |

未确认时返回 `PERMISSION_DENIED`，**不会写库**。

### update_ticket（写入，需确认）

| 参数 | 类型 | 必填 |
|---|---|---|
| `ticket_id` | `INC-YYYY-NNNN` | 是 |
| `status` | enum | 四选一 |
| `priority` | enum | 四选一 |
| `assignee` | string | 四选一 |
| `comment` | string(≤300) | 四选一 |

`status/priority/assignee/comment` 至少提供一个。

### request_user_input（控制）

| 参数 | 类型 | 必填 |
|---|---|---|
| `question` | string(3..200) | 是 |
| `options` | string[]（≤4） | 否 |

返回 `{"waiting": true, ...}`；Runner 据此把任务置为 `waiting_for_input`（M2 实现）。

## 3. 错误码

| code | 场景 | 模型应如何处理 |
|---|---|---|
| `TOOL_NOT_FOUND` | 调用了未注册工具 | 改用已注册工具 |
| `INVALID_ARGUMENTS` | 缺字段、类型错、多传字段、格式不符 | 修正参数后重试；不要猜业务数据 |
| `PERMISSION_DENIED` | 写工具没有确认凭证，或凭证与参数不符 | 先向用户展示操作并申请确认 |
| `NOT_FOUND` | 设备/工单/员工不存在 | 向用户确认编号 |
| `CONFLICT` | 姓名对应多个工号等 | 要求用户提供工号 |
| `TOOL_ERROR` | 业务规则拒绝 | 根据错误信息改道 |
| `INTERNAL` | 未预期异常 | 停止并向用户说明，可转人工 |

所有错误都以 `{"ok": false, "error": {"code", "message", "details"}}` 返回，不会把异常堆栈暴露给模型。

## 4. 安全边界

- 没有 `run_sql`、`read_file`、`http_request`、`shell` 之类工具；
- 参数模型全部 `extra="forbid"`，多传字段会被拒绝；
- 工具返回的数据是数据，不是指令：制度片段或工单描述中的"忽略规则"不会改变程序权限；
- 写操作只能由审批服务在人工确认后签发 token 才会执行；
- 员工/工单/资产不存在时返回明确错误，不自动创建或猜测。

## 5. 扩展一个工具

1. 在 `tools/schemas.py` 增加参数模型（继承 `ToolArgs`，写清 `Field(description=...)`）；
2. 在 `tools/builtin.py` 写 handler（只调用 `DeskStore`/`PolicyIndex`，不直接开连接）；
3. 在 `build_registry()` 注册：名称、描述、权限、参数模型；
4. 在 `tests/test_tools.py` 增加参数校验与权限测试。

JSON Schema 由 Pydantic 生成并直接传给模型，避免"文档与实现不一致"。
