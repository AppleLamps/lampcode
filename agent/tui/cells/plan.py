"""Render plan and compaction cells."""

from __future__ import annotations

from agent.tui.cells.base import CompactionCell, PlanCell
from agent.tui.cells.panel import expand_affordance
from agent.tui.cells.tool import row_meta, status_glyph


def render_plan_cell(cell: PlanCell) -> str:
    body_lines = len(cell.body.splitlines()) if cell.body else 0
    collapsible = body_lines > 4
    show_body = cell.expanded or not collapsible

    lines: list[str] = []
    meta: list[str] = []
    if collapsible:
        meta.append(expand_affordance(expanded=cell.expanded, lines_hidden=body_lines))
    meta_text = f"  {row_meta(meta)}" if meta else ""
    lines.append(
        f"[bold yellow]{status_glyph('pending')} 📋 Plan[/bold yellow] [dim]{cell.summary}[/dim]{meta_text}"
    )

    if show_body and cell.body:
        for line in cell.body.splitlines():
            lines.append(f"  {line}")
    elif collapsible:
        preview = cell.body[:120].rstrip()
        lines.append(f"  {preview}… [dim](expand for full plan)[/dim]")

    return "\n".join(lines)


def render_compaction_cell(cell: CompactionCell) -> str:
    if cell.kind == "started":
        return f"\n[dim]⟳ Compacting… summarized {cell.summarized_items} items[/dim]"
    if cell.kind == "completed":
        before = cell.tokens_before or "?"
        after = cell.tokens_after or "?"
        return (
            f"\n[dim]⟳ Compacted — removed {cell.removed_items} items "
            f"({before} → {after} tokens est.)[/dim]"
        )
    return f"\n[yellow]⚠ Compaction warning:[/yellow] {cell.message}"
