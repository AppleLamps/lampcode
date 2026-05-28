"""Tool dispatch result application and sandbox prechecks."""
from __future__ import annotations

from typing import Any

from agent.config import Config
from agent.events import EventEmitter
from agent.mcp.manager import McpManager
from agent.multi_agent.registry import WorkerRegistry
from agent.models import (
    CollabWorkerItem,
    CommandExecutionItem,
    FileChangeItem,
    McpToolCallItem,
    Thread,
    Turn,
    WebSearchItem,
)
from agent.sandbox.enforcer import (
    check_apply_patch,
    check_mcp_tool,
    check_run_command,
    check_write_file,
)
from agent.sandbox.retry import apply_sandbox_escalation_for_reason
from agent.session import HarnessSession
from agent.store import ThreadStore
from agent.turn.context_guard import spill_item_output
from agent.turn.types import TrackingItem
from approval.gate import exec_policy_block_reason
from tools.registry import DispatchResult

def apply_dispatch_results(
    result: DispatchResult,
    tracking_items: list[TrackingItem],
    store: ThreadStore,
    thread: Thread,
    turn_id: str,
    emitter: EventEmitter,
    *,
    config: Config | None = None,
) -> str:
    if result.mcp_item and tracking_items:
        item = tracking_items[0]
        if isinstance(item, McpToolCallItem):
            item.status = result.mcp_item.status
            raw_out = result.mcp_item.output
            if config and raw_out:
                inline, path, chars = spill_item_output(
                    config, thread_id=thread.id, item_id=item.id, text=raw_out
                )
                item.output = inline
                item.artifact_path = path
                item.output_chars = chars
            else:
                item.output = raw_out
            item.error = result.mcp_item.error
            store.append_item(thread, turn_id, item)
            emitter.item_completed(thread.id, turn_id, item.type, item.id, item.status)
        return result.text

    if result.command_item and tracking_items:
        item = tracking_items[0]
        if isinstance(item, CommandExecutionItem):
            item.status = result.command_item.status
            raw_out = result.command_item.output
            if config and raw_out:
                inline, path, chars = spill_item_output(
                    config, thread_id=thread.id, item_id=item.id, text=raw_out
                )
                item.output = inline
                item.artifact_path = path
                item.output_chars = chars
            else:
                item.output = raw_out
            item.exit_code = result.command_item.exit_code
            item.duration_ms = result.command_item.duration_ms
            store.append_item(thread, turn_id, item)
            emitter.item_completed(thread.id, turn_id, item.type, item.id, item.status)
        return result.text

    if result.web_search_item and tracking_items:
        item = tracking_items[0]
        if isinstance(item, WebSearchItem):
            item.status = result.web_search_item.status
            item.results = result.web_search_item.results
            item.error = result.web_search_item.error
            store.append_item(thread, turn_id, item)
            emitter.item_completed(thread.id, turn_id, item.type, item.id, item.status)
        return result.text

    if result.file_items:
        primary = tracking_items[0] if tracking_items else None
        if isinstance(primary, FileChangeItem) and result.file_items:
            first = result.file_items[0]
            primary.path = first.path if len(result.file_items) == 1 else "(patch)"
            primary.status = first.status
            primary.summary = result.text
            primary.change_type = first.change_type
            primary.diff_snippet = first.diff_snippet
            store.append_item(thread, turn_id, primary)
            emitter.item_completed(
                thread.id, turn_id, primary.type, primary.id, primary.status
            )
            for extra in result.file_items[1:]:
                turn = thread.turns[-1]
                turn.items.append(extra)
                store.append_item(thread, turn_id, extra)
                emitter.item_completed(
                    thread.id, turn_id, extra.type, extra.id, extra.status
                )
        return result.text

    return result.text
def exec_policy_block(tool_name: str, arguments: dict, config: Config) -> str | None:
    if tool_name == "run_command":
        cmd = arguments.get("cmd", "")
        return exec_policy_block_reason(cmd, config)
    return None


def precheck_tool(
    tool_name: str,
    arguments: dict,
    config: Config,
    mcp_manager: McpManager,
    *,
    session: HarnessSession | None = None,
    read_only_review: bool = False,
) -> tuple[str | None, bool]:
    if read_only_review and tool_name in ("apply_patch", "write_file", "git_commit"):
        return f"{tool_name} blocked in read-only review mode (pass --fix to allow writes)", False
    if tool_name == "request_permissions" and read_only_review:
        return "permission escalation denied in read-only review mode", False
    if tool_name == "run_command":
        cmd = arguments.get("cmd", "")
        decision = check_run_command(cmd, config.cwd, config.sandbox_mode, session=session)
        if decision.blocked:
            return decision.reason, decision.retryable
    elif tool_name == "write_file":
        decision = check_write_file(
            arguments.get("path", ""),
            config.cwd,
            config.sandbox_mode,
            memories=config.memories,
        )
        if decision.blocked:
            return decision.reason, decision.retryable
    elif tool_name == "apply_patch":
        decision = check_apply_patch(config.cwd, config.sandbox_mode)
        if decision.blocked:
            return decision.reason, decision.retryable
    elif tool_name == "web_search":
        if config.sandbox_mode.value == "read-only":
            return "web search denied in read-only sandbox", False
    elif tool_name in ("spawn_worker", "spawn_worker_batch"):
        if not config.multi_agent.enabled:
            return f"{tool_name} requires multi_agent.enabled or --multi-agent", False
    elif tool_name in ("wait_workers", "list_workers", "get_worker_graph"):
        if not config.multi_agent.enabled:
            return f"{tool_name} requires multi_agent.enabled or --multi-agent", False
    elif mcp_manager.is_mcp_tool(tool_name):
        ref = mcp_manager.tool_map.get(tool_name)
        decision = check_mcp_tool(
            tool_name,
            config.sandbox_mode,
            require_approval=ref.require_approval if ref else True,
        )
        if decision.blocked:
            return decision.reason, decision.retryable
    return None, False


def handle_blocked_tool(
    reason: str,
    tracking_items: list[TrackingItem],
    store: ThreadStore,
    thread: Thread,
    turn: Turn,
    emitter: EventEmitter,
    tool_call_id: str,
    messages: list,
    config: Config,
) -> None:
    cmd = None
    for item in tracking_items:
        item.status = "denied"
        if isinstance(item, CommandExecutionItem):
            item.output = reason
            cmd = item.command
        elif isinstance(item, McpToolCallItem):
            item.output = reason
        elif isinstance(item, WebSearchItem):
            item.error = reason
        else:
            item.summary = reason
        store.append_item(thread, turn.id, item)
        emitter.item_completed(thread.id, turn.id, item.type, item.id, "denied")
    emitter.sandbox_blocked(
        thread.id,
        turn.id,
        mode=config.sandbox_mode.value,
        reason=reason,
        command=cmd,
    )
    messages.append(
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": f"Blocked by sandbox/policy: {reason}",
        }
    )


def emit_execution_events(
    result: DispatchResult,
    thread_id: str,
    turn_id: str,
    config: Config,
    emitter: EventEmitter,
) -> None:
    meta = result.execution_meta or result.isolation_meta
    if not meta:
        return
    if meta.get("isolated"):
        emitter.isolation_applied(
            thread_id,
            turn_id,
            pid=meta.get("pid"),
            cwd=meta.get("cwd", str(config.cwd)),
            stripped_env_count=meta.get("stripped_env_count", 0),
        )
    if meta.get("backend") == "docker":
        image = meta.get("image") or config.execution.default_image
        emitter.execution_docker_started(
            thread_id,
            turn_id,
            image=image,
            container_id=meta.get("container_id"),
        )
        if result.command_item:
            emitter.execution_docker_completed(
                thread_id,
                turn_id,
                image=image,
                exit_code=result.command_item.exit_code or -1,
            )
    if meta.get("backend") == "ssh":
        host = meta.get("remote_host") or config.execution.ssh.host
        emitter.execution_ssh_completed(
            thread_id,
            turn_id,
            host=host,
            exit_code=result.command_item.exit_code if result.command_item else meta.get("exit_code", -1),
            duration_ms=result.command_item.duration_ms if result.command_item else 0,
        )
    if result.file_tool_meta and result.file_tool_meta.get("via_mount"):
        path = ""
        if result.file_items:
            path = result.file_items[0].path
        emitter.execution_docker_file_tool_applied(
            thread_id,
            turn_id,
            tool_name=result.file_tool_meta.get("tool_name", "file"),
            path=path,
            image=result.file_tool_meta.get("image"),
        )


def sync_worker_items(
    thread: Thread,
    turn: Turn,
    registry: WorkerRegistry,
    store: ThreadStore,
) -> None:
    for item in turn.items:
        if not isinstance(item, CollabWorkerItem):
            continue
        record = registry._workers.get(item.worker_id)
        if not record:
            continue
        item.worker_thread_id = record.worker_thread_id
        item.status = record.status  # type: ignore[assignment]
        item.summary = record.summary
        store.append_item(thread, turn.id, item)
