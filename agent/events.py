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

    def turn_completed(
        self,
        thread_id: str,
        turn_id: str,
        status: str,
        *,
        estimated_tokens: int | None = None,
    ) -> None:
        data: dict[str, Any] = {"status": status}
        if estimated_tokens is not None:
            data["estimated_tokens"] = estimated_tokens
        self.emit(
            AgentEvent(
                "turn.completed",
                thread_id=thread_id,
                turn_id=turn_id,
                data=data,
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
        self, thread_id: str, turn_id: str, tool_name: str, arguments: dict[str, Any], *, source: str = "builtin"
    ) -> None:
        self.emit(
            AgentEvent(
                "tool.pending",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"tool_name": tool_name, "arguments": arguments, "source": source},
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

    def compaction_completed(
        self,
        thread_id: str,
        *,
        removed_items: int,
        summary_chars: int,
        estimated_tokens_before: int,
        estimated_tokens_after: int,
    ) -> None:
        self.emit(
            AgentEvent(
                "compaction.completed",
                thread_id=thread_id,
                data={
                    "removed_items": removed_items,
                    "summary_chars": summary_chars,
                    "estimated_tokens_before": estimated_tokens_before,
                    "estimated_tokens_after": estimated_tokens_after,
                },
            )
        )

    def sandbox_blocked(
        self,
        thread_id: str,
        turn_id: str,
        *,
        mode: str,
        reason: str,
        command: str | None = None,
    ) -> None:
        self.emit(
            AgentEvent(
                "sandbox.blocked",
                thread_id=thread_id,
                turn_id=turn_id,
                data={
                    "mode": mode,
                    "reason": reason,
                    "command": command,
                },
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

    def mcp_server_connected(self, thread_id: str | None, server: str) -> None:
        self.emit(
            AgentEvent(
                "mcp.server.connected",
                thread_id=thread_id,
                data={"server": server},
            )
        )

    def mcp_server_failed(self, thread_id: str | None, server: str, error: str) -> None:
        self.emit(
            AgentEvent(
                "mcp.server.failed",
                thread_id=thread_id,
                data={"server": server, "error": error},
            )
        )

    def project_rules_loaded(self, thread_id: str | None, path: str, char_count: int) -> None:
        self.emit(
            AgentEvent(
                "project.rules.loaded",
                thread_id=thread_id,
                data={"path": path, "char_count": char_count},
            )
        )

    def skill_activation(self, thread_id: str, turn_id: str, skills: list[str]) -> None:
        self.emit(
            AgentEvent(
                "skill.activation",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"skills": skills},
            )
        )

    def tool_completed(
        self,
        thread_id: str,
        turn_id: str,
        tool_name: str,
        status: str,
        *,
        source: str = "builtin",
    ) -> None:
        self.emit(
            AgentEvent(
                "tool.completed",
                thread_id=thread_id,
                turn_id=turn_id,
                data={"tool_name": tool_name, "status": status, "source": source},
            )
        )
