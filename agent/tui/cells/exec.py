"""Render ToolExecCell and ToolGroupCell."""

from __future__ import annotations

from agent.tui.cells.base import ToolExecCell, ToolGroupCell
from agent.tui.cells.panel import expand_affordance, panel_bottom_border, panel_top_border
from agent.tui.cells.tool import tool_header

_DEFAULT_OUTPUT_LINES = 8
_EXPANDED_OUTPUT_LINES = 200


def _output_line_count(text: str | None) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def _truncate_output(text: str | None, *, expanded: bool) -> list[str]:
    if not text:
        return []
    lines = text.splitlines()
    limit = _EXPANDED_OUTPUT_LINES if expanded else _DEFAULT_OUTPUT_LINES
    if len(lines) > limit:
        remaining = len(lines) - limit
        lines = lines[:limit] + [f"... ({remaining} more lines — press e to expand)"]
    return lines


def _collapsible_tool(cell: ToolExecCell) -> bool:
    if cell.status == "running":
        return False
    return _output_line_count(cell.output) > 3


def render_tool_exec(cell: ToolExecCell) -> str:
    collapsible = _collapsible_tool(cell)
    show_body = cell.expanded or not collapsible or cell.status == "running"
    out_lines = _output_line_count(cell.output)

    lines: list[str] = [panel_top_border()]
    header = tool_header(cell.tool_name, cell.status)
    affordance = ""
    if collapsible:
        affordance = f"  {expand_affordance(expanded=cell.expanded, lines_hidden=out_lines)}"
    lines.append(f"[bold cyan]{header}[/bold cyan]{affordance}")

    if cell.args_brief:
        lines.append(f"  [dim]args[/dim]  {cell.args_brief}")

    if show_body and cell.output:
        for ol in _truncate_output(cell.output, expanded=cell.expanded):
            lines.append(f"  [dim]{ol}[/dim]")
    elif collapsible and out_lines:
        lines.append(f"  [dim]({out_lines} lines hidden)[/dim]")

    if cell.exit_code is not None and cell.status in ("completed", "failed"):
        color = "green" if cell.exit_code == 0 else "red"
        dur = f" · {cell.duration_ms}ms" if cell.duration_ms else ""
        lines.append(f"  [{color}]exit {cell.exit_code}{dur}[/{color}]")

    lines.append(panel_bottom_border())
    return "\n".join(lines)


def render_tool_group(cell: ToolGroupCell) -> str:
    count = len(cell.tool_names)
    collapsible = cell.status != "running" and count > 1
    show_body = cell.expanded or not collapsible

    lines: list[str] = [panel_top_border()]
    header = f"▸ parallel reads ({count})"
    affordance = ""
    if collapsible:
        affordance = f"  {expand_affordance(expanded=cell.expanded, lines_hidden=count)}"
    lines.append(f"[bold cyan]{header}[/bold cyan]{affordance}")

    if show_body:
        for name, brief in zip(cell.tool_names, cell.args_briefs, strict=False):
            lines.append(f"  [dim]└ {name}[/dim]  {brief}")
    elif collapsible:
        lines.append(f"  [dim]({count} files — expand to list)[/dim]")

    lines.append(panel_bottom_border())
    return "\n".join(lines)
