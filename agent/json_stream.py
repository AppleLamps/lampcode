from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent.events import AgentEvent

V2_EVENT_TYPES = frozenset(
    {
        "thread.started",
        "turn.started",
        "turn.completed",
        "agent.delta",
        "tool.pending",
        "tool.completed",
        "approval.requested",
        "approval.decided",
        "command.execution",
        "file.change",
        "error",
        "run.summary",
        "run.result",
        "permission.escalated",
        "permission.denied",
        "hook.failed",
        "user.input",
        "compaction.completed",
        "compaction.warning",
    }
)

_LEGACY_MAP: dict[str, str] = {
    "turn.started": "turn.started",
    "turn.completed": "turn.completed",
    "agent.delta": "agent.delta",
    "tool.pending": "tool.pending",
    "tool.completed": "tool.completed",
    "approval.requested": "approval.requested",
    "error": "error",
    "item.completed": "file.change",
    "commandExecution": "command.execution",
    "fileChange": "file.change",
    "sandbox.blocked": "error",
    "compaction": "compaction.completed",
    "compaction.completed": "compaction.completed",
    "compaction.warning": "compaction.warning",
}


@dataclass
class NormalizedEvent:
    type: str
    ts: str
    thread_id: str | None
    turn_id: str | None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "type": self.type,
                "ts": self.ts,
                "thread_id": self.thread_id,
                "turn_id": self.turn_id,
                "payload": self.payload,
            },
            ensure_ascii=False,
        )


def normalize_event(event: AgentEvent) -> NormalizedEvent:
    etype = _LEGACY_MAP.get(event.type, event.type)
    payload = dict(event.data)

    if event.type == "item.completed":
        item_type = payload.get("item_type", "")
        if item_type == "commandExecution":
            etype = "command.execution"
        elif item_type == "fileChange":
            etype = "file.change"

    if etype == "tool.pending":
        payload.setdefault("tool_name", payload.get("tool_name"))
    if etype == "turn.completed":
        payload.setdefault("status", payload.get("status"))

    return NormalizedEvent(
        type=etype,
        ts=event.timestamp,
        thread_id=event.thread_id,
        turn_id=event.turn_id,
        payload=payload,
    )


class JsonStreamHandler:
    """Emit Codex-like normalized JSONL on stdout."""

    def __init__(self, *, emit_legacy: bool = False) -> None:
        self._emit_legacy = emit_legacy
        self._lines: list[str] = []

    @property
    def lines(self) -> list[str]:
        return list(self._lines)

    def handle(self, event: AgentEvent) -> str | None:
        if self._emit_legacy:
            line = event.to_json()
        else:
            line = normalize_event(event).to_json()
        self._lines.append(line)
        return line

    def emit_run_summary(self, *, thread_id: str, turn_id: str, summary: dict[str, Any]) -> str:
        ev = NormalizedEvent(
            type="run.summary",
            ts=summary.get("ts", ""),
            thread_id=thread_id,
            turn_id=turn_id,
            payload=summary,
        )
        line = ev.to_json()
        self._lines.append(line)
        return line

    def emit_run_result(self, *, thread_id: str, turn_id: str, result: dict[str, Any]) -> str:
        ev = NormalizedEvent(
            type="run.result",
            ts=result.get("ts", ""),
            thread_id=thread_id,
            turn_id=turn_id,
            payload=result,
        )
        line = ev.to_json()
        self._lines.append(line)
        return line
