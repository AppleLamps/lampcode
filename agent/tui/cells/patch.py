"""Render PatchCell with colored diff."""

from __future__ import annotations

from agent.tui.cells.base import PatchCell
from agent.tui.cells.panel import expand_affordance, panel_bottom_border, panel_top_border
from agent.tui.cells.tool import tool_header
from agent.tui.diff_render import format_diff_lines, format_file_summary


def _diff_line_count(cell: PatchCell) -> int:
    if not cell.diff_text:
        return 0
    return len(cell.diff_text.splitlines())


def render_patch_cell(cell: PatchCell) -> str:
    diff_lines = _diff_line_count(cell)
    collapsible = cell.status != "running" and diff_lines > 6
    show_diff = cell.expanded or not collapsible or cell.status == "running"

    lines: list[str] = [panel_top_border()]
    header = tool_header("apply_patch", cell.status)
    affordance = ""
    if collapsible:
        affordance = f"  {expand_affordance(expanded=cell.expanded, lines_hidden=diff_lines)}"
    lines.append(f"[bold cyan]{header}[/bold cyan]{affordance}")

    if cell.files:
        summary = format_file_summary([(f.path, f.change_type) for f in cell.files])
        lines.append(f"  [dim]{summary}[/dim]")

    if show_diff and cell.diff_text:
        max_lines = 200 if cell.expanded else 40
        for diff_line in format_diff_lines(
            cell.diff_text, max_lines=max_lines, line_numbers=True
        ):
            lines.append(f"  {diff_line}")
    elif collapsible and diff_lines:
        lines.append(f"  [dim]({diff_lines} diff lines hidden)[/dim]")

    lines.append(panel_bottom_border())
    return "\n".join(lines)
