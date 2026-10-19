"""内置工具实现：7 个业务工具 + 1 个控制工具。

写工具这里只是"执行函数"，注册表在没有人工确认凭证时不会调用它们。
"""

from __future__ import annotations

from typing import Any

from ..context import ToolContext
from .registry import ToolPermission, ToolRegistry, ToolSpec
from .schemas import (
    CreateTicketArgs,
    GetAssetArgs,
    GetTicketArgs,
    ListTicketsArgs,
    QueryInventoryArgs,
    RequestUserInputArgs,
    SearchPoliciesArgs,
    UpdateTicketArgs,
)


def _search_policies(context: ToolContext, args: SearchPoliciesArgs) -> dict[str, Any]:
    results = context.policies.search(args.query, args.limit)
    payload: dict[str, Any] = {"query": args.query, "count": len(results), "results": results}
    if not results:
        payload["message"] = "制度库中没有匹配的条款；不要编造规定，可以换个关键词或转人工。"
    return payload


def _query_inventory(context: ToolContext, args: QueryInventoryArgs) -> dict[str, Any]:
    items = context.store.inventory(category=args.category, only_available=args.only_available)
    return {
        "items": items,
        "count": len(items),
        "total_available": sum(item["available"] for item in items),
        "message": "库存为 0 的型号不在此结果中" if args.only_available else "包含全部状态",
    }


def _get_asset(context: ToolContext, args: GetAssetArgs) -> dict[str, Any]:
    data = context.store.get_asset(asset_id=args.asset_id, assignee=args.assignee)
    assets = data if isinstance(data, list) else [data]
    return {"assets": assets, "count": len(assets)}


def _get_ticket(context: ToolContext, args: GetTicketArgs) -> dict[str, Any]:
    return {"ticket": context.store.get_ticket(args.ticket_id)}


def _list_tickets(context: ToolContext, args: ListTicketsArgs) -> dict[str, Any]:
    tickets = context.store.list_tickets(status=args.status, assignee=args.assignee, limit=args.limit)
    return {"tickets": tickets, "count": len(tickets)}


def _create_ticket(context: ToolContext, args: CreateTicketArgs) -> dict[str, Any]:
    ticket = context.store.create_ticket(
        title=args.title,
        description=args.description,
        category=args.category,
        priority=args.priority,
        requester_id=args.requester,
    )
    return {"ticket": ticket, "created": True}


def _update_ticket(context: ToolContext, args: UpdateTicketArgs) -> dict[str, Any]:
    ticket = context.store.update_ticket(
        ticket_id=args.ticket_id,
        status=args.status,
        priority=args.priority,
        assignee=args.assignee,
        comment=args.comment,
    )
    return {"ticket": ticket, "updated": True}


def _request_user_input(context: ToolContext, args: RequestUserInputArgs) -> dict[str, Any]:
    return {"question": args.question, "options": args.options, "waiting": True}


def build_registry() -> ToolRegistry:
    specs = [
        ToolSpec(
            name="search_policies",
            description=(
                "检索企业 IT 制度文档（电脑领用、设备申请、故障处理、账号权限、信息安全）。"
                "回答制度问题时必须先调用它，并引用返回的文档标题与章节。"
            ),
            permission=ToolPermission.READ,
            arg_model=SearchPoliciesArgs,
            handler=_search_policies,
        ),
        ToolSpec(
            name="query_inventory",
            description="查询各设备类别/型号的库存数量与可用状态。需要判断是否有货、是否缺货时使用。",
            permission=ToolPermission.READ,
            arg_model=QueryInventoryArgs,
            handler=_query_inventory,
        ),
        ToolSpec(
            name="get_asset",
            description="按设备编号查询单台设备，或按领用人查询其名下设备。需要核对设备归属与状态时使用。",
            permission=ToolPermission.READ,
            arg_model=GetAssetArgs,
            handler=_get_asset,
        ),
        ToolSpec(
            name="get_ticket",
            description="查询单个工单的详情、状态、处理人与评论。",
            permission=ToolPermission.READ,
            arg_model=GetTicketArgs,
            handler=_get_ticket,
        ),
        ToolSpec(
            name="list_tickets",
            description="按状态或处理人列出工单，用于了解待办与历史记录。",
            permission=ToolPermission.READ,
            arg_model=ListTicketsArgs,
            handler=_list_tickets,
        ),
        ToolSpec(
            name="create_ticket",
            description="创建 IT 服务工单。属于写操作，必须先向用户展示工单内容并获得人工确认，程序会挂起等待确认。",
            permission=ToolPermission.WRITE,
            arg_model=CreateTicketArgs,
            handler=_create_ticket,
        ),
        ToolSpec(
            name="update_ticket",
            description="修改工单状态、优先级、处理人或追加备注。属于写操作，必须经人工确认后才会执行。",
            permission=ToolPermission.WRITE,
            arg_model=UpdateTicketArgs,
            handler=_update_ticket,
        ),
        ToolSpec(
            name="request_user_input",
            description="当缺少必要信息（如员工编号、设备类别、故障现象）时，向用户提出一个具体问题并暂停任务。",
            permission=ToolPermission.CONTROL,
            arg_model=RequestUserInputArgs,
            handler=_request_user_input,
        ),
    ]
    return ToolRegistry(specs)
