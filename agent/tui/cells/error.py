"""Render error, approval, turn summary, reasoning, and system cells."""

from __future__ import annotations

from agent.tui.cells.base import (
    ApprovalCell,
    ErrorCell,
    ReasoningCell,
    SystemCell,
    TurnSummaryCell,
)
from agent.tui.diff_render import format_diff_lines
from agent.tui.cells.panel import expand_affordance
from agent.tui.cells.tool import row_meta, status_glyph


def render_error_cell(cell: ErrorCell) -> str:
    color = "red" if cell.severity == "error" else "yellow"
    source = cell.source or ("Internal error" if cell.severity == "error" else "Warning")
    lines = [f"\n[{color} bold]{status_glyph('failed' if cell.severity == 'error' else 'blocked')} {source}[/{color} bold]  {cell.message}"]
    if cell.command:
        lines.append(f"  [dim]command[/dim] {cell.command}")
    if cell.details:
        lines.append(f"  [dim]{cell.details}[/dim]")
    return "\n".join(lines)


def render_approval_cell(cell: ApprovalCell) -> str:
    return f"\n[yellow]⚠ {cell.summary}[/yellow] [dim][y/n/a/A][/dim]"


def render_turn_summary(cell: TurnSummaryCell) -> str:
    parts = [status_glyph(cell.status), f"[dim]turn[/dim] {cell.status}"]
    if cell.model:
        parts.append(f"· {cell.model}")
    if cell.fallback_used:
        parts.append("· [yellow]fallback[/yellow]")
    if cell.cost is not None:
        parts.append(f"· ${cell.cost:.3f}")
    if cell.files_changed:
        parts.append(f"· {cell.files_changed} files")
        if cell.lines_added or cell.lines_removed:
            parts.append(f"+{cell.lines_added}/-{cell.lines_removed}")
    if cell.commands_run:
        parts.append(f"· {cell.commands_run} cmds")
    if cell.routing_note:
        parts.append(f"· [dim]{cell.routing_note}[/dim]")
    parts.append(f"· {cell.status}")
    return " ".join(parts)


def render_reasoning_cell(cell: ReasoningCell) -> str:
    line_count = len(cell.text.splitlines())
    meta = row_meta([expand_affordance(expanded=cell.expanded, lines_hidden=line_count)])
    lines = [f"[dim italic]… Thinking[/dim italic]  {meta}"]
    if cell.expanded:
        for line in cell.text.splitlines():
            lines.append(f"  [dim italic]{line}[/dim italic]")
    elif line_count:
        lines.append(f"  [dim italic]({line_count} lines hidden)[/dim italic]")
    return "\n".join(lines)


def render_system_cell(cell: SystemCell) -> str:
    return f"\n[dim]{cell.text}[/dim]"


def render_approval_banner_text(
    summary: str,
    *,
    diff_preview: str | None = None,
    tool_name: str | None = None,
    source_path: str | None = None,
) -> str:
    lines = [
        f"[yellow bold]Approve[/yellow bold]  {summary}",
        "[dim]Keys: y yes | n no | a all this turn | A all session (no Enter needed)[/dim]",
    ]
    if tool_name == "apply_patch" and not diff_preview:
        lines.append("  [yellow]No diff preview — review carefully before approving.[/yellow]")
    if diff_preview:
        lines.append("  [dim]Patch preview:[/dim]")
        for diff_line in format_diff_lines(
            diff_preview,
            max_lines=32,
            line_numbers=True,
            source_path=source_path,
            hunk_aware=True,
        ):
            lines.append(f"  {diff_line}")
    return "\n".join(lines)
