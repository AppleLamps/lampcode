"""Lightweight render keys for incremental transcript sync."""

from __future__ import annotations

from agent.tui.cells.base import (
    ApprovalCell,
    AssistantMessageCell,
    CompactionCell,
    ErrorCell,
    PatchCell,
    PlanCell,
    ReasoningCell,
    SystemCell,
    ToolExecCell,
    ToolGroupCell,
    TranscriptCell,
    TurnSummaryCell,
    UserMessageCell,
    WorkingCell,
)


def cell_render_signature(cell: TranscriptCell) -> tuple:
    """Fields that affect `render_cell()` output — used to skip unchanged widgets."""
    if isinstance(cell, UserMessageCell):
        return ("user", cell.text)
    if isinstance(cell, AssistantMessageCell):
        return ("asst", cell.text, cell.streaming)
    if isinstance(cell, ToolExecCell):
        return (
            "tool",
            cell.tool_name,
            cell.args_brief,
            cell.status,
            cell.output,
            cell.exit_code,
            cell.duration_ms,
            cell.expanded,
            cell.source,
        )
    if isinstance(cell, PatchCell):
        files = tuple((f.path, f.change_type) for f in cell.files)
        return ("patch", files, cell.diff_text, cell.status, cell.expanded)
    if isinstance(cell, PlanCell):
        return ("plan", cell.summary, cell.body, cell.expanded)
    if isinstance(cell, CompactionCell):
        return (
            "compact",
            cell.kind,
            cell.summarized_items,
            cell.removed_items,
            cell.message,
            cell.tokens_before,
            cell.tokens_after,
        )
    if isinstance(cell, ApprovalCell):
        return (
            "approval",
            cell.summary,
            cell.tool_name,
            cell.diff_preview,
            cell.resolved,
        )
    if isinstance(cell, ErrorCell):
        return ("error", cell.message, cell.severity, cell.command)
    if isinstance(cell, TurnSummaryCell):
        return (
            "summary",
            cell.status,
            cell.model,
            cell.cost,
            cell.files_changed,
            cell.lines_added,
            cell.lines_removed,
            cell.commands_run,
            cell.fallback_used,
            cell.routing_note,
        )
    if isinstance(cell, ReasoningCell):
        return ("reasoning", cell.text, cell.expanded)
    if isinstance(cell, ToolGroupCell):
        return (
            "group",
            tuple(cell.tool_names),
            tuple(cell.args_briefs),
            cell.status,
            cell.expanded,
        )
    if isinstance(cell, WorkingCell):
        return ("working", cell.message)
    if isinstance(cell, SystemCell):
        return ("system", cell.text)
    return (type(cell).__name__, cell.cell_id)
