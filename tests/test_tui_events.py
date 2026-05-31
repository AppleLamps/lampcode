"""Tests for TUI event → state wiring."""

from agent.events import AgentEvent
from agent.tui.cells.base import (
    CompactionCell,
    PatchCell,
    PlanCell,
    ToolExecCell,
    TurnSummaryCell,
    UserMessageCell,
)
from agent.tui.view_model import TuiState, apply_event_to_state


def test_apply_event_agent_delta() -> None:
    state = TuiState()
    event = AgentEvent("agent.delta", thread_id="t", turn_id="u", data={"text": "Hi"})
    state = apply_event_to_state(state, event)
    assert state.assistant_buffer == "Hi"


def test_apply_event_tool_pending_and_completed() -> None:
    state = TuiState()
    state = apply_event_to_state(
        state,
        AgentEvent(
            "tool.pending",
            thread_id="t",
            turn_id="u",
            data={"tool_name": "run_command", "arguments": {"cmd": "pytest -q"}},
        ),
    )
    assert isinstance(state.transcript[-1], ToolExecCell)
    assert state.transcript[-1].status == "running"

    state = apply_event_to_state(
        state,
        AgentEvent(
            "tool.completed",
            thread_id="t",
            turn_id="u",
            data={
                "tool_name": "run_command",
                "status": "completed",
                "output": "1 passed",
                "exit_code": 0,
            },
        ),
    )
    cell = state.transcript[-1]
    assert isinstance(cell, ToolExecCell)
    assert cell.status == "completed"
    assert cell.output == "1 passed"


def test_apply_event_patch_completed() -> None:
    state = TuiState()
    state = apply_event_to_state(
        state,
        AgentEvent(
            "tool.pending",
            thread_id="t",
            turn_id="u",
            data={"tool_name": "apply_patch", "arguments": {}},
        ),
    )
    assert isinstance(state.transcript[-1], PatchCell)

    state = apply_event_to_state(
        state,
        AgentEvent(
            "tool.completed",
            thread_id="t",
            turn_id="u",
            data={
                "tool_name": "apply_patch",
                "status": "completed",
                "diff_preview": "+new line",
            },
        ),
    )
    cell = state.transcript[-1]
    assert isinstance(cell, PatchCell)
    assert "+new line" in cell.diff_text


def test_apply_event_plan_proposed() -> None:
    state = TuiState()
    state = apply_event_to_state(
        state,
        AgentEvent(
            "plan.proposed",
            thread_id="t",
            turn_id="u",
            data={"summary": "Fix auth", "text": "Step 1: read auth.py"},
        ),
    )
    assert isinstance(state.transcript[-1], PlanCell)
    assert state.transcript[-1].summary == "Fix auth"


def test_apply_event_compaction() -> None:
    state = TuiState()
    state = apply_event_to_state(
        state,
        AgentEvent("compaction", thread_id="t", data={"summarized_items": 10}),
    )
    assert isinstance(state.transcript[-1], CompactionCell)
    assert state.transcript[-1].kind == "started"

    state = apply_event_to_state(
        state,
        AgentEvent(
            "compaction.completed",
            thread_id="t",
            data={"removed_items": 5, "estimated_tokens_before": 1000, "estimated_tokens_after": 500},
        ),
    )
    assert state.transcript[-1].kind == "completed"


def test_apply_event_turn_completed() -> None:
    state = TuiState()
    state.assistant_buffer = "Done!"
    state.turn_model = "claude"
    state = apply_event_to_state(
        state,
        AgentEvent("turn.completed", thread_id="t", turn_id="u", data={"status": "completed"}),
    )
    assert state.assistant_buffer == ""
    assert isinstance(state.transcript[-1], TurnSummaryCell)


def test_apply_event_approval_pending() -> None:
    state = TuiState()
    event = AgentEvent(
        "approval.requested",
        thread_id="t",
        turn_id="u",
        data={"summary": "run_command: pytest", "tool_name": "run_command"},
    )
    state = apply_event_to_state(state, event)
    assert state.pending_approval_summary == "run_command: pytest"


def test_apply_event_approval_with_diff_preview() -> None:
    from agent.tui.cells.base import ApprovalCell, WorkingCell

    state = TuiState()
    state = apply_event_to_state(
        state,
        AgentEvent(
            "approval.requested",
            thread_id="t",
            turn_id="u",
            data={
                "summary": "apply_patch",
                "tool_name": "apply_patch",
                "diff_preview": "+added",
            },
        ),
    )
    assert state.pending_approval_diff == "+added"
    assert not any(isinstance(c, ApprovalCell) for c in state.transcript)
    assert not any(isinstance(c, WorkingCell) for c in state.transcript)
    assert state.status_detail == "Waiting for your approval…"


def test_turn_summary_includes_routing_and_fallback() -> None:
    state = TuiState()
    state.turn_model = "gpt-4"
    state.turn_cost = 0.01
    state.turn_fallback_used = True
    state.routing_note = "profile=fast"
    state = apply_event_to_state(
        state,
        AgentEvent("turn.completed", thread_id="t", turn_id="u", data={"status": "completed"}),
    )
    cell = state.transcript[-1]
    assert isinstance(cell, TurnSummaryCell)
    assert cell.fallback_used is True
    assert cell.routing_note == "profile=fast"


def test_apply_event_reasoning() -> None:
    from agent.tui.cells.base import ReasoningCell

    state = TuiState()
    state = apply_event_to_state(
        state,
        AgentEvent("agent.reasoning", thread_id="t", turn_id="u", data={"text": "Let me think..."}),
    )
    assert isinstance(state.transcript[-1], ReasoningCell)


def test_tool_completed_updates_group_cell() -> None:
    from agent.tui.cells.base import ToolGroupCell

    state = TuiState()
    group = ToolGroupCell(
        tool_names=["read_file", "read_file"],
        args_briefs=["a.py", "b.py"],
        status="running",
    )
    state.transcript.append(group)
    state.pending_tool_cell_id = group.cell_id
    state = apply_event_to_state(
        state,
        AgentEvent(
            "tool.completed",
            thread_id="t",
            turn_id="u",
            data={"tool_name": "read_file", "status": "completed"},
        ),
    )
    assert state.transcript[-1].status == "completed"
