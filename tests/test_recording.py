from pathlib import Path

from agent.events import AgentEvent, EventBus, RecordingHandler, build_event_emitter
from agent.recording.replay import format_run_human, replay_events_to_lines
from agent.recording.store import RunStore


def test_event_bus_fanout() -> None:
    seen: list[str] = []

    def h1(event: AgentEvent) -> None:
        seen.append(f"h1:{event.type}")

    def h2(event: AgentEvent) -> None:
        seen.append(f"h2:{event.type}")

    bus = EventBus([h1, h2])
    bus.emit(AgentEvent("turn.started", thread_id="t1", turn_id="u1"))
    assert seen == ["h1:turn.started", "h2:turn.started"]


def test_recording_writes_events(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path / "runs")
    recorder = RecordingHandler(store=store, keep_last_runs_per_thread=10)
    event = AgentEvent("tool.pending", thread_id="th1", turn_id="tu1", data={"x": 1})
    recorder.handle(event)
    loaded = store.load_events("tu1", thread_id="th1")
    assert len(loaded) == 1
    assert loaded[0].type == "tool.pending"


def test_recording_prunes_old_runs(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path / "runs")
    for i in range(5):
        store.append_event(
            "th1",
            f"turn{i}",
            AgentEvent("turn.completed", thread_id="th1", turn_id=f"turn{i}"),
        )
    removed = store.prune_thread("th1", keep_last=2)
    assert removed == 3
    assert len(store.list_runs("th1")) == 2


def test_build_event_emitter_with_recording(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path / "runs")
    captured: list[str] = []

    def capture(event: AgentEvent) -> None:
        captured.append(event.type)

    emitter = build_event_emitter(
        capture,
        recording=True,
        recording_keep=5,
        run_store=store,
    )
    emitter.turn_started("t1", "u1")
    assert captured == ["turn.started"]
    assert store.load_events("u1", thread_id="t1")


def test_replay_formatter() -> None:
    events = [
        AgentEvent("turn.started", thread_id="t", turn_id="u"),
        AgentEvent("agent.delta", thread_id="t", turn_id="u", data={"text": "Hello"}),
        AgentEvent(
            "turn.completed",
            thread_id="t",
            turn_id="u",
            data={"status": "completed", "estimated_tokens": 42},
        ),
    ]
    lines = replay_events_to_lines(events)
    assert any("assistant: Hello" in line for line in lines)
    assert any("turn completed" in line for line in lines)
    human = format_run_human(events)
    assert "Hello" in human
