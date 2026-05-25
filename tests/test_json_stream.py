from __future__ import annotations

import json

from agent.events import AgentEvent
from agent.json_stream import JsonStreamHandler, normalize_event


def test_normalize_tool_pending() -> None:
    ev = AgentEvent(
        "tool.pending",
        thread_id="t1",
        turn_id="u1",
        data={"tool_name": "read_file", "arguments": {"path": "a.py"}},
    )
    norm = normalize_event(ev)
    assert norm.type == "tool.pending"
    assert norm.payload["tool_name"] == "read_file"


def test_json_stream_handler_sequence() -> None:
    handler = JsonStreamHandler()
    handler.handle(AgentEvent("turn.started", thread_id="t", turn_id="u"))
    handler.handle(
        AgentEvent(
            "tool.pending",
            thread_id="t",
            turn_id="u",
            data={"tool_name": "read_file", "arguments": {}},
        )
    )
    handler.handle(
        AgentEvent(
            "tool.completed",
            thread_id="t",
            turn_id="u",
            data={"tool_name": "read_file", "status": "completed"},
        )
    )
    handler.emit_run_summary(thread_id="t", turn_id="u", summary={"status": "completed"})
    assert len(handler.lines) == 4
    types = [json.loads(ln)["type"] for ln in handler.lines]
    assert types == ["turn.started", "tool.pending", "tool.completed", "run.summary"]


def test_item_completed_maps_to_command_execution() -> None:
    ev = AgentEvent(
        "item.completed",
        thread_id="t",
        turn_id="u",
        data={"item_type": "commandExecution", "item_id": "x", "status": "completed"},
    )
    norm = normalize_event(ev)
    assert norm.type == "command.execution"
