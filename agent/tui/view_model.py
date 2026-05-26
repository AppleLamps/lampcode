from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent.events import AgentEvent
from agent.loop import brief_args
from agent.models import (
    CommandExecutionItem,
    ContextCompactionItem,
    FileChangeItem,
    McpToolCallItem,
    PlanProposalItem,
    Thread,
    WebSearchItem,
)
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
    TurnSummaryCell,
    UserMessageCell,
    WorkingCell,
)
from agent.turn_stats import aggregate_turn_stats


@dataclass
class ThreadListEntry:
    thread_id: str
    label: str
    updated_at: str


@dataclass
class TuiState:
    transcript: list[TranscriptCell] = field(default_factory=list)
    threads: list[ThreadListEntry] = field(default_factory=list)
    status_line: str = ""
    pending_approval_summary: str | None = None
    pending_approval_diff: str | None = None
    pending_approval_tool: str | None = None
    assistant_buffer: str = ""
    pending_tool_cell_id: str | None = None
    pending_read_batch: list[str] = field(default_factory=list)
    turn_model: str = ""
    turn_cost: float | None = None
    turn_fallback_used: bool = False
    routing_note: str = ""
    last_user_prompt: str = ""
    turn_active: bool = False
    working_frame: int = 0


# Legacy compat
TranscriptLine = SystemCell


def filter_threads_by_cwd(threads: list[Thread], cwd: Path) -> list[Thread]:
    target = str(cwd.resolve())
    filtered = [t for t in threads if str(Path(t.cwd).resolve()) == target]
    filtered.sort(key=lambda t: t.updated_at, reverse=True)
    return filtered


def filter_session_threads(threads: list[Thread], cwd: Path) -> list[Thread]:
    """User-facing sessions only — hide worker forks from pickers and resume UI."""
    return [t for t in filter_threads_by_cwd(threads, cwd) if not t.forked_from]


def threads_to_entries(threads: list[Thread]) -> list[ThreadListEntry]:
    return [
        ThreadListEntry(
            thread_id=t.id,
            label=t.display_label(),
            updated_at=t.updated_at[:19],
        )
        for t in threads
    ]


def _last_assistant_cell(state: TuiState) -> AssistantMessageCell | None:
    for cell in reversed(state.transcript):
        if isinstance(cell, AssistantMessageCell):
            return cell
    return None


def _remove_working_cells(state: TuiState) -> None:
    state.transcript = [c for c in state.transcript if not isinstance(c, WorkingCell)]


def _set_working(state: TuiState, message: str) -> None:
    for cell in state.transcript:
        if isinstance(cell, WorkingCell):
            cell.message = message
            return
    state.transcript.append(WorkingCell(message=message))


def _flush_assistant_buffer(state: TuiState) -> None:
    if state.assistant_buffer:
        text = state.assistant_buffer.rstrip()
        last = _last_assistant_cell(state)
        if last and last.text.strip() == text:
            state.assistant_buffer = ""
            return
        if last and text.startswith(last.text.strip()) and len(text) > len(last.text.strip()):
            last.text = text
            state.assistant_buffer = ""
            return
        state.transcript.append(AssistantMessageCell(text=text, streaming=False))
        state.assistant_buffer = ""


def _args_brief(tool_name: str, args: dict) -> str:
    return brief_args(tool_name, args)


def _find_pending_tool_cell(
    state: TuiState, tool_name: str
) -> ToolExecCell | PatchCell | ToolGroupCell | None:
    if state.pending_tool_cell_id:
        for cell in reversed(state.transcript):
            if cell.cell_id == state.pending_tool_cell_id:
                if isinstance(cell, ToolGroupCell) and cell.status == "running":
                    return cell
                if isinstance(cell, (ToolExecCell, PatchCell)) and cell.status == "running":
                    return cell
        state.pending_tool_cell_id = None
    for cell in reversed(state.transcript):
        if isinstance(cell, ToolGroupCell) and tool_name == "read_file" and cell.status == "running":
            return cell
        if isinstance(cell, ToolExecCell) and cell.tool_name == tool_name and cell.status == "running":
            return cell
        if isinstance(cell, PatchCell) and tool_name == "apply_patch" and cell.status == "running":
            return cell
    return None


def _maybe_group_read_tools(state: TuiState, tool_name: str, args_brief: str) -> None:
    """Group parallel read_file tools into ToolGroupCell."""
    if tool_name != "read_file":
        state.pending_read_batch = []
        return
    state.pending_read_batch.append(args_brief)
    if len(state.pending_read_batch) >= 2:
        # Replace individual pending cells with a group if we have 2+ reads in batch
        batch = state.pending_read_batch[:]
        # Remove individual pending read cells from this batch
        to_remove: list[str] = []
        read_count = 0
        for cell in reversed(state.transcript):
            if isinstance(cell, ToolExecCell) and cell.tool_name == "read_file" and cell.status == "running":
                to_remove.append(cell.cell_id)
                read_count += 1
                if read_count >= len(batch):
                    break
        if read_count >= 2:
            state.transcript = [c for c in state.transcript if c.cell_id not in to_remove]
            group = ToolGroupCell(
                tool_names=["read_file"] * len(batch),
                args_briefs=batch,
                status="running",
            )
            state.transcript.append(group)
            state.pending_tool_cell_id = group.cell_id
        state.pending_read_batch = []


def apply_event_to_state(state: TuiState, event: AgentEvent) -> TuiState:
    etype = event.type
    data = event.data

    if etype == "agent.delta":
        _remove_working_cells(state)
        state.assistant_buffer += data.get("text", "")

    elif etype == "agent.reasoning":
        text = data.get("text", "")
        if text:
            state.transcript.append(ReasoningCell(text=text.rstrip(), expanded=False))

    elif etype == "turn.started":
        state.status_line = "Turn running..."
        state.pending_read_batch = []
        state.turn_active = True
        _set_working(state, "Thinking…")

    elif etype == "turn.completed":
        _remove_working_cells(state)
        _flush_assistant_buffer(state)
        state.turn_active = False
        status = data.get("status", "")
        state.status_line = f"Turn {status}"
        state.pending_approval_summary = None
        state.pending_approval_diff = None
        state.pending_approval_tool = None
        state.pending_tool_cell_id = None
        state.pending_read_batch = []
        stats = aggregate_turn_stats_from_data(data)
        state.transcript.append(
            TurnSummaryCell(
                status=status,
                model=state.turn_model or data.get("model", ""),
                cost=state.turn_cost,
                files_changed=stats.get("files_touched", 0),
                lines_added=stats.get("lines_added", 0),
                lines_removed=stats.get("lines_removed", 0),
                commands_run=stats.get("commands_run", 0),
                fallback_used=state.turn_fallback_used,
                routing_note=state.routing_note,
            )
        )

    elif etype == "tool.pending":
        _remove_working_cells(state)
        _flush_assistant_buffer(state)
        name = data.get("tool_name", "")
        args = data.get("arguments", {})
        brief = _args_brief(name, args)
        source = data.get("source", "builtin")

        if name == "apply_patch":
            from agent.tui.patch_preview import patch_files_from_arguments

            cell: ToolExecCell | PatchCell = PatchCell(
                status="running",
                diff_text="",
                files=patch_files_from_arguments(args),
            )
        else:
            cell = ToolExecCell(
                tool_name=name,
                args_brief=brief,
                status="running",
                source=source,
            )
        state.transcript.append(cell)
        state.pending_tool_cell_id = cell.cell_id
        _maybe_group_read_tools(state, name, brief)

    elif etype == "tool.completed":
        if state.turn_active:
            _set_working(state, "Working on next step…")
        name = data.get("tool_name", "")
        status = data.get("status", "completed")
        mapped_status = status if status in ("completed", "failed", "denied", "blocked") else "completed"
        output = data.get("output") or data.get("summary")
        diff_preview = data.get("diff_preview")

        pending = _find_pending_tool_cell(state, name)
        if pending:
            if isinstance(pending, ToolGroupCell):
                pending.status = "completed" if mapped_status == "completed" else "running"
            else:
                pending.status = mapped_status  # type: ignore[assignment]
            if isinstance(pending, ToolExecCell):
                pending.output = output
                pending.exit_code = data.get("exit_code")
                pending.duration_ms = data.get("duration_ms")
            elif isinstance(pending, PatchCell) and diff_preview:
                pending.diff_text = diff_preview
            state.pending_tool_cell_id = None
        elif name == "apply_patch":
            state.transcript.append(
                PatchCell(
                    diff_text=diff_preview or output or "",
                    status=mapped_status,  # type: ignore[arg-type]
                )
            )
        else:
            state.transcript.append(
                ToolExecCell(
                    tool_name=name,
                    args_brief="",
                    status=mapped_status,  # type: ignore[arg-type]
                    output=output,
                    exit_code=data.get("exit_code"),
                    duration_ms=data.get("duration_ms"),
                    source=data.get("source", "builtin"),
                )
            )
        state.pending_read_batch = []

    elif etype == "plan.proposed":
        _flush_assistant_buffer(state)
        state.transcript.append(
            PlanCell(
                summary=data.get("summary", ""),
                body=data.get("text", ""),
            )
        )

    elif etype == "compaction":
        state.transcript.append(
            CompactionCell(
                kind="started",
                summarized_items=data.get("summarized_items", 0),
            )
        )

    elif etype == "compaction.completed":
        state.transcript.append(
            CompactionCell(
                kind="completed",
                removed_items=data.get("removed_items", 0),
                tokens_before=data.get("estimated_tokens_before"),
                tokens_after=data.get("estimated_tokens_after"),
            )
        )

    elif etype == "compaction.warning":
        state.transcript.append(
            CompactionCell(kind="warning", message=data.get("message", ""))
        )

    elif etype == "approval.requested":
        summary = data.get("summary", "")
        tool_name = data.get("tool_name", "")
        state.pending_approval_summary = summary
        state.pending_approval_tool = tool_name
        state.pending_approval_diff = data.get("diff_preview")
        _set_working(state, "Waiting for your approval…")

    elif etype == "sandbox.blocked":
        state.transcript.append(
            ErrorCell(
                message=f"sandbox blocked ({data.get('mode', '')}): {data.get('reason', '')}",
                severity="warning",
                command=data.get("command"),
            )
        )

    elif etype == "error":
        state.transcript.append(
            ErrorCell(message=data.get("message", ""), severity="error")
        )

    elif etype == "isolation.applied":
        state.transcript.append(
            SystemCell(
                text=(
                    f"[isolation] pid={data.get('pid')} "
                    f"stripped_env={data.get('stripped_env_count')}"
                )
            )
        )

    elif etype == "execution.sync.started":
        state.transcript.append(
            SystemCell(
                text=(
                    f"[sync {data.get('direction', '')}] "
                    f"{data.get('transport', '')} "
                    f"~{data.get('bytes_estimated', 0)} bytes"
                )
            )
        )

    elif etype == "execution.sync.completed":
        state.transcript.append(
            SystemCell(
                text=(
                    f"[sync done] {data.get('files', 0)} files, "
                    f"{data.get('bytes', 0)} bytes, {data.get('duration_ms', 0)}ms"
                )
            )
        )

    elif etype == "execution.sync.plan":
        counts = data.get("counts", {})
        state.transcript.append(
            SystemCell(
                text=(
                    f"[sync plan] push={counts.get('push', 0)} "
                    f"pull={counts.get('pull', 0)} "
                    f"conflicts={len(data.get('conflicts', []))}"
                )
            )
        )

    elif etype == "execution.sync.failed":
        state.transcript.append(
            SystemCell(text=f"[sync failed] {data.get('reason', '')}")
        )

    elif etype in ("execution.ssh.pool.acquire", "execution.ssh.pool.release"):
        action = "acquired" if etype.endswith("acquire") else "released"
        state.transcript.append(
            SystemCell(text=f"[ssh pool] {action} session to {data.get('host', '')}")
        )

    elif etype == "collab.checkpoint.saved":
        state.transcript.append(
            SystemCell(text=f"[checkpoint] saved {data.get('path', '')}")
        )

    elif etype == "collab.worker.started":
        state.transcript.append(
            ToolExecCell(
                tool_name="worker",
                args_brief=f"{data.get('worker_id', '')}: {data.get('task', '')[:80]}",
                status="running",
            )
        )

    elif etype == "collab.worker.completed":
        state.transcript.append(
            ToolExecCell(
                tool_name="worker",
                args_brief=data.get("worker_id", ""),
                status="completed" if data.get("status") != "failed" else "failed",
            )
        )

    return state


def aggregate_turn_stats_from_data(data: dict) -> dict:
    stats = data.get("stats")
    if isinstance(stats, dict):
        return stats
    return {}


def handle_approval_key(key: str) -> tuple[bool | None, str]:
    """Return (approved, message). None means invalid key."""
    normalized = key.strip()
    if not normalized:
        return None, "empty key"
    if normalized.lower() in ("y", "yes"):
        return True, "approved"
    if normalized.lower() in ("n", "no"):
        return False, "denied"
    if normalized.lower() in ("a", "all"):
        return True, "approved for turn"
    if normalized == "A":
        return True, "approved for session"
    return None, f"invalid key: {normalized!r}"


def approval_key_to_response(key: str) -> str | None:
    approved, _ = handle_approval_key(key)
    if approved is None:
        return None
    if key.strip() == "A":
        return "A"
    if key.strip().lower() in ("a", "all"):
        return "a"
    if approved:
        return "y"
    return "n"


def _file_change_to_patch_cell(item: FileChangeItem) -> PatchCell:
    status_map = {
        "completed": "completed",
        "failed": "failed",
        "denied": "denied",
        "pending": "running",
        "approved": "running",
    }
    return PatchCell(
        files=[FileChange(path=item.path, change_type=item.change_type or "update")],
        diff_text=item.diff_snippet or "",
        status=status_map.get(item.status, "completed"),  # type: ignore[arg-type]
    )


def _command_to_tool_cell(item: CommandExecutionItem) -> ToolExecCell:
    status_map = {
        "completed": "completed",
        "failed": "failed",
        "denied": "denied",
        "pending": "running",
        "approved": "running",
        "running": "running",
    }
    return ToolExecCell(
        tool_name="run_command",
        args_brief=item.command,
        status=status_map.get(item.status, "completed"),  # type: ignore[arg-type]
        output=item.output,
        exit_code=item.exit_code,
        duration_ms=item.duration_ms,
    )


def _mcp_to_tool_cell(item: McpToolCallItem) -> ToolExecCell:
    status_map = {
        "completed": "completed",
        "failed": "failed",
        "denied": "denied",
        "pending": "running",
        "approved": "running",
        "running": "running",
    }
    brief = f"{item.server}/{item.tool}"
    return ToolExecCell(
        tool_name=f"mcp__{item.server}__{item.tool}",
        args_brief=brief,
        status=status_map.get(item.status, "completed"),  # type: ignore[arg-type]
        output=item.output or item.error,
        duration_ms=item.duration_ms,
        source="mcp",
    )


def _web_search_to_tool_cell(item: WebSearchItem) -> ToolExecCell:
    status_map = {
        "completed": "completed",
        "failed": "failed",
        "denied": "denied",
        "pending": "running",
        "approved": "running",
    }
    results_brief = "; ".join(r.title[:40] for r in item.results[:3])
    output = results_brief or item.error
    return ToolExecCell(
        tool_name="web_search",
        args_brief=item.query,
        status=status_map.get(item.status, "completed"),  # type: ignore[arg-type]
        output=output,
    )


def thread_transcript_from_store(thread: Thread) -> list[TranscriptCell]:
    lines: list[TranscriptCell] = []
    for turn in thread.turns:
        for item in turn.items:
            if item.type == "userMessage":
                lines.append(UserMessageCell(text=item.text))
            elif item.type == "agentMessage":
                lines.append(AssistantMessageCell(text=item.text))
            elif item.type == "commandExecution":
                lines.append(_command_to_tool_cell(item))
            elif item.type == "fileChange":
                lines.append(_file_change_to_patch_cell(item))
            elif item.type == "planProposal":
                lines.append(PlanCell(summary="Plan proposal", body=item.text))
            elif item.type == "contextCompaction":
                lines.append(
                    CompactionCell(
                        kind="completed",
                        removed_items=item.summarized_items or 0,
                    )
                )
            elif item.type == "mcpToolCall":
                lines.append(_mcp_to_tool_cell(item))
            elif item.type == "webSearch":
                lines.append(_web_search_to_tool_cell(item))
            elif item.type == "collabWorker":
                lines.append(
                    ToolExecCell(
                        tool_name="worker",
                        args_brief=f"{item.worker_id} ({item.status}): {item.task}",
                        status="completed" if item.status == "completed" else "failed",
                    )
                )
            elif item.type == "workspaceSync":
                lines.append(
                    SystemCell(
                        text=f"sync {item.direction} ({item.status}): {item.summary[:120]}"
                    )
                )
        # Turn summary per turn
        if turn.status in ("completed", "cancelled", "failed"):
            stats = aggregate_turn_stats(turn)
            cost = turn.usage.estimated_cost_usd
            lines.append(
                TurnSummaryCell(
                    status=turn.status,
                    model=turn.usage.model_used or thread.model,
                    cost=cost,
                    files_changed=stats.files_touched,
                    lines_added=stats.lines_added,
                    lines_removed=stats.lines_removed,
                    commands_run=stats.commands_run,
                )
            )
    return lines


def merge_run_events_into_state(state: TuiState, events: list[AgentEvent]) -> TuiState:
    for event in events:
        state = apply_event_to_state(state, event)
    return state
