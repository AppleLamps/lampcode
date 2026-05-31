"""Tool family icons, status glyphs, and header labels."""

from __future__ import annotations

_TOOL_ICONS: dict[str, str] = {
    "run_command": "$",
    "apply_patch": "✎",
    "read_file": "📄",
    "write_file": "✎",
    "search_repo": "🔍",
    "web_search": "🌐",
    "file_outline": "◎",
    "go_to_definition": "⇢",
    "find_references": "⇄",
    "file_imports": "⤴",
}


def tool_icon(tool_name: str) -> str:
    if tool_name in _TOOL_ICONS:
        return _TOOL_ICONS[tool_name]
    if tool_name.startswith("mcp__"):
        return "⚙"
    return "▸"


def status_glyph(status: str) -> str:
    if status == "running":
        return "[#58a6ff]…[/#58a6ff]"
    if status == "completed":
        return "[green]✓[/green]"
    if status == "failed":
        return "[red]×[/red]"
    if status in ("denied", "blocked"):
        return "[yellow]![/yellow]"
    if status in ("waiting", "pending"):
        return "[yellow]?[/yellow]"
    return "[dim]·[/dim]"


def status_label(status: str) -> str:
    labels = {
        "running": "running",
        "completed": "completed",
        "failed": "failed",
        "denied": "denied",
        "blocked": "blocked",
        "waiting": "waiting",
        "pending": "pending",
    }
    return labels.get(status, status or "status")


def compact_tool_label(tool_name: str) -> str:
    label = tool_name.replace("mcp__", "mcp:")
    if label.startswith("mcp:"):
        parts = label.split("__")
        if len(parts) >= 2:
            return f"mcp:{parts[-1]}"
    return label


def tool_header(tool_name: str, status: str = "") -> str:
    icon = tool_icon(tool_name)
    label = compact_tool_label(tool_name)
    prefix = f"{status_glyph(status)} " if status else ""
    return f"{prefix}{icon} {label}"


def row_meta(parts: list[str]) -> str:
    clean = [p for p in parts if p]
    if not clean:
        return ""
    return "[dim]" + " · ".join(clean) + "[/dim]"
