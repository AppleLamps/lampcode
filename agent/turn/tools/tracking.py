"""Tool call tracking items for thread store."""
from __future__ import annotations

from typing import Any

from agent.config import Config
from agent.mcp.manager import McpManager
from agent.models import (
    CollabWorkerItem,
    CommandExecutionItem,
    FileChangeItem,
    McpToolCallItem,
    WebSearchItem,
)
from agent.turn.types import TrackingItem

def create_tracking_items(
    tool_name: str,
    arguments: dict,
    config: Config,
    tool_call_id: str,
    raw_args: str,
    mcp_manager: McpManager,
    parent_thread_id: str = "",
) -> list[TrackingItem]:
    if mcp_manager.is_mcp_tool(tool_name):
        ref = mcp_manager.tool_map.get(tool_name)
        return [
            McpToolCallItem(
                server=ref.server if ref else "unknown",
                tool=ref.tool if ref else tool_name,
                arguments=arguments,
                status="pending",
                tool_call_id=tool_call_id,
            )
        ]
    if tool_name == "run_command":
        return [
            CommandExecutionItem(
                command=arguments.get("cmd", ""),
                cwd=str(config.cwd),
                status="pending",
                tool_call_id=tool_call_id,
                tool_arguments=raw_args,
            )
        ]
    if tool_name == "write_file":
        return [
            FileChangeItem(
                path=arguments.get("path", ""),
                status="pending",
                tool_call_id=tool_call_id,
                tool_arguments=raw_args,
                content=arguments.get("content"),
                change_type="overwrite",
            )
        ]
    if tool_name == "apply_patch":
        return [
            FileChangeItem(
                path="(patch)",
                status="pending",
                tool_call_id=tool_call_id,
                tool_arguments=raw_args,
                change_type="update",
            )
        ]
    if tool_name == "web_search":
        return [
            WebSearchItem(
                query=arguments.get("query", ""),
                status="pending",
                tool_call_id=tool_call_id,
                tool_arguments=raw_args,
            )
        ]
    if tool_name == "spawn_worker":
        worker_id = arguments.get("worker_id") or ""
        return [
            CollabWorkerItem(
                worker_id=worker_id or "pending",
                worker_thread_id="",
                parent_thread_id=parent_thread_id,
                task=arguments.get("task", ""),
                status="queued",
                title=arguments.get("title"),
                model=arguments.get("model"),
                execution_backend=arguments.get("execution_backend"),
                tool_call_id=tool_call_id,
                tool_arguments=raw_args,
            )
        ]
    return []


def tool_completed_extra(
    tool_name: str,
    tracking_items: list[TrackingItem],
    result_text: str,
    *,
    max_output: int = 2000,
) -> dict[str, Any]:
    """Build optional output/exit_code/duration fields for tool_completed events."""
    extra: dict[str, Any] = {}
    if tool_name == "apply_patch":
        return extra
    if tracking_items:
        item = tracking_items[0]
        if isinstance(item, CommandExecutionItem):
            extra["output"] = (item.output or result_text or "")[:max_output] or None
            extra["exit_code"] = item.exit_code
            extra["duration_ms"] = item.duration_ms
            return extra
        if isinstance(item, McpToolCallItem):
            extra["output"] = (item.output or item.error or result_text or "")[:max_output] or None
            extra["duration_ms"] = item.duration_ms
            return extra
        if isinstance(item, WebSearchItem):
            extra["output"] = result_text[:max_output] if result_text else None
            return extra
    if result_text and tool_name != "apply_patch":
        extra["output"] = result_text[:max_output]
    return extra


def mark_denied(items: list[TrackingItem], store, thread, turn_id) -> None:
    for item in items:
        item.status = "denied"
        if isinstance(item, CommandExecutionItem):
            item.output = "User denied this action."
        elif isinstance(item, McpToolCallItem):
            item.output = "User denied this action."
        elif isinstance(item, WebSearchItem):
            item.error = "User denied this action."
        else:
            item.summary = "User denied this action."
        store.append_item(thread, turn_id, item)


def mark_approved(items: list[TrackingItem]) -> None:
    for item in items:
        if isinstance(item, CollabWorkerItem):
            continue
        item.status = "approved"
