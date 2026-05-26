"""Human-readable action log for debugging agent behavior."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.events import AgentEvent
from agent.settings import ActionLogSettings


def action_log_dir(settings: ActionLogSettings) -> Path:
    return Path(settings.dir).expanduser()


def thread_log_path(settings: ActionLogSettings, thread_id: str) -> Path:
    return action_log_dir(settings) / f"{thread_id}.log"


def project_action_log_path(cwd: Path) -> Path:
    return cwd.resolve() / ".agent-cli" / "action.log"


def _brief_args(arguments: dict[str, Any]) -> str:
    if not arguments:
        return ""
    for key in ("cmd", "command", "query", "path", "file_path", "pattern"):
        val = arguments.get(key)
        if val:
            text = str(val).replace("\n", " ")
            return text[:200] + ("…" if len(text) > 200 else "")
    raw = json.dumps(arguments, ensure_ascii=False)
    return raw[:200] + ("…" if len(raw) > 200 else "")


def _format_assistant(text: str) -> str:
    body = text.strip().replace("\r\n", "\n")
    if "\n" in body:
        indented = "\n".join(f"  {line}" for line in body.splitlines())
        return f"assistant:\n{indented}"
    return f"assistant: {body}"


def format_action_log_line(event: AgentEvent) -> str | None:
    """Format one event as a single log line (no agent.delta — buffered separately)."""
    etype = event.type
    data = event.data

    if etype in ("agent.delta",):
        return None

    if etype == "user.message":
        text = (data.get("text") or "").strip().replace("\r\n", "\n")
        if "\n" in text:
            indented = "\n".join(f"  {line}" for line in text.splitlines())
            return f"user:\n{indented}"
        return f"user: {text}"

    if etype == "turn.started":
        return "--- turn started ---"

    if etype == "turn.completed":
        status = data.get("status", "?")
        tokens = data.get("estimated_tokens")
        suffix = f" tokens_est={tokens}" if tokens is not None else ""
        return f"--- turn {status}{suffix} ---"

    if etype == "thread.started":
        title = data.get("title") or ""
        return f"thread started{f' title={title!r}' if title else ''}"

    if etype == "agent.reasoning":
        text = (data.get("text") or "").strip().replace("\n", " ")
        return f"reasoning: {text[:300]}{'…' if len(text) > 300 else ''}"

    if etype == "tool.pending":
        name = data.get("tool_name", "")
        brief = _brief_args(data.get("arguments") or {})
        source = data.get("source", "")
        src = f" ({source})" if source and source != "builtin" else ""
        return f"tool pending: {name}{src} {brief}".rstrip()

    if etype == "tool.completed":
        name = data.get("tool_name", "")
        status = data.get("status", "")
        output = (data.get("output") or data.get("summary") or "")[:120]
        out = f" output={output!r}" if output else ""
        return f"tool done: {name} status={status}{out}"

    if etype == "approval.requested":
        return f"approval requested: {data.get('summary') or data.get('tool_name', '')}"

    if etype == "approval.decided":
        return f"approval decided: {data.get('decision', data.get('status', ''))}"

    if etype == "item.completed":
        return (
            f"item {data.get('item_type')} id={str(data.get('item_id', ''))[:8]} "
            f"status={data.get('status', '')}"
        )

    if etype == "item.started":
        return f"item started: {data.get('item_type')} id={str(data.get('item_id', ''))[:8]}"

    if etype in ("compaction", "compaction.completed"):
        before = data.get("estimated_tokens_before")
        after = data.get("estimated_tokens_after")
        tok = f" tokens {before}→{after}" if before is not None and after is not None else ""
        return f"compaction removed={data.get('removed_items', data.get('summarized_items', '?'))}{tok}"

    if etype == "compaction.warning":
        return f"compaction warning: {data.get('message', '')}"

    if etype == "error":
        return f"ERROR: {data.get('message', data)}"

    if etype == "sandbox.blocked":
        return (
            f"sandbox blocked ({data.get('mode')}): {data.get('reason', '')} "
            f"cmd={data.get('command', '')!r}"
        )

    if etype == "skill.activation":
        skills = ", ".join(data.get("skills") or [])
        return f"skills activated: {skills}"

    if etype == "plan.proposed":
        return f"plan proposed: {(data.get('summary') or '')[:120]}"

    if etype == "execution.backend.selected":
        return f"execution backend: {data.get('backend')}" + (
            f" image={data.get('image')}" if data.get("image") else ""
        )

    if etype.startswith("execution."):
        brief = json.dumps(data, ensure_ascii=False)[:160]
        return f"{etype}: {brief}"

    if etype.startswith("collab.") or etype.startswith("multi_agent."):
        brief = json.dumps(data, ensure_ascii=False)[:160]
        return f"{etype}: {brief}"

    if etype.startswith("context."):
        return f"{etype}: {json.dumps(data, ensure_ascii=False)[:160]}"

    # Generic fallback for any other event
    if data:
        brief = json.dumps(data, ensure_ascii=False)[:240]
        return f"{etype}: {brief}"
    return etype


class ActionLogHandler:
    """Append every agent action to per-thread and optional project action.log files."""

    def __init__(
        self,
        settings: ActionLogSettings,
        *,
        project_cwd: Path | None = None,
        model: str | None = None,
    ) -> None:
        self._settings = settings
        self._project_cwd = project_cwd.resolve() if project_cwd else None
        self._model = model
        self._buffers: dict[tuple[str, str], str] = {}

    def handle(self, event: AgentEvent) -> None:
        if not self._settings.enabled or not event.thread_id:
            return

        thread_id = event.thread_id
        turn_id = event.turn_id or ""

        if event.type == "agent.delta":
            key = (thread_id, turn_id)
            self._buffers[key] = self._buffers.get(key, "") + event.data.get("text", "")
            return

        lines: list[str] = []
        if event.type == "turn.completed":
            key = (thread_id, turn_id)
            buf = self._buffers.pop(key, "")
            if buf.strip():
                lines.append(_format_assistant(buf))

        formatted = format_action_log_line(event)
        if formatted:
            lines.append(formatted)

        for line in lines:
            self._write_line(thread_id, line, event.timestamp)

    def _write_line(self, thread_id: str, line: str, timestamp: str) -> None:
        row = f"{timestamp} {line}\n"
        for path in self._target_paths(thread_id):
            path.parent.mkdir(parents=True, exist_ok=True)
            new_file = not path.exists() or path.stat().st_size == 0
            with path.open("a", encoding="utf-8") as f:
                if new_file:
                    f.write(self._session_header(thread_id))
                f.write(row)

    def _target_paths(self, thread_id: str) -> list[Path]:
        paths = [thread_log_path(self._settings, thread_id)]
        if self._settings.mirror_to_project and self._project_cwd is not None:
            paths.append(project_action_log_path(self._project_cwd))
        return paths

    def _session_header(self, thread_id: str) -> str:
        parts = [
            f"===== agent-cli action log thread={thread_id} =====",
        ]
        if self._project_cwd is not None:
            parts.append(f"cwd: {self._project_cwd}")
        if self._model:
            parts.append(f"model: {self._model}")
        parts.append("")
        return "\n".join(parts) + "\n"
