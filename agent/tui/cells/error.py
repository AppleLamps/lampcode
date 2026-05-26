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


def render_error_cell(cell: ErrorCell) -> str:
    color = "red" if cell.severity == "error" else "yellow"
    lines = [f"\n[{color} bold]✕ {cell.message}[/{color} bold]"]
    if cell.command:
        lines.append(f"  [dim]command: {cell.command}[/dim]")
    return "\n".join(lines)


def render_approval_cell(cell: ApprovalCell) -> str:
    return f"\n[yellow]⚠ {cell.summary}[/yellow] [dim][y/n/a/A][/dim]"


def render_turn_summary(cell: TurnSummaryCell) -> str:
    parts = ["[dim]───[/dim]", f"[dim][done][/dim] {cell.model}"]
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
    from agent.tui.cells.panel import expand_affordance, panel_bottom_border, panel_top_border

    line_count = len(cell.text.splitlines())
    affordance = expand_affordance(expanded=cell.expanded, lines_hidden=line_count)
    lines = [
        panel_top_border(),
        f"[dim italic]Thinking[/dim italic]  {affordance}",
    ]
    if cell.expanded:
        for line in cell.text.splitlines():
            lines.append(f"  [dim italic]{line}[/dim italic]")
    elif line_count:
        lines.append(f"  [dim italic]({line_count} lines hidden)[/dim italic]")
    lines.append(panel_bottom_border())
    return "\n".join(lines)


def render_system_cell(cell: SystemCell) -> str:
    return f"\n[dim]{cell.text}[/dim]"


def render_approval_banner_text(summary: str, *, diff_preview: str | None = None) -> str:
    lines = [
        f"[yellow bold]Approve:[/yellow bold] {summary}?  "
        f"[dim][y] yes  [n] no  [a] turn  [A] session[/dim]"
    ]
    if diff_preview:
        for diff_line in format_diff_lines(
            diff_preview, max_lines=12, line_numbers=True
        ):
            lines.append(f"  {diff_line}")
    return "\n".join(lines)
