import json

from agent.events import AgentEvent, EventEmitter


def test_event_to_json() -> None:
    event = AgentEvent(
        type="agent.delta",
        thread_id="t1",
        turn_id="turn1",
        data={"text": "hello"},
    )
    parsed = json.loads(event.to_json())
    assert parsed["type"] == "agent.delta"
    assert parsed["data"]["text"] == "hello"


def test_turn_started_event() -> None:
    events: list[AgentEvent] = []
    emitter = EventEmitter(events.append)
    emitter.turn_started("thread-1", "turn-1")
    assert events[0].type == "turn.started"
    assert events[0].thread_id == "thread-1"


def test_compaction_event() -> None:
    events: list[AgentEvent] = []
    emitter = EventEmitter(events.append)
    emitter.compaction("thread-1", 42)
    assert events[0].type == "compaction"
    assert events[0].data["summarized_items"] == 42
