from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

from agent.models import (
    AgentMessageItem,
    CommandExecutionItem,
    FileChangeItem,
    Item,
    Thread,
    UserMessageItem,
)


def load_project_context(cwd: Path, max_bytes: int = 8192) -> str:
    parts: list[str] = []
    for name in ("AGENTS.md", "README.md"):
        path = cwd / name
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                if len(content.encode("utf-8")) > max_bytes:
                    content = content.encode("utf-8")[:max_bytes].decode(
                        "utf-8", errors="ignore"
                    )
                    content += "\n\n[... truncated ...]"
                parts.append(f"## {name}\n\n{content}")
            except OSError:
                continue
    return "\n\n".join(parts)


def build_system_prompt(cwd: Path) -> str:
    project_context = load_project_context(cwd)
    os_info = f"{platform.system()} {platform.release()} ({platform.machine()})"

    prompt = f"""You are a local coding agent working in the user's project directory.

Current working directory: {cwd}
Operating system: {os_info}

Your job is to investigate the codebase, run commands, and make focused edits to complete the user's task.

Rules:
- Inspect files and search the repo before editing. Do not guess file contents.
- Prefer small, focused changes over large refactors.
- Run relevant tests or commands to verify your work when appropriate.
- Explain briefly what you are doing as you work.
- Use the provided tools instead of assuming anything about the codebase.

Safety:
- Only access paths inside the project working directory.
- Avoid destructive commands unless necessary for the task.
- Do not exfiltrate secrets or credentials.

When you have completed the task, provide a clear final summary of what you found and changed.
"""
    if project_context:
        prompt += f"\n\n# Project context\n\n{project_context}\n"

    return prompt


def item_to_messages(item: Item) -> list[dict[str, Any]]:
    """Convert a persisted item to OpenAI-style chat messages."""
    if isinstance(item, UserMessageItem):
        return [{"role": "user", "content": item.text}]

    if isinstance(item, AgentMessageItem):
        return [{"role": "assistant", "content": item.text}]

    if isinstance(item, CommandExecutionItem):
        if not item.tool_call_id:
            return []
        content = item.output or ""
        if item.status == "denied":
            content = "User denied this action."
        return [
            {
                "role": "tool",
                "tool_call_id": item.tool_call_id,
                "content": content,
            }
        ]

    if isinstance(item, FileChangeItem):
        if not item.tool_call_id:
            return []
        content = item.summary or ""
        if item.status == "denied":
            content = "User denied this action."
        return [
            {
                "role": "tool",
                "tool_call_id": item.tool_call_id,
                "content": content,
            }
        ]

    return []


def build_assistant_tool_call_message(
    text: str,
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": text or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def build_messages_from_turn_items(
    items: list[Item],
    pending_assistant: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Build OpenAI messages from turn items, preserving tool-call pairing.

    Groups consecutive tool results under their preceding assistant tool_calls message.
    """
    messages: list[dict[str, Any]] = []
    idx = 0
    while idx < len(items):
        item = items[idx]

        if isinstance(item, UserMessageItem):
            messages.append({"role": "user", "content": item.text})
            idx += 1
            continue

        if isinstance(item, AgentMessageItem):
            messages.append({"role": "assistant", "content": item.text})
            idx += 1
            continue

        if isinstance(item, (CommandExecutionItem, FileChangeItem)):
            # Collect batch of tool execution items that share a tool call round
            batch: list[CommandExecutionItem | FileChangeItem] = []
            while idx < len(items) and isinstance(
                items[idx], (CommandExecutionItem, FileChangeItem)
            ):
                batch.append(items[idx])  # type: ignore[arg-type]
                idx += 1

            tool_calls = []
            for exec_item in batch:
                if exec_item.tool_call_id:
                    tool_calls.append(
                        {
                            "id": exec_item.tool_call_id,
                            "type": "function",
                            "function": {
                                "name": _tool_name_for_item(exec_item),
                                "arguments": _tool_args_for_item(exec_item),
                            },
                        }
                    )

            if tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": tool_calls,
                    }
                )

            for exec_item in batch:
                messages.extend(item_to_messages(exec_item))

            continue

        idx += 1

    if pending_assistant:
        messages.append(pending_assistant)

    return messages


def _tool_name_for_item(item: CommandExecutionItem | FileChangeItem) -> str:
    if isinstance(item, CommandExecutionItem):
        return "run_command"
    return "write_file"


def _tool_args_for_item(item: CommandExecutionItem | FileChangeItem) -> str:
    import json

    if item.tool_arguments:
        return item.tool_arguments
    if isinstance(item, CommandExecutionItem):
        return json.dumps({"cmd": item.command})
    return json.dumps({"path": item.path, "content": item.content or ""})


def build_thread_messages(thread: Thread, current_turn_items: list[Item] | None = None) -> list[dict[str, Any]]:
    """Build full message list: system + all completed turns + current turn."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(Path(thread.cwd))}
    ]

    completed_turns = thread.turns[:-1] if thread.turns else []
    current = thread.turns[-1] if thread.turns else None

    for turn in completed_turns:
        messages.extend(build_messages_from_turn_items(turn.items))

    if current:
        items = current_turn_items if current_turn_items is not None else current.items
        messages.extend(build_messages_from_turn_items(items))

    return messages
