from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent.events import AgentEvent
from agent.models import Thread


@dataclass
class TranscriptLine:
    role: str
    text: str
    pending_approval: bool = False


@dataclass
class ThreadListEntry:
    thread_id: str
    label: str
    updated_at: str


@dataclass
class TuiState:
    transcript: list[TranscriptLine] = field(default_factory=list)
    threads: list[ThreadListEntry] = field(default_factory=list)
    status_line: str = ""
    pending_approval_summary: str | None = None
    assistant_buffer: str = ""


def filter_threads_by_cwd(threads: list[Thread], cwd: Path) -> list[Thread]:
    target = str(cwd.resolve())
    filtered = [t for t in threads if str(Path(t.cwd).resolve()) == target]
    filtered.sort(key=lambda t: t.updated_at, reverse=True)
    return filtered


def threads_to_entries(threads: list[Thread]) -> list[ThreadListEntry]:
    return [
        ThreadListEntry(
            thread_id=t.id,
            label=t.display_label(),
            updated_at=t.updated_at[:19],
        )
        for t in threads
    ]


def apply_event_to_state(state: TuiState, event: AgentEvent) -> TuiState:
    etype = event.type
    data = event.data

    if etype == "agent.delta":
        state.assistant_buffer += data.get("text", "")
    elif etype == "turn.completed":
        if state.assistant_buffer:
            state.transcript.append(
                TranscriptLine(role="assistant", text=state.assistant_buffer.rstrip())
            )
            state.assistant_buffer = ""
        status = data.get("status", "")
        state.status_line = f"Turn {status}"
        state.pending_approval_summary = None
    elif etype == "turn.started":
        state.status_line = "Turn running..."
    elif etype == "tool.pending":
        if state.assistant_buffer:
            state.transcript.append(
                TranscriptLine(role="assistant", text=state.assistant_buffer.rstrip())
            )
            state.assistant_buffer = ""
        name = data.get("tool_name", "")
        args = data.get("arguments", {})
        brief = args.get("cmd") or args.get("query") or args.get("path") or str(args)[:80]
        state.transcript.append(TranscriptLine(role="tool", text=f"{name}: {brief}"))
    elif etype == "approval.requested":
        summary = data.get("summary", "")
        state.pending_approval_summary = summary
        state.transcript.append(
            TranscriptLine(
                role="approval",
                text=f"{summary} [y/n/a/A]",
                pending_approval=True,
            )
        )
    elif etype == "sandbox.blocked":
        state.transcript.append(
            TranscriptLine(
                role="system",
                text=f"[sandbox blocked] {data.get('reason', '')}",
            )
        )
    elif etype == "isolation.applied":
        state.transcript.append(
            TranscriptLine(
                role="system",
                text=(
                    f"[isolation] pid={data.get('pid')} "
                    f"stripped_env={data.get('stripped_env_count')}"
                ),
            )
        )
    elif etype == "error":
        state.transcript.append(
            TranscriptLine(role="system", text=f"Error: {data.get('message', '')}")
        )
    elif etype == "execution.sync.started":
        state.transcript.append(
            TranscriptLine(
                role="system",
                text=(
                    f"[sync {data.get('direction', '')}] "
                    f"{data.get('transport', '')} "
                    f"~{data.get('bytes_estimated', 0)} bytes"
                ),
            )
        )
    elif etype == "execution.sync.completed":
        state.transcript.append(
            TranscriptLine(
                role="system",
                text=(
                    f"[sync done] {data.get('files', 0)} files, "
                    f"{data.get('bytes', 0)} bytes, {data.get('duration_ms', 0)}ms"
                ),
            )
        )
    elif etype == "execution.sync.failed":
        state.transcript.append(
            TranscriptLine(
                role="system",
                text=f"[sync failed] {data.get('reason', '')}",
            )
        )
    elif etype in ("execution.ssh.pool.acquire", "execution.ssh.pool.release"):
        action = "acquired" if etype.endswith("acquire") else "released"
        state.transcript.append(
            TranscriptLine(
                role="system",
                text=f"[ssh pool] {action} session to {data.get('host', '')}",
            )
        )
    elif etype == "collab.checkpoint.saved":
        state.transcript.append(
            TranscriptLine(
                role="system",
                text=f"[checkpoint] saved {data.get('path', '')}",
            )
        )
    elif etype == "collab.worker.started":
        state.transcript.append(
            TranscriptLine(
                role="tool",
                text=f"worker {data.get('worker_id', '')} started: {data.get('task', '')[:80]}",
            )
        )
    elif etype == "collab.worker.completed":
        state.transcript.append(
            TranscriptLine(
                role="tool",
                text=(
                    f"worker {data.get('worker_id', '')} "
                    f"{data.get('status', 'completed')}"
                ),
            )
        )

    return state


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


def thread_transcript_from_store(thread: Thread) -> list[TranscriptLine]:
    lines: list[TranscriptLine] = []
    for turn in thread.turns:
        for item in turn.items:
            if item.type == "userMessage":
                lines.append(TranscriptLine(role="user", text=item.text))
            elif item.type == "agentMessage":
                lines.append(TranscriptLine(role="assistant", text=item.text))
            elif item.type == "commandExecution":
                lines.append(
                    TranscriptLine(
                        role="tool",
                        text=f"run_command ({item.status}): {item.command}",
                    )
                )
            elif item.type == "collabWorker":
                lines.append(
                    TranscriptLine(
                        role="tool",
                        text=f"worker {item.worker_id} ({item.status}): {item.task}",
                    )
                )
            elif item.type == "workspaceSync":
                lines.append(
                    TranscriptLine(
                        role="system",
                        text=f"sync {item.direction} ({item.status}): {item.summary[:120]}",
                    )
                )
    return lines


def merge_run_events_into_state(state: TuiState, events: list[AgentEvent]) -> TuiState:
    for event in events:
        state = apply_event_to_state(state, event)
    return state
