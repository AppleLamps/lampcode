"""Turn helper utilities."""
from __future__ import annotations

from agent.config import Config

def approval_diff_preview_for_tool(
    tool_name: str, arguments: dict, config: Config
) -> str | None:
    if tool_name != "apply_patch":
        return None
    from agent.tui.patch_preview import patch_approval_diff_preview

    return patch_approval_diff_preview(arguments, cwd=config.cwd)

def brief_args(tool_name: str, arguments: dict) -> str:
    if tool_name == "run_command":
        return arguments.get("cmd", "")
    if tool_name == "write_file":
        return arguments.get("path", "")
    if tool_name == "apply_patch":
        from tools.patch import format_patch_brief

        return format_patch_brief(arguments.get("patch", ""))
    if tool_name == "read_file":
        return arguments.get("path", "")
    if tool_name == "search_repo":
        return arguments.get("pattern", "")
    if tool_name == "web_search":
        return arguments.get("query", "")
    if tool_name == "spawn_worker":
        return arguments.get("task", "")[:80]
    if tool_name.startswith("mcp__"):
        return str(arguments)[:80]
    return str(arguments)
