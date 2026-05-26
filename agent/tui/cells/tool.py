"""Tool family icons and header labels."""

from __future__ import annotations

_TOOL_ICONS: dict[str, str] = {
    "run_command": "▸",
    "apply_patch": "✎",
    "read_file": "📄",
    "write_file": "✎",
    "search_repo": "🔍",
    "web_search": "🌐",
}


def tool_icon(tool_name: str) -> str:
    if tool_name in _TOOL_ICONS:
        return _TOOL_ICONS[tool_name]
    if tool_name.startswith("mcp__"):
        return "⚙"
    return "▸"


def tool_header(tool_name: str, status: str = "") -> str:
    icon = tool_icon(tool_name)
    label = tool_name.replace("mcp__", "mcp:")
    if status and status != "running":
        return f"{icon} {label} ({status})"
    return f"{icon} {label}"
