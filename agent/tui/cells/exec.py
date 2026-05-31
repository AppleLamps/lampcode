"""Render ToolExecCell and ToolGroupCell."""

from __future__ import annotations

from agent.tui.cells.base import ToolExecCell, ToolGroupCell
from agent.tui.cells.panel import expand_affordance
from agent.tui.cells.tool import row_meta, status_glyph, tool_header
from agent.tui.output_truncation import format_tool_output_lines


def _output_line_count(text: str | None) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def _collapsible_tool(cell: ToolExecCell) -> bool:
    if cell.status == "running":
        return False
    return _output_line_count(cell.output) > 3


def render_tool_exec(cell: ToolExecCell) -> str:
    collapsible = _collapsible_tool(cell)
    show_body = cell.expanded or not collapsible or cell.status == "running"
    out_lines = _output_line_count(cell.output)

    lines: list[str] = []
    header = tool_header(cell.tool_name, cell.status)
    meta: list[str] = []
    if cell.args_brief:
        meta.append(cell.args_brief)
    if cell.exit_code is not None and cell.status in ("completed", "failed"):
        dur = f"{cell.duration_ms}ms" if cell.duration_ms else ""
        meta.append(f"exit {cell.exit_code}" + (f" in {dur}" if dur else ""))
    if collapsible:
        meta.append(expand_affordance(expanded=cell.expanded, lines_hidden=out_lines))
    meta_text = f"  {row_meta(meta)}" if meta else ""
    lines.append(f"[bold cyan]{header}[/bold cyan]{meta_text}")

    if cell.expanded and cell.args_brief:
        lines.append(f"  [dim]args[/dim] {cell.args_brief}")

    if show_body and cell.output:
        display = format_tool_output_lines(cell.output, expanded=cell.expanded)
        for idx, ol in enumerate(display):
            if idx == 0 and ol.startswith("Total output lines:"):
                lines.append(f"  [dim]{ol}[/dim]")
            else:
                lines.append(f"  [dim]{ol}[/dim]")
    elif collapsible and out_lines:
        lines.append(f"  [dim]({out_lines} output lines hidden)[/dim]")

    return "\n".join(lines)


def render_tool_group(cell: ToolGroupCell) -> str:
    count = len(cell.tool_names)
    collapsible = cell.status != "running" and count > 1
    show_body = cell.expanded or not collapsible

    lines: list[str] = []
    header = f"{status_glyph(cell.status)} 📄 parallel reads"
    meta = [f"{count} files"]
    if collapsible:
        meta.append(expand_affordance(expanded=cell.expanded, lines_hidden=count))
    lines.append(f"[bold cyan]{header}[/bold cyan]  {row_meta(meta)}")

    if show_body:
        for name, brief in zip(cell.tool_names, cell.args_briefs, strict=False):
            lines.append(f"  [dim]• {name}[/dim]  {brief}")
    elif collapsible:
        lines.append(f"  [dim]({count} files — expand to list)[/dim]")

    return "\n".join(lines)
