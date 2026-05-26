"""Tests for persisting orphan live-stream text at turn end."""

from agent.tui.cells.base import AssistantMessageCell, ToolExecCell, TurnSummaryCell
from agent.tui.transcript_controller import TranscriptController
from agent.tui.view_model import TuiState


def test_commit_orphan_live_stream_appends_cell() -> None:
    state = TuiState()
    state.transcript.append(
        ToolExecCell(tool_name="run_command", args_brief="echo hi", status="completed")
    )
    ctrl = TranscriptController()
    ctrl._live_text = "Here is the summary of the project."

    ctrl.commit_orphan_live_stream(state)

    assert isinstance(state.transcript[-1], AssistantMessageCell)
    assert state.transcript[-1].text == "Here is the summary of the project."
    assert ctrl._live_text == ""


def test_commit_orphan_skips_duplicate_tail() -> None:
    state = TuiState()
    state.transcript.append(
        AssistantMessageCell(text="Already saved.", streaming=False)
    )
    ctrl = TranscriptController()
    ctrl._live_text = "Already saved."

    ctrl.commit_orphan_live_stream(state)

    assert len(state.transcript) == 1


def test_commit_orphan_skips_when_assistant_before_turn_summary() -> None:
    state = TuiState()
    state.transcript.append(
        AssistantMessageCell(text="Final answer.", streaming=False)
    )
    state.transcript.append(
        TurnSummaryCell(status="completed", model="m")
    )
    ctrl = TranscriptController()
    ctrl._live_text = "Final answer."

    ctrl.commit_orphan_live_stream(state)

    assert len(state.transcript) == 2
    assert isinstance(state.transcript[0], AssistantMessageCell)


def test_commit_orphan_appends_extension_after_prior_assistant() -> None:
    state = TuiState()
    state.transcript.append(
        AssistantMessageCell(text="Part one.", streaming=False)
    )
    ctrl = TranscriptController()
    ctrl._live_text = "Part one. Part two."

    ctrl.commit_orphan_live_stream(state)

    assert len(state.transcript) == 1
    assert state.transcript[0].text == "Part one. Part two."
