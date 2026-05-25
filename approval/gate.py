from __future__ import annotations

from typing import Any

from rich.console import Console

console = Console()


def prompt_approval(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    auto_approve: bool = False,
) -> bool:
    if auto_approve:
        return True

    summary = _format_tool_summary(tool_name, arguments)
    console.print(f"[yellow][approval][/yellow] {summary}")
    console.print("[dim]Allow? [y/N][/dim]", end=" ")

    try:
        response = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False

    return response in ("y", "yes")


def _format_tool_summary(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name == "run_command":
        cmd = arguments.get("cmd", "")
        workdir = arguments.get("workdir")
        if workdir:
            return f"run_command in {workdir}: {cmd}"
        return f"run_command: {cmd}"

    if tool_name == "write_file":
        path = arguments.get("path", "")
        content = arguments.get("content", "")
        lines = len(content.splitlines())
        return f"write_file: {path} ({lines} lines)"

    parts = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
    return f"{tool_name}: {parts}"
