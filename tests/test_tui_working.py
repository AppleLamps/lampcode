"""Working status and approval dedupe in TUI view model."""

from agent.events import AgentEvent
from agent.tui.cells.base import ApprovalCell, AssistantMessageCell, WorkingCell
from agent.tui.view_model import TuiState, apply_event_to_state


def test_turn_started_updates_status_detail_without_working_cell() -> None:
    state = TuiState()
    state = apply_event_to_state(
        state,
        AgentEvent("turn.started", thread_id="t", turn_id="u", data={}),
    )
    assert state.status_detail == "Thinking…"
    assert not any(isinstance(c, WorkingCell) for c in state.transcript)


def test_agent_delta_removes_working_cell() -> None:
    state = TuiState()
    state = apply_event_to_state(
        state, AgentEvent("turn.started", thread_id="t", turn_id="u", data={})
    )
    state = apply_event_to_state(
        state,
        AgentEvent("agent.delta", thread_id="t", turn_id="u", data={"text": "Hi"}),
    )
    assert not any(isinstance(c, WorkingCell) for c in state.transcript)


def test_approval_requested_no_transcript_cell() -> None:
    state = TuiState()
    state = apply_event_to_state(
        state,
        AgentEvent(
            "approval.requested",
            thread_id="t",
            turn_id="u",
            data={"summary": "write_file: index.html", "tool_name": "write_file"},
        ),
    )
    assert state.pending_approval_summary
    assert not any(isinstance(c, ApprovalCell) for c in state.transcript)
    assert not any(isinstance(c, WorkingCell) for c in state.transcript)
    assert state.status_detail == "Waiting for your approval…"


def test_flush_assistant_buffer_dedupes_identical_text() -> None:
    state = TuiState()
    state.transcript.append(AssistantMessageCell(text="Same text.", streaming=False))
    state.assistant_buffer = "Same text."
    state = apply_event_to_state(
        state,
        AgentEvent("turn.completed", thread_id="t", turn_id="u", data={"status": "completed"}),
    )
    assert sum(isinstance(c, AssistantMessageCell) for c in state.transcript) == 1
