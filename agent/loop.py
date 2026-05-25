from __future__ import annotations

from typing import Callable

from agent.cancel import CancelToken, CancelledError
from agent.compaction import compact_thread_if_needed
from agent.config import Config
from agent.context import build_thread_messages
from agent.events import EventEmitter
from agent.models import (
    AgentMessageItem,
    CommandExecutionItem,
    FileChangeItem,
    Thread,
    Turn,
    UserMessageItem,
)
from agent.store import ThreadStore
from approval.gate import TurnApprovalState, format_tool_summary, prompt_approval
from model.openrouter import OpenRouterClient, OpenRouterError
from tools.registry import (
    TOOL_REGISTRY,
    DispatchResult,
    dispatch_tool,
    get_tool_schemas,
    parse_tool_arguments,
)


def run_turn(
    thread: Thread,
    user_text: str,
    config: Config,
    store: ThreadStore,
    *,
    on_text_delta: Callable[[str], None] | None = None,
    events: EventEmitter | None = None,
    cancel_token: CancelToken | None = None,
    quiet_tools: bool = False,
) -> Turn:
    emitter = events or EventEmitter()
    cancel = cancel_token or CancelToken()
    turn_state = TurnApprovalState()

    turn = Turn()
    thread.turns.append(turn)
    emitter.turn_started(thread.id, turn.id)

    user_item = UserMessageItem(text=user_text)
    turn.items.append(user_item)
    store.append_item(thread, turn.id, user_item)

    client = OpenRouterClient(config)
    tools = get_tool_schemas()
    messages = build_thread_messages(thread)

    try:
        return _run_loop(
            thread=thread,
            turn=turn,
            config=config,
            store=store,
            client=client,
            tools=tools,
            messages=messages,
            emitter=emitter,
            cancel=cancel,
            turn_state=turn_state,
            on_text_delta=on_text_delta,
            quiet_tools=quiet_tools,
        )
    except CancelledError:
        _finalize_cancelled(thread, turn, store, emitter)
        raise


def _run_loop(
    *,
    thread: Thread,
    turn: Turn,
    config: Config,
    store: ThreadStore,
    client: OpenRouterClient,
    tools: list,
    messages: list,
    emitter: EventEmitter,
    cancel: CancelToken,
    turn_state: TurnApprovalState,
    on_text_delta: Callable[[str], None] | None,
    quiet_tools: bool,
) -> Turn:
    for _round in range(config.max_rounds):
        cancel.check()

        summarized = compact_thread_if_needed(thread, config, store, client)
        if summarized:
            emitter.compaction(thread.id, summarized)
            messages = build_thread_messages(thread)

        def delta_handler(text: str) -> None:
            cancel.check()
            emitter.agent_delta(thread.id, turn.id, text)
            if on_text_delta:
                on_text_delta(text)

        try:
            result = client.stream_completion(
                messages,
                tools=tools,
                on_delta=delta_handler,
                cancel_token=cancel,
            )
        except CancelledError:
            raise
        except OpenRouterError as exc:
            turn.status = "failed"
            emitter.error(thread.id, str(exc))
            store.append_turn(thread, turn)
            raise

        if result.usage:
            turn.usage.input_tokens = result.usage.get("prompt_tokens")
            turn.usage.output_tokens = result.usage.get("completion_tokens")

        if not result.tool_calls:
            agent_item = AgentMessageItem(text=result.content)
            turn.items.append(agent_item)
            store.append_item(thread, turn.id, agent_item)
            turn.status = "completed"
            store.append_turn(thread, turn)
            store.save_thread(thread)
            emitter.turn_completed(thread.id, turn.id, turn.status)
            return turn

        assistant_msg = {
            "role": "assistant",
            "content": result.content or None,
            "tool_calls": result.tool_calls,
        }
        messages.append(assistant_msg)

        for tc in result.tool_calls:
            cancel.check()
            tool_name = tc["function"]["name"]
            tool_call_id = tc["id"]
            raw_args = tc["function"].get("arguments", "")
            arguments = parse_tool_arguments(raw_args)

            emitter.tool_pending(thread.id, turn.id, tool_name, arguments)
            if not quiet_tools and on_text_delta is None:
                pass  # human handler prints via CLI

            tracking_items = _create_tracking_items(
                tool_name, arguments, config, tool_call_id, raw_args
            )
            for item in tracking_items:
                turn.items.append(item)
                store.append_item(thread, turn.id, item)
                emitter.item_started(thread.id, turn.id, item.type, item.id)

            spec = TOOL_REGISTRY.get(tool_name)
            requires_approval = spec.requires_approval if spec else False

            if requires_approval:
                summary = format_tool_summary(tool_name, arguments)
                emitter.approval_requested(thread.id, turn.id, tool_name, summary)
                approved = prompt_approval(
                    tool_name,
                    arguments,
                    auto_approve=config.auto_approve,
                    turn_state=turn_state,
                )
                if not approved:
                    _mark_denied(tracking_items, store, thread, turn.id)
                    for item in tracking_items:
                        emitter.item_completed(
                            thread.id, turn.id, item.type, item.id, "denied"
                        )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": "User denied this action.",
                        }
                    )
                    continue

                _mark_approved(tracking_items)

            cancel.check()
            dispatch_result = dispatch_tool(tool_name, arguments, config)
            result_text = _apply_dispatch_results(
                dispatch_result,
                tracking_items,
                store,
                thread,
                turn.id,
                emitter,
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result_text,
                }
            )

    turn.status = "failed"
    fail_item = AgentMessageItem(
        text=f"Stopped: exceeded maximum tool rounds ({config.max_rounds})."
    )
    turn.items.append(fail_item)
    store.append_item(thread, turn.id, fail_item)
    store.append_turn(thread, turn)
    store.save_thread(thread)
    emitter.turn_completed(thread.id, turn.id, turn.status)
    return turn


def _create_tracking_items(
    tool_name: str,
    arguments: dict,
    config: Config,
    tool_call_id: str,
    raw_args: str,
) -> list[CommandExecutionItem | FileChangeItem]:
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
    return []


def _mark_denied(items: list[CommandExecutionItem | FileChangeItem], store, thread, turn_id) -> None:
    for item in items:
        item.status = "denied"
        if isinstance(item, CommandExecutionItem):
            item.output = "User denied this action."
        else:
            item.summary = "User denied this action."
        store.append_item(thread, turn_id, item)


def _mark_approved(items: list[CommandExecutionItem | FileChangeItem]) -> None:
    for item in items:
        item.status = "approved"


def _apply_dispatch_results(
    result: DispatchResult,
    tracking_items: list[CommandExecutionItem | FileChangeItem],
    store: ThreadStore,
    thread: Thread,
    turn_id: str,
    emitter: EventEmitter,
) -> str:
    if result.command_item and tracking_items:
        item = tracking_items[0]
        if isinstance(item, CommandExecutionItem):
            item.status = result.command_item.status
            item.output = result.command_item.output
            item.exit_code = result.command_item.exit_code
            item.duration_ms = result.command_item.duration_ms
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


def _finalize_cancelled(
    thread: Thread,
    turn: Turn,
    store: ThreadStore,
    emitter: EventEmitter,
) -> None:
    turn.status = "cancelled"
    msg = AgentMessageItem(text="Turn cancelled by user.")
    turn.items.append(msg)
    store.append_item(thread, turn.id, msg)
    store.append_turn(thread, turn)
    store.save_thread(thread)
    emitter.turn_completed(thread.id, turn.id, turn.status)


def brief_args(tool_name: str, arguments: dict) -> str:
    if tool_name == "run_command":
        return arguments.get("cmd", "")
    if tool_name == "write_file":
        return arguments.get("path", "")
    if tool_name == "apply_patch":
        patch = arguments.get("patch", "")
        return patch.splitlines()[0][:80] if patch else ""
    if tool_name == "read_file":
        return arguments.get("path", "")
    if tool_name == "search_repo":
        return arguments.get("pattern", "")
    return str(arguments)
