from __future__ import annotations

import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.models import (
    AgentMessageItem,
    CommandExecutionItem,
    ContextCompactionItem,
    FileChangeItem,
    Item,
    McpToolCallItem,
    SkillActivationItem,
    Thread,
    UserMessageItem,
)
from agent.skills.discovery import Skill
from agent.skills.injector import build_skills_prompt


@dataclass
class ProjectRules:
    path: Path
    content: str
    char_count: int


def find_project_rules(cwd: Path) -> ProjectRules | None:
    candidates = [
        cwd / "AGENTS.md",
        cwd / "agents.md",
        cwd / ".agents" / "AGENTS.md",
    ]
    for path in candidates:
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                return ProjectRules(path=path, content=content, char_count=len(content))
            except OSError:
                continue
    return None


def load_project_rules(cwd: Path, max_chars: int = 8000) -> tuple[str, ProjectRules | None]:
    rules = find_project_rules(cwd)
    if not rules:
        return "", None

    content = rules.content
    if len(content) > max_chars:
        content = content[: max_chars - 20] + "\n[... truncated ...]"
    return f"# Project Rules\n\n{content}\n", rules


def load_project_context(cwd: Path, max_bytes: int = 8192) -> str:
    parts: list[str] = []
    for name in ("README.md",):
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


def build_system_prompt(
    cwd: Path,
    repo_root: str | None = None,
    *,
    active_skills: list[Skill] | None = None,
    skills_max_body: int = 4000,
    project_rules: str = "",
) -> str:
    project_context = load_project_context(cwd)
    os_info = f"{platform.system()} {platform.release()} ({platform.machine()})"

    prompt = f"""You are a local coding agent working in the user's project directory.

Current working directory: {cwd}
"""
    if repo_root:
        prompt += f"Git repository root: {repo_root}\n"

    prompt += f"""Operating system: {os_info}

Your job is to investigate the codebase, run commands, and make focused edits to complete the user's task.

Rules:
- Inspect files and search the repo before editing. Do not guess file contents.
- Prefer small, focused changes over large refactors.
- Use `apply_patch` for modifying existing files; reserve `write_file` for new files or full rewrites.
- Run relevant tests or commands to verify your work when appropriate.
- Explain briefly what you are doing as you work.
- Use the provided tools instead of assuming anything about the codebase.
- When MCP tools are available, you may use them for specialized tasks.

Safety:
- Only access paths inside the project working directory.
- Avoid destructive commands unless necessary for the task.
- Do not exfiltrate secrets or credentials.

When you have completed the task, provide a clear final summary of what you found and changed.
"""
    if project_rules:
        prompt += f"\n\n{project_rules}\n"
    if active_skills:
        prompt += "\n" + build_skills_prompt(active_skills, max_body_chars=skills_max_body)
    if project_context:
        prompt += f"\n\n# Project context\n\n{project_context}\n"

    return prompt


def item_to_messages(item: Item) -> list[dict[str, Any]]:
    """Convert a persisted item to OpenAI-style chat messages."""
    if isinstance(item, UserMessageItem):
        return [{"role": "user", "content": item.text}]

    if isinstance(item, AgentMessageItem):
        return [{"role": "assistant", "content": item.text}]

    if isinstance(item, (SkillActivationItem, ContextCompactionItem)):
        return []

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

    if isinstance(item, McpToolCallItem):
        if not item.tool_call_id:
            return []
        content = item.output or item.error or ""
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
        if item.diff_snippet:
            content = f"{content}\n\nDiff:\n{item.diff_snippet}"
        return [
            {
                "role": "tool",
                "tool_call_id": item.tool_call_id,
                "content": content,
            }
        ]

    return []


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    import json

    total_chars = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif content is not None:
            total_chars += len(str(content))
        for tc in msg.get("tool_calls") or []:
            total_chars += len(json.dumps(tc))
    return total_chars // 4


def build_messages_from_turn_items(
    items: list[Item],
    pending_assistant: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
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

        if isinstance(item, (SkillActivationItem, ContextCompactionItem)):
            idx += 1
            continue

        if isinstance(
            item, (CommandExecutionItem, FileChangeItem, McpToolCallItem)
        ):
            batch: list[CommandExecutionItem | FileChangeItem | McpToolCallItem] = []
            while idx < len(items) and isinstance(
                items[idx],
                (CommandExecutionItem, FileChangeItem, McpToolCallItem),
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


def _tool_name_for_item(
    item: CommandExecutionItem | FileChangeItem | McpToolCallItem,
) -> str:
    if isinstance(item, McpToolCallItem):
        from agent.mcp.adapter import exposed_tool_name

        return exposed_tool_name(item.server, item.tool)
    if isinstance(item, CommandExecutionItem):
        return "run_command"
    if item.tool_arguments and "patch" in item.tool_arguments:
        return "apply_patch"
    if item.change_type in ("update", "add", "delete"):
        return "apply_patch"
    return "write_file"


def _tool_args_for_item(
    item: CommandExecutionItem | FileChangeItem | McpToolCallItem,
) -> str:
    import json

    if isinstance(item, McpToolCallItem):
        return json.dumps(item.arguments)
    if item.tool_arguments:
        return item.tool_arguments
    if isinstance(item, CommandExecutionItem):
        return json.dumps({"cmd": item.command})
    return json.dumps({"path": item.path, "content": item.content or ""})


def build_thread_messages(
    thread: Thread,
    current_turn_items: list[Item] | None = None,
    *,
    active_skills: list[Skill] | None = None,
    skills_max_body: int = 4000,
    project_rules: str = "",
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": build_system_prompt(
                Path(thread.cwd),
                thread.repo_root,
                active_skills=active_skills,
                skills_max_body=skills_max_body,
                project_rules=project_rules,
            ),
        }
    ]

    completed_turns = thread.turns[:-1] if thread.turns else []
    current = thread.turns[-1] if thread.turns else None

    for turn in completed_turns:
        messages.extend(build_messages_from_turn_items(turn.items))

    if current:
        items = current_turn_items if current_turn_items is not None else current.items
        messages.extend(build_messages_from_turn_items(items))

    return messages
