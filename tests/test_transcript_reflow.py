from __future__ import annotations

from agent.tui.transcript_reflow import TranscriptReflowState


def test_observe_width_initial_no_reflow() -> None:
    state = TranscriptReflowState()
    assert state.observe_width(80) is False
    assert state.pending_reflow_width is None


def test_observe_width_change_sets_pending() -> None:
    state = TranscriptReflowState()
    state.observe_width(80)
    assert state.observe_width(100) is True
    assert state.pending_reflow_width == 100
    assert state.needs_rebuild(100) is True


def test_mark_rebuilt_clears_pending() -> None:
    state = TranscriptReflowState()
    state.observe_width(80)
    state.observe_width(100)
    state.mark_rebuilt(100)
    assert state.pending_reflow_width is None
    assert state.last_reflow_width == 100
    assert state.needs_rebuild(100) is False
