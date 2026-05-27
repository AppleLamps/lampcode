"""Typed transcript cell model for TUI rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agent.models import new_id


@dataclass
class TranscriptCell:
    cell_id: str = field(default_factory=new_id)


@dataclass
class UserMessageCell(TranscriptCell):
    text: str = ""


@dataclass
class AssistantMessageCell(TranscriptCell):
    text: str = ""
    streaming: bool = False


@dataclass
class FileChange:
    path: str
    change_type: str = "update"


@dataclass
class ToolExecCell(TranscriptCell):
    tool_name: str = ""
    args_brief: str = ""
    status: Literal["running", "completed", "failed", "denied", "blocked"] = "running"
    output: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    expanded: bool = False
    source: str = "builtin"


@dataclass
class PatchCell(TranscriptCell):
    files: list[FileChange] = field(default_factory=list)
    diff_text: str = ""
    status: Literal["running", "completed", "failed", "denied", "blocked"] = "running"
    expanded: bool = False


@dataclass
class PlanCell(TranscriptCell):
    summary: str = ""
    body: str = ""
    expanded: bool = False


@dataclass
class CompactionCell(TranscriptCell):
    kind: Literal["started", "completed", "warning"] = "started"
    summarized_items: int = 0
    removed_items: int = 0
    message: str = ""
    tokens_before: int | None = None
    tokens_after: int | None = None


@dataclass
class ApprovalCell(TranscriptCell):
    summary: str = ""
    tool_name: str = ""
    diff_preview: str | None = None
    resolved: bool = False


@dataclass
class ErrorCell(TranscriptCell):
    message: str = ""
    severity: Literal["error", "warning"] = "error"
    command: str | None = None


@dataclass
class TurnSummaryCell(TranscriptCell):
    status: str = ""
    model: str = ""
    cost: float | None = None
    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    commands_run: int = 0
    fallback_used: bool = False
    routing_note: str = ""


@dataclass
class ReasoningCell(TranscriptCell):
    text: str = ""
    expanded: bool = False


@dataclass
class ToolGroupCell(TranscriptCell):
    """Grouped parallel read tools."""
    tool_names: list[str] = field(default_factory=list)
    args_briefs: list[str] = field(default_factory=list)
    status: Literal["running", "completed"] = "running"
    expanded: bool = False


@dataclass
class WorkingCell(TranscriptCell):
    """Ephemeral in-transcript spinner between model steps."""

    message: str = "Agent is working…"


@dataclass
class SystemCell(TranscriptCell):
    text: str = ""


# Legacy alias for migration
TranscriptLine = SystemCell
