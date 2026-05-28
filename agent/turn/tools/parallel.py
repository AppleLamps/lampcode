"""Parallel read-only tool round execution."""
from __future__ import annotations

from typing import Any

from agent.cancel import CancelToken
from agent.config import Config
from agent.events import EventEmitter
from agent.mcp.manager import McpManager
from agent.models import Thread, Turn
from agent.session import HarnessSession
from agent.store import ThreadStore
from agent.tool_round import run_parallel_tool_dispatches
from agent.turn.helpers import approval_diff_preview_for_tool
from agent.turn.hooks_bridge import prompt_with_hooks
from agent.turn.tools.dispatch import (
    apply_dispatch_results,
    emit_execution_events,
    exec_policy_block,
    handle_blocked_tool,
    precheck_tool,
)
from agent.turn.tools.tracking import (
    create_tracking_items,
    mark_approved,
    mark_denied,
    tool_completed_extra,
)
from approval.gate import TurnApprovalState, format_tool_summary, needs_approval_prompt
import agent.loop as loop_shim
from tools.registry import parse_tool_arguments, tool_requires_approval

def run_parallel_read_tool_round(
    tool_calls_list: list,
    *,
    thread: Thread,
    turn: Turn,
    config: Config,
    store: ThreadStore,
    mcp_manager: McpManager,
    messages: list,
    emitter: EventEmitter,
    cancel: CancelToken,
    allowed_tools: list[str] | None,
    allow_mcp_servers: list[str] | None = None,
    turn_state: TurnApprovalState,
    session: HarnessSession,
    hooks_runner,
    max_workers: int,
) -> None:
    prepared: list[dict] = []

    for tc in tool_calls_list:
        cancel.check()
        tool_name = tc["function"]["name"]
        tool_call_id = tc["id"]
        raw_args = tc["function"].get("arguments", "")
        arguments = parse_tool_arguments(raw_args)
        source = "mcp" if mcp_manager.is_mcp_tool(tool_name) else "builtin"

        from agent.tool_access import is_tool_allowed

        if not is_tool_allowed(
            tool_name, allowed_tools, allow_mcp_servers=allow_mcp_servers
        ):
            emitter.tool_pending(thread.id, turn.id, tool_name, arguments, source=source)
            emitter.tool_completed(thread.id, turn.id, tool_name, "blocked", source=source)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": f"Tool {tool_name} is not available in this mode.",
                }
            )
            continue

        emitter.tool_pending(thread.id, turn.id, tool_name, arguments, source=source)
        if hooks_runner:
            hooks_runner.run(
                "on_tool_pending",
                {"tool_name": tool_name, "arguments": arguments},
                tool_name=tool_name,
                thread_id=thread.id,
                turn_id=turn.id,
            )

        tracking_items = create_tracking_items(
            tool_name, arguments, config, tool_call_id, raw_args, mcp_manager, thread.id
        )
        for item in tracking_items:
            turn.items.append(item)
            store.append_item(thread, turn.id, item)
            emitter.item_started(thread.id, turn.id, item.type, item.id)

        if hooks_runner:
            pre_hook = hooks_runner.run_event(
                "on_pre_tool_use",
                {"tool_name": tool_name, "arguments": arguments},
                tool_name=tool_name,
                thread_id=thread.id,
                turn_id=turn.id,
            )
            if pre_hook.block:
                handle_blocked_tool(
                    pre_hook.block_reason or "Blocked by on_pre_tool_use hook",
                    tracking_items,
                    store,
                    thread,
                    turn,
                    emitter,
                    tool_call_id,
                    messages,
                    config,
                )
                continue

        policy_block = exec_policy_block(tool_name, arguments, config)
        if policy_block:
            handle_blocked_tool(
                policy_block,
                tracking_items,
                store,
                thread,
                turn,
                emitter,
                tool_call_id,
                messages,
                config,
            )
            continue

        requires_approval = tool_requires_approval(tool_name, mcp_manager, config)
        if requires_approval and needs_approval_prompt(
            tool_name,
            arguments,
            config,
            turn_state=turn_state,
            session=session,
        ):
            summary = format_tool_summary(tool_name, arguments)
            diff_preview = approval_diff_preview_for_tool(
                tool_name, arguments, config
            )
            emitter.approval_requested(
                thread.id,
                turn.id,
                tool_name,
                summary,
                diff_preview=diff_preview,
            )
            approved = prompt_with_hooks(
                hooks_runner,
                thread,
                turn,
                tool_name,
                arguments,
                summary,
                config,
                turn_state,
                session,
            )
            if not approved:
                mark_denied(tracking_items, store, thread, turn.id)
                for item in tracking_items:
                    emitter.item_completed(thread.id, turn.id, item.type, item.id, "denied")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": "User denied this action.",
                    }
                )
                continue
            mark_approved(tracking_items)
        elif requires_approval:
            mark_approved(tracking_items)
            session.approval_cache.record(tool_name, arguments)

        block_reason, retryable = precheck_tool(
            tool_name,
            arguments,
            config,
            mcp_manager,
            session=session,
        )
        if block_reason and retryable:
            apply_sandbox_escalation_for_reason(session, block_reason)
            block_reason, _ = precheck_tool(
                tool_name,
                arguments,
                config,
                mcp_manager,
                session=session,
            )
        if block_reason:
            handle_blocked_tool(
                block_reason,
                tracking_items,
                store,
                thread,
                turn,
                emitter,
                tool_call_id,
                messages,
                config,
            )
            continue

        prepared.append(
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "tracking_items": tracking_items,
                "source": source,
            }
        )

    if not prepared:
        return

    def _dispatch_entry(entry: dict) -> str:
        emitter.tool_executing(
            thread.id,
            turn.id,
            entry["tool_name"],
            entry["arguments"],
            source=entry["source"],
        )
        dispatch_result = loop_shim.dispatch_tool(
            entry["tool_name"],
            entry["arguments"],
            config,
            mcp_manager=mcp_manager,
            thread_id=thread.id,
        )
        emit_execution_events(dispatch_result, thread.id, turn.id, config, emitter)
        return apply_dispatch_results(
            dispatch_result,
            entry["tracking_items"],
            store,
            thread,
            turn.id,
            emitter,
            config=config,
        )

    jobs = [(index, lambda entry=entry: _dispatch_entry(entry)) for index, entry in enumerate(prepared)]
    results = run_parallel_tool_dispatches(jobs, max_workers=max_workers)

    for (index, result_text), entry in zip(results, prepared, strict=True):
        tool_name = entry["tool_name"]
        tracking_items = entry["tracking_items"]
        emitter.tool_completed(
            thread.id,
            turn.id,
            tool_name,
            tracking_items[0].status if tracking_items else "completed",
            source=entry["source"],
            **tool_completed_extra(tool_name, tracking_items, result_text),
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": entry["tool_call_id"],
                "content": result_text,
            }
        )
