from __future__ import annotations

from agent.events import AgentEvent
from agent.models import Thread
from agent.recording.replay import replay_events_to_lines


def export_thread_markdown(thread: Thread, *, sandbox: str | None = None, backend: str | None = None) -> str:
    lines: list[str] = [
        f"# Thread: {thread.display_label()}",
        "",
        f"- **ID:** `{thread.id}`",
        f"- **CWD:** `{thread.cwd}`",
        f"- **Model:** {thread.model}",
    ]
    if thread.title:
        lines.append(f"- **Title:** {thread.title}")
    if thread.forked_from:
        lines.append(f"- **Forked from:** `{thread.forked_from}`")
    if sandbox:
        lines.append(f"- **Sandbox:** {sandbox}")
    if backend:
        lines.append(f"- **Execution backend:** {backend}")
    lines.extend(["", "---", ""])

    for i, turn in enumerate(thread.turns, start=1):
        lines.append(f"## Turn {i} ({turn.status})")
        lines.append("")
        for item in turn.items:
            if item.type == "userMessage":
                lines.append(f"**User:** {item.text}")
            elif item.type == "agentMessage":
                lines.append(f"**Assistant:** {item.text}")
            elif item.type == "commandExecution":
                extra = ""
                if item.backend:
                    extra = f" [{item.backend}"
                    if item.image:
                        extra += f" {item.image}"
                    extra += "]"
                lines.append(
                    f"**Command** ({item.status}){extra}: `{item.command}`"
                )
                if item.output:
                    lines.append(f"```\n{item.output[:2000]}\n```")
            elif item.type == "collabWorker":
                lines.append(
                    f"**Worker** `{item.worker_id}` ({item.status}, depth={item.depth}): {item.task}"
                )
                if item.worker_thread_id:
                    lines.append(f"- Worker thread: `{item.worker_thread_id}`")
                if item.execution_backend:
                    lines.append(f"- Backend: {item.execution_backend}")
                if item.summary:
                    lines.append(f"- Summary: {item.summary[:1000]}")
            elif item.type == "collabSpawn":
                lines.append(
                    f"**Worker spawn** ({item.status}): {item.task}"
                )
                if item.worker_thread_id:
                    lines.append(f"- Worker thread: `{item.worker_thread_id}`")
                if item.summary:
                    lines.append(f"- Summary: {item.summary[:1000]}")
            elif item.type == "webSearch":
                lines.append(f"**Web search** ({item.status}): {item.query}")
            elif item.type == "fileChange":
                lines.append(f"**File** ({item.status}): `{item.path}`")
            elif item.type == "contextCompaction":
                lines.append("*Context compaction checkpoint*")
            lines.append("")

    return "\n".join(lines)


def export_run_markdown(events: list[AgentEvent]) -> str:
    lines = ["# Run replay", ""]
    for line in replay_events_to_lines(events):
        lines.append(line)
    return "\n".join(lines)
