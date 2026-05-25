from __future__ import annotations

from typing import Callable

from rich.console import Console

from agent.config import Config
from agent.context import build_thread_messages
from agent.models import (
    AgentMessageItem,
    CommandExecutionItem,
    FileChangeItem,
    Thread,
    Turn,
    UserMessageItem,
)
from agent.store import ThreadStore
from approval.gate import prompt_approval
from model.openrouter import OpenRouterClient, OpenRouterError
from tools.registry import TOOL_REGISTRY, dispatch_tool, get_tool_schemas, parse_tool_arguments

console = Console()


def run_turn(
    thread: Thread,
    user_text: str,
    config: Config,
    store: ThreadStore,
    *,
    on_text_delta: Callable[[str], None] | None = None,
) -> Turn:
    turn = Turn()
    thread.turns.append(turn)

    user_item = UserMessageItem(text=user_text)
    turn.items.append(user_item)
    store.append_item(thread, turn.id, user_item)

    client = OpenRouterClient(config)
    tools = get_tool_schemas()

    # In-memory messages for the active loop (includes partial turn)
    messages = build_thread_messages(thread)

    for round_num in range(config.max_rounds):
        collected_text: list[str] = []

        def delta_handler(text: str) -> None:
            collected_text.append(text)
            if on_text_delta:
                on_text_delta(text)

        try:
            result = client.stream_completion(
                messages,
                tools=tools,
                on_delta=delta_handler,
            )
        except OpenRouterError as exc:
            turn.status = "failed"
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
            return turn

        # Assistant message with tool calls
        assistant_msg = {
            "role": "assistant",
            "content": result.content or None,
            "tool_calls": result.tool_calls,
        }
        messages.append(assistant_msg)

        for tc in result.tool_calls:
            tool_name = tc["function"]["name"]
            tool_call_id = tc["id"]
            raw_args = tc["function"].get("arguments", "")
            arguments = parse_tool_arguments(raw_args)

            console.print(f"[cyan][tool][/cyan] {tool_name}: {_brief_args(tool_name, arguments)}")

            spec = TOOL_REGISTRY.get(tool_name)
            requires_approval = spec.requires_approval if spec else False

            tracking_item: CommandExecutionItem | FileChangeItem | None = None

            if tool_name == "run_command":
                tracking_item = CommandExecutionItem(
                    command=arguments.get("cmd", ""),
                    cwd=str(config.cwd),
                    status="pending",
                    tool_call_id=tool_call_id,
                    tool_arguments=raw_args,
                )
                turn.items.append(tracking_item)
                store.append_item(thread, turn.id, tracking_item)
            elif tool_name == "write_file":
                tracking_item = FileChangeItem(
                    path=arguments.get("path", ""),
                    status="pending",
                    tool_call_id=tool_call_id,
                    tool_arguments=raw_args,
                    content=arguments.get("content"),
                )
                turn.items.append(tracking_item)
                store.append_item(thread, turn.id, tracking_item)

            if requires_approval:
                approved = prompt_approval(
                    tool_name, arguments, auto_approve=config.auto_approve
                )
                if not approved:
                    if isinstance(tracking_item, CommandExecutionItem):
                        tracking_item.status = "denied"
                        tracking_item.output = "User denied this action."
                        store.append_item(thread, turn.id, tracking_item)
                    elif isinstance(tracking_item, FileChangeItem):
                        tracking_item.status = "denied"
                        tracking_item.summary = "User denied this action."
                        store.append_item(thread, turn.id, tracking_item)

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": "User denied this action.",
                        }
                    )
                    continue

                if isinstance(tracking_item, CommandExecutionItem):
                    tracking_item.status = "approved"
                elif isinstance(tracking_item, FileChangeItem):
                    tracking_item.status = "approved"

            result_text, executed_item = dispatch_tool(tool_name, arguments, config)

            if isinstance(tracking_item, CommandExecutionItem) and executed_item:
                tracking_item.status = executed_item.status
                tracking_item.output = executed_item.output
                tracking_item.exit_code = executed_item.exit_code
                tracking_item.duration_ms = executed_item.duration_ms
                store.append_item(thread, turn.id, tracking_item)
            elif isinstance(tracking_item, FileChangeItem) and executed_item:
                tracking_item.status = executed_item.status
                tracking_item.summary = executed_item.summary
                store.append_item(thread, turn.id, tracking_item)

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
    return turn


def _brief_args(tool_name: str, arguments: dict) -> str:
    if tool_name == "run_command":
        return arguments.get("cmd", "")
    if tool_name == "write_file":
        return arguments.get("path", "")
    if tool_name == "read_file":
        return arguments.get("path", "")
    if tool_name == "search_repo":
        return arguments.get("pattern", "")
    return str(arguments)
