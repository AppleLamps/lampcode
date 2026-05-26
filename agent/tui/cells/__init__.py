"""Transcript cell types and renderers."""

from __future__ import annotations

from agent.tui.cells.base import (
    ApprovalCell,
    AssistantMessageCell,
    CompactionCell,
    ErrorCell,
    FileChange,
    PatchCell,
    PlanCell,
    ReasoningCell,
    SystemCell,
    ToolExecCell,
    ToolGroupCell,
    TranscriptCell,
    TranscriptLine,
    TurnSummaryCell,
    UserMessageCell,
    WorkingCell,
)
from agent.tui.cells.error import (
    render_approval_banner_text,
    render_approval_cell,
    render_error_cell,
    render_reasoning_cell,
    render_system_cell,
    render_turn_summary,
)
from agent.tui.cells.exec import render_tool_exec, render_tool_group
from agent.tui.cells.message import render_assistant_message, render_user_message
from agent.tui.cells.patch import render_patch_cell
from agent.tui.cells.plan import render_compaction_cell, render_plan_cell
from agent.tui.cells.working import render_working_cell


def render_cell(cell: TranscriptCell) -> str:
    """Render any transcript cell to Rich markup string."""
    if isinstance(cell, UserMessageCell):
        return render_user_message(cell)
    if isinstance(cell, AssistantMessageCell):
        return render_assistant_message(cell)
    if isinstance(cell, ToolExecCell):
        return render_tool_exec(cell)
    if isinstance(cell, PatchCell):
        return render_patch_cell(cell)
    if isinstance(cell, PlanCell):
        return render_plan_cell(cell)
    if isinstance(cell, CompactionCell):
        return render_compaction_cell(cell)
    if isinstance(cell, ApprovalCell):
        return render_approval_cell(cell)
    if isinstance(cell, ErrorCell):
        return render_error_cell(cell)
    if isinstance(cell, TurnSummaryCell):
        return render_turn_summary(cell)
    if isinstance(cell, ReasoningCell):
        return render_reasoning_cell(cell)
    if isinstance(cell, ToolGroupCell):
        return render_tool_group(cell)
    if isinstance(cell, SystemCell):
        return render_system_cell(cell)
    if isinstance(cell, WorkingCell):
        return render_working_cell(cell)
    return f"\n[dim]{cell!r}[/dim]"


__all__ = [
    "ApprovalCell",
    "AssistantMessageCell",
    "CompactionCell",
    "ErrorCell",
    "FileChange",
    "PatchCell",
    "PlanCell",
    "ReasoningCell",
    "SystemCell",
    "ToolExecCell",
    "ToolGroupCell",
    "TranscriptCell",
    "TranscriptLine",
    "TurnSummaryCell",
    "UserMessageCell",
    "WorkingCell",
    "render_approval_banner_text",
    "render_cell",
]
