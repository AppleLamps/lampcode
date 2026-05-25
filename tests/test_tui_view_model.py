from pathlib import Path

from agent.events import AgentEvent
from agent.models import Thread, Turn, UserMessageItem
from agent.tui.view_model import (
    apply_event_to_state,
    approval_key_to_response,
    filter_threads_by_cwd,
    handle_approval_key,
    thread_transcript_from_store,
    threads_to_entries,
)
from approval.gate import parse_approval_response


def test_filter_threads_by_cwd(tmp_path: Path) -> None:
    cwd_a = tmp_path / "a"
    cwd_b = tmp_path / "b"
    cwd_a.mkdir()
    cwd_b.mkdir()
    threads = [
        Thread(
            id="1",
            cwd=str(cwd_a),
            model="m",
            updated_at="2020-01-01T00:00:00+00:00",
        ),
        Thread(id="2", cwd=str(cwd_b), model="m"),
        Thread(
            id="3",
            cwd=str(cwd_a),
            model="m",
            updated_at="2020-01-02T00:00:00+00:00",
        ),
    ]
    filtered = filter_threads_by_cwd(threads, cwd_a)
    assert len(filtered) == 2
    assert filtered[0].id == "3"


def test_threads_to_entries() -> None:
    threads = [Thread(id="abc123456789", cwd="/tmp", model="m", title="fix bug")]
    entries = threads_to_entries(threads)
    assert entries[0].label == "fix bug"


def test_apply_event_agent_delta() -> None:
    from agent.tui.view_model import TuiState

    state = TuiState()
    event = AgentEvent("agent.delta", thread_id="t", turn_id="u", data={"text": "Hi"})
    state = apply_event_to_state(state, event)
    assert state.assistant_buffer == "Hi"


def test_apply_event_approval_pending() -> None:
    from agent.tui.view_model import TuiState

    state = TuiState()
    event = AgentEvent(
        "approval.requested",
        thread_id="t",
        turn_id="u",
        data={"summary": "run_command: pytest"},
    )
    state = apply_event_to_state(state, event)
    assert state.pending_approval_summary == "run_command: pytest"
    assert state.transcript[-1].role == "approval"


def test_handle_approval_keys() -> None:
    assert handle_approval_key("y")[0] is True
    assert handle_approval_key("n")[0] is False
    assert handle_approval_key("a")[0] is True
    assert handle_approval_key("A")[0] is True
    assert handle_approval_key("x")[0] is None


def test_approval_key_to_response() -> None:
    assert approval_key_to_response("y") == "y"
    assert approval_key_to_response("A") == "A"
    assert approval_key_to_response("bogus") is None


def test_parse_approval_response_turn_all() -> None:
    from approval.gate import TurnApprovalState

    state = TurnApprovalState()
    assert parse_approval_response("a", turn_state=state) is True
    assert state.approve_all is True


def test_apply_event_sync_and_checkpoint() -> None:
    from agent.tui.view_model import TuiState

    state = TuiState()
    state = apply_event_to_state(
        state,
        AgentEvent(
            "execution.sync.started",
            thread_id="t",
            turn_id="u",
            data={"direction": "push", "transport": "scp", "bytes_estimated": 1024},
        ),
    )
    assert "[sync push]" in state.transcript[-1].text
    state = apply_event_to_state(
        state,
        AgentEvent(
            "collab.checkpoint.saved",
            thread_id="t",
            turn_id="u",
            data={"path": "/tmp/cp.json"},
        ),
    )
    assert "[checkpoint]" in state.transcript[-1].text


def test_thread_transcript_from_store() -> None:
    thread = Thread(id="t", cwd="/tmp", model="m")
    turn = Turn()
    turn.items = [UserMessageItem(text="hello")]
    thread.turns.append(turn)
    lines = thread_transcript_from_store(thread)
    assert lines[0].role == "user"
    assert lines[0].text == "hello"
