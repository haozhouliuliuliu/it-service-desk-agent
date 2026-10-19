"""工具注册表测试：白名单、参数校验、权限与写操作确认。"""

from __future__ import annotations

import pytest

from it_agent.context import ToolContext
from it_agent.tools import (
    ApprovalToken,
    ToolErrorCode,
    ToolPermission,
    ToolRegistry,
)


def _create_args(**overrides):
    payload = {
        "title": "新员工设备申请",
        "description": "下周一入职的研发同事，需要笔记本与显示器。",
        "category": "hardware",
        "priority": "P3",
        "requester": "EMP-1005",
    }
    payload.update(overrides)
    return payload


def test_registry_has_eight_tools(registry: ToolRegistry) -> None:
    assert len(registry.names()) == 8


def test_registry_permissions(registry: ToolRegistry) -> None:
    catalog = {item["name"]: item for item in registry.catalog()}
    assert catalog["create_ticket"]["permission"] == ToolPermission.WRITE.value
    assert catalog["update_ticket"]["permission"] == ToolPermission.WRITE.value
    assert catalog["search_policies"]["permission"] == ToolPermission.READ.value
    assert catalog["request_user_input"]["permission"] == ToolPermission.CONTROL.value
    assert catalog["create_ticket"]["requires_approval"] is True
    assert catalog["query_inventory"]["requires_approval"] is False


def test_schema_is_llm_ready(registry: ToolRegistry) -> None:
    for schema in registry.schemas():
        function = schema["function"]
        assert schema["type"] == "function"
        assert function["name"]
        assert function["description"]
        assert function["parameters"]["type"] == "object"
        assert "$defs" not in function["parameters"]


def test_create_ticket_schema_has_required(registry: ToolRegistry) -> None:
    schema = next(item for item in registry.schemas() if item["function"]["name"] == "create_ticket")
    required = schema["function"]["parameters"]["required"]
    assert set(required) == {"title", "description", "category", "priority", "requester"}


def test_unknown_tool(registry: ToolRegistry, context: ToolContext) -> None:
    result = registry.invoke("run_sql", {"sql": "DROP TABLE tickets"}, context)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TOOL_NOT_FOUND


def test_invalid_arguments(registry: ToolRegistry, context: ToolContext) -> None:
    result = registry.invoke("search_policies", {"query": ""}, context)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert result.error.details


def test_extra_arguments_are_rejected(registry: ToolRegistry, context: ToolContext) -> None:
    result = registry.invoke("search_policies", {"query": "电脑", "sql": "select 1"}, context)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS


def test_invalid_ticket_id(registry: ToolRegistry, context: ToolContext) -> None:
    result = registry.invoke("get_ticket", {"ticket_id": "INC-1"}, context)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS


def test_write_requires_approval(registry: ToolRegistry, context: ToolContext) -> None:
    before = len(context.store.list_tickets(limit=50))
    result = registry.invoke("create_ticket", _create_args(), context)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.PERMISSION_DENIED
    assert len(context.store.list_tickets(limit=50)) == before


def test_approved_write_executes(registry: ToolRegistry, context: ToolContext) -> None:
    args = _create_args()
    token = registry.build_approval("ACT-1", "create_ticket", args)
    result = registry.invoke("create_ticket", args, context, approval=token)
    assert result.ok is True
    assert result.data["created"] is True
    ticket_id = result.data["ticket"]["ticket_id"]
    assert context.store.get_ticket(ticket_id)["status"] == "open"


def test_approval_is_bound_to_arguments(registry: ToolRegistry, context: ToolContext) -> None:
    token = registry.build_approval("ACT-1", "create_ticket", _create_args())
    changed = _create_args(title="被篡改的工单标题")
    result = registry.invoke("create_ticket", changed, context, approval=token)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.PERMISSION_DENIED


def test_approval_is_bound_to_tool(registry: ToolRegistry, context: ToolContext) -> None:
    token = registry.build_approval("ACT-1", "create_ticket", _create_args())
    update_args = {"ticket_id": "INC-2026-0001", "status": "closed"}
    result = registry.invoke("update_ticket", update_args, context, approval=token)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.PERMISSION_DENIED


def test_approved_update_executes(registry: ToolRegistry, context: ToolContext) -> None:
    args = {"ticket_id": "INC-2026-0001", "status": "in_progress", "comment": "已安排处理"}
    token = registry.build_approval("ACT-2", "update_ticket", args)
    result = registry.invoke("update_ticket", args, context, approval=token)
    assert result.ok is True
    assert result.data["ticket"]["status"] == "in_progress"


def test_read_tool_executes_without_approval(registry: ToolRegistry, context: ToolContext) -> None:
    result = registry.invoke("query_inventory", {"category": "笔记本电脑"}, context)
    assert result.ok is True
    assert result.data["total_available"] == 4


def test_control_tool(registry: ToolRegistry, context: ToolContext) -> None:
    result = registry.invoke(
        "request_user_input",
        {"question": "请问新员工使用什么岗位的电脑？", "options": ["研发", "职能"]},
        context,
    )
    assert result.ok is True
    assert result.data["waiting"] is True


def test_tool_not_found_error_shape(registry: ToolRegistry, context: ToolContext) -> None:
    result = registry.invoke("delete_everything", {}, context)
    assert result.ok is False
    payload = result.model_dump(mode="json")
    assert payload["error"]["code"] == "TOOL_NOT_FOUND"
    assert payload["data"] is None


def test_result_is_json_serializable(registry: ToolRegistry, context: ToolContext) -> None:
    result = registry.invoke("get_ticket", {"ticket_id": "INC-2026-0001"}, context)
    assert result.model_dump_json()


def test_catalog_exposes_parameter_schema(registry: ToolRegistry) -> None:
    catalog = {item["name"]: item for item in registry.catalog()}
    properties = catalog["create_ticket"]["parameters"]["properties"]
    assert {"title", "description", "category", "priority", "requester"} <= set(properties)


@pytest.mark.parametrize("name", ["search_policies", "query_inventory", "get_asset", "get_ticket", "list_tickets"])
def test_read_tools_are_callable(registry: ToolRegistry, context: ToolContext, name: str) -> None:
    args = {
        "search_policies": {"query": "电脑"},
        "query_inventory": {},
        "get_asset": {"asset_id": "ASSET-1001"},
        "get_ticket": {"ticket_id": "INC-2026-0001"},
        "list_tickets": {"limit": 3},
    }[name]
    result = registry.invoke(name, args, context)
    assert result.ok is True


def test_approval_token_shape(registry: ToolRegistry) -> None:
    token = registry.build_approval("ACT-1", "create_ticket", _create_args())
    assert isinstance(token, ApprovalToken)
    assert token.action_id == "ACT-1"
    assert len(token.args_hash) == 64


def test_context_does_not_expose_paths(registry: ToolRegistry, context: ToolContext) -> None:
    result = registry.invoke("search_policies", {"query": "/etc/passwd"}, context)
    assert result.ok is True
    assert result.data["count"] == 0
