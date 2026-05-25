from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

EventHandler = Callable[["AgentEvent"], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentEvent:
    type: str
    thread_id: str | None = None
    turn_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_utc_now)

    def to_json(self) -> str:
        return json.dumps(
            {
                "type": self.type,
                "thread_id": self.thread_id,
                "turn_id": self.turn_id,
                "data": self.data,
                "timestamp": self.timestamp,
            },
            ensure_ascii=False,
        )


class EventEmitter:
    def __init__(self, handler: EventHandler | None = None) -> None:
        self._handler = handler

    def emit(self, event: AgentEvent) -> None:
        if self._handler:
            self._handler(event)

    def turn_started(self, thread_id: str, turn_id: str) -> None:
        self.emit(AgentEvent("turn.started", thread_id=thread_id, turn_id=turn_id))

    def turn_completed(self, thread_id: str, turn_id: str, status: str) -> None:
        self.emit(
            AgentEvent(
                "turn.completed",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"status": status},
            )
        )

    def agent_delta(self, thread_id: str, turn_id: str, text: str) -> None:
        self.emit(
            AgentEvent(
                "agent.delta",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"text": text},
            )
        )

    def tool_pending(
        self, thread_id: str, turn_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> None:
        self.emit(
            AgentEvent(
                "tool.pending",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"tool_name": tool_name, "arguments": arguments},
            )
        )

    def approval_requested(
        self, thread_id: str, turn_id: str, tool_name: str, summary: str
    ) -> None:
        self.emit(
            AgentEvent(
                "approval.requested",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"tool_name": tool_name, "summary": summary},
            )
        )

    def item_started(
        self, thread_id: str, turn_id: str, item_type: str, item_id: str
    ) -> None:
        self.emit(
            AgentEvent(
                "item.started",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"item_type": item_type, "item_id": item_id},
            )
        )

    def item_completed(
        self,
        thread_id: str,
        turn_id: str,
        item_type: str,
        item_id: str,
        status: str,
    ) -> None:
        self.emit(
            AgentEvent(
                "item.completed",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"item_type": item_type, "item_id": item_id, "status": status},
            )
        )

    def compaction(self, thread_id: str, summarized_items: int) -> None:
        self.emit(
            AgentEvent(
                "compaction",
                thread_id=thread_id,
                data={"summarized_items": summarized_items},
            )
        )

    def error(self, thread_id: str | None, message: str) -> None:
        self.emit(
            AgentEvent(
                "error",
                thread_id=thread_id,
                data={"message": message},
            )
        )
